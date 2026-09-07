"""Running one ``makemkvcon backup`` from start to finish.

A job is more than a line reader: it resolves the makemkv disc index, proves
the disc in the drive is the one the operator picked, checks free space,
spawns, reads, judges the output tree, and ejects. On a worker thread that
reads as ordinary sequential code, which is why this is a thread rather than
a callback in a selector loop.

The worker touches no application state. It emits :class:`JobEvent` objects
and the GUI thread applies them.

``spawn`` is injectable, and that is the point: a fake process replaying a
captured transcript exercises this whole file -- verification, judging,
cancellation -- with no subprocess, no D-Bus and no disc.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .. import events, model
from ..config import Config, has_room_for
from . import command, inspect as layouts, messages
from .enumeration import Resolution, parse_drives, resolve
from .outcome import BackupObservation, OutcomePolicy, Verdict, judge
from .records import (ATTR_DURATION, ATTR_NAME, ATTR_SIZE_BYTES,
                      ATTR_SOURCE_FILE, ATTR_TYPE, Cinfo, Msg, Prgc,
                      Prgt, Prgv, Tcount, Tinfo, parse_line)

logger = logging.getLogger(__name__)

#: How often a live job may emit a progress event, in seconds. Rate limiting
#: at the source keeps a multi-hour job from flooding the GUI queue.
PROGRESS_INTERVAL_S = 0.25

TERMINATE_GRACE_S = 10.0
KILL_GRACE_S = 5.0


class _Aborted(Exception):
    """Raised internally when a step has already reported its own failure."""


class ProcessHandle(Protocol):
    """The parts of a running process this module uses."""

    def lines(self) -> Iterator[str]: ...
    def wait(self) -> int: ...
    def signal(self, sig: int) -> None: ...
    def alive(self) -> bool: ...


class _Popen:
    """A real makemkvcon process, in its own session.

    makemkvcon spawns helpers -- a JRE for BD-J discs among them -- so signals
    go to the process *group*. Killing only the parent orphans children that
    keep the drive open, which shows up as a mysteriously busy drive several
    discs later.
    """

    def __init__(self, argv: list[str]) -> None:
        self._proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
            start_new_session=True,
        )
        self._lock = threading.Lock()
        self._reaped = False

    def lines(self) -> Iterator[str]:
        assert self._proc.stdout is not None
        yield from self._proc.stdout

    def wait(self) -> int:
        """Only the worker thread may call this."""
        code = self._proc.wait()
        with self._lock:
            self._reaped = True
        return code

    def alive(self) -> bool:
        return self._proc.poll() is None

    def signal(self, sig: int) -> None:
        """Signal the whole group. Safe to call from any thread.

        Guarded because a reaped PID can be recycled by the kernel, and
        signalling a recycled PID would hit an unrelated process.
        """
        with self._lock:
            if self._reaped:
                return
            try:
                os.killpg(os.getpgid(self._proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                pass


def _real_spawn(argv: list[str]) -> ProcessHandle:
    return _Popen(argv)


@dataclass
class BackupRequest:
    """Everything a job needs. Assembled on the GUI thread."""

    job_id: str
    collection_id: str
    disc_id: str
    device: str
    expected_label: str
    disc_size_bytes: int
    #: Where makemkvcon writes: a directory for a Blu-ray, an ISO file for a
    #: DVD. Named for the destination rather than a directory, because it is
    #: not always one -- and that kind of misnaming is what this whole class
    #: of bug is made of.
    dest: Path
    log_path: Path
    attempt: int
    cfg: Config
    drive_object_path: str = ""
    block_object_path: str = ""
    mounted: bool = False


class BackupRunner:
    """Runs one backup on its own thread."""

    def __init__(
        self,
        request: BackupRequest,
        emit: Callable[[events.JobEvent], None],
        *,
        spawn: Callable[[list[str]], ProcessHandle] = _real_spawn,
        clock: Callable[[], float] = time.monotonic,
        ejector: Callable[..., None] | None = None,
        policy: OutcomePolicy | None = None,
    ) -> None:
        self.request = request
        self._emit = emit
        self._spawn = spawn
        self._clock = clock
        self._ejector = ejector
        self._policy = policy or OutcomePolicy(
            size_ratio_floor=request.cfg.size_ratio_floor)

        self._thread: threading.Thread | None = None
        self._process: ProcessHandle | None = None
        self._process_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._cancel_reason = ""
        self._last_progress_at = 0.0
        self._last_activity_at = 0.0
        self._titles: list[model.Title] = []
        self._disc_name = ""
        self._disc_type = ""

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"job-{self.request.job_id[:8]}", daemon=True)
        self._thread.start()

    def cancel(self, reason: str = model.ERR_CANCELLED) -> None:
        """Ask the job to stop. Safe from any thread.

        Robot mode has no cancellation, so this is signals: TERM to the group,
        then KILL if it is still there.
        """
        self._cancel_reason = reason
        self._cancelled.set()
        with self._process_lock:
            process = self._process
        if process is None:
            return
        process.signal(signal.SIGTERM)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- emitting -----------------------------------------------------------

    def _state(self, state: str, step: str = "") -> None:
        self._emit(events.JobEvent(self.request.job_id, events.STATE,
                                   state=state, step=step))

    def _message(self, code: int, text: str) -> None:
        self._emit(events.JobEvent(self.request.job_id, events.MESSAGE,
                                   code=code, message=text))

    def _finish(self, verdict: Verdict, obs: BackupObservation, error_kind: str = "") -> None:
        self._emit(events.JobEvent(
            self.request.job_id, events.FINISHED,
            verdict=verdict, observation=obs, error_kind=error_kind,
            titles=tuple(self._titles),
        ))

    def _fail(self, reason: str, error_kind: str, obs: BackupObservation | None = None) -> None:
        obs = obs or BackupObservation(disc_size_bytes=self.request.disc_size_bytes)
        self._finish(Verdict("failure", reason, {"error_kind": error_kind}),
                     obs, error_kind)

    # -- the job ------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._run_inner()
        except _Aborted:
            # The step that raised has already emitted its own FINISHED event;
            # emitting a second one would release the drive slot twice.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s crashed", self.request.job_id)
            self._fail(f"internal error: {exc}", model.ERR_SPAWN)

    def _run_inner(self) -> None:
        req = self.request

        # 1. Which disc:N is this device right now?
        self._state(model.RESOLVING, "identifying the drive")
        resolution = self._resolve_index()
        if not resolution:
            self._fail(resolution.detail, resolution.error_kind)
            return

        if self._cancelled.is_set():
            self._fail("cancelled before the copy started", model.ERR_CANCELLED)
            return

        # 2. Will it fit?
        if req.disc_size_bytes and not has_room_for(req.cfg, req.disc_size_bytes):
            self._fail(
                f"not enough free space for {req.disc_size_bytes / 1024**3:.1f} GB "
                f"plus the configured margin", model.ERR_NO_SPACE)
            return

        # 3. The destination has to be exactly what makemkvcon expects. A
        #    Blu-ray goes into a directory, which may already be there so long
        #    as it is empty; a DVD becomes an image file, and makemkvcon
        #    refuses any path already taken -- answering MSG:5068, "already
        #    contains a backup", about an empty directory it simply did not
        #    create. Measured 2026-09-06, after it cost a real backup.
        if req.dest.is_dir():
            if not layouts.is_empty(req.dest):
                self._fail(
                    "the destination directory is not empty; the previous "
                    "attempt should have been moved aside first", model.ERR_COPY)
                return
        elif req.dest.exists():
            self._fail(
                "something is already at the destination path; makemkvcon "
                "writes the disc image itself and will not overwrite",
                model.ERR_COPY)
            return

        # 4. Title inventory, so the archive is searchable later.
        if req.cfg.scan_titles:
            self._state(model.SCANNING, "reading the disc")
            self._scan_titles(resolution.index)
            if self._cancelled.is_set():
                self._fail("cancelled during the disc scan", model.ERR_CANCELLED)
                return

        # 5. The copy itself.
        self._state(model.COPYING, "copying")
        obs = self._copy(resolution.index)

        # 6. Judge it.
        self._state(model.VERIFYING, "checking the copy")
        obs.layout = layouts.classify_layout(req.dest)
        obs.bytes_written = layouts.tree_size(req.dest)
        obs.disc_size_bytes = req.disc_size_bytes
        verdict = judge(obs, self._policy)

        error_kind = ""
        if verdict.outcome == "cancelled":
            error_kind = self._cancel_reason or model.ERR_CANCELLED
        elif not verdict.is_good:
            error_kind = model.ERR_COPY

        # 7. Eject only a disc we are finished with. A failed disc stays put
        #    so the operator can take it out, clean it and retry.
        if verdict.is_good and req.cfg.eject_on_success:
            self._state(model.EJECTING, "ejecting")
            self._eject()

        self._finish(verdict, obs, error_kind)

    # -- steps --------------------------------------------------------------

    def _resolve_index(self) -> Resolution:
        req = self.request
        argv = command.enumerate_argv(req.cfg)
        lines = list(self._run_to_completion(argv))
        drives = parse_drives(lines)
        resolution = resolve(drives, req.device, req.expected_label)

        # A drive that is still spinning up is worth waiting for.
        attempts = 0
        while (not resolution and resolution.error_kind == "drive_loading"
               and attempts < 3 and not self._cancelled.is_set()):
            attempts += 1
            self._sleep(2.0)
            lines = list(self._run_to_completion(argv))
            resolution = resolve(parse_drives(lines), req.device, req.expected_label)
        return resolution

    def _scan_titles(self, index: int) -> None:
        """Read the disc inventory, and what MakeMKV calls the disc.

        The CINFO records are the answer to "why does that DVD say
        DVD_VIDEO?". A DVD's volume label is very often a generic stamp, but
        MakeMKV identifies the disc anyway -- the one that failed on
        2026-09-06 reads ``DVD_VIDEO`` on the label and "Fresh Horses" here.
        The scan already fetched them; they were simply being dropped.
        """
        argv = command.info_argv(self.request.cfg, index)
        current: dict[int, model.Title] = {}
        for line in self._run_to_completion(argv):
            record = parse_line(line)
            if isinstance(record, Cinfo):
                if record.id == ATTR_NAME:
                    self._disc_name = record.value
                elif record.id == ATTR_TYPE:
                    self._disc_type = record.value
                continue
            if not isinstance(record, Tinfo):
                continue
            title = current.setdefault(record.title, model.Title(index=record.title))
            if record.id == ATTR_NAME:
                title.name = record.value
            elif record.id == ATTR_DURATION:
                title.duration = record.value
            elif record.id == ATTR_SIZE_BYTES:
                try:
                    title.size_bytes = int(record.value)
                except ValueError:
                    pass
            elif record.id == ATTR_SOURCE_FILE:
                title.source = record.value
        self._titles = [current[k] for k in sorted(current)]

        if not self._disc_name and self._titles:
            # CINFO:2 is not guaranteed. Every title on a disc tends to carry
            # the disc's name, so the feature's name is the best stand-in.
            self._disc_name = max(self._titles,
                                  key=lambda t: t.size_bytes).name

        logger.info("job %s: %r, %d title(s)", self.request.job_id,
                    self._disc_name, len(self._titles))
        self._emit(events.JobEvent(
            self.request.job_id, events.IDENTIFIED,
            disc_name=self._disc_name, disc_type=self._disc_type,
            titles=tuple(self._titles)))

    def _copy(self, index: int) -> BackupObservation:
        req = self.request
        argv = command.backup_argv(req.cfg, index, req.dest)
        obs = BackupObservation(disc_size_bytes=req.disc_size_bytes)

        try:
            process = self._spawn(argv)
        except OSError as exc:
            obs.exit_code = None
            self._fail(f"could not start makemkvcon: {exc}", model.ERR_SPAWN, obs)
            raise _Aborted from exc

        with self._process_lock:
            self._process = process

        started = self._clock()
        self._last_activity_at = started
        log_budget = req.cfg.max_log_bytes
        written = 0
        truncated = False

        req.log_path.parent.mkdir(parents=True, exist_ok=True)
        with req.log_path.open("w", errors="replace") as log:
            log.write("# " + " ".join(argv) + "\n")
            for line in process.lines():
                if written < log_budget:
                    log.write(line)
                    written += len(line)
                elif not truncated:
                    truncated = True
                    log.write("\n# ... log truncated at "
                              f"{log_budget} bytes ...\n")
                self._consume(line, obs)
                self._check_watchdogs(obs, started)

        obs.exit_code = process.wait()
        obs.terminated_by_us = self._cancelled.is_set()
        if obs.terminated_by_us and not obs.stall_reason:
            obs.stall_reason = self._cancel_reason or "cancelled"

        with self._process_lock:
            self._process = None
        return obs

    def _consume(self, line: str, obs: BackupObservation) -> None:
        record = parse_line(line)
        if record is None:
            return
        now = self._clock()

        if isinstance(record, Msg):
            self._last_activity_at = now
            obs.message_codes[record.code] = obs.message_codes.get(record.code, 0) + 1
            # A read-error storm would flood the GUI; the count is what matters.
            if record.code not in messages.READ_ERRORS:
                self._message(record.code, record.text)
        elif isinstance(record, Prgv):
            obs.saw_any_progress = True
            obs.progress_max = record.max or obs.progress_max
            obs.max_total_progress = max(obs.max_total_progress, record.total)
            if record.total or record.current:
                self._last_activity_at = now
            self._maybe_emit_progress(record, now)
        elif isinstance(record, (Prgt, Prgc)):
            self._last_activity_at = now
            self._emit(events.JobEvent(self.request.job_id, events.PROGRESS,
                                       step=record.name))

    def _maybe_emit_progress(self, record: Prgv, now: float) -> None:
        # The update that says "done" is never dropped. makemkvcon emits its
        # last few PRGV records back to back, so the rate limiter would eat
        # the 100% one and leave the bar stopped at 97% on a finished job --
        # the one reading an operator watching a multi-hour copy trusts.
        final = record.max > 0 and record.total >= record.max
        if not final and now - self._last_progress_at < PROGRESS_INTERVAL_S:
            return
        self._last_progress_at = now
        self._emit(events.JobEvent(
            self.request.job_id, events.PROGRESS,
            total_pct=record.total_pct, step_pct=record.step_pct,
        ))

    def _check_watchdogs(self, obs: BackupObservation, started: float) -> None:
        if self._cancelled.is_set():
            return
        cfg = self.request.cfg
        now = self._clock()
        if now - started > cfg.max_job_duration_s:
            obs.stall_reason = (
                f"gave up after {cfg.max_job_duration_s / 3600:.1f} hours")
            self.cancel(model.ERR_TIMEOUT)
        elif now - self._last_activity_at > cfg.stall_timeout_s:
            # A disc grinding through read retries still emits MSG:2003, so
            # genuine silence means the process is wedged, not working.
            obs.stall_reason = (
                f"no output for {cfg.stall_timeout_s // 60} minutes")
            self.cancel(model.ERR_STALLED)

    def _eject(self) -> None:
        if self._ejector is None:
            return
        req = self.request
        try:
            self._ejector(req.drive_object_path, req.block_object_path,
                          mounted=req.mounted)
        except Exception as exc:  # noqa: BLE001 -- a stuck tray is not a failed copy
            logger.warning("job %s: eject failed: %s", req.job_id, exc)
            self._message(0, f"The copy is good, but the disc did not eject: {exc}")

    # -- helpers ------------------------------------------------------------

    def _run_to_completion(self, argv: list[str]) -> list[str]:
        """Run a short makemkvcon command and collect its output."""
        try:
            process = self._spawn(argv)
        except OSError as exc:
            logger.warning("could not run %s: %s", argv[0], exc)
            return []
        with self._process_lock:
            self._process = process
        lines = list(process.lines())
        process.wait()
        with self._process_lock:
            self._process = None
        return lines

    def _sleep(self, seconds: float) -> None:
        self._cancelled.wait(seconds)
