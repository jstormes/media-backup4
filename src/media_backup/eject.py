"""Ejecting a disc through udisks2.

We already hold a system-bus connection for drive detection, ``Drive.Eject``
is present on the drive objects, and going through udisks2 keeps a single
permission story (polkit) rather than adding a second one for a subprocess.
It also means the existing :class:`DriveMonitor` observes the resulting
signal and updates the UI without being told.

Called from the job worker thread. ``Gio.DBusConnection.call_sync`` is
thread-safe and needs no main loop, and an unmount can block for seconds
flushing buffers -- which is exactly why it must not run on the GUI thread.
"""

from __future__ import annotations

import logging

try:
    from gi.repository import Gio, GLib
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyGObject is required for ejecting discs") from exc

from .drives import IFACE_FILESYSTEM, UDISKS2_BUS, DriveScanner

logger = logging.getLogger(__name__)

IFACE_DRIVE = "org.freedesktop.UDisks2.Drive"
NO_OPTIONS = GLib.Variant("(a{sv})", ({},))


class EjectError(Exception):
    """The disc could not be ejected; the operator must remove it by hand."""


def _call(connection, path: str, interface: str, method: str, args) -> None:
    connection.call_sync(
        UDISKS2_BUS, path, interface, method, args, None,
        Gio.DBusCallFlags.NONE, -1, None,
    )


def unmount(block_object_path: str, *, force: bool = False, connection=None) -> None:
    """Unmount a mounted disc. The kernel will not eject a mounted device."""
    connection = connection or DriveScanner._bus()
    options = {"force": GLib.Variant("b", True)} if force else {}
    _call(connection, block_object_path, IFACE_FILESYSTEM, "Unmount",
          GLib.Variant("(a{sv})", (options,)))


def eject(drive_object_path: str, block_object_path: str = "", *,
          mounted: bool = False, connection=None) -> None:
    """Eject the disc in a drive, unmounting first if it is mounted.

    Raises :class:`EjectError` with the underlying D-Bus message. Callers
    should record the failure and tell the operator to remove the disc
    manually rather than retrying in a loop -- a drive that will not open is
    not going to open on the third ask.
    """
    if not drive_object_path:
        raise EjectError("no udisks2 drive object path for this device")

    connection = connection or DriveScanner._bus()

    if mounted and block_object_path:
        try:
            unmount(block_object_path, connection=connection)
        except GLib.GError as exc:
            logger.info("plain unmount failed (%s); retrying forced", exc)
            try:
                unmount(block_object_path, force=True, connection=connection)
            except GLib.GError as forced:
                raise EjectError(f"could not unmount the disc: {forced}") from forced

    try:
        _call(connection, drive_object_path, IFACE_DRIVE, "Eject", NO_OPTIONS)
    except GLib.GError as exc:
        raise EjectError(f"could not eject the disc: {exc}") from exc

    logger.info("ejected %s", drive_object_path)


def eject_drive_state(drive, *, connection=None) -> None:
    """Eject the disc described by a :class:`DriveState`."""
    eject(
        drive.drive_object_path,
        drive.object_path,
        mounted=bool(getattr(drive, "mount_points", None)),
        connection=connection,
    )
