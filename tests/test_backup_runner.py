"""Tests for the backup job runner.

No subprocess is spawned, no D-Bus call is made and no disc is touched: the
process is injected, and the clock with it, so watchdog tests are instant.
There is no sleep anywhere in this file.
"""

import signal
import tempfile
import threading
import unittest
from pathlib import Path

from media_backup import events, model
from media_backup.config import Config
from media_backup.makemkv import outcome
from media_backup.makemkv.runner import BackupRequest, BackupRunner

from . import makemkv_fixtures as fx

DISC_SIZE = 4_556_390_400


def make_iso(path, ratio=0.99):
    """A sparse stand-in for a decrypted DVD image.

    Judging reads ``st_size`` and the ISO 9660 descriptor at 0x8001, never the
    rest of the bytes, so materialising 4.5 GB would cost gigabytes of RAM to
    prove nothing. This file is what a real DVD backup produces -- measured
    2026-09-06, ``file`` calls it "ISO 9660 CD-ROM filesystem data".
    """
    from media_backup.makemkv.inspect import ISO_MAGIC, ISO_MAGIC_OFFSET
    with path.open("wb") as handle:
        handle.truncate(int(DISC_SIZE * ratio))
        handle.seek(ISO_MAGIC_OFFSET)
        handle.write(ISO_MAGIC)


class FakeProcess:
    """Replays a captured transcript instead of running makemkvcon."""

    def __init__(self, lines, exit_code=0, on_line=None):
        self._lines = list(lines)
        self._exit_code = exit_code
        self._on_line = on_line
        self.signals = []
        self.waited = False
        self._stopped = threading.Event()

    def lines(self):
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
        code = self.exit_code if "backup" in argv else 0
        proc = FakeProcess(lines, code, self.on_line if "backup" in argv else None)
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
        # A Blu-ray destination: a directory, which makemkvcon is content to
        # find already there so long as it is empty. The DVD shape -- an
        # absent path that becomes an ISO -- is covered separately below.
        self.dest = self.root / "data"
        self.dest.mkdir()
        self.cfg = Config(media_path=self.root, scan_titles=False,
                          min_free_margin_bytes=0, use_stdbuf=False)

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

    def make_output(self, ratio=0.99):
        """Create a plausible finished BDMV tree in the destination.

        The stream file is sparse. Judging reads ``st_size`` and never the
        bytes, so materialising a disc-sized buffer would cost gigabytes of
        RAM -- twice over on the usual tmpfs /tmp -- to prove nothing.
        """
        (self.dest / "BDMV" / "STREAM").mkdir(parents=True, exist_ok=True)
        (self.dest / "BDMV" / "index.bdmv").write_bytes(b"x" * 64)
        with (self.dest / "BDMV" / "STREAM" / "00001.m2ts").open("wb") as stream:
            stream.truncate(max(1, int(DISC_SIZE * ratio) - 64))

    def run_job(self, harness, request=None):
        runner = BackupRunner(
            request or self.request(), harness.emit,
            spawn=harness.spawn, clock=harness.clock, ejector=harness.eject)
        runner.start()
        runner.join(timeout=10)
        return runner


class TestSuccessfulBackup(RunnerTestCase):
    def setUp(self):
        super().setUp()
        self.h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])
        self.h.on_line = lambda proc, line: self.make_output()
        self.run_job(self.h)

    def test_reports_success(self):
        self.assertEqual(self.h.final.verdict.outcome, outcome.SUCCESS)

    def test_moves_through_the_expected_states(self):
        self.assertEqual(
            self.h.states,
            [model.RESOLVING, model.COPYING, model.VERIFYING, model.EJECTING])

    def test_resolves_the_device_to_its_disc_index(self):
        backup_argv = [a for a in self.h.argvs if "backup" in a][0]
        self.assertIn("disc:1", backup_argv, "sr1 is disc:1 in the fixture")

    def test_ejects_a_good_disc(self):
        self.assertEqual(len(self.h.ejects), 1)
        self.assertEqual(self.h.ejects[0][0], "/drives/x")

    def test_writes_the_log(self):
        log = (self.root / "logs" / "attempt-1.log").read_text()
        self.assertIn("Backup done.", log)
        self.assertTrue(log.startswith("# "), "the command line is recorded first")

    def test_emits_progress(self):
        self.assertTrue(self.h.progress_events())
        self.assertAlmostEqual(self.h.progress_events()[-1].total_pct, 100.0)


