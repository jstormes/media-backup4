"""The job coordinator: one queue, one slot per drive, one writer of state.

:mod:`makemkv.runner` runs a single backup on a worker thread and knows
nothing about the application. This module is the other half: it owns the
queue, decides when a job may start, and is the only place that turns a
:class:`~media_backup.events.JobEvent` into a change in the model and a write
through :mod:`store`.

Threading
---------

Everything below runs on the GUI thread. A runner thread only ever calls
:meth:`JobManager._on_event`, which hands the event straight back to the GUI
thread through ``dispatch`` -- ``root.after(0, ...)`` under tkinter, and a
direct call when there is no GUI, which is what makes this testable without
one. There is no lock anywhere in this file because there is exactly one
writer of ``_jobs``, ``_queue`` and ``_busy``.

Slots
-----

One job per drive, always: a second makemkvcon on the same device would fight
the first for the tray. ``cfg.max_concurrent_jobs`` adds a global cap on top
of that when it is non-zero, for a machine whose disk cannot keep up with
four drives at once.

Retries are manual, deliberately
--------------------------------

A failed disc is left in the drive by the runner, and its ``Disc`` stays in
``FAILED`` -- retryable, but never retried on its own. An automatic retry
would re-read the same unreadable sectors of the same dirty disc for hours,
and each attempt costs a :meth:`store.CollectionStore.prepare_attempt` move of
tens of gigabytes. The operator cleans the disc and asks again, which is just
:meth:`JobManager.enqueue` a second time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import events, model
from .config import Config
from .makemkv import outcome
from .makemkv.runner import BackupRequest, BackupRunner
from .store import CollectionStore, StoreError

logger = logging.getLogger(__name__)

#: How long :meth:`JobManager.shutdown` waits for all workers, in seconds.
SHUTDOWN_GRACE_S = 15.0


class JobError(Exception):
    """A job could not be started. The message is meant for the operator."""


def _default_ejector(drive_object_path: str, block_object_path: str,
                     *, mounted: bool = False) -> None:
    # Imported here rather than at module scope: eject needs PyGObject, and
    # this module should be importable (and testable) without a D-Bus stack.
    from .eject import eject

    eject(drive_object_path, block_object_path, mounted=mounted)


def _call_now(func: Callable, *args) -> None:
    """The no-GUI dispatcher: apply the event on the thread that raised it."""
    func(*args)


@dataclass
class JobStatus:
    """Live state for one job -- the part that is never persisted.

    Progress arrives four times a second for hours; writing it through
    :mod:`store` would be a JSON rewrite per update to record a number nobody
    reads after the job ends. The durable record of a job is its
    :class:`model.Attempt`; this is what the progress bar reads.
    """

    job_id: str
    collection_id: str
    disc_id: str
    device: str
    state: str = model.QUEUED
    step: str = ""
    total_pct: float = 0.0
    step_pct: float = 0.0
    message: str = ""
    attempt: int = 0

    @property
    def is_running(self) -> bool:
        return self.state != model.QUEUED


@dataclass
class _Job:
    """A queued or running job, and the model objects it is writing into."""

    job_id: str
    collection: model.Collection
    disc: model.Disc
    drive: object
    device: str
    status: JobStatus
    #: What the disc was before it was queued, so cancelling from the queue
    #: puts it back rather than inventing a failed attempt that never ran.
    previous_state: str = model.PENDING
    runner: BackupRunner | None = None
    #: Written by the worker thread inside the eject call, read by the GUI
    #: thread when it applies FINISHED. The event is emitted after the eject
    #: returns, so the ordering is the queue's, not a race.
    eject_ok: bool | None = None
    eject_error: str = ""


class JobManager:
    """Owns the backup queue and applies its events to the model."""

    def __init__(
        self,
        cfg: Config,
        store: CollectionStore,
        *,
        runner_factory: Callable[..., BackupRunner] = BackupRunner,
        ejector: Callable[..., None] = _default_ejector,
        on_change: Callable[[JobStatus], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self._runner_factory = runner_factory
        self._ejector = ejector
        #: Called on the GUI thread after every applied event, with the
        #: job's live :class:`JobStatus`. Public so a window that did not
        #: build the manager can still wire itself to it.
        self.on_change = on_change
        self._dispatch: Callable[..., None] = _call_now

        self._jobs: dict[str, _Job] = {}
        self._queue: list[str] = []
        self._busy: dict[str, str] = {}  # device -> job_id

    # -- wiring -------------------------------------------------------------

    def attach_to_tkinter(self, root) -> None:
        """Marshal runner events onto the tkinter loop.

        Call this before enqueuing anything. Without it events are applied on
        the runner thread, which is right for tests and wrong for a GUI.
        """
        self._dispatch = lambda func, *args: root.after(0, func, *args)

    # -- queries ------------------------------------------------------------

    @property
    def running_count(self) -> int:
        return len(self._busy)

    @property
    def queued_count(self) -> int:
        return len(self._queue)

    def status(self, job_id: str) -> JobStatus | None:
        job = self._jobs.get(job_id)
        return job.status if job else None

    def status_for_disc(self, disc_id: str) -> JobStatus | None:
        job = self._job_for_disc(disc_id)
        return job.status if job else None

    def statuses(self) -> list[JobStatus]:
        """Every live job: running first, then the queue in its own order."""
        running = [self._jobs[j].status for j in self._busy.values()
                   if j in self._jobs]
        queued = [self._jobs[j].status for j in self._queue if j in self._jobs]
        return running + queued

    def is_busy(self, device: str) -> bool:
        return device in self._busy

    # -- operator actions ---------------------------------------------------

    def enqueue(self, collection: model.Collection, disc: model.Disc, drive) -> str:
        """Queue a backup of the disc now in ``drive``. Returns the job id.

        This is also the retry path: a disc in ``FAILED`` is enqueued exactly
        the same way, and :meth:`store.CollectionStore.prepare_attempt` moves
        the previous partial copy aside when the job actually starts.
        """
        if self._job_for_disc(disc.disc_id) is not None or disc.is_active:
            raise JobError(f"{disc.display_name} is already being backed up")
        if disc.state == model.DONE:
            raise JobError(
                f"{disc.display_name} is already backed up. Remove it from the "
                "collection first if you really mean to copy it again.")
        if not getattr(drive, "has_media", False):
            raise JobError(f"there is no disc in {getattr(drive, 'device', '?')}")

        job_id = model.new_id()
        job = _Job(
            job_id=job_id,
            collection=collection,
            disc=disc,
            drive=drive,
            device=drive.device,
            status=JobStatus(job_id=job_id, collection_id=collection.collection_id,
                             disc_id=disc.disc_id, device=drive.device),
            previous_state=disc.state,
        )
        self._jobs[job_id] = job
        self._queue.append(job_id)

        self._set_disc_state(job, model.QUEUED, "waiting for the drive")
        self._save(collection)
        self._notify(job)
        self._pump()
        return job_id

    def cancel(self, job_id: str, reason: str = model.ERR_CANCELLED) -> bool:
        """Stop a job. False if there is no such live job.

        A running job is signalled and reports its own FINISHED in its own
        time -- makemkvcon can take seconds to die -- so the slot is not
        released here.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.runner is None:
            self._retire(job, job.previous_state, "")
            return True
        job.runner.cancel(reason)
        return True

    def cancel_disc(self, disc_id: str, reason: str = model.ERR_CANCELLED) -> bool:
        job = self._job_for_disc(disc_id)
        return self.cancel(job.job_id, reason) if job else False

    def abandon(self, collection: model.Collection, disc: model.Disc,
                note: str = "") -> None:
        """Give up on a disc for good. Cancel its job first, if it has one."""
        if self._job_for_disc(disc.disc_id) is not None:
            raise JobError(
                f"{disc.display_name} is still being copied; cancel it first")
        disc.state = model.ABANDONED
        disc.state_detail = note or "the operator gave up on this disc"
        disc.updated_at = model.now()
        self._save(collection)

    def shutdown(self, timeout: float = SHUTDOWN_GRACE_S) -> None:
        """Signal every running job and wait for the threads to unwind.

        The FINISHED events these jobs emit are dispatched to a GUI loop that
        is on its way out, so they may never be applied -- which is exactly
        what :meth:`store.CollectionStore.recover_interrupted` is for at the
        next startup.
        """
        running = [job for job in self._jobs.values() if job.runner is not None]
        for job in running:
            job.runner.cancel(model.ERR_INTERRUPTED)
        for job in running:
            job.runner.join(timeout=timeout)

    # -- the queue ----------------------------------------------------------

    def _pump(self) -> None:
        """Start whatever the slots now allow, in the order it was queued."""
        for job_id in list(self._queue):
            job = self._jobs.get(job_id)
            if job is None:
                self._queue.remove(job_id)
                continue
            if self.cfg.max_concurrent_jobs and \
                    len(self._busy) >= self.cfg.max_concurrent_jobs:
                break
            if job.device in self._busy:
                # Another disc is in this drive's slot. A job for a different
                # drive further down the queue can still start.
                continue
            self._queue.remove(job_id)
            self._busy[job.device] = job_id
            self._start(job)

    def _start(self, job: _Job) -> None:
        try:
            dest, log_path, attempt_no = self.store.prepare_attempt(
                job.collection, job.disc)
        except StoreError as exc:
            logger.error("job %s could not be prepared: %s", job.job_id, exc)
            self._fail_before_start(job, str(exc), model.ERR_STORE)
            return

        drive = job.drive
        # makemkv_index, source_spec and argv stay empty here: the runner
        # resolves the index on its own thread and does not report it back.
        # The argv it actually ran is the first line of the attempt log.
        attempt = model.Attempt(
            attempt=attempt_no,
            device=job.device,
            drive_model=getattr(drive, "display_name", ""),
            drive_serial=getattr(drive, "serial", "") or "",
            log=self.store.relative(job.collection, log_path),
        )
        job.disc.attempts.append(attempt)
        job.status.attempt = attempt_no

        request = BackupRequest(
            job_id=job.job_id,
            collection_id=job.collection.collection_id,
            disc_id=job.disc.disc_id,
            device=job.device,
            expected_label=job.disc.label or getattr(drive, "label", ""),
            disc_size_bytes=job.disc.disc_size_bytes or getattr(drive, "size", 0),
            dest=dest,
            log_path=log_path,
            attempt=attempt_no,
            cfg=self.cfg,
            drive_object_path=getattr(drive, "drive_object_path", ""),
            block_object_path=getattr(drive, "object_path", ""),
            mounted=bool(getattr(drive, "mount_points", None)),
        )

        job.runner = self._runner_factory(
            request, self._on_event, ejector=self._ejector_for(job))
        self._set_disc_state(job, model.RESOLVING, "starting")
        self._save(job.collection)
        self._notify(job)
        logger.info("job %s: attempt %d on %s (%s)",
                    job.job_id, attempt_no, job.device, job.disc.display_name)
        job.runner.start()

    def _ejector_for(self, job: _Job) -> Callable[..., None]:
        """Wrap the ejector so the attempt records whether the tray opened."""

        def _eject(drive_object_path: str, block_object_path: str,
                   *, mounted: bool = False) -> None:
            try:
                self._ejector(drive_object_path, block_object_path,
                              mounted=mounted)
            except Exception as exc:  # noqa: BLE001 -- re-raised immediately
                job.eject_ok = False
                job.eject_error = str(exc)
                raise  # the runner turns this into a message for the operator
            job.eject_ok = True

        return _eject

    # -- events -------------------------------------------------------------

    def _on_event(self, event: events.JobEvent) -> None:
        """Called on a runner thread. Hands the event to the GUI thread."""
        self._dispatch(self._apply, event)

    def _apply(self, event: events.JobEvent) -> None:
        job = self._jobs.get(event.job_id)
        if job is None:
            # Already retired -- a shutdown, or an event that outran a cancel.
            return

        if event.kind == events.STATE:
            self._set_disc_state(job, event.state, event.step)
            self._save(job.collection)
        elif event.kind == events.PROGRESS:
            if event.step:
                job.status.step = event.step
            if event.total_pct or event.step_pct:
                job.status.total_pct = event.total_pct
                job.status.step_pct = event.step_pct
        elif event.kind == events.IDENTIFIED:
            # Recorded as soon as the scan knows it, rather than at the end:
            # a job that fails an hour later should still have said what the
            # disc was, and the operator should not read "DVD_VIDEO" for the
            # length of a copy.
            if event.disc_name:
                job.disc.makemkv_disc_name = event.disc_name
            if event.titles:
                job.disc.titles = list(event.titles)
            job.disc.updated_at = model.now()
            self._save(job.collection)
        elif event.kind == events.MESSAGE:
            job.status.message = event.message
            logger.info("job %s: MSG:%s %s",
                        job.job_id, event.code, event.message)
        elif event.kind == events.FINISHED:
            self._finish(job, event)
            return

        self._notify(job)

    def _finish(self, job: _Job, event: events.JobEvent) -> None:
        verdict = event.verdict
        obs = event.observation

        attempt = job.disc.last_attempt
        if attempt is None or attempt.ended_at:
            # The job died before _start could file one, or something already
            # closed it. Either way the run has to leave a record behind.
            attempt = model.Attempt(attempt=job.disc.attempt_count + 1,
                                    device=job.device)
            job.disc.attempts.append(attempt)

        attempt.ended_at = model.now()
        attempt.outcome = verdict.outcome if verdict else outcome.FAILURE
        attempt.failure_reason = "" if self._is_good(verdict) else (
            verdict.reason if verdict else "the job ended without a verdict")
        attempt.error_kind = event.error_kind
        attempt.eject_ok = job.eject_ok
        attempt.eject_error = job.eject_error

        if obs is not None:
            attempt.exit_code = obs.exit_code
            attempt.final_progress = obs.max_total_progress
            attempt.progress_max = obs.progress_max
            attempt.bytes_written = obs.bytes_written
            attempt.layout = obs.layout
            attempt.read_error_count = obs.read_error_count
            attempt.hash_error_count = obs.hash_error_count
            # JSON keys are strings; convert once here rather than leaving the
            # model to round-trip int keys into strings behind our back.
            attempt.message_codes = {str(code): count for code, count
                                     in sorted(obs.message_codes.items())}

        if event.titles:
            job.disc.titles = list(event.titles)

        good = self._is_good(verdict)
        detail = verdict.reason if verdict else "the job ended without a verdict"
        logger.info("job %s finished: %s (%s)",
                    job.job_id, attempt.outcome, detail)
        self._retire(job, model.DONE if good else model.FAILED, detail)

    @staticmethod
    def _is_good(verdict) -> bool:
        return bool(verdict is not None and verdict.is_good)

    # -- state changes ------------------------------------------------------

    def _set_disc_state(self, job: _Job, state: str, detail: str) -> None:
        job.disc.state = state
        job.disc.state_detail = detail
        job.disc.updated_at = model.now()
        job.status.state = state
        job.status.step = detail or job.status.step

    def _fail_before_start(self, job: _Job, reason: str, error_kind: str) -> None:
        """Record a job that never got as far as spawning anything."""
        job.disc.attempts.append(model.Attempt(
            attempt=job.disc.attempt_count + 1,
            device=job.device,
            ended_at=model.now(),
            outcome=outcome.FAILURE,
            failure_reason=reason,
            error_kind=error_kind,
        ))
        self._retire(job, model.FAILED, reason)

    def _retire(self, job: _Job, state: str, detail: str) -> None:
        """Terminal for this job: write it down, free the drive, start the next.

        The slot is released here and only here, and the job is dropped from
        ``_jobs`` in the same breath, so a second FINISHED for the same job --
        which the runner is careful never to emit -- could not release it
        twice even if one arrived.
        """
        self._set_disc_state(job, state, detail)
        job.status.message = detail
        self._save(job.collection)

        if self._busy.get(job.device) == job.job_id:
            del self._busy[job.device]
        if job.job_id in self._queue:
            self._queue.remove(job.job_id)
        self._jobs.pop(job.job_id, None)

        self._notify(job)
        self._pump()

    # -- plumbing -----------------------------------------------------------

    def _job_for_disc(self, disc_id: str) -> _Job | None:
        for job in self._jobs.values():
            if job.disc.disc_id == disc_id:
                return job
        return None

    def _save(self, collection: model.Collection) -> None:
        """Persist, but never let a full disk take the job pipeline with it.

        The copy itself is still running and may well succeed; losing the
        JSON is recoverable at the next startup, and the operator has bigger
        news to act on than a stale timestamp.
        """
        try:
            self.store.save(collection)
        except StoreError as exc:
            logger.error("could not persist collection %s: %s",
                         collection.collection_id, exc)

    def _notify(self, job: _Job) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(job.status)
        except Exception:  # noqa: BLE001 -- a GUI bug must not stall the queue
            logger.exception("on_change callback failed for job %s", job.job_id)
