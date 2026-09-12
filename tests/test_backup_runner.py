"""Tests for the backup job runner.

No subprocess is spawned, no D-Bus call is made and no disc is touched: the
process is injected, and the clock with it, so watchdog tests are instant.
There is no sleep anywhere in this file.
"""

import dataclasses
import signal
import tempfile
import threading
import unittest
from pathlib import Path

from unittest import mock

from media_backup import tracks
from media_backup import events, model
from media_backup.config import Config
from media_backup.makemkv import isolation, outcome
from media_backup.makemkv.enumeration import DriveIndex
from media_backup.makemkv.runner import BackupRequest, BackupRunner

from . import makemkv_fixtures as fx
from .mkv_fixtures import write_mkv

DISC_SIZE = 4_556_390_400


#: What DVD_SCAN says the feature weighs and how long it runs. Selection picks
#: that title alone at the default policy: 1:42:39 against extras of 2:32.
FEATURE_BYTES = 4_245_336_064
FEATURE_SECONDS = 6159
#: Every title DVD_SCAN reports, which is now every title that gets saved:
#: the feature and its three clips, as (seconds, bytes).
DVD_SCAN_TITLES = ((FEATURE_SECONDS, FEATURE_BYTES), (152, 99_866_624),
                   (152, 101_634_048), (152, 80_885_760))


#: A transcript standing for a process that emits nothing and never exits --
#: what a drive that hangs MakeMKV's probe actually does. Only a signal ends
#: it, which is the behaviour the idle watchdogs have to produce.
SILENT = object()


class FastClock:
    """A clock that jumps forward on every reading.

    Watchdog tests need time to pass while nothing happens, and a silent
    process gives no line to hang a clock bump on.
    """

    def __init__(self, step):
        self.step = step
        self.now = 0.0

    def __call__(self):
        self.now += self.step
        return self.now


class FakeProcess:
    """Replays a captured transcript instead of running makemkvcon."""

    def __init__(self, lines, exit_code=0, on_line=None):
        self._silent = lines is SILENT
        self._lines = [] if self._silent else list(lines)
        self._exit_code = exit_code
        self._on_line = on_line
        self.signals = []
        self.waited = False
        self._stopped = threading.Event()

    def lines(self):
        if self._silent:
            self._stopped.wait(10)      # ends only when signalled
            return
        for line in self._lines:
            if self._stopped.is_set():
                return
            if self._on_line is not None:
                self._on_line(self, line)
            yield line if line.endswith("\n") else line + "\n"

    def wait(self):
        self.waited = True
        return self._exit_code

    def alive(self):
        return not self._stopped.is_set()

    def signal(self, sig):
        self.signals.append(sig)
        self._stopped.set()


