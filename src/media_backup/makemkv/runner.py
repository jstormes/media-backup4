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
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .. import events, mkv, model
from ..config import Config, has_room_for
from . import command, inspect as layouts, isolation, messages, selection
from .enumeration import DriveIndex, Resolution, parse_drives, resolve
from .outcome import BackupObservation, OutcomePolicy, Verdict, judge
from .records import (ATTR_CHAPTER_COUNT, ATTR_COMMENT, ATTR_DURATION,
                      ATTR_NAME, ATTR_OUTPUT_FILE, ATTR_SEGMENTS_MAP,
                      ATTR_SIZE_BYTES, ATTR_SOURCE_FILE, ATTR_TYPE,
                      Cinfo, Msg, Prgc, Prgt, Prgv, Sinfo, Tcount,
                      Tinfo, parse_line)

logger = logging.getLogger(__name__)

#: How often a live job may emit a progress event, in seconds. Rate limiting
#: at the source keeps a multi-hour job from flooding the GUI queue.
PROGRESS_INTERVAL_S = 0.25

TERMINATE_GRACE_S = 10.0
KILL_GRACE_S = 5.0

#: How often an idle read wakes to run the watchdogs. Short enough that a
#: cancel is noticed promptly, long enough not to spin.
IDLE_POLL_S = 1.0

#: Pushed by the reader thread when the process's output ends.
_DONE = object()