class TestFailures(RunnerTestCase):
    def test_dirty_disc_fails_and_counts_read_errors(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_DIRTY_DISC.splitlines()])
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.FAILURE)
        self.assertEqual(h.final.observation.read_error_count, 3)

    def test_a_failed_disc_is_not_ejected(self):
        """It must stay in the drive so the operator can clean and retry."""
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_DIRTY_DISC.splitlines()])
        self.run_job(h)
        self.assertEqual(h.ejects, [])

    def test_read_error_storm_is_not_forwarded_to_the_gui(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_DIRTY_DISC.splitlines()])
        self.run_job(h)
        codes = [e.code for e in h.events if e.kind == events.MESSAGE]
        self.assertNotIn(2003, codes, "counted, not forwarded one by one")

    def test_permissions_failure_explains_itself(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_NO_ACCESS.splitlines()])
        self.run_job(h)
        self.assertIn("permissions", h.final.verdict.reason)

    def test_truncated_copy_fails_even_with_a_success_message(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])
        h.on_line = lambda proc, line: self.make_output(ratio=0.2)
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.FAILURE)
        self.assertIn("20%", h.final.verdict.reason)

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

    def test_a_taken_image_path_is_refused(self):
        """A DVD destination is a file makemkvcon insists on creating itself.

        Handed a path already taken it answers MSG:5068, "already contains a
        backup" -- about an empty directory it simply had not made. Measured
        2026-09-06, after it cost a real backup.
        """
        image = self.root / "data.iso"
        image.write_bytes(b"an earlier attempt")
        h = Harness([fx.ENUMERATION_LINES])
        self.run_job(h, self.request(dest=image))
        self.assertEqual(h.final.error_kind, model.ERR_COPY)
        self.assertIn("already at the destination", h.final.verdict.reason)
        self.assertFalse(any("backup" in a for a in h.argvs))

    def test_an_absent_image_path_is_allowed_through(self):
        """The normal DVD case: nothing there, so makemkvcon may create it."""
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])
        image = self.root / "data.iso"
        h.on_line = lambda proc, line: make_iso(image)
        self.run_job(h, self.request(dest=image))
        self.assertTrue(any("backup" in a for a in h.argvs))
        self.assertEqual(h.final.observation.layout, "iso")
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)

    def test_insufficient_space_is_refused_without_touching_the_drive(self):
        self.cfg = Config(media_path=self.root, scan_titles=False,
                          min_free_margin_bytes=10**18)
        h = Harness([fx.ENUMERATION_LINES])
        self.run_job(h, self.request(cfg=self.cfg))
        self.assertEqual(h.final.error_kind, model.ERR_NO_SPACE)
        self.assertFalse(any("backup" in a for a in h.argvs))


class TestCancellation(RunnerTestCase):
    def test_cancel_signals_the_process_group_and_reports_cancelled(self):
        started = threading.Event()
        release = threading.Event()

        def block_midway(proc, line):
            if "PRGV:16384" in line:
                started.set()
                release.wait(5)

        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()],
                    on_line=block_midway)
        runner = BackupRunner(self.request(), h.emit, spawn=h.spawn,
                              clock=h.clock, ejector=h.eject)
        runner.start()
        self.assertTrue(started.wait(5), "the fake process never got going")
        runner.cancel()
        release.set()
        runner.join(timeout=10)

        self.assertIn(signal.SIGTERM, h.processes[-1].signals)
        self.assertEqual(h.final.verdict.outcome, outcome.CANCELLED)

    def test_a_cancelled_job_is_not_ejected(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])
        runner = BackupRunner(self.request(), h.emit, spawn=h.spawn,
                              clock=h.clock, ejector=h.eject)
        runner.cancel()
        runner.start()
        runner.join(timeout=10)
        self.assertEqual(h.ejects, [])


class TestWatchdogs(RunnerTestCase):
    def test_a_silent_process_is_stopped(self):
        """Time is injected, so this is instant."""
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])

        def jump_the_clock(proc, line):
            h.now += self.cfg.stall_timeout_s + 1

        h.on_line = jump_the_clock
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.CANCELLED)
        self.assertIn("no output for", h.final.observation.stall_reason)

    def test_an_overrunning_job_is_stopped(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])

        def jump_the_clock(proc, line):
            h.now += self.cfg.max_job_duration_s + 1

        h.on_line = jump_the_clock
        self.run_job(h)
        self.assertIn("gave up after", h.final.observation.stall_reason)