class Harness:
    """Collects the events a runner emits and scripts its processes."""

    def __init__(self, transcripts, exit_code=0, on_line=None):
        self.transcripts = list(transcripts)
        self.exit_code = exit_code
        self.on_line = on_line
        self.events = []
        self.argvs = []
        self.processes = []
        self.ejects = []
        self.now = 1000.0

    def spawn(self, argv):
        self.argvs.append(argv)
        lines = self.transcripts.pop(0) if self.transcripts else []
        if lines is SILENT:
            self.processes.append(proc := FakeProcess(SILENT))
            return proc
        saving = "mkv" in argv
        code = self.exit_code if saving else 0
        proc = FakeProcess(lines, code, self.on_line if saving else None)
        self.processes.append(proc)
        return proc

    def clock(self):
        return self.now

    def eject(self, drive_path, block_path, mounted=False):
        self.ejects.append((drive_path, block_path, mounted))

    def emit(self, event):
        self.events.append(event)

    # -- queries ------------------------------------------------------------

    @property
    def states(self):
        return [e.state for e in self.events if e.kind == events.STATE]

    @property
    def final(self):
        terminal = [e for e in self.events if e.is_terminal]
        assert len(terminal) == 1, f"expected one FINISHED, got {len(terminal)}"
        return terminal[0]

    def progress_events(self):
        return [e for e in self.events if e.kind == events.PROGRESS and e.total_pct]


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # One shape for every disc now: mkv saves titles into a directory and
        # is content to find an empty one already there.
        self.dest = self.root / "data"
        self.dest.mkdir()
        # isolate_drives off: the sandbox is about real drives, and these
        # tests never spawn a process. tests/test_makemkv_isolation.py has it.
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0,
                          use_stdbuf=False, isolate_drives=False)

    def request(self, **kw):
        base = dict(
            job_id="job-1", collection_id="c1", disc_id="d1",
            device=fx.SR1, expected_label=fx.SR1_LABEL,
            disc_size_bytes=DISC_SIZE, dest=self.dest,
            log_path=self.root / "logs" / "attempt-1.log", attempt=1, cfg=self.cfg,
            drive_object_path="/drives/x", block_object_path="/blocks/sr1",
        )
        base.update(kw)
        return BackupRequest(**base)

    def make_output(self, titles=None, ratio=0.99, count=None):
        """Write the .mkv files a saved-titles run leaves behind.

        Real Matroska headers, because the run reads each file back and
        compares its duration to what the disc said. Sparse past the header:
        judging reads the declared duration and ``st_size``, never the frames.

        ``titles`` is (seconds, size) per file, defaulting to the one title
        DVD_SCAN's feature yields. Sizes differ per file on purpose -- that is
        what lets match_files pair them up.
        """
        if titles is None:
            titles = (list(DVD_SCAN_TITLES) if count is None
                      else [(FEATURE_SECONDS + i, FEATURE_BYTES // count + i)
                            for i in range(count)])
        for i, (seconds, size) in enumerate(titles):
            write_mkv(self.dest / f"Fresh Horses-A{i}_t0{i}.mkv",
                      seconds, max(1, int(size * ratio)))

    def scripted(self, saving=None, scan=None, saves=4):
        """The processes a run spawns: enumerate, scan, then one save a title.

        ``saves`` defaults to the four titles DVD_SCAN reports, since every
        title is now copied rather than a chosen one.
        """
        return Harness([fx.ENUMERATION_LINES,
                        (scan or fx.DVD_SCAN).splitlines()]
                       + [(saving or fx.MKV_SUCCESS).splitlines()] * saves)

    #: What the fake machine reports free. Larger than any fixture disc so a
    #: test asserting what a run *does* is not also asserting how much room
    #: the host happens to have -- the suite runs on an 8 GB tmpfs and the
    #: Hancock fixture alone declares 42 GB of titles.
    FREE_SPACE = 10 * 1024**4

    #: Injected for the same reason FREE_SPACE is. The real one runs mkvmerge
    #: and mkvpropedit against the file just written, and these tests write
    #: fixtures that are not Matroska at all -- one is a 4.4 GB hole with no
    #: header, which mkvmerge reads to the end looking for one. The flagging
    #: itself is tested in test_tracks.py, without a subprocess.
    @staticmethod
    def no_reflagging(path):
        return []

    def run_job(self, harness, request=None):
        runner = BackupRunner(
            request or self.request(), harness.emit,
            spawn=harness.spawn, clock=harness.clock, ejector=harness.eject,
            free_space=lambda: self.FREE_SPACE,
            set_default_audio=self.no_reflagging)
        runner.start()
        runner.join(timeout=10)
        return runner


class TestSuccessfulBackup(RunnerTestCase):
    def setUp(self):
        super().setUp()
        self.h = self.scripted()
        self.h.on_line = lambda proc, line: self.make_output()
        self.run_job(self.h)

    def test_reports_success(self):
        self.assertEqual(self.h.final.verdict.outcome, outcome.SUCCESS)

    def test_moves_through_the_expected_states(self):
        self.assertEqual(
            self.h.states,
            [model.RESOLVING, model.SCANNING, model.COPYING, model.VERIFYING,
             model.EJECTING])

    def test_resolves_the_device_to_its_disc_index(self):
        saving = [a for a in self.h.argvs if "mkv" in a][0]
        self.assertIn("disc:1", saving, "sr1 is disc:1 in the fixture")
        self.assertEqual(saving[-2], "0", "and title 0 is the feature")

    def test_ejects_a_good_disc(self):
        self.assertEqual(len(self.h.ejects), 1)
        self.assertEqual(self.h.ejects[0][0], "/drives/x")

    def test_writes_the_log(self):
        log = (self.root / "logs" / "attempt-1.log").read_text()
        self.assertIn("Copy complete.", log)
        self.assertTrue(log.startswith("# "), "the command line is recorded first")

    def test_emits_progress(self):
        self.assertTrue(self.h.progress_events())
        self.assertAlmostEqual(self.h.progress_events()[-1].total_pct, 100.0)


class TestTheDefaultAudioTrack(RunnerTestCase):
    """Every written file gets an English audio track flagged as the default.

    The flagging itself is tested in test_tracks.py. What matters here is that
    the run does it, does it to the file it just wrote, records it, and is not
    derailed when it cannot.
    """

    def scripted_with(self, flagger, cfg=None):
        h = self.scripted()
        h.on_line = lambda proc, line: self.make_output()
        runner = BackupRunner(
            self.request(**({"cfg": cfg} if cfg else {})), h.emit,
            spawn=h.spawn, clock=h.clock,
            ejector=h.eject, free_space=lambda: self.FREE_SPACE,
            set_default_audio=flagger)
        runner.start()
        runner.join(timeout=10)
        return h

    def test_every_file_written_is_flagged_and_only_after_it_exists(self):
        seen = []

        def recorder(path):
            assert path.exists(), f"{path.name} flagged before it was written"
            seen.append(path)
            return [tracks.Change(2, "flag-default", True, "test")]

        h = self.scripted_with(recorder)
        self.assertEqual(sorted(p.name for p in seen),
                         ["Fresh Horses-A0_t00.mkv", "Fresh Horses-A1_t01.mkv",
                          "Fresh Horses-A2_t02.mkv", "Fresh Horses-A3_t03.mkv"])
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)

    def test_files_already_defaulting_to_english_are_not_counted(self):
        """An empty change list means the header was left alone."""
        h = self.scripted_with(lambda path: [])
        self.assertEqual(h.final.verdict.detail["tracks_reflagged"], 0)

    def test_reflagged_files_are_counted_in_the_report(self):
        h = self.scripted_with(
            lambda path: [tracks.Change(2, "flag-default", True, "test")])
        self.assertEqual(h.final.verdict.detail["tracks_reflagged"], 4)

    def test_a_header_that_cannot_be_written_does_not_fail_the_disc(self):
        """The copy is correct either way; the wrong track just plays first.

        Failing a 27 GB backup over a flag would be the worst trade in the
        program.
        """
        def raiser(path):
            raise tracks.TrackError("mkvpropedit: read-only file system")

        h = self.scripted_with(raiser)
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)
        self.assertEqual(h.final.verdict.detail["tracks_reflagged"], 0)

    def test_the_step_can_be_turned_off(self):
        """An operator who wants the disc's own choice left alone."""
        seen = []
        h = self.scripted_with(
            lambda path: seen.append(path) or [],
            cfg=dataclasses.replace(self.cfg, default_audio_english=False))
        self.assertEqual(seen, [])
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)