def _read_counts(record: Msg, obs: BackupObservation) -> None:
    """Take the saved/failed title counts out of MakeMKV's own tally.

    ``5036``/``5005`` carry one parameter and ``5037``/``5004`` carry two, so
    the counts come from the run rather than from counting files afterwards.
    The files are counted too: the two agreeing is the point.
    """
    def number(index: int) -> int:
        try:
            return int(record.params[index])
        except (IndexError, ValueError):
            return 0

    if record.code in (messages.MKV_COMPLETE, messages.MKV_SAVED):
        obs.titles_saved = number(0)
    elif record.code in (messages.MKV_COMPLETE_PARTIAL, messages.MKV_SAVED_PARTIAL):
        obs.titles_saved = number(0)
        obs.titles_failed = number(1)


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
        drive_index: DriveIndex | None = None,
    ) -> None:
        self.request = request
        #: Shared by every job the manager starts, so a round of jobs runs
        #: one drive enumeration between them. A runner built on its own
        #: gets a private one and behaves exactly as it did before.
        self._drive_index = drive_index or DriveIndex(clock=clock)
        self._emit = emit
        self._spawn = spawn
        self._clock = clock
        self._ejector = ejector
        self._policy = policy or OutcomePolicy(
            size_ratio_floor=request.cfg.size_ratio_floor,
            size_ratio_ceiling=request.cfg.size_ratio_ceiling)
        self._selection_policy = selection.SelectionPolicy(
            feature_ratio=request.cfg.feature_ratio,
            min_feature_seconds=request.cfg.min_feature_seconds,
            max_feature_titles=request.cfg.max_feature_titles)

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
        #: Where the run is in its list of titles, for scaling progress: each
        #: makemkvcon run reports its own 0-100%, and the operator wants one
        #: bar for the job.
        self._titles_done = 0
        self._titles_total = 1
        self._log_written = 0
        self._log_truncated = False

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

        # 3. An empty directory, which mkv is content to find already there.
        if not layouts.is_empty(req.dest):
            self._fail(
                "the destination directory is not empty; the previous "
                "attempt should have been moved aside first", model.ERR_COPY)
            return

        # 4. What is on the disc. Not optional any more: the run saves titles,
        #    so something has to know what the titles are before it starts.
        self._state(model.SCANNING, "reading the disc")
        self._scan_titles(resolution.index)
        if self._cancelled.is_set():
            self._fail("cancelled during the disc scan", model.ERR_CANCELLED)
            return

        # 5. Which of them to save -- and whether this disc can be done at all
        #    without a human. See makemkv.selection.
        chosen = selection.choose(self._titles, self._selection_policy)
        if not chosen:
            self._fail(chosen.reason, chosen.error_kind)
            return
        logger.info("job %s: saving %d of %d title(s): %s",
                    req.job_id, len(chosen.titles), len(self._titles),
                    ", ".join(t.source or str(t.index) for t in chosen.titles))

        # 6. The copy itself.
        self._state(model.COPYING, "copying")
        obs = self._save_titles(resolution.index, chosen)

        # 7. Judge it.
        self._state(model.VERIFYING, "checking the copy")
        obs.layout = layouts.classify_layout(req.dest)
        obs.bytes_written = layouts.tree_size(req.dest)
        obs.disc_size_bytes = req.disc_size_bytes
        obs.titles_expected = len(chosen.titles)
        obs.expected_bytes = selection.expected_bytes(chosen)
        obs.files_written = layouts.count_mkv(req.dest)
        self._match_output(chosen, obs)
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
        argv = command.enumerate_argv(req.cfg, req.device)
        isolated = bool(isolation.prefix(req.cfg, req.device))

        def enumerate_once() -> list[str]:
            return self._run_to_completion(argv, "drive enumeration")

        def read_drives(*, force: bool = False):
            """This job's drive list, shared with other jobs only if it can be.

            An isolated enumeration sees one drive -- its own -- so another
            job's copy would resolve to device_not_found. The shared list
            exists to stop N jobs each probing every drive, and isolation
            already stops that: an isolated probe touches one drive, so
            running it per job costs nothing to anyone else.
            """
            if isolated:
                return parse_drives(enumerate_once())
            return self._drive_index.drives(enumerate_once, force=force)

        drives = read_drives()
        resolution = resolve(drives, req.device, req.expected_label)

        # A drive that is still spinning up is worth waiting for -- and the
        # cached list is exactly the one that said "loading", so re-reading
        # it would just say so again.
        attempts = 0
        while (not resolution and resolution.error_kind == "drive_loading"
               and attempts < 3 and not self._cancelled.is_set()):
            attempts += 1
            self._sleep(2.0)
            drives = read_drives(force=True)
            resolution = resolve(drives, req.device, req.expected_label)
        return resolution

    def _scan_titles(self, index: int) -> None:
        """Read the disc inventory, and what MakeMKV calls the disc.

        The CINFO records are the answer to "why does that DVD say
        DVD_VIDEO?". A DVD's volume label is very often a generic stamp, but
        MakeMKV identifies the disc anyway -- the one that failed on
        2026-09-06 reads ``DVD_VIDEO`` on the label and "Fresh Horses" here.
        The scan already fetched them; they were simply being dropped.
        """
        argv = command.info_argv(self.request.cfg, index, self.request.device)
        current: dict[int, model.Title] = {}
        streams: dict[int, set[int]] = {}
        for line in self._run_to_completion(argv, "disc scan"):
            record = parse_line(line)
            if isinstance(record, Sinfo):
                streams.setdefault(record.title, set()).add(record.stream)
                continue
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
            elif record.id == ATTR_SEGMENTS_MAP:
                title.segments = record.value
            elif record.id == ATTR_OUTPUT_FILE:
                title.suggested_file = record.value
            elif record.id == ATTR_COMMENT:
                title.designator = record.value
            elif record.id == ATTR_CHAPTER_COUNT:
                try:
                    title.chapters = int(record.value)
                except ValueError:
                    pass
        for index_, title in current.items():
            title.streams = len(streams.get(index_, ()))
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

    def _save_titles(self, index: int, chosen: selection.Selection) -> BackupObservation:
        """Save each chosen title, one makemkvcon run apiece.

        One run per title rather than one ``all`` pass: a length filter cannot
        say "these two of the four", and cannot separate a title from another
        of the same runtime at all. It also reads less, not more -- the pass
        that wrote Hancock twice read the disc twice over to do it.
        """
        req = self.request
        obs = BackupObservation(disc_size_bytes=req.disc_size_bytes)
        started = self._clock()
        self._last_activity_at = started
        self._log_written = 0
        self._log_truncated = False
        self._titles_done = 0
        self._titles_total = max(1, len(chosen.titles))
        codes: list[int] = []

        req.log_path.parent.mkdir(parents=True, exist_ok=True)
        with req.log_path.open("w", errors="replace") as log:
            for position, title in enumerate(chosen.titles):
                if self._cancelled.is_set():
                    break
                self._titles_done = position
                codes.append(self._save_one(index, title, obs, log, started))

        # The first thing to go wrong is the thing to report. A later run
        # exiting 0 does not undo an earlier one that did not.
        obs.exit_code = next((c for c in codes if c), 0) if codes else None
        obs.terminated_by_us = self._cancelled.is_set()
        if obs.terminated_by_us and not obs.stall_reason:
            obs.stall_reason = self._cancel_reason or "cancelled"
        return obs

    def _save_one(self, index: int, title: model.Title,
                  obs: BackupObservation, log, started: float) -> int:
        req = self.request
        argv = command.mkv_argv(req.cfg, index, req.dest, title.index, req.device)

        try:
            process = self._spawn(argv)
        except OSError as exc:
            self._fail(f"could not start makemkvcon: {exc}", model.ERR_SPAWN, obs)
            raise _Aborted from exc

        with self._process_lock:
            self._process = process

        log.write(f"# title {title.index} ({title.source or title.duration})\n")
        self._log(log, "# " + " ".join(argv) + "\n")
        # Through _iter_lines, so the watchdogs also run while the process
        # is silent. Driving them from arriving lines alone means a wedged
        # makemkvcon -- the one case stall_timeout_s exists for -- is the one
        # case they never catch.
        for line in self._iter_lines(
                process, lambda: self._check_watchdogs(obs, started)):
            self._log(log, line)
            self._consume(line, obs)
            self._check_watchdogs(obs, started)

        code = process.wait()
        with self._process_lock:
            self._process = None
        return code

    def _log(self, log, line: str) -> None:
        """Append to the attempt log, up to the configured budget.

        The budget spans the whole attempt, not each title: a read-error storm
        on the first of four titles must not buy the other three a fresh
        allowance apiece.
        """
        budget = self.request.cfg.max_log_bytes
        if self._log_written < budget:
            log.write(line)
            self._log_written += len(line)
        elif not self._log_truncated:
            self._log_truncated = True
            log.write(f"\n# ... log truncated at {budget} bytes ...\n")

    def _consume(self, line: str, obs: BackupObservation) -> None:
        record = parse_line(line)
        if record is None:
            return
        now = self._clock()

        if isinstance(record, Msg):
            self._last_activity_at = now
            obs.message_codes[record.code] = obs.message_codes.get(record.code, 0) + 1
            _read_counts(record, obs)
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
        # Each run reports its own progress; the bar is for the whole job.
        overall = ((self._titles_done + record.total_pct / 100.0)
                   / self._titles_total * 100.0)
        self._emit(events.JobEvent(
            self.request.job_id, events.PROGRESS,
            total_pct=overall, step_pct=record.step_pct,
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

    def _match_output(self, chosen: selection.Selection,
                      obs: BackupObservation) -> None:
        """Record which file each chosen title became, and read it back.

        Without the matching, the archive says four titles were saved and
        leaves whoever comes back to it to guess which .mkv is the extended
        cut -- MakeMKV's suggested filename is not reliable on its own, since
        the index in it counts within whatever list it was showing.

        Reading each file back is what lets the run be judged honestly. The
        disc said how long the title is; the file says how long it is. A copy
        that stopped early is short, and unlike size neither figure moves with
        container overhead.
        """
        tolerance = self.request.cfg.duration_tolerance_s
        files = layouts.mkv_files(self.request.dest)
        matched = selection.match_files(chosen.titles, files)
        by_index = {t.index: t for t in self._titles}
        for index, name in matched.items():
            title = by_index.get(index)
            if title is None:
                continue
            title.output_file = name
            measured = mkv.duration_seconds(self.request.dest / name)
            if measured is None:
                obs.titles_unverified += 1
                logger.warning("job %s: %s would not say how long it is",
                               self.request.job_id, name)
            elif title.seconds and measured < title.seconds - tolerance:
                obs.titles_short += 1
                logger.warning("job %s: %s runs %.0fs, disc says %ds",
                               self.request.job_id, name, measured,
                               title.seconds)

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

    def _iter_lines(self, process, on_idle: Callable[[], None]) -> Iterator[str]:
        """Yield the process's lines, waking every IDLE_POLL_S regardless.

        Iterating a pipe blocks until a line arrives, so a wedged makemkvcon
        -- one burning CPU and emitting nothing -- is indistinguishable from
        a slow one, and no amount of watchdog code in the loop body ever
        runs. A drive that hangs MakeMKV's probe produces exactly that: zero
        output, forever. Measured on an LG GHA2N, 2026-09-07.

        So the blocking read happens on its own thread and this loop waits on
        a queue instead, calling ``on_idle`` whenever nothing arrived. The
        reader thread is daemonic and ends when the pipe closes, which the
        kill path guarantees by signalling the process group.
        """
        # Bounded, so the reader stays in step with the consumer. An
        # unbounded queue would let a chatty process buffer its whole output
        # in memory, and would let _last_activity_at lag behind the lines
        # actually consumed, which is what the watchdogs measure.
        items: queue.Queue = queue.Queue(maxsize=1)

        def pump() -> None:
            try:
                for line in process.lines():
                    items.put(line)
            except Exception as exc:  # the pipe died under us; end the loop
                logger.debug("reader thread ended: %s", exc)
            finally:
                items.put(_DONE)

        threading.Thread(target=pump, name="makemkvcon-reader",
                         daemon=True).start()

        while True:
            try:
                item = items.get(timeout=IDLE_POLL_S)
            except queue.Empty:
                on_idle()
                continue
            if item is _DONE:
                return
            yield item

    def _probe_watchdog(self, started: float, what: str) -> Callable[[], None]:
        """Give up on a probe that has gone silent past its budget."""
        def check() -> None:
            if self._cancelled.is_set():
                return
            budget = self.request.cfg.probe_timeout_s
            if self._clock() - started > budget:
                logger.warning("job %s: %s produced no result in %ds",
                               self.request.job_id, what, budget)
                self.cancel(model.ERR_TIMEOUT)
        return check

    def _run_to_completion(self, argv: list[str], what: str = "probe") -> list[str]:
        """Run a short makemkvcon command and collect its output.

        Bounded by ``probe_timeout_s``: these commands finish in seconds, and
        the job has nothing to wait on if one never returns.
        """
        try:
            process = self._spawn(argv)
        except OSError as exc:
            logger.warning("could not run %s: %s", argv[0], exc)
            return []
        with self._process_lock:
            self._process = process
        started = self._clock()
        lines = list(self._iter_lines(process, self._probe_watchdog(started, what)))
        process.wait()
        with self._process_lock:
            self._process = None
        return lines

    def _sleep(self, seconds: float) -> None:
        self._cancelled.wait(seconds)
