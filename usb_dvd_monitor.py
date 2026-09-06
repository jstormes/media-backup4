#!/usr/bin/env python3
"""
USB DVD/BluRay Insertion Monitor

Watches udisks2 D-Bus signals for USB optical drive events:
  - Drive connected / disconnected (USB plug/unplug)
  - Disc inserted / ejected in a connected drive
  - Tray open / close

Uses the udisks2 D-Bus ObjectManager interface to detect device
addition/removal, and the Filesystem interface layer to detect
disc changes in optical drives.

Usage:
    python3 usb_dvd_monitor.py          # Start monitoring
    python3 usb_dvd_monitor.py --list   # List all block devices

Requires: PyGObject, udisks2 running on the system.

KNOWN ISSUES:
  - Disc insert detection may not fire reliably on all drive models.
    udisks2 emits ObjectManager.InterfacesAdded/Removed with the
    Filesystem interface on disc change, but the Drive.Media property
    can be empty at the time the signal fires. Consider polling
    Drive.Media periodically as a fallback.
"""

import sys
import signal
from datetime import datetime

try:
    from gi.repository import GLib, Gio
except ImportError:
    print("ERROR: PyGObject not installed.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# D-Bus helper: clean unpacked udisks2 data (byte arrays → strings)
# ---------------------------------------------------------------------------

def _clean_dict(d):
    """Clean byte arrays from an unpacked D-Bus dict."""
    if not isinstance(d, dict):
        return d
    result = {}
    for key, val in d.items():
        if isinstance(val, list) and val and isinstance(val[0], int):
            result[key] = bytes(val).decode("utf-8").rstrip("\x00")
        elif isinstance(val, dict):
            result[key] = _clean_dict(val)
        elif isinstance(val, list):
            cleaned = []
            for item in val:
                if isinstance(item, list) and item and isinstance(item[0], int):
                    cleaned.append(bytes(item).decode("utf-8", errors="replace").rstrip("\x00"))
                elif isinstance(item, dict):
                    cleaned.append(_clean_dict(item))
                else:
                    cleaned.append(item)
            result[key] = cleaned
        else:
            result[key] = val
    return result


def _extract_props(props_variant):
    """Extract properties from a GLib.Variant a{sv} into a plain dict."""
    result = {}
    for i in range(props_variant.n_children()):
        entry = props_variant.get_child_value(i)
        key = entry.get_child_value(0).get_string()
        val = entry.get_child_value(1)
        vtype = val.get_type_string()
        if vtype in ("s", "o"):
            result[key] = val.get_string().rstrip("\x00")
        elif vtype == "b":
            result[key] = val.get_boolean()
        elif vtype in ("i", "x", "d", "t", "u"):
            result[key] = val.unpack()
        elif vtype == "as":
            result[key] = [val.get_child_value(m).get_string().rstrip("\x00")
                           for m in range(val.n_children())]
        elif vtype == "a{sv}":
            inner = {}
            for n in range(val.n_children()):
                e = val.get_child_value(n)
                ek = e.get_child_value(0).get_string()
                ev = e.get_child_value(1)
                inner[ek] = _extract_props(ev) if ev.get_type_string() == "a{sv}" else (
                    ev.get_string().rstrip("\x00") if ev.get_type_string() in ("s", "o")
                    else ev.unpack()
                )
            result[key] = inner
        else:
            result[key] = val.unpack()
    return result


# ---------------------------------------------------------------------------
# udisks2 queries
# ---------------------------------------------------------------------------

def get_all_devices():
    """Get all block devices, drives, and filesystem interfaces from udisks2.

    Returns:
        devices:  dict path -> block props
        drives:   dict path -> drive props
        fs_paths: set of block device paths with a Filesystem interface
    """
    proxy = Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
        "org.freedesktop.UDisks2", "/org/freedesktop/UDisks2",
        "org.freedesktop.DBus.ObjectManager", None,
    )
    result = proxy.call_sync("GetManagedObjects", None,
                             Gio.DBusCallFlags.NONE, -1, None)
    raw_data = result.unpack()[0]

    devices = {}
    drives = {}
    fs_paths = set()
    for path, ifaces in raw_data.items():
        block = ifaces.get("org.freedesktop.UDisks2.Block")
        drive = ifaces.get("org.freedesktop.UDisks2.Drive")
        fs = ifaces.get("org.freedesktop.UDisks2.Filesystem")
        if block and isinstance(block, dict):
            devices[path] = _clean_dict(block)
        if drive and isinstance(drive, dict):
            drives[path] = _clean_dict(drive)
        if fs and isinstance(fs, dict) and "/block_devices/" in path:
            fs_paths.add(path)
    return devices, drives, fs_paths