class TestFailures(RunnerTestCase):
    def test_dirty_disc_fails_and_counts_read_errors(self):
        h = self.scripted(saving=fx.MKV_DIRTY_DISC, scan=fx.ONE_TITLE_SCAN,
                          saves=1)
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.FAILURE)
        self.assertEqual(h.final.observation.read_error_count, 3)

    def test_a_failed_disc_is_not_ejected(self):
        """It must stay in the drive so the operator can clean and retry."""
        h = self.scripted(saving=fx.MKV_DIRTY_DISC)
        self.run_job(h)
        self.assertEqual(h.ejects, [])

    def test_read_error_storm_is_not_forwarded_to_the_gui(self):
        h = self.scripted(saving=fx.MKV_DIRTY_DISC)
        self.run_job(h)
        codes = [e.code for e in h.events if e.kind == events.MESSAGE]
        self.assertNotIn(2003, codes, "counted, not forwarded one by one")

    def test_permissions_failure_explains_itself(self):
        h = self.scripted(saving=fx.MKV_NO_ACCESS)
        self.run_job(h)
        self.assertIn("permissions", h.final.verdict.reason)

    def test_a_copy_that_stopped_early_fails_despite_a_success_message(self):
        """The check that matters: the file is shorter than the disc says.

        Measured, not inferred from size. MakeMKV reports a title's size on
        the disc and a remux comes in under it -- 84% on a Blu-ray -- so a
        size ratio cannot tell a short copy from an ordinary one. A duration
        can: a copy that stopped early is short.
        """
        h = self.scripted(scan=fx.ONE_TITLE_SCAN, saves=1)
        h.on_line = lambda proc, line: self.make_output(
            [(FEATURE_SECONDS * 0.2, FEATURE_BYTES // 5)])
        self.run_job(h)

        self.assertEqual(h.final.verdict.outcome, outcome.FAILURE)
        self.assertIn("run short", h.final.verdict.reason)
        self.assertEqual(h.final.observation.titles_short, 1)

    def test_a_file_that_will_not_say_its_length_is_not_called_verified(self):
        """Saying "success" on evidence this thin is how a lie gets told."""
        def sparse_bytes(proc, line):
            self.dest.mkdir(parents=True, exist_ok=True)
            with (self.dest / "Fresh Horses-A0_t00.mkv").open("wb") as handle:
                handle.truncate(int(FEATURE_BYTES * 0.99))

        h = self.scripted(scan=fx.ONE_TITLE_SCAN, saves=1)
        h.on_line = sparse_bytes
        self.run_job(h)

        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS_UNVERIFIED)
        self.assertIn("would not say how long", h.final.verdict.reason)
        self.assertTrue(h.final.verdict.is_good, "kept, but not claimed as checked")

    def test_a_full_length_copy_passes_at_84_percent_of_the_reported_size(self):
        """Hancock's real numbers, which a 0.90 size floor failed twice."""
        h = self.scripted()
        h.on_line = lambda proc, line: self.make_output(ratio=0.84)
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)

    def test_unknown_device_never_starts_a_backup(self):
        h = Harness([fx.ENUMERATION_LINES])
        self.run_job(h, self.request(device="/dev/sr9"))
        self.assertEqual(h.final.error_kind, "device_not_found")
        self.assertFalse(any("backup" in a for a in h.argvs))

    def test_wrong_disc_is_refused_before_copying(self):
        """The operator picked one disc and a different one is loaded."""
        h = Harness([fx.ENUMERATION_LINES])
        self.run_job(h, self.request(expected_label="SOME_OTHER_FILM"))
        self.assertEqual(h.final.error_kind, "disc_changed")
        self.assertFalse(any("backup" in a for a in h.argvs))

    def test_non_empty_destination_is_refused(self):
        """makemkvcon would reject it with MSG:5068 anyway."""
        (self.dest / "leftover").write_text("x")
        h = Harness([fx.ENUMERATION_LINES])
        self.run_job(h)
        self.assertEqual(h.final.error_kind, model.ERR_COPY)
        self.assertIn("not empty", h.final.verdict.reason)

    def test_insufficient_space_is_refused_without_touching_the_drive(self):
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=10**18,
                          isolate_drives=False)
        h = Harness([fx.ENUMERATION_LINES])
        self.run_job(h, self.request(cfg=self.cfg))
        self.assertEqual(h.final.error_kind, model.ERR_NO_SPACE)
        self.assertFalse(any("backup" in a for a in h.argvs))

    def test_a_selection_bigger_than_the_disc_is_measured_against_the_disk(self):
        """Step 2 checks the disc; this checks what will actually be written.

        Power Rangers, 2026-09-11: a 46.6 GiB disc that fits the free-space
        check comfortably, and a selection of 308 titles at 24.5 GB apiece
        that does not. The disc's own size says nothing about the run -- a
        disc authoring its feature more than once writes more than it holds,
        and Hancock already writes 149 GB from 45.

        Refused before the first title: a run that fills the volume takes
        every other job on it down with it.
        """
        two_cuts = "\n".join([
            'TCOUNT:2',
            'TINFO:0,2,0,"Film"', 'TINFO:0,9,0,"1:32:13"',
            'TINFO:0,11,0,"20000000000"', 'TINFO:0,26,0,"1,2,3,4"',
            'TINFO:1,2,0,"Film"', 'TINFO:1,9,0,"1:42:14"',
            'TINFO:1,11,0,"22000000000"', 'TINFO:1,26,0,"1,5,3,6"',
        ])
        h = self.scripted(scan=two_cuts)
        runner = BackupRunner(
            self.request(), h.emit, spawn=h.spawn, clock=h.clock,
            ejector=h.eject, set_default_audio=self.no_reflagging,
            # Room for the 4.5 GB disc, nowhere near the 42 GB of titles.
            free_space=lambda: 20 * 1024**3)
        runner.start()
        runner.join(timeout=10)

        self.assertEqual(h.final.error_kind, model.ERR_NO_SPACE)
        self.assertIn("title(s) come to", h.final.verdict.reason)
        self.assertFalse([a for a in h.argvs if "mkv" in a],
                         "refused before the first title, not during the run")


