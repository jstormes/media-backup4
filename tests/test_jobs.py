"""Tests for the job coordinator.

Two levels, deliberately. The queue tests drive a fake runner so the slot
arithmetic is exact and instant; the pipeline tests drive a *real*
:class:`BackupRunner` over a fake makemkvcon, so what is being checked is the
whole path from a transcript to a state change to a file on disk.

Nothing here spawns a process, touches D-Bus or sleeps.
"""

import collections
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path

from media_backup import events, jobs, model
from media_backup.config import Config
from media_backup.drives import DriveState
from media_backup.makemkv import outcome
from media_backup.makemkv.runner import BackupRunner
from media_backup.store import CollectionStore, StoreError

from . import makemkv_fixtures as fx
from .test_backup_runner import FakeProcess

DISC_SIZE = 4_556_390_400


#: What DVD_SCAN says the feature weighs; the default policy picks it alone.
FEATURE_BYTES = 4_245_336_064


def make_output(dest: Path, ratio: float = 0.99) -> None:
    """Write the .mkv a saved-titles run leaves behind. Sparse.

    Judging reads ``st_size`` and counts files, never the bytes; materialising
    four gigabytes would cost four gigabytes to prove nothing.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / "Fresh Horses-A1_t00.mkv").open("wb") as handle:
        handle.truncate(max(1, int(FEATURE_BYTES * ratio)))


def drive(device=fx.SR1, label=fx.SR1_LABEL, has_media=True, **kw):
    return DriveState(
        device=device, model="BD-RE BU40N", vendor="HL-DT-ST",
        serial="902HS017569", label=label, has_media=has_media,
        size=DISC_SIZE, media="optical_bd",
        object_path=f"/blocks{device}", drive_object_path=f"/drives{device}",
        **kw)


class FakeRunner:
    """A runner that starts nothing and finishes when the test says so."""

    def __init__(self, request, emit, *, ejector=None):
        self.request = request
        self.emit = emit
        self.ejector = ejector
        self.started = False
        self.cancel_reason = None
        self.joined = False

    def start(self):
        self.started = True

    def cancel(self, reason=model.ERR_CANCELLED):
        self.cancel_reason = reason

    def join(self, timeout=None):
        self.joined = True

    # -- what the test drives ----------------------------------------------

    def state(self, state, step=""):
        self.emit(events.JobEvent(self.request.job_id, events.STATE,
                                  state=state, step=step))

    def progress(self, total_pct):
        self.emit(events.JobEvent(self.request.job_id, events.PROGRESS,
                                  total_pct=total_pct, step_pct=total_pct))

    def finish(self, good=True, reason="", error_kind="", **obs_kw):
        verdict = (outcome.Verdict(outcome.SUCCESS, reason or "backup completed")
                   if good else
                   outcome.Verdict(outcome.FAILURE, reason or "it broke"))
        obs_kw.setdefault("exit_code", 0)
        obs_kw.setdefault("bytes_written", DISC_SIZE)
        obs_kw.setdefault("disc_size_bytes", DISC_SIZE)
        obs = outcome.BackupObservation(**obs_kw)
        self.emit(events.JobEvent(self.request.job_id, events.FINISHED,
                                  verdict=verdict, observation=obs,
                                  error_kind=error_kind))


class JobsTestCase(unittest.TestCase):
    """A manager over a real store in a temp directory."""

    max_concurrent_jobs = 0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = Config(media_path=self.root, min_free_margin_bytes=0, use_stdbuf=False,
                          max_concurrent_jobs=self.max_concurrent_jobs)
        self.store = CollectionStore(self.cfg)
        self.collection = self.store.create(identifier="test-set")
        self.changes = []
        self.ejects = []
        self.eject_raises = None
        self.runners = []
        self.manager = jobs.JobManager(
            self.cfg, self.store,
            runner_factory=self.runner_factory,
            ejector=self.eject,
            on_change=lambda status: self.changes.append(copy.copy(status)))

    # -- collaborators ------------------------------------------------------

    def runner_factory(self, request, emit, *, ejector=None):
        runner = FakeRunner(request, emit, ejector=ejector)
        self.runners.append(runner)
        return runner

    def eject(self, drive_object_path, block_object_path, *, mounted=False):
        self.ejects.append((drive_object_path, block_object_path, mounted))
        if self.eject_raises is not None:
            raise self.eject_raises

    # -- helpers ------------------------------------------------------------

    def add_disc(self, dev=fx.SR1, label=fx.SR1_LABEL):
        d = drive(device=dev, label=label)
        return self.store.add_disc(self.collection, d), d

    def reload(self):
        """The collection as it would come back after a restart."""
        loaded = CollectionStore(self.cfg).load_all()
        self.assertEqual(len(loaded), 1)
        return loaded[0]


# ---------------------------------------------------------------------------
# Queue and slots
# ---------------------------------------------------------------------------


class TestSlots(JobsTestCase):
    def test_one_disc_starts_immediately(self):
        disc, dev = self.add_disc()
        job_id = self.manager.enqueue(self.collection, disc, dev)
        self.assertEqual(self.manager.running_count, 1)
        self.assertEqual(self.manager.queued_count, 0)
        self.assertTrue(self.runners[0].started)
        self.assertEqual(self.manager.status(job_id).device, fx.SR1)

    def test_a_second_disc_for_the_same_drive_waits(self):
        first, dev = self.add_disc()
        second = self.store.add_disc(self.collection, dev)
        self.manager.enqueue(self.collection, first, dev)
        self.manager.enqueue(self.collection, second, dev)

        self.assertEqual(self.manager.running_count, 1)
        self.assertEqual(self.manager.queued_count, 1)
        self.assertEqual(len(self.runners), 1, "the second one never spawned")
        self.assertEqual(second.state, model.QUEUED)

    def test_finishing_frees_the_drive_for_the_next_disc(self):
        first, dev = self.add_disc()
        second = self.store.add_disc(self.collection, dev)
        self.manager.enqueue(self.collection, first, dev)
        self.manager.enqueue(self.collection, second, dev)

        self.runners[0].finish()

        self.assertEqual(first.state, model.DONE)
        self.assertEqual(self.manager.running_count, 1)
        self.assertEqual(len(self.runners), 2)
        self.assertEqual(self.runners[1].request.disc_id, second.disc_id)

    def test_two_drives_run_at_once(self):
        first, dev0 = self.add_disc(fx.SR0, fx.SR0_LABEL)
        second, dev1 = self.add_disc(fx.SR1, fx.SR1_LABEL)
        self.manager.enqueue(self.collection, first, dev0)
        self.manager.enqueue(self.collection, second, dev1)

        self.assertEqual(self.manager.running_count, 2)
        self.assertTrue(self.manager.is_busy(fx.SR0))
        self.assertTrue(self.manager.is_busy(fx.SR1))

    def test_a_queued_job_for_a_free_drive_is_not_blocked_by_a_busy_one(self):
        """Head-of-line blocking would idle a perfectly free drive."""
        first, dev0 = self.add_disc(fx.SR0, fx.SR0_LABEL)
        second = self.store.add_disc(self.collection, dev0)
        third, dev1 = self.add_disc(fx.SR1, fx.SR1_LABEL)

        self.manager.enqueue(self.collection, first, dev0)
        self.manager.enqueue(self.collection, second, dev0)   # waits on sr0
        self.manager.enqueue(self.collection, third, dev1)    # sr1 is free

        self.assertEqual(self.manager.running_count, 2)
        self.assertEqual(self.manager.queued_count, 1)
        self.assertEqual(third.state, model.RESOLVING)


class TestGlobalCap(JobsTestCase):
    max_concurrent_jobs = 1

    def test_the_cap_holds_a_free_drive_back(self):
        first, dev0 = self.add_disc(fx.SR0, fx.SR0_LABEL)
        second, dev1 = self.add_disc(fx.SR1, fx.SR1_LABEL)
        self.manager.enqueue(self.collection, first, dev0)
        self.manager.enqueue(self.collection, second, dev1)

        self.assertEqual(self.manager.running_count, 1)
        self.assertEqual(self.manager.queued_count, 1)

        self.runners[0].finish()
        self.assertEqual(self.manager.running_count, 1)
        self.assertEqual(len(self.runners), 2)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestRefusals(JobsTestCase):
    def test_the_same_disc_cannot_be_queued_twice(self):
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)
        with self.assertRaises(jobs.JobError):
            self.manager.enqueue(self.collection, disc, dev)

    def test_a_finished_disc_is_not_copied_again(self):
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)
        self.runners[0].finish()
        with self.assertRaises(jobs.JobError):
            self.manager.enqueue(self.collection, disc, dev)

    def test_an_empty_drive_is_refused(self):
        disc, dev = self.add_disc()
        with self.assertRaises(jobs.JobError):
            self.manager.enqueue(self.collection, disc,
                                 drive(has_media=False))


# ---------------------------------------------------------------------------
# Cancelling, abandoning, shutting down
# ---------------------------------------------------------------------------


class TestCancellation(JobsTestCase):
    def test_cancelling_a_running_job_signals_it_and_keeps_the_slot(self):
        disc, dev = self.add_disc()
        job_id = self.manager.enqueue(self.collection, disc, dev)
        self.assertTrue(self.manager.cancel(job_id))

        self.assertEqual(self.runners[0].cancel_reason, model.ERR_CANCELLED)
        self.assertEqual(self.manager.running_count, 1,
                         "makemkvcon takes its time dying; the slot is the "
                         "runner's until it reports FINISHED")

        self.runners[0].finish(good=False, reason="cancelled",
                               error_kind=model.ERR_CANCELLED)
        self.assertEqual(disc.state, model.FAILED)
        self.assertEqual(self.manager.running_count, 0)

    def test_cancelling_a_queued_job_restores_the_disc(self):
        first, dev = self.add_disc()
        second = self.store.add_disc(self.collection, dev)
        self.manager.enqueue(self.collection, first, dev)
        job_id = self.manager.enqueue(self.collection, second, dev)

        self.manager.cancel(job_id)

        self.assertEqual(second.state, model.PENDING,
                         "it never ran, so it is not a failed attempt")
        self.assertEqual(second.attempts, [])
        self.assertEqual(self.manager.queued_count, 0)

    def test_cancelling_an_unknown_job_is_not_an_error(self):
        self.assertFalse(self.manager.cancel("no-such-job"))

    def test_abandon_marks_a_disc_terminal(self):
        disc, dev = self.add_disc()
        self.manager.abandon(self.collection, disc, "the disc is cracked")
        self.assertEqual(disc.state, model.ABANDONED)
        self.assertEqual(self.reload().discs[0].state_detail,
                         "the disc is cracked")

    def test_abandon_refuses_while_a_job_is_live(self):
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)
        with self.assertRaises(jobs.JobError):
            self.manager.abandon(self.collection, disc)

    def test_shutdown_signals_every_running_job(self):
        first, dev0 = self.add_disc(fx.SR0, fx.SR0_LABEL)
        second, dev1 = self.add_disc(fx.SR1, fx.SR1_LABEL)
        self.manager.enqueue(self.collection, first, dev0)
        self.manager.enqueue(self.collection, second, dev1)

        self.manager.shutdown(timeout=0.1)

        self.assertEqual([r.cancel_reason for r in self.runners],
                         [model.ERR_INTERRUPTED] * 2)
        self.assertTrue(all(r.joined for r in self.runners))


# ---------------------------------------------------------------------------
# Applying events to the model
# ---------------------------------------------------------------------------


class TestEventApplication(JobsTestCase):
    def setUp(self):
        super().setUp()
        self.disc, self.dev = self.add_disc()
        self.job_id = self.manager.enqueue(self.collection, self.disc, self.dev)
        self.runner = self.runners[0]

    def test_state_events_move_the_disc_and_persist(self):
        self.runner.state(model.COPYING, "copying")
        self.assertEqual(self.disc.state, model.COPYING)
        self.assertEqual(self.reload().discs[0].state, model.COPYING)

    def test_progress_is_live_only_and_never_persisted(self):
        before = self.reload().discs[0].updated_at
        self.runner.progress(42.0)
        self.assertEqual(self.manager.status(self.job_id).total_pct, 42.0)
        self.assertEqual(self.reload().discs[0].updated_at, before,
                         "a JSON rewrite four times a second, for hours")

    def test_a_finished_job_records_its_attempt(self):
        self.runner.finish(good=True, exit_code=0,
                           message_codes={5081: 1, 2003: 4})
        attempt = self.disc.last_attempt
        self.assertEqual(self.disc.state, model.DONE)
        self.assertEqual(attempt.attempt, 1)
        self.assertEqual(attempt.outcome, outcome.SUCCESS)
        self.assertEqual(attempt.device, fx.SR1)
        self.assertEqual(attempt.drive_model, "HL-DT-ST BD-RE BU40N")
        self.assertEqual(attempt.bytes_written, DISC_SIZE)
        self.assertEqual(attempt.read_error_count, 4)
        self.assertTrue(attempt.ended_at)
        self.assertEqual(attempt.failure_reason, "")

    def test_message_codes_are_stored_with_string_keys(self):
        """They are JSON object keys; converting once here beats a surprise."""
        self.runner.finish(message_codes={5081: 1})
        self.assertEqual(self.reload().discs[0].last_attempt.message_codes,
                         {"5081": 1})

    def test_a_failed_job_records_why(self):
        self.runner.finish(good=False, reason="only 20% of the disc was written",
                           error_kind=model.ERR_COPY)
        self.assertEqual(self.disc.state, model.FAILED)
        self.assertEqual(self.disc.state_detail,
                         "only 20% of the disc was written")
        self.assertEqual(self.disc.last_attempt.error_kind, model.ERR_COPY)

    def test_identification_names_the_disc_and_persists_it(self):
        """The row should stop saying DVD_VIDEO the moment the scan knows."""
        self.assertEqual(self.disc.display_name, fx.SR1_LABEL)
        self.runner.emit(events.JobEvent(
            self.job_id, events.IDENTIFIED, disc_name="Fresh Horses",
            disc_type="DVD disc",
            titles=(model.Title(index=0, name="Fresh Horses",
                                duration="1:42:39", size_bytes=4_245_336_064),)))

        self.assertEqual(self.disc.display_name, "Fresh Horses")
        reloaded = self.reload().discs[0]
        self.assertEqual(reloaded.makemkv_disc_name, "Fresh Horses")
        self.assertEqual(reloaded.main_title.duration, "1:42:39",
                         "recorded mid-job, not held back until it ends")
        self.assertEqual(self.manager.running_count, 1, "still running")

    def test_an_unnamed_identification_leaves_the_label_alone(self):
        self.runner.emit(events.JobEvent(self.job_id, events.IDENTIFIED))
        self.assertEqual(self.disc.display_name, fx.SR1_LABEL)

    def test_titles_from_the_scan_are_recorded(self):
        self.runner.emit(events.JobEvent(
            self.job_id, events.FINISHED,
            verdict=outcome.Verdict(outcome.SUCCESS, "ok"),
            observation=outcome.BackupObservation(),
            titles=(model.Title(index=0, name="Feature"),)))
        self.assertEqual([t.name for t in self.reload().discs[0].titles],
                         ["Feature"])

    def test_the_slot_is_released_exactly_once(self):
        second = self.store.add_disc(self.collection, self.dev)
        self.manager.enqueue(self.collection, second, self.dev)

        self.runner.finish()
        self.runner.finish()  # the runner never does this; prove it is harmless

        self.assertEqual(len(self.runners), 2, "the queue advanced once")
        self.assertEqual(self.disc.attempt_count, 1)
        self.assertEqual(self.manager.running_count, 1)

    def test_events_for_a_retired_job_are_ignored(self):
        self.runner.finish()
        self.runner.state(model.COPYING, "copying")
        self.assertEqual(self.disc.state, model.DONE)

    def test_the_gui_hears_about_every_change(self):
        self.changes.clear()
        self.runner.state(model.COPYING, "copying")
        self.runner.finish()
        self.assertEqual([c.state for c in self.changes],
                         [model.COPYING, model.DONE])

    def test_a_broken_gui_callback_does_not_stall_the_queue(self):
        self.manager.on_change = lambda status: 1 / 0
        self.runner.finish()
        self.assertEqual(self.disc.state, model.DONE)
        self.assertEqual(self.manager.running_count, 0)


# ---------------------------------------------------------------------------
# Ejecting
# ---------------------------------------------------------------------------


class TestEjectRecording(JobsTestCase):
    def test_a_successful_eject_is_recorded_on_the_attempt(self):
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)
        runner = self.runners[0]
        runner.ejector(dev.drive_object_path, dev.object_path, mounted=False)
        runner.finish()

        self.assertEqual(self.ejects, [(f"/drives{fx.SR1}", f"/blocks{fx.SR1}",
                                        False)])
        self.assertTrue(disc.last_attempt.eject_ok)

    def test_a_stuck_tray_is_recorded_but_is_not_a_failed_copy(self):
        self.eject_raises = RuntimeError("the tray did not open")
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)
        runner = self.runners[0]
        with self.assertRaises(RuntimeError):
            runner.ejector(dev.drive_object_path, dev.object_path)
        runner.finish()

        self.assertEqual(disc.state, model.DONE, "the copy is still good")
        self.assertIs(disc.last_attempt.eject_ok, False)
        self.assertIn("tray did not open", disc.last_attempt.eject_error)

    def test_a_disc_that_was_never_ejected_says_so(self):
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)
        self.runners[0].finish(good=False, error_kind=model.ERR_COPY)
        self.assertIsNone(disc.last_attempt.eject_ok)


# ---------------------------------------------------------------------------
# Failing before anything spawns
# ---------------------------------------------------------------------------


class TestPrepareFailure(JobsTestCase):
    def test_a_store_failure_fails_the_job_and_frees_the_drive(self):
        def explode(collection, disc):
            raise StoreError("could not move the previous partial copy aside")

        self.store.prepare_attempt = explode
        disc, dev = self.add_disc()
        self.manager.enqueue(self.collection, disc, dev)

        self.assertEqual(self.runners, [], "nothing was ever spawned")
        self.assertEqual(disc.state, model.FAILED)
        self.assertEqual(disc.last_attempt.error_kind, model.ERR_STORE)
        self.assertIn("partial copy aside", disc.state_detail)
        self.assertEqual(self.manager.running_count, 0)

    def test_the_next_disc_still_gets_the_drive(self):
        calls = []

        real = self.store.prepare_attempt

        def explode_once(collection, disc):
            calls.append(disc.disc_id)
            if len(calls) == 1:
                raise StoreError("nope")
            return real(collection, disc)

        self.store.prepare_attempt = explode_once
        first, dev = self.add_disc()
        second = self.store.add_disc(self.collection, dev)
        self.manager.enqueue(self.collection, first, dev)
        self.manager.enqueue(self.collection, second, dev)

        self.assertEqual(first.state, model.FAILED)
        self.assertEqual(second.state, model.RESOLVING)
        self.assertEqual(len(self.runners), 1)


# ---------------------------------------------------------------------------
# The whole pipeline, over a real runner and a fake makemkvcon
# ---------------------------------------------------------------------------


class PipelineTestCase(JobsTestCase):
    """Real BackupRunner over a fake makemkvcon, with a stand-in GUI loop.

    The manager's contract is that events are applied on the GUI thread, and
    these runners are real threads, so the test has to be that thread: the
    dispatcher parks events in a queue and :meth:`wait` drains it. Applying
    them inline on the worker instead would let a worker's ``save()`` race the
    test's own ``enqueue()`` -- which is not a bug in the manager, but it is
    not what production does either, and a test that lies about which thread
    it is on cannot say anything useful about a coordinator whose whole job is
    to have one writer.
    """

    def setUp(self):
        super().setUp()
        self.transcripts = []
        self.output_ratio = 0.99
        self.now = 1000.0
        self.pending = collections.deque()
        self.woken = threading.Event()
        self.manager._dispatch = self._park

    def runner_factory(self, request, emit, *, ejector=None):
        def on_line(proc, line):
            if self.output_ratio is not None:
                make_output(request.dest, self.output_ratio)

        def spawn(argv):
            lines = self.transcripts.pop(0) if self.transcripts else []
            return FakeProcess(lines, 0, on_line if "mkv" in argv else None)

        runner = BackupRunner(request, emit, spawn=spawn,
                              clock=lambda: self.now, ejector=ejector)
        self.runners.append(runner)
        return runner

    def _park(self, func, *args):
        """The dispatcher: called on a worker thread, drained on this one."""
        self.pending.append((func, args))
        self.woken.set()

    def wait(self, timeout=10.0):
        """Run the stand-in GUI loop until every job has retired.

        ``running_count`` only falls to zero once FINISHED has been applied
        here, so it is a sound stopping condition -- and it covers the jobs a
        finishing job starts, since those are started from this thread too.
        """
        deadline = time.monotonic() + timeout
        while True:
            self.woken.clear()
            while self.pending:
                func, args = self.pending.popleft()
                func(*args)
            if not self.manager.running_count and not self.manager.queued_count:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.woken.wait(remaining):
                self.fail("the queue did not settle")

    def run_one(self, disc, dev, transcripts):
        self.transcripts = [list(t) for t in transcripts]
        job_id = self.manager.enqueue(self.collection, disc, dev)
        self.wait()
        return job_id


class TestSuccessfulPipeline(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.disc, self.dev = self.add_disc()
        self.run_one(self.disc, self.dev,
                     [fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                      fx.MKV_SUCCESS_ONE.splitlines()])

    def test_the_disc_ends_up_done(self):
        self.assertEqual(self.disc.state, model.DONE)
        self.assertEqual(self.reload().discs[0].state, model.DONE)

    def test_the_slot_is_free_again(self):
        self.assertEqual(self.manager.running_count, 0)
        self.assertFalse(self.manager.is_busy(fx.SR1))

    def test_the_attempt_is_a_complete_record(self):
        attempt = self.reload().discs[0].last_attempt
        self.assertEqual(attempt.outcome, outcome.SUCCESS)
        self.assertEqual(attempt.exit_code, 0)
        self.assertEqual(attempt.layout, "mkv")
        self.assertGreater(attempt.bytes_written, 0)
        self.assertTrue(attempt.eject_ok)

    def test_the_log_path_is_relative_to_the_collection(self):
        attempt = self.reload().discs[0].last_attempt
        self.assertEqual(
            attempt.log,
            f"discs/{self.disc.disc_id}/logs/attempt-1.log")
        self.assertIn("Copy complete.",
                      (self.store.collection_dir(self.collection)
                       / attempt.log).read_text())

    def test_the_copy_landed_where_the_store_says(self):
        data = self.store.data_dir(self.collection, self.disc)
        self.assertEqual([p.suffix for p in data.iterdir()], [".mkv"])

    def test_the_disc_was_ejected(self):
        self.assertEqual(len(self.ejects), 1)
        self.assertEqual(self.ejects[0][0], f"/drives{fx.SR1}")

    def test_the_live_status_is_gone_once_the_job_retires(self):
        self.assertEqual(self.manager.status_for_disc(self.disc.disc_id), None,
                         "the job is retired, so it has no live status")


class TestFailureAndRetry(PipelineTestCase):
    def test_a_dirty_disc_fails_and_is_left_in_the_drive(self):
        disc, dev = self.add_disc()
        self.output_ratio = 0.5
        self.run_one(disc, dev,
                     [fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                      fx.MKV_DIRTY_DISC.splitlines()])

        self.assertEqual(disc.state, model.FAILED)
        self.assertEqual(disc.last_attempt.error_kind, model.ERR_COPY)
        self.assertEqual(disc.last_attempt.read_error_count, 3)
        self.assertEqual(self.ejects, [], "the operator has to clean it")
        self.assertEqual(self.manager.running_count, 0)

    def test_nothing_is_retried_on_its_own(self):
        disc, dev = self.add_disc()
        self.run_one(disc, dev,
                     [fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                      fx.MKV_DIRTY_DISC.splitlines()])
        self.assertEqual(self.manager.queued_count, 0)
        self.assertEqual(disc.attempt_count, 1)

    def test_a_manual_retry_files_the_partial_copy_and_succeeds(self):
        disc, dev = self.add_disc()
        self.output_ratio = 0.5
        self.run_one(disc, dev,
                     [fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                      fx.MKV_DIRTY_DISC.splitlines()])
        self.assertEqual(disc.state, model.FAILED)

        self.output_ratio = 0.99
        self.run_one(disc, dev,
                     [fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
                      fx.MKV_SUCCESS_ONE.splitlines()])

        reloaded = self.reload().discs[0]
        self.assertEqual(reloaded.state, model.DONE)
        self.assertEqual(reloaded.attempt_count, 2)
        self.assertEqual(reloaded.attempts[0].outcome, outcome.FAILURE)
        self.assertEqual(reloaded.attempts[1].outcome, outcome.SUCCESS)
        self.assertEqual(reloaded.attempts[0].rejected_path,
                         f"discs/{disc.disc_id}/rejected/attempt-1")
        self.assertTrue((self.store.collection_dir(self.collection)
                         / reloaded.attempts[0].rejected_path).is_dir())

    def test_the_wrong_disc_is_refused_without_copying(self):
        disc, dev = self.add_disc()
        disc.label = "SOME_OTHER_FILM"
        self.run_one(disc, dev, [fx.ENUMERATION_LINES])

        self.assertEqual(disc.state, model.FAILED)
        self.assertEqual(disc.last_attempt.error_kind, "disc_changed")


class TestPipelineQueue(PipelineTestCase):
    def test_a_queued_disc_starts_when_the_drive_frees_up(self):
        first, dev = self.add_disc()
        second = self.store.add_disc(self.collection, dev)
        self.transcripts = [
            fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
            fx.MKV_SUCCESS_ONE.splitlines(),
            fx.ENUMERATION_LINES, fx.DVD_SCAN.splitlines(),
            fx.MKV_SUCCESS_ONE.splitlines(),
        ]
        self.manager.enqueue(self.collection, first, dev)
        self.manager.enqueue(self.collection, second, dev)
        self.wait()

        self.assertEqual(first.state, model.DONE)
        self.assertEqual(second.state, model.DONE)
        self.assertEqual(self.manager.running_count, 0)
        self.assertEqual(len(self.ejects), 2)


if __name__ == "__main__":
    unittest.main()
