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


def mkv_argv(cfg, disc_index: int, dest: Path, title_id: int) -> list[str]:
    """Argv that saves **one** title of a disc into ``dest`` as MKV.

    One title per invocation. The alternative -- ``all`` with a ``--minlength``
    computed from the selection -- cannot express "these two of the four", and
    a length filter cannot separate a title from another of the same runtime.
    Hancock offers its feature twice and that cost 88 GB where the film is 44,
    measured 2026-09-07.

    No ``--minlength`` here, deliberately: a title id is a position in the list
    MakeMKV is showing, so the save has to see the same list the scan did, and
    the scan does not pass one either.

    The source must be ``disc:N``. The binary rejects ``dev:`` outright, which
    is why the caller resolves a device path to an index immediately before
    the run.

    Which *tracks* are kept is not settable here. ``--profile`` is accepted and
    ignored -- measured 2026-09-07, MakeMKV's own FLAC profile left the audio
    as AC3 -- and the only lever is ``app_DefaultSelectionString`` in the
    operator's ``~/.MakeMKV/settings.conf``. See
    docs/makemkv/track-selection.md.
    """
    argv = [*_prefix(cfg), "-r", "--progress=-same"]
    if cfg.decrypt:
        argv.append("--decrypt")
    argv.append(f"--cache={cfg.cache_mb}")
    argv += ["mkv", f"disc:{disc_index}", str(title_id), str(dest)]
    return argv