class TestTitleScan(RunnerTestCase):
    def test_records_the_title_inventory(self):
        self.cfg = Config(media_path=self.root, scan_titles=True,
                          min_free_margin_bytes=0)
        h = Harness([fx.ENUMERATION_LINES, fx.DISC_SCAN.splitlines(),
                     fx.BACKUP_SUCCESS.splitlines()])
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
        self.cfg = Config(media_path=self.root, scan_titles=True,
                          min_free_margin_bytes=0)
        h = Harness([fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                     fx.BACKUP_SUCCESS.splitlines()])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        identified = [e for e in h.events if e.kind == events.IDENTIFIED]
        self.assertEqual(len(identified), 1)
        self.assertEqual(identified[0].disc_name, "Fresh Horses")
        self.assertEqual(identified[0].disc_type, "DVD disc")
        self.assertEqual(len(identified[0].titles), 4)

    def test_identification_arrives_before_the_copy_starts(self):
        """An hour into a copy is too late to stop saying "DVD_VIDEO"."""
        self.cfg = Config(media_path=self.root, scan_titles=True,
                          min_free_margin_bytes=0)
        h = Harness([fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                     fx.BACKUP_SUCCESS.splitlines()])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        kinds = [e.kind for e in h.events]
        states = [e.state for e in h.events if e.kind == events.STATE]
        self.assertLess(kinds.index(events.IDENTIFIED),
                        kinds.index(events.PROGRESS))
        self.assertIn(model.COPYING, states)

    def test_a_disc_with_no_cinfo_is_named_after_its_feature(self):
        """CINFO:2 is not established for DVDs, so there is a fallback."""
        self.cfg = Config(media_path=self.root, scan_titles=True,
                          min_free_margin_bytes=0)
        h = Harness([fx.ENUMERATION_LINES, fx.DVD_SCAN_NO_CINFO.splitlines(),
                     fx.BACKUP_SUCCESS.splitlines()])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))

        identified = [e for e in h.events if e.kind == events.IDENTIFIED][0]
        self.assertEqual(identified.disc_name, "Fresh Horses",
                         "the largest title, not the first one")
        self.assertEqual(identified.disc_type, "")

    def test_scanning_can_be_turned_off(self):
        h = Harness([fx.ENUMERATION_LINES, fx.BACKUP_SUCCESS.splitlines()])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h)
        self.assertNotIn(model.SCANNING, h.states)
        self.assertFalse([e for e in h.events if e.kind == events.IDENTIFIED],
                         "nothing was scanned, so nothing was identified")


class TestRobustness(RunnerTestCase):
    def test_a_spawn_failure_is_reported_once(self):
        h = Harness([fx.ENUMERATION_LINES])

        def explode(argv):
            if "backup" in argv:
                raise OSError("no such binary")
            return h.__class__.spawn(h, argv)

        runner = BackupRunner(self.request(), h.emit, spawn=explode,
                              clock=h.clock, ejector=h.eject)
        runner.start()
        runner.join(timeout=10)
        self.assertEqual(h.final.error_kind, model.ERR_SPAWN)  # exactly one FINISHED

    def test_garbage_output_does_not_kill_the_worker(self):
        h = Harness([fx.ENUMERATION_LINES,
                     ["\x00garbage", "MSG:", "PRGV:a,b,c", 'DRV:"unterminated',
                      'MSG:5081,0,0,"Backup done.","Backup done."']])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h)
        self.assertEqual(h.final.verdict.outcome, outcome.SUCCESS)

    def test_log_is_truncated_rather_than_growing_without_bound(self):
        """A read-error storm can produce an enormous log."""
        self.cfg = Config(media_path=self.root, scan_titles=False,
                          min_free_margin_bytes=0, max_log_bytes=200)
        noisy = ['MSG:2003,0,0,"read error at %d","x"' % i for i in range(500)]
        noisy.append('MSG:5081,0,0,"Backup done.","Backup done."')
        h = Harness([fx.ENUMERATION_LINES, noisy])
        h.on_line = lambda proc, line: self.make_output()
        self.run_job(h, self.request(cfg=self.cfg))
        log = (self.root / "logs" / "attempt-1.log").read_text()
        self.assertIn("log truncated", log)
        self.assertLess(len(log), 2000)
        self.assertEqual(h.final.observation.read_error_count, 500,
                         "counting is unaffected by log truncation")


if __name__ == "__main__":
    unittest.main()
