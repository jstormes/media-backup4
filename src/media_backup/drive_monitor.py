"""D-Bus driven optical drive state monitor -- zero polling.

Listens for udisks2 ObjectManager.InterfacesAdded / InterfacesRemoved
signals and maintains an internal ``dict[str, DriveState]`` of all
optical drives, keyed by device path (``/dev/sr0``).

Every relevant signal triggers a full rescan.  A rescan is a single
D-Bus round trip with no subprocesses, and rebuilding the whole state
avoids a class of partial-update bugs -- notably the fact that
``InterfacesRemoved`` fires for *both* a disc being ejected (the
``Filesystem`` interface goes away, the drive stays) and a drive being
unplugged (the ``Block`` interface goes away).  The two are told apart
by inspecting the removed-interface list, and only the latter is
allowed to drop a drive from the list.

Signals arrive on a background thread running its own GLib.MainLoop();
bursts are coalesced by a short debounce, and the resulting callback is
marshalled onto the tkinter main thread.

Usage
-----
    monitor = DriveMonitor()
    monitor.attach_to_tkinter(root)
    monitor.connect(on_changed=your_callback)
    # ... later, when done ...
    monitor.disconnect()
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

try:
    from gi.repository import GLib, Gio
except ImportError as exc:
    raise ImportError("PyGObject is required for drive monitoring") from exc

from media_backup.drives import IFACE_BLOCK, UDISKS2_PATH, DriveScanner, DriveState

logger = logging.getLogger(__name__)

#: udisks2 emits several InterfacesAdded/Removed signals per disc change.
#: Coalesce them so one physical event produces one rescan.
RESCAN_DEBOUNCE_MS = 150


# ---------------------------------------------------------------------------
# Background D-Bus signal thread
# ---------------------------------------------------------------------------


class _DBusSignalThread(threading.Thread):
    """Background thread that runs a GLib.MainLoop to receive D-Bus signals.

    D-Bus signals from udisks2 are delivered via GLib's main context,
    which only works inside a GLib.MainLoop.  This thread owns the loop.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="dbus-signal")
        self._loop: GLib.MainLoop | None = None
        self._bus: Gio.DBusConnection | None = None
        self._sub_ids: list[int] = []
        self._on_signal: Callable[[str, str], None] | None = None

    def start_listening(self, on_signal: Callable[[str, str], None]) -> None:
        """Start the background signal loop.

        Args:
            on_signal:  Called on the signal thread with
                        (object_path, "added"|"disc-removed"|"drive-removed").
        """
        self._on_signal = on_signal
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self._loop = GLib.MainLoop()
        self.start()
        self._subscribe_signals()
        logger.info("D-Bus signal thread started")

    def _subscribe_signals(self) -> None:
        bus = self._bus
        assert bus is not None

        for sig in ("InterfacesAdded", "InterfacesRemoved"):
            self._sub_ids.append(
                bus.signal_subscribe(
                    None,
                    "org.freedesktop.DBus.ObjectManager",
                    sig,
                    UDISKS2_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_bus_signal,
                )
            )

    def _on_bus_signal(
        self,
        _conn,
        _sender,
        _obj_path,
        _interface,
        sig_name,
        params,
    ) -> None:
        """Classify a udisks2 ObjectManager signal and hand it upstream."""
        try:
            obj_path = params.get_child_value(0).get_string()
        except Exception:
            logger.debug("Ignoring malformed %s signal", sig_name, exc_info=True)
            return

        if "/block_devices/" not in obj_path:
            return

        if sig_name == "InterfacesRemoved":
            try:
                removed = list(params.get_child_value(1).unpack())
            except Exception:
                logger.debug("Could not read removed interfaces", exc_info=True)
                removed = []
            # The Block interface going away means the device itself is gone.
            # Anything else (typically Filesystem) means the device is still
            # there and only its disc changed.
            event = "drive-removed" if IFACE_BLOCK in removed else "disc-removed"
        else:
            event = "added"

        logger.info("SIGNAL %-14s %s", event, obj_path)

        if self._on_signal is not None:
            self._on_signal(obj_path, event)

    def run(self) -> None:
        """Run the GLib main loop until quit()."""
        assert self._loop is not None
        self._loop.run()

    def stop(self) -> None:
        """Unsubscribe and signal the main loop to quit."""
        if self._bus is not None:
            for sub_id in self._sub_ids:
                self._bus.signal_unsubscribe(sub_id)
            self._sub_ids.clear()
        if self._loop is not None:
            loop, self._loop = self._loop, None
            GLib.idle_add(loop.quit)
        self.join(timeout=2.0)
        logger.info("D-Bus signal thread stopped")