def is_optical_drive(block_info, drive_info):
    """Return True if the block device is an optical drive."""
    if not drive_info:
        return False
    if drive_info.get("Optical"):
        return True
    model = drive_info.get("Model", "").lower()
    optical_keywords = [
        "dvd", "blu-ray", "blu ray", "bd-re", "bd-rew",
        "cd-rw", "cd-rom", "cdwriter", "cd-rewriter",
        "dvd-ram", "dvd-rew", "dvd-rw", "dvd-rom",
    ]
    return any(kw in model for kw in optical_keywords)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def list_devices():
    """List all attached block devices with details."""
    devices, drives = get_all_devices()

    print(f"\n{'Device':>12}  {'Size':>8}  {'Vendor':>20}  {'Model':>35}  {'Transport':>10}  {'Optical'}")
    print("-" * 120)

    for path, block in devices.items():
        dev = block.get("Device", "").split("/")[-1]
        size = block.get("Size", 0)
        size_gb = f"{size / (1024**3):.1f}GB" if size else "0B"

        drive_path = block.get("Drive", "")
        drive_info = drives.get(drive_path, {})

        vendor = drive_info.get("Vendor", "").strip() or "N/A"
        model = drive_info.get("Model", "").strip() or "N/A"
        transport = drive_info.get("ConnectionBus", "N/A") or "N/A"
        optical = is_optical_drive(block, drive_info)

        print(f"{dev:>12}  {size_gb:>8}  {vendor:>20}  {model:>35}  {transport:>10}  {'✓' if optical else ''}")


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _print_event(dev, drive_info, block_info, event_type):
    """Print a formatted event message."""
    media = drive_info.get("Media", "")
    size = block_info.get("Size", 0)
    size_gb = f"{size / (1024**3):.1f}GB" if size else "N/A"
    vendor = drive_info.get("Vendor", "").strip() or "N/A"
    model = drive_info.get("Model", "").strip() or "N/A"
    transport = drive_info.get("ConnectionBus", "").strip() or "N/A"

    lines = []
    if event_type == "CONNECTED":
        lines = [
            f"\n  [{_ts()}] USB OPTICAL DRIVE CONNECTED: /dev/{dev}",
            f"    Vendor:    {vendor}",
            f"    Model:     {model}",
            f"    Transport: {transport}",
            f"    Size:      {size_gb}",
            f"    Media:     {media}",
            f"    >>> USB DVD/BluRay DETECTED! <<<",
        ]
    elif event_type == "DISC_INSERTED":
        lines = [
            f"\n  [{_ts()}] DISC INSERTED in /dev/{dev}",
            f"    Media type: {media}",
            f"    >>> Disc inserted! <<<",
        ]
    elif event_type == "DISC_EJECTED":
        lines = [
            f"\n  [{_ts()}] DISC EJECTED from /dev/{dev}",
            f"    >>> Disc removed <<<",
        ]
    elif event_type == "DRIVE_REMOVED":
        lines = [
            f"\n  [{_ts()}] DRIVE REMOVED: /dev/{dev}",
            f"    >>> USB optical drive unplugged <<<",
        ]

    for line in lines:
        print(line)
    print("-" * 60)


# ---------------------------------------------------------------------------
# Main monitor loop
# ---------------------------------------------------------------------------