class TestCancellation(RunnerTestCase):
    def test_cancel_signals_the_process_group_and_reports_cancelled(self):
        started = threading.Event()
        release = threading.Event()

        def block_midway(proc, line):
            if "PRGV:65536" in line:
                started.set()
                release.wait(5)

        h = self.scripted()
        h.on_line = block_midway
        runner = BackupRunner(self.request(), h.emit, spawn=h.spawn,
                              clock=h.clock, ejector=h.eject,
                              set_default_audio=self.no_reflagging)
        runner.start()
        self.assertTrue(started.wait(5), "the fake process never got going")
        runner.cancel()
        release.set()
        runner.join(timeout=10)

        self.assertIn(signal.SIGTERM, h.processes[-1].signals)
        self.assertEqual(h.final.verdict.outcome, outcome.CANCELLED)

    def test_a_cancelled_job_is_not_ejected(self):
        h = self.scripted()
        runner = BackupRunner(self.request(), h.emit, spawn=h.spawn,
                              clock=h.clock, ejector=h.eject)
        runner.cancel()
        runner.start()
        runner.join(timeout=10)
        self.assertEqual(h.ejects, [])


class TestWatchdogs(RunnerTestCase):
    def test_a_silent_process_is_stopped(self):
        """Time is injected, so this is instant."""
        h = self.scripted()

        def jump_the_clock(proc, line):
            h.now += self.cfg.stall_timeout_s + 1

        h.on_line = jump_the_clock
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.CANCELLED)
        self.assertIn("no output for", h.final.observation.stall_reason)

    def test_an_overrunning_job_is_stopped(self):
        h = self.scripted()

        def jump_the_clock(proc, line):
            h.now += self.cfg.max_job_duration_s + 1

        h.on_line = jump_the_clock
        self.run_job(h)
        self.assertIn("gave up after", h.final.observation.stall_reason)

    def test_a_probe_that_never_speaks_is_given_up_on(self):
        """The case the line-driven watchdogs could never see.

        A drive that hangs MakeMKV's probe emits nothing at all -- no DRV
        rows, no messages -- while burning CPU. Watchdogs driven by arriving
        lines never run, so the job waited forever. Measured on an LG GHA2N,
        2026-09-07.
        """
        h = Harness([SILENT])          # the enumeration never says a word
        runner = BackupRunner(
            self.request(), h.emit, spawn=h.spawn,
            clock=FastClock(self.cfg.probe_timeout_s + 1), ejector=h.eject)
        runner.start()
        runner.join(timeout=10)

        self.assertIn(signal.SIGTERM, h.processes[-1].signals)
        self.assertEqual(h.ejects, [], "a wedged drive must not be ejected")

    def test_a_silent_save_is_stopped(self):
        """stall_timeout_s now applies to silence, not only to slow output."""
        h = Harness([fx.ENUMERATION_LINES,
                     fx.DVD_SCAN.splitlines(),
                     SILENT])
        runner = BackupRunner(
            self.request(), h.emit, spawn=h.spawn,
            clock=FastClock(self.cfg.stall_timeout_s + 1), ejector=h.eject)
        runner.start()
        runner.join(timeout=10)

        self.assertIn(signal.SIGTERM, h.processes[-1].signals)
        self.assertEqual(h.final.verdict.outcome, outcome.CANCELLED)


