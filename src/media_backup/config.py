"""Application configuration.

Lives in ``config.json`` at the repo root (git-ignored; see
``config.example.json``). ``MEDIA_BACKUP_CONFIG`` overrides the path.

:func:`validate` never raises. It returns a list of problems for the GUI to
render, because the common failure here -- ``media_path`` pointing at a
volume that is not mounted yet -- must produce an explanation the operator
can act on, not a traceback at startup.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .makemkv import isolation

ENV_VAR = "MEDIA_BACKUP_CONFIG"
DEFAULT_FILENAME = "config.json"

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    level: str
    text: str

    @property
    def is_fatal(self) -> bool:
        return self.level == ERROR


@dataclass(frozen=True)
class Config:
    """Everything tunable. Defaults are chosen to be safe, not fast."""

    #: Base path for all output. Nothing is assumed about where it points.
    media_path: Path = Path("/srv/media-backup")
    #: Where finished collections are moved. Must be on the same filesystem as
    #: media_path, so the move is an atomic rename rather than a copy of tens
    #: of gigabytes. Defaults to media_path/finished.
    finished_path: Path | None = None
    #: Cancelled collections are moved here, not deleted -- a mis-click must
    #: not destroy hours of ripping.
    cancelled_path: Path | None = None
    #: Where obfuscated discs' navigation data is captured for study. Unlike
    #: the two above this is never renamed into, so it may sit on another
    #: filesystem -- and arguably should: a collection is reproducible by
    #: re-ripping the disc, while the confirmed answers accumulated here are
    #: somebody's afternoons and cannot be re-derived. Defaults to
    #: media_path/forensics. See docs/makemkv/playlist-obfuscation.md.
    forensics_path: Path | None = None

    makemkvcon: Path = Path("/usr/local/bin/makemkvcon")
    cache_mb: int = 1024
    decrypt: bool = True
    #: makemkvcon's stdout is a pipe, which libc may block-buffer. This only
    #: affects how smoothly progress moves, never a verdict.
    use_stdbuf: bool = True
    #: Wrap each makemkvcon run so it sees only the drive it was asked about.
    #: MakeMKV probes every drive on the machine at engine startup whatever
    #: source it is given, which reaches into whichever drive is mid-rip. See
    #: makemkv/isolation.py. Best-effort: a run whose drive cannot be isolated
    #: goes ahead unisolated rather than failing.
    isolate_drives: bool = True
    bwrap: Path = Path("/usr/bin/bwrap")
    #: 0 means one job per drive with no cap.
    max_concurrent_jobs: int = 0
    #: Headroom required beyond the disc size before a rip may start.
    min_free_margin_bytes: int = 10 * 1024**3
    #: Fraction of the scanned titles' reported size that must land on disk.
    #: Low because the yardstick over-estimates: MakeMKV reports a title's
    #: size on the disc, and the remux comes out under it -- 0.84 on a
    #: Blu-ray, 0.98 on a DVD. See makemkv/outcome.py.
    size_ratio_floor: float = 0.70
    #: ...and no more than this multiple of it. Catches a run that saved the
    #: same footage twice, which a floor alone waves through.
    size_ratio_ceiling: float = 1.5
    #: How much shorter than the disc says a saved title may run before the
    #: run is judged incomplete. Seconds. Measured 2026-09-07, a correct
    #: Blu-ray title came back 0.6s under what the scan reported.
    duration_tolerance_s: int = 10
    #: A run with no progress and no messages for this long is wedged. A disc
    #: grinding through read retries still emits MSG:2003, so it is not silent.
    stall_timeout_s: int = 1800
    #: Budget for the short probes -- enumerate and scan. These finish in
    #: seconds on healthy hardware (14s for four loaded drives, measured
    #: 2026-09-07), and they produce no output at all when a drive wedges,
    #: so stall_timeout_s cannot see them. A drive that hangs MakeMKV's
    #: probe holds the job forever without this.
    probe_timeout_s: int = 300
    max_job_duration_s: int = 21600
    eject_on_success: bool = True
    #: How many failed attempts' data to keep. Logs are always kept; three
    #: failed Blu-ray attempts is 100+ GB of partial output.
    keep_rejected_attempts: int = 1
    max_log_bytes: int = 50 * 1024**2

    # -- derived ------------------------------------------------------------

    @property
    def collections_path(self) -> Path:
        return self.media_path / "collections"

    @property
    def finished_dir(self) -> Path:
        return self.finished_path or (self.media_path / "finished")

    @property
    def cancelled_dir(self) -> Path:
        return self.cancelled_path or (self.media_path / "cancelled")

    @property
    def forensics_dir(self) -> Path:
        return self.forensics_path or (self.media_path / "forensics")

    @property
    def lock_path(self) -> Path:
        return self.media_path / ".media-backup.lock"

    def to_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = str(value) if isinstance(value, Path) else value
        return out


def default_path() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / DEFAULT_FILENAME


def load(path: Path | None = None) -> Config:
    """Load config, falling back to defaults for anything unspecified.

    A missing file is not an error -- the defaults are a working starting
    point and validate() will explain what needs setting.
    """
    path = path or default_path()
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}

    known = {f.name: f for f in fields(Config)}
    kwargs = {}
    for name, value in data.items():
        f = known.get(name)
        if f is None or value is None and name in (
                "finished_path", "cancelled_path", "forensics_path"):
            if f is not None:
                kwargs[name] = None
            continue
        if "Path" in str(f.type):
            kwargs[name] = Path(value)
        else:
            kwargs[name] = value
    return Config(**kwargs)


def validate(cfg: Config) -> list[Problem]:
    """Check the environment. Returns problems; never raises."""
    problems: list[Problem] = []

    if not cfg.media_path.exists():
        problems.append(Problem(ERROR,
            f"media_path does not exist: {cfg.media_path}\n"
            "If this is a drive that has not been mounted yet, mount it and "
            "restart. Nothing will be written until it exists."))
        return problems

    if not cfg.media_path.is_dir():
        problems.append(Problem(ERROR, f"media_path is not a directory: {cfg.media_path}"))
        return problems

    if not os.access(cfg.media_path, os.W_OK):
        problems.append(Problem(ERROR, f"media_path is not writable: {cfg.media_path}"))

    # The whole finish-by-rename design rests on this being one filesystem.
    for name, target in (("finished_path", cfg.finished_dir),
                         ("cancelled_path", cfg.cancelled_dir)):
        anchor = target if target.exists() else target.parent
        try:
            if anchor.exists() and anchor.stat().st_dev != cfg.media_path.stat().st_dev:
                problems.append(Problem(ERROR,
                    f"{name} ({target}) is on a different filesystem from "
                    f"media_path ({cfg.media_path}). Finishing a collection "
                    "would copy every byte instead of renaming it."))
        except OSError as exc:
            problems.append(Problem(WARNING, f"could not stat {target}: {exc}"))

    if not cfg.makemkvcon.is_file() or not os.access(cfg.makemkvcon, os.X_OK):
        problems.append(Problem(ERROR,
            f"makemkvcon not found or not executable: {cfg.makemkvcon}"))

    for text in isolation.problems(cfg):
        problems.append(Problem(WARNING, text))

    if cfg.use_stdbuf and shutil.which("stdbuf") is None:
        problems.append(Problem(WARNING,
            "use_stdbuf is on but stdbuf is not on PATH; progress may arrive "
            "in bursts. This does not affect whether a backup is judged good."))

    try:
        free = shutil.disk_usage(cfg.media_path).free
        if free < cfg.min_free_margin_bytes:
            problems.append(Problem(WARNING,
                f"only {free / 1024**3:.1f} GB free on {cfg.media_path}"))
    except OSError as exc:
        problems.append(Problem(WARNING, f"could not check free space: {exc}"))

    if not 0.0 < cfg.size_ratio_floor <= 1.0:
        problems.append(Problem(ERROR, "size_ratio_floor must be between 0 and 1"))
    if cfg.size_ratio_ceiling < 1.0:
        problems.append(Problem(ERROR, "size_ratio_ceiling must be at least 1"))
    if cfg.cache_mb < 1:
        problems.append(Problem(ERROR, "cache_mb must be at least 1"))
    if cfg.probe_timeout_s < 1:
        problems.append(Problem(ERROR, "probe_timeout_s must be at least 1"))

    return problems


def ensure_directories(cfg: Config) -> None:
    """Create the standard subdirectories. Assumes validate() passed."""
    for path in (cfg.collections_path, cfg.finished_dir, cfg.cancelled_dir,
                 cfg.forensics_dir):
        path.mkdir(parents=True, exist_ok=True)


def free_bytes(cfg: Config) -> int:
    try:
        return shutil.disk_usage(cfg.media_path).free
    except OSError:
        return 0


def room_for(free: int, cfg: Config, size_bytes: int) -> bool:
    """True if ``size_bytes`` fits in ``free`` with the margin intact.

    Split from :func:`has_room_for` so a caller that already knows the free
    space -- or is a test that must not depend on the host's -- can ask the
    same question without touching the filesystem.
    """
    return free >= int(size_bytes * 1.05) + cfg.min_free_margin_bytes


def has_room_for(cfg: Config, disc_size_bytes: int) -> bool:
    """True if a disc of this size can be written with the margin intact."""
    return room_for(free_bytes(cfg), cfg, disc_size_bytes)
