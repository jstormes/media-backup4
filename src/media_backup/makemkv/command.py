"""Construction of ``makemkvcon`` argv lists.

Near enough pure to assert in tests and record verbatim in the collection
metadata: the only thing these builders read is the machine's list of SCSI
generic nodes, and both that and the ``/sys`` tree it comes from are
injectable in :mod:`.isolation`.

Every builder takes the ``device`` the command is aimed at, and not because
makemkvcon is told it -- the source is still ``disc:N``. It is what
:mod:`.isolation` needs to hide the other drives from the process, and the
builders are where the two halves of the command meet. A caller that passes
no device gets the bare command, unisolated.
"""

from __future__ import annotations

from pathlib import Path

from . import isolation

#: Sentinel disc index used to enumerate drives. Opening it always fails --
#: that trailing MSG:5010 is expected -- but the DRV rows are emitted first.
ENUMERATION_INDEX = 9999


def _prefix(cfg, device: str = "") -> list[str]:
    """Command prefix: the drive sandbox, then line buffering, then the binary.

    makemkvcon's stdout is a pipe here, not a TTY, so libc may block-buffer
    it and deliver progress in bursts. This affects only how smoothly the
    progress bar moves: success is judged from message codes and the output
    tree, so buffering can never change a verdict.
    """
    sandbox = isolation.prefix(cfg, device)
    if getattr(cfg, "use_stdbuf", True):
        return [*sandbox, "stdbuf", "-oL", "-eL", str(cfg.makemkvcon)]
    return [*sandbox, str(cfg.makemkvcon)]


def enumerate_argv(cfg, device: str = "") -> list[str]:
    """Argv that lists the drives and their ``disc:N`` indices.

    Every drive, unless ``device`` is isolated -- then this lists that one
    drive, at index 0. Either way the DRV row carries the device path, which
    is what :func:`.enumeration.resolve` matches on.
    """
    return [
        *_prefix(cfg, device), "-r", f"--cache={cfg.cache_mb}",
        "info", f"disc:{ENUMERATION_INDEX}",
    ]


def info_argv(cfg, disc_index: int, device: str = "") -> list[str]:
    """Argv that scans one disc for its title inventory."""
    return [
        *_prefix(cfg, device), "-r", "--progress=-same", f"--cache={cfg.cache_mb}",
        "info", f"disc:{disc_index}",
    ]


def mkv_argv(cfg, disc_index: int, dest: Path, title_id: int,
             device: str = "") -> list[str]:
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
    argv = [*_prefix(cfg, device), "-r", "--progress=-same"]
    if cfg.decrypt:
        argv.append("--decrypt")
    argv.append(f"--cache={cfg.cache_mb}")
    argv += ["mkv", f"disc:{disc_index}", str(title_id), str(dest)]
    return argv