class TestTitleScan(RunnerTestCase):
    def test_records_the_title_inventory(self):
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0, isolate_drives=False)
        h = self.scripted(scan=fx.DISC_SCAN)
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        titles = h.final.titles
        self.assertEqual(len(titles), 2)
        self.assertEqual(titles[0].name, "Spider-Man: Across The Spider-Verse")
        self.assertEqual(titles[0].size_bytes, 31506235392)
        self.assertEqual(titles[0].duration, "2:20:05")
        self.assertIn(model.SCANNING, h.states)

    def test_identifies_the_disc_from_its_cinfo(self):
        """The answer to "why does that DVD say DVD_VIDEO?".

        The volume label is a generic stamp; MakeMKV names the disc anyway,
        and the scan was already fetching it and dropping it on the floor.
        """
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0, isolate_drives=False)
        h = self.scripted(scan=fx.DVD_SCAN)
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        identified = [e for e in h.events if e.kind == events.IDENTIFIED]
        self.assertEqual(len(identified), 1)
        self.assertEqual(identified[0].disc_name, "Fresh Horses")
        self.assertEqual(identified[0].disc_type, "DVD disc")
        self.assertEqual(len(identified[0].titles), 4)

    def test_identification_arrives_before_the_copy_starts(self):
        """An hour into a copy is too late to stop saying "DVD_VIDEO"."""
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0, isolate_drives=False)
        h = self.scripted(scan=fx.DVD_SCAN)
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        kinds = [e.kind for e in h.events]
        states = [e.state for e in h.events if e.kind == events.STATE]
        self.assertLess(kinds.index(events.IDENTIFIED),
                        kinds.index(events.PROGRESS))
        self.assertIn(model.COPYING, states)

    def test_a_disc_with_no_cinfo_is_named_after_its_feature(self):
        """CINFO:2 is not established for DVDs, so there is a fallback."""
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0, isolate_drives=False)
        h = self.scripted(scan=fx.DVD_SCAN_NO_CINFO)
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        identified = [e for e in h.events if e.kind == events.IDENTIFIED][0]
        self.assertEqual(identified.disc_name, "Fresh Horses",
                         "the largest title, not the first one")
        self.assertEqual(identified.disc_type, "")


