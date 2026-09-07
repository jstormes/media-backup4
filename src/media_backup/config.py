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

    makemkvcon: Path = Path("/usr/local/bin/makemkvcon")
    cache_mb: int = 1024
    decrypt: bool = True
    #: makemkvcon's stdout is a pipe, which libc may block-buffer. This only
    #: affects how smoothly progress moves, never a verdict.
    use_stdbuf: bool = True
    #: 0 means one job per drive with no cap.
    max_concurrent_jobs: int = 0
    #: Headroom required beyond the disc size before a rip may start.
    min_free_margin_bytes: int = 10 * 1024**3
    #: Fraction of the scanned titles' reported size that must land on disk.
    #: Judged against what the scan said those titles weigh, not against the
    #: disc: an MKV run leaves out menus, duplicate angles and unwanted
    #: tracks by design, so the disc's own size says nothing about it.
    size_ratio_floor: float = 0.90
    #: A title counts as feature-length at this fraction of the longest one.
    #: See :mod:`makemkv.selection` for why this is relative and not absolute.
    feature_ratio: float = 0.90
    #: ...and at least this long outright.
    min_feature_seconds: int = 600
    #: More feature-length titles than this means the disc is hiding its
    #: feature among decoys. Those are left to the operator by hand.
    max_feature_titles: int = 5
    #: A run with no progress and no messages for this long is wedged. A disc
    #: grinding through read retries still emits MSG:2003, so it is not silent.
    stall_timeout_s: int = 1800
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
        if f is None or value is None and name in ("finished_path", "cancelled_path"):
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
    if not 0.0 < cfg.feature_ratio <= 1.0:
        problems.append(Problem(ERROR, "feature_ratio must be between 0 and 1"))
    if cfg.max_feature_titles < 1:
        problems.append(Problem(ERROR, "max_feature_titles must be at least 1"))
    if cfg.cache_mb < 1:
        problems.append(Problem(ERROR, "cache_mb must be at least 1"))

    return problems


def ensure_directories(cfg: Config) -> None:
    """Create the standard subdirectories. Assumes validate() passed."""
    for path in (cfg.collections_path, cfg.finished_dir, cfg.cancelled_dir):
        path.mkdir(parents=True, exist_ok=True)


def free_bytes(cfg: Config) -> int:
    try:
        return shutil.disk_usage(cfg.media_path).free
    except OSError:
        return 0


def has_room_for(cfg: Config, disc_size_bytes: int) -> bool:
    """True if a disc of this size can be written with the margin intact."""
    needed = int(disc_size_bytes * 1.05) + cfg.min_free_margin_bytes
    return free_bytes(cfg) >= needed
