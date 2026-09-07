"""Construction of ``makemkvcon`` argv lists.

Pure, so the exact command a run used can be asserted in tests and recorded
verbatim in the collection metadata.
"""

from __future__ import annotations

from pathlib import Path

#: Sentinel disc index used to enumerate drives. Opening it always fails --
#: that trailing MSG:5010 is expected -- but the DRV rows are emitted first.
ENUMERATION_INDEX = 9999


def _prefix(cfg) -> list[str]:
    """Command prefix, optionally forcing line-buffered output.

    makemkvcon's stdout is a pipe here, not a TTY, so libc may block-buffer
    it and deliver progress in bursts. This affects only how smoothly the
    progress bar moves: success is judged from message codes and the output
    tree, so buffering can never change a verdict.
    """
    if getattr(cfg, "use_stdbuf", True):
        return ["stdbuf", "-oL", "-eL", str(cfg.makemkvcon)]
    return [str(cfg.makemkvcon)]


def enumerate_argv(cfg) -> list[str]:
    """Argv that lists every drive and its ``disc:N`` index."""
    return [
        *_prefix(cfg), "-r", f"--cache={cfg.cache_mb}",
        "info", f"disc:{ENUMERATION_INDEX}",
    ]


def info_argv(cfg, disc_index: int) -> list[str]:
    """Argv that scans one disc for its title inventory."""
    return [
        *_prefix(cfg), "-r", "--progress=-same", f"--cache={cfg.cache_mb}",
        "info", f"disc:{disc_index}",
    ]


def backup_argv(cfg, disc_index: int, dest: Path) -> list[str]:
    """Argv that backs one disc up to ``dest``.

    The source must be ``disc:N``. The binary rejects ``dev:`` outright with
    ``Backup source must start with "disc:"``, which is why the caller has to
    resolve a device path to an index immediately before the run.
    """
    argv = [*_prefix(cfg), "-r", "--progress=-same"]
    if cfg.decrypt:
        argv.append("--decrypt")
    argv.append(f"--cache={cfg.cache_mb}")
    argv += ["backup", f"disc:{disc_index}", str(dest)]
    return argv