class TestTitleSelection(RunnerTestCase):
    """What gets saved, and which discs are handed back to the operator."""

    def test_every_title_on_the_disc_is_saved(self):
        """The feature and its three clips, one run each.

        Nothing here decides which of them is the film. That question moved
        to publish time on 2026-09-08, after a length threshold twice threw
        away the second feature of a double bill.
        """
        h = self.scripted()
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h)

        saving = [a for a in h.argvs if "mkv" in a]
        self.assertEqual(len(saving), 4, "one run per title")
        self.assertEqual(sorted(a[-2] for a in saving), ["0", "1", "2", "3"])
        self.assertEqual(saving[0][-2], "0", "the 1:42:39 feature goes first")
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)
        self.assertEqual(h.final.observation.titles_expected, 4)

    def test_each_chosen_title_gets_its_own_run(self):
        """Regression: a length filter cannot say "these two of the four".

        Hancock offers its feature twice, so `mkv ... all --minlength` wrote
        88 GB where the film is 44, and the second copy was claimed by no
        title. Naming each title is what stops that.
        """
        two_cuts = "\n".join([
            fx.ENUMERATION_LINES[0], "TCOUNT:2",
            'TINFO:0,2,0,"Film"', 'TINFO:0,9,0,"1:32:13"',
            'TINFO:0,11,0,"20000000000"', 'TINFO:0,26,0,"1,2,3,4"',
            'TINFO:1,2,0,"Film"', 'TINFO:1,9,0,"1:42:14"',
            'TINFO:1,11,0,"22000000000"', 'TINFO:1,26,0,"1,5,3,6"',
        ])
        h = self.scripted(scan=two_cuts)
        h.on_line = lambda proc, line: self.make_output(
            [(6134, 22_000_000_000), (5533, 20_000_000_000)])
        self.run_job(h)

        saving = [a for a in h.argvs if "mkv" in a]
        self.assertEqual(len(saving), 2, "one run per cut")
        self.assertEqual(sorted(a[-2] for a in saving), ["0", "1"])
        self.assertEqual(h.final.observation.titles_expected, 2)
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)

    def test_writing_the_same_footage_twice_is_flagged(self):
        """A floor alone waves a doubled run through; Hancock's ratio was 2.0."""
        h = self.scripted(scan=fx.ONE_TITLE_SCAN, saves=1)
        h.on_line = lambda proc, line: self.make_output(
            [(FEATURE_SECONDS, FEATURE_BYTES)], ratio=2.0)
        self.run_job(h)

        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS_UNVERIFIED)
        self.assertIn("more than once", h.final.verdict.reason)
        self.assertTrue(h.final.verdict.is_good, "the bytes are all there")

    def test_forty_titles_of_one_length_are_copied_rather_than_refused(self):
        """Length alone never refuses a disc, and this fixture is only length.

        Telling a decoy from a feature by runtime was the same guess as
        telling a feature from an episode, and getting it wrong lost real
        films. That has not changed: forty titles at 2:18 could as easily be
        a box set.

        Note what this scan does *not* carry -- a clip list. Since 2026-09-11
        a disc is refused when many titles are the same clips in different
        orders (see makemkv.selection.obfuscation), and no TINFO:26 here means
        there is nothing for that to see. A real obfuscated disc reports its
        clip lists and is caught; one that somehow did not would still be
        copied in full, as this one is.
        """
        h = self.scripted(scan=fx.DECOY_SCAN)
        h.on_line = lambda proc, line: self.make_output(
            [(8280 + i, 30_000_000_000 + i) for i in range(40)])
        self.run_job(h)

        self.assertEqual(h.final.error_kind, "")
        self.assertEqual(len([a for a in h.argvs if "mkv" in a]), 40)

    def test_the_forensics_parser_reads_a_scan_the_same_way_this_does(self):
        """Two parsers, one attribute mapping. Pin them together.

        The runner reads a live process and fills in stream counts as it
        goes; media_backup.forensics reads a transcript already on disk. They
        map the same TINFO attributes onto the same fields, and a corpus
        analysed differently from the app that produced it would be a corpus
        that answers a slightly different question.
        """
        from media_backup import forensics

        h = self.scripted(scan=fx.DVD_SCAN)
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h)

        live = {t.index: t for t in h.final.titles}
        saved = {t.index: t for t in forensics.titles_from_scan(fx.DVD_SCAN)}
        self.assertEqual(sorted(live), sorted(saved), "same titles found")
        for index, title in live.items():
            other = saved[index]
            self.assertEqual(
                (title.duration, title.segments, title.source,
                 title.size_bytes, title.chapters),
                (other.duration, other.segments, other.source,
                 other.size_bytes, other.chapters),
                f"title {index} read differently")

    def test_short_titles_are_saved_like_any_others(self):
        """Three minutes was once too short to be worth saving. No longer."""
        short = "\n".join(
            [fx.ENUMERATION_LINES[0], "TCOUNT:3"]
            + [f'TINFO:{i},9,0,"0:03:0{i}"\nTINFO:{i},11,0,"100000"'
               for i in range(3)])
        h = self.scripted(scan=short, saves=3)
        h.on_line = lambda proc, line: self.make_output(
            [(180 + i, 100_000 + i) for i in range(3)])
        self.run_job(h)

        self.assertEqual(len([a for a in h.argvs if "mkv" in a]), 3)

    def test_a_disc_with_no_titles_at_all_is_refused(self):
        """The one refusal left: there is nothing to copy."""
        h = self.scripted(scan="\n".join([fx.ENUMERATION_LINES[0], "TCOUNT:0"]))
        self.run_job(h)

        self.assertEqual(h.final.error_kind, model.ERR_NO_FEATURE)
        self.assertFalse(any("mkv" in a for a in h.argvs))