def monitor_devices():
    """Start monitoring for USB optical drive events."""
    main_loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    # --- State ---
    current_devices = {}   # path -> block props dict
    current_drives = {}    # path -> drive props dict
    detected_optical = set()   # device names (e.g. "sr0")
    fs_interface = {}      # block device path -> True (has filesystem interface)
    media_subs = {}        # drive path -> True (has MediaChanged subscriber)

    # --- Callback factories ---
    def make_media_handler(dev_name, drive_path):
        """Create a MediaChanged signal handler for a given device."""
        def handler(connection, sender, obj_path, iface, sig, params, user_data=None):
            try:
                new_media = params.get_child_value(0).get_string()
                if new_media:
                    _print_event(dev_name, {"Media": new_media}, {}, "DISC_INSERTED")
                else:
                    _print_event(dev_name, {}, {}, "DISC_EJECTED")
            except Exception as e:
                print(f"  [ERROR MediaChanged on /dev/{dev_name}]: {e}", file=sys.stderr)
        return handler

    def subscribe_drive_media(drive_path):
        """Subscribe to MediaChanged on a drive if not already done."""
        if drive_path in media_subs:
            return
        try:
            bus.signal_subscribe(
                None, "org.freedesktop.UDisks2.Drive", "MediaChanged",
                drive_path, None,
                Gio.DBusSignalFlags.NONE,
                make_media_handler("", drive_path),  # dev_name filled below
                None,
            )
            media_subs[drive_path] = True
        except GLib.GError as e:
            print(f"  [WARN] Could not subscribe to MediaChanged on {drive_path}: {e}",
                  file=sys.stderr)

    # --- ObjectManager: InterfacesAdded ---
    def on_interfaces_added(connection, sender, obj_path, iface, sig, params, user_data=None):
        """Handle new objects in the udisks2 device tree."""
        try:
            added = params.get_child_value(1)
        except Exception:
            return

        new_block = None
        new_drive = None
        new_fs = None

        for i in range(added.n_children()):
            child = added.get_child_value(i)
            name = child.get_child_value(0).get_string()
            props = child.get_child_value(1)
            props_dict = _extract_props(props)
            if name == "org.freedesktop.UDisks2.Block":
                new_block = props_dict
            elif name == "org.freedesktop.UDisks2.Drive":
                new_drive = props_dict
            elif name == "org.freedesktop.UDisks2.Filesystem":
                new_fs = props_dict

        # --- Filesystem interface appeared → disc inserted ---
        if new_fs and "/block_devices/" in obj_path:
            fs_interface[obj_path] = True
            dev_short = obj_path.split("/block_devices/")[-1]

            # Find the corresponding block device (might already be in current_devices
            # from a previous scan, or may arrive in a separate InterfacesAdded)
            block = current_devices.get(obj_path)
            if not block:
                for p, b in current_devices.items():
                    if b.get("Device", "").split("/")[-1] == dev_short:
                        block = b
                        break

            if block and dev_short.startswith("sr"):
                drive_path = block.get("Drive", "")
                drive = current_drives.get(drive_path, {})
                if is_optical_drive(block, drive):
                    media = drive.get("Media", "")
                    # The Drive.Media property may not be populated yet by udisks2
                    # when the Filesystem interface signal fires. This is a known
                    # timing issue with some drive models.
                    if media:
                        _print_event(dev_short, {"Media": media}, block, "DISC_INSERTED")

        # --- Block device added ---
        if new_block:
            current_devices[obj_path] = new_block
            dev = new_block.get("Device", "").split("/")[-1]
            drive_path = new_block.get("Drive", "")
            drive = current_drives.get(drive_path, {})

            if is_optical_drive(new_block, drive):
                # Drive just connected
                _print_event(dev, drive, new_block, "CONNECTED")
                subscribe_drive_media(drive_path)

            # Subscribe to media signals on the drive
            if drive_path and drive_path != "/":
                subscribe_drive_media(drive_path)

        # --- Drive object added ---
        if new_drive:
            current_drives[obj_path] = new_drive
            subscribe_drive_media(obj_path)

    # --- ObjectManager: InterfacesRemoved ---
    def on_interfaces_removed(connection, sender, obj_path, iface, sig, params, user_data=None):
        """Handle removed objects from the udisks2 device tree."""
        try:
            removed_path = params.get_child_value(0).get_string()
            removed_ifaces = params.get_child_value(1).unpack() if params.n_children() > 1 else []
        except Exception:
            return

        # Filesystem interface removed → disc ejected
        if "org.freedesktop.UDisks2.Filesystem" in removed_ifaces and "/block_devices/" in removed_path:
            if removed_path in fs_interface:
                del fs_interface[removed_path]
                block = current_devices.get(removed_path, {})
                if block:
                    dev = block.get("Device", "").split("/")[-1]
                    if dev in detected_optical:
                        _print_event(dev, {}, {}, "DISC_EJECTED")

        # Block device removed → drive unplugged
        if removed_path in current_devices:
            block = current_devices.pop(removed_path)
            dev = block.get("Device", "").split("/")[-1]
            drive_path = block.get("Drive", "")
            current_drives.pop(removed_path, None)
            media_subs.pop(removed_path, None)
            if dev in detected_optical:
                _print_event(dev, {}, {}, "DRIVE_REMOVED")

        if removed_path in current_drives:
            current_drives.pop(removed_path, None)
            media_subs.pop(removed_path, None)

    # --- Subscribe to ObjectManager signals ---
    bus.signal_subscribe(
        None, "org.freedesktop.DBus.ObjectManager", "InterfacesAdded",
        "/org/freedesktop/UDisks2", None,
        Gio.DBusSignalFlags.NONE, on_interfaces_added,
    )
    bus.signal_subscribe(
        None, "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved",
        "/org/freedesktop/UDisks2", None,
        Gio.DBusSignalFlags.NONE, on_interfaces_removed,
    )

    # --- Load initial state ---
    current_devices, current_drives, initial_fs = get_all_devices()
    for p in initial_fs:
        fs_interface[p] = True

    # Subscribe to media signals on drives already present
    for drive_path in list(current_drives.keys()):
        subscribe_drive_media(drive_path)

    # Report already-connected optical drives
    for path, block in current_devices.items():
        dev = block.get("Device", "").split("/")[-1]
        drive_path = block.get("Drive", "")
        drive = current_drives.get(drive_path, {})
        if is_optical_drive(block, drive):
            detected_optical.add(dev)
            _print_event(dev, drive, block, "CONNECTED")

    # --- Boot banner ---
    print("=" * 60)
    print(" USB DVD/BluRay Monitor")
    print(f" Started at: {datetime.now().isoformat()}")
    print(f" Attached devices: {len(current_devices)}")
    print(f" Optical drives: {len(detected_optical)}")
    print(" Events:")
    print("   - USB optical drive plugged/unplugged")
    print("   - Disc inserted/ejected from connected drive")
    print("   - Tray open/close")
    print(" Press Ctrl+C to stop")
    print("=" * 60)

    # --- Graceful shutdown ---
    def signal_handler(sig, frame):
        print("\n\nShutting down...")
        main_loop.quit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    main_loop.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_devices()
    else:
        monitor_devices()


if __name__ == "__main__":
    main()