# ---------------------------------------------------------------------------
# DriveMonitor
# ---------------------------------------------------------------------------


class DriveMonitor:
    """Stateful D-Bus optical-drive monitor.

    Drive state is stored in ``self.drives`` (``dict[str, DriveState]``
    keyed by device path such as ``/dev/sr0``).  The public API is minimal:

    * ``attach_to_tkinter(root)`` -- record the tkinter root so callbacks
      can be marshalled onto its event loop.
    * ``connect(on_changed)`` -- connect to D-Bus, do an initial scan, start
      listening for insert/eject signals.
    * ``disconnect()`` -- drop the D-Bus subscription and stop the thread.
    """

    def __init__(self) -> None:
        self._drives: dict[str, DriveState] = {}
        self._signal_thread: _DBusSignalThread | None = None
        self._on_changed: Callable[[list[DriveState]], None] | None = None
        self._tk_root = None
        self._lock = threading.Lock()
        self._rescan_pending = False
        self._last_notified: list[DriveState] | None = None

    # -- public properties --------------------------------------------------

    @property
    def drives(self) -> dict[str, DriveState]:
        """Current drive state (``{device_path: DriveState, ...}``)."""
        return self._drives

    # -- connection ---------------------------------------------------------

    def attach_to_tkinter(self, root) -> None:
        """Record the tkinter root used to marshal callbacks onto its loop.

        Signals arrive on the D-Bus thread; ``root.after()`` hands the
        resulting update to the tkinter main loop.  Call this before
        :meth:`connect` so the initial scan is delivered too.
        """
        self._tk_root = root

    def connect(
        self,
        on_changed: Callable[[list[DriveState]], None],
    ) -> None:
        """Connect to D-Bus, scan existing drives, and start listening.

        Args:
            on_changed:  Called on the tkinter main thread with the
                         full list of :class:`DriveState` objects whenever
                         drive state changes.
        """
        if self._signal_thread is not None:
            return  # already connected

        self._on_changed = on_changed
        self._signal_thread = _DBusSignalThread()
        self._signal_thread.start_listening(self._on_dbus_signal)

        logger.info("Initial drive scan ...")
        self._rescan()

    def disconnect(self) -> None:
        """Unsubscribe from D-Bus signals and stop the signal thread."""
        if self._signal_thread is not None:
            self._signal_thread.stop()
            self._signal_thread = None

    # -- internal: signal handling ------------------------------------------

    def _on_dbus_signal(self, obj_path: str, event: str) -> None:
        """Handle a classified D-Bus signal (runs on the signal thread).

        A drive being unplugged is applied immediately so the card cannot
        linger; everything else -- including a disc being ejected -- goes
        through a rescan, which reports the drive as empty rather than
        making it disappear.
        """
        if event == "drive-removed":
            device = f"/dev/{obj_path.rsplit('/', 1)[-1]}"
            if self._drives.pop(device, None) is not None:
                logger.info("Removed %s", device)
                self._notify()
        self._schedule_rescan()

    def _schedule_rescan(self) -> None:
        """Queue a debounced rescan, coalescing signal bursts into one."""
        with self._lock:
            if self._rescan_pending:
                return
            self._rescan_pending = True
        GLib.timeout_add(RESCAN_DEBOUNCE_MS, self._rescan)

    def _rescan(self) -> bool:
        """Rebuild drive state from udisks2 and notify if it changed."""
        with self._lock:
            self._rescan_pending = False

        self._drives = {drive.device: drive for drive in DriveScanner.scan()}
        logger.info("Scan found %d drive(s)", len(self._drives))
        for drive in self._drives.values():
            logger.debug("  %s media=%s", drive, drive.has_media)
        self._notify()
        return GLib.SOURCE_REMOVE

    # -- internal: notification ---------------------------------------------

    def _notify(self) -> None:
        """Tell observers that drive state changed, if it actually did."""
        if self._on_changed is None:
            return

        drive_list = sorted(self._drives.values(), key=lambda d: d.device)
        if drive_list == self._last_notified:
            return
        self._last_notified = drive_list

        if self._tk_root is not None:
            # after(0) runs at the next tkinter idle -- safe for UI updates.
            self._tk_root.after(0, self._on_changed, drive_list)
        else:
            self._on_changed(drive_list)
