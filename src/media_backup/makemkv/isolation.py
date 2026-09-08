"""Confining one ``makemkvcon`` run to the drive it was asked about.

MakeMKV probes **every** optical drive on the machine while its engine starts,
before it looks at the source argument at all. Measured 2026-09-08 against
v1.18.4 with ``strace -f -e trace=openat,ioctl``, four drives, none loaded:

| command                          | drives sent SCSI commands |
| -------------------------------- | ------------------------- |
| ``info disc:9999``               | sg0, sg1, sg2, sg3        |
| ``info dev:/dev/sr0``            | sg0, sg1, sg2, sg3 (70)   |
| ``--noscan info dev:/dev/sr0``   | sg0, sg1, sg2, sg3 (60)   |
| ``info disc:1``                  | sg0, sg1, sg2, sg3 (69)   |

Every drive that answers takes 22-25 of those commands; sg1 takes one, being a
drive that has been faulting since that morning and refusing INQUIRY.

So no source form avoids it, and neither does ``--noscan`` nor the hidden
``io_SingleDrive`` setting -- that one is GUI-only and has never worked; set
to ``"1"``, ``"0"`` and ``"/dev/sr0"`` it changed nothing here. This is old,
known behaviour: reported to MakeMKV in 2011 and again in 2015 ("even if
``dev:`` is among the parameters"), and still reported in 2024 against 1.17.8,
where seven drives cost 1m55s of startup before the first record appeared.

It costs a *running* job, which is the reason this module exists: the probe
from a job starting on one drive reaches into a drive that is mid-rip and
makes it thrash.

What does work is taking the other drives away from the process. MakeMKV finds
drives through their SCSI generic node -- ``/dev/sgN``, not ``/dev/srN`` -- and
a node it cannot open is dropped in silence, with no SCSI command issued at
all. So each run is wrapped in ``bwrap`` with every ``/dev/sg*`` except its own
masked by a bind mount of ``/dev/null``; bwrap mounts binds ``nodev`` inside
its user namespace, so the open fails with EACCES before it ever reaches
``/dev/null``. Verified: 24 SG_IO to the target's node, zero to the other
three, one DRV row in the output.

Every MakeMKV Docker image works the same way -- one container per drive, with
only that drive's ``sg`` node passed in.

Isolation is best-effort by design. If bwrap is missing, or the device cannot
be mapped to an ``sg`` node, the run proceeds unwrapped rather than failing:
probing every drive is the old behaviour and merely slow, where a job that
will not start is a disc that does not get backed up. :func:`problems` puts
that in front of the operator at startup instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

__all__ = ["prefix", "sg_name", "sg_nodes", "problems"]

DEV_DIR = Path("/dev")
SYS_BLOCK = Path("/sys/class/block")

#: How long the startup check gives ``bwrap`` to prove it can build a
#: namespace here. It either works immediately or is refused immediately.
CHECK_TIMEOUT_S = 10.0


def sg_name(device: str, *, sys_block: Path = SYS_BLOCK) -> str:
    """The name of the SCSI generic node behind ``device`` -- ``"sg0"``.

    A name and not a path, because the path this is compared against comes
    from listing the device directory, and the two must agree on more than
    spelling.

    ``/sys/class/block/sr0/device/scsi_generic/`` holds exactly one entry,
    named for the node. Asked of sysfs rather than assuming ``srN`` is
    ``sgN``: the two numberings count different things. ``sg`` numbers every
    SCSI device -- on this machine ``sg4`` is the system SSD -- so one more
    disk enumerating before an optical drive shifts the ``sg`` numbers while
    the ``sr`` numbers stay put. They line up here today; that is not a rule.
    """
    name = Path(device).resolve().name
    if not name:
        return ""
    try:
        entries = sorted(p.name for p in (sys_block / name / "device" / "scsi_generic").iterdir())
    except OSError:
        return ""
    return entries[0] if entries else ""


def sg_nodes(*, dev_dir: Path = DEV_DIR) -> list[str]:
    """Every SCSI generic node on the machine."""
    try:
        return sorted(str(p) for p in dev_dir.glob("sg*") if p.name[2:].isdigit())
    except OSError:
        return []


def prefix(cfg, device: str, *, dev_dir: Path = DEV_DIR,
           sys_block: Path = SYS_BLOCK) -> list[str]:
    """The bwrap argv that hides every drive but ``device``'s.

    Empty when isolation is off, unavailable, or the device cannot be mapped
    -- in which case the caller runs makemkvcon directly.

    The mask covers *all* other ``sg`` nodes, not only the optical ones. A
    node MakeMKV was never going to open costs nothing to mask, and choosing
    which to leave visible is how a drive plugged in since the last scan ends
    up probed anyway. Ordinary file I/O does not go through ``sg``: the
    destination filesystem is unaffected.
    """
    if not getattr(cfg, "isolate_drives", False) or not device:
        return []

    bwrap = str(getattr(cfg, "bwrap", ""))
    if not bwrap or not os.access(bwrap, os.X_OK):
        return []

    keep = sg_name(device, sys_block=sys_block)
    if not keep:
        return []

    argv = [bwrap, "--dev-bind", "/", "/"]
    for node in sg_nodes(dev_dir=dev_dir):
        if Path(node).name != keep:
            argv += ["--bind", "/dev/null", node]
    argv.append("--")
    return argv


def problems(cfg) -> list[str]:
    """What would stop isolation working, in words for the operator.

    Called from :func:`config.validate`, so the answer arrives at startup
    rather than as an unexplained thrashing drive three discs later.
    """
    if not getattr(cfg, "isolate_drives", False):
        return []

    bwrap = str(getattr(cfg, "bwrap", ""))
    if not bwrap or not os.access(bwrap, os.X_OK):
        return [f"isolate_drives is on but bwrap is not executable: {bwrap}. "
                "Runs will proceed unisolated, probing every drive."]

    try:
        done = subprocess.run([bwrap, "--dev-bind", "/", "/", "--", "true"],
                              capture_output=True, text=True,
                              timeout=CHECK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"isolate_drives is on but bwrap could not be run: {exc}. "
                "Runs will proceed unisolated, probing every drive."]

    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return [f"isolate_drives is on but bwrap cannot create a namespace "
                f"here{': ' + detail[-1] if detail else ''}. "
                "Runs will proceed unisolated, probing every drive."]
    return []