class TestRobustness(RunnerTestCase):
    def test_a_spawn_failure_is_reported_once(self):
        h = self.scripted()

        def explode(argv):
            if "mkv" in argv:
                raise OSError("no such binary")
            return h.__class__.spawn(h, argv)

        runner = BackupRunner(self.request(), h.emit, spawn=explode,
                              clock=h.clock, ejector=h.eject)
        runner.start()
        runner.join(timeout=10)
        self.assertEqual(h.final.error_kind, model.ERR_SPAWN)  # exactly one FINISHED

    def test_garbage_output_does_not_kill_the_worker(self):
        h = Harness([fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                     ["\x00garbage", "MSG:", "PRGV:a,b,c", 'DRV:"unterminated',
                      'MSG:5036,260,1,"Copy complete. 1 titles saved.",'
                      '"Copy complete. %1 titles saved.","1"']])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)

    def test_log_is_truncated_rather_than_growing_without_bound(self):
        """A read-error storm can produce an enormous log."""
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0,
                          max_log_bytes=200, isolate_drives=False)
        noisy = ['MSG:2003,0,0,"read error at %d","x"' % i for i in range(500)]
        noisy.append('MSG:5081,0,0,"Backup done.","Backup done."')
        h = Harness([fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(), noisy])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))
        log = (self.root / "logs" / "attempt-1.log").read_text()
        self.assertIn("log truncated", log)
        self.assertLess(len(log), 2000)
        self.assertEqual(h.final.observation.read_error_count, 500,
                         "counting is unaffected by log truncation")


class TestIsolatedJobsDoNotShareADriveList(RunnerTestCase):
    """An isolated enumeration is about one drive, so it cannot be lent out.

    The shared DriveIndex exists so a round of jobs runs one enumeration
    between them instead of N, each of which probes every drive. Isolation
    removes that cost -- a sandboxed probe touches one drive -- and makes the
    sharing wrong: a list built inside sr0's sandbox names only sr0, and sr1's
    job would resolve it to device_not_found and never start.
    """

    def setUp(self):
        super().setUp()
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0,
                          use_stdbuf=False, isolate_drives=True)
        # A stand-in for the real sandbox: this machine's /dev decides what
        # the real one produces, and a test may not depend on that.
        patcher = mock.patch.object(isolation, "prefix",
                                    lambda cfg, device: ["/bin/sh", "--"])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.shared = DriveIndex()

    def run_isolated(self, device, label, enumeration):
        # A destination per job, as the store gives every disc its own. Two
        # jobs sharing one would fail the not-empty check, which is a real
        # rule about real discs and nothing to do with what is under test.
        self.dest = self.root / f"data{Path(device).name}"
        self.dest.mkdir(exist_ok=True)
        harness = Harness([enumeration.splitlines(),
                           fx.DVD_SCAN.splitlines(),
                           fx.MKV_SUCCESS_ONE.splitlines()])
        harness.on_line = lambda proc, line: self.make_output()
        runner = BackupRunner(
            self.request(cfg=self.cfg, device=device, expected_label=label,
                         dest=self.dest,
                         log_path=self.root / "logs" / f"{Path(device).name}.log"),
            harness.emit, spawn=harness.spawn, clock=harness.clock,
            ejector=harness.eject, drive_index=self.shared)
        runner.start()
        runner.join(timeout=10)
        return harness

    def test_each_job_enumerates_inside_its_own_sandbox(self):
        first = self.run_isolated(fx.SR0, fx.SR0_LABEL, fx.ISOLATED_SR0)
        second = self.run_isolated(fx.SR1, fx.SR1_LABEL, fx.ISOLATED_SR1)

        self.assertEqual(first.final.verdict.outcome, outcome.SUCCESS)
        self.assertEqual(second.final.verdict.outcome, outcome.SUCCESS,
                         "sr1 resolved its own drive, not the list sr0 made")
        self.assertEqual(len(second.argvs), 6,
                         "enumerate, scan, four saves -- it got past resolving")

    def test_the_isolated_disc_index_is_the_sandbox_s_own(self):
        """Index 0 for sr1, which is index 1 when every drive is listed."""
        harness = self.run_isolated(fx.SR1, fx.SR1_LABEL, fx.ISOLATED_SR1)
        saving = [a for a in harness.argvs if "mkv" in a][0]
        self.assertIn("disc:0", saving)


if __name__ == "__main__":
    unittest.main()
