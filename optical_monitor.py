#!/usr/bin/env python3
"""
Optical Drive Insert/Eject Monitor

Listens for D-Bus ObjectManager.InterfacesAdded / InterfacesRemoved signals
on /org/freedesktop/UDisks2 and reports optical drive devices (sr0, sr1, …).
Zero polling.

Usage:
    python3 optical_monitor.py          # Monitor (Ctrl+C to stop)
    python3 optical_monitor.py --list   # List optical drives, then exit
"""

import signal
import sys
from datetime import datetime

try:
    from gi.repository import GLib, Gio
except ImportError:
    print("ERROR: PyGObject not installed.", file=sys.stderr)
    sys.exit(1)

UDEDISK2 = "/org/freedesktop/UDisks2"


def _device_name(path):
    """Extract short device name: /org/freedesktop/UDisks2/block_devices/sr0 → sr0"""
    return path.split("/block_devices/")[-1]


# ---------------------------------------------------------------------------
# Monitor mode
# ---------------------------------------------------------------------------

def monitor():
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def on_signal(conn, sender, obj_path, interface, sig_name, params, user_data=None):
        if interface != "org.freedesktop.DBus.ObjectManager":
            return

        ts = datetime.now().strftime("%H:%M:%S")

        # Safely extract arg0 (the device object path)
        try:
            obj_path = params.get_child_value(0).get_string()
        except Exception:
            return

        # Only care about block devices
        if "/block_devices/" not in obj_path:
            return

        # All SCSI optical drives are sr0, sr1, …
        dev = _device_name(obj_path)
        if not dev.startswith("sr"):
            return

        # Mark the signal
        if sig_name == "InterfacesAdded":
            print(f"  [{ts}] ▶ INSERT   {dev}")
        elif sig_name == "InterfacesRemoved":
            print(f"  [{ts}] ✖ EJECT    {dev}")

    # Subscribe to both signals
    bus.signal_subscribe(
        None, "org.freedesktop.DBus.ObjectManager", "InterfacesAdded",
        UDEDISK2, None, Gio.DBusSignalFlags.NONE, on_signal,
    )
    bus.signal_subscribe(
        None, "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved",
        UDEDISK2, None, Gio.DBusSignalFlags.NONE, on_signal,
    )

    print("=" * 50)
    print(" Optical Drive Monitor  (D-Bus, no polling)")
    print(f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(" Press Ctrl+C to stop")
    print("=" * 50)

    loop = GLib.MainLoop()

    def shutdown(sig, frame):
        print()
        loop.quit()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    loop.run()


# ---------------------------------------------------------------------------
# --list mode: one-shot lookup
# ---------------------------------------------------------------------------

def list_drives():
    proxy = Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
        "org.freedesktop.UDisks2", UDEDISK2,
        "org.freedesktop.DBus.ObjectManager", None,
    )
    result = proxy.call_sync("GetManagedObjects", None,
                             Gio.DBusCallFlags.NONE, -1, None)
    managed = result.unpack()[0]

    # Quick extraction: path → interface props dict
    def extract(ifaces):
        d = {}
        for i in range(len(ifaces)):
            entry = ifaces[i]
            name = entry[0]
            props = {}
            for j in range(len(entry[1])):
                p = entry[1][j]
                props[p[0]] = p[1] if not isinstance(p[1], dict) else dict(p[1])
            d[name] = props
        return d

    optical = []
    for path, ifaces in managed.items():
        props = extract(ifaces) if isinstance(ifaces, GLib.Variant) else {}
        drive = props.get("org.freedesktop.UDisks2.Drive", {})
        if drive.get("Optical"):
            dev = path.split("/block_devices/")[-1] if "/block_devices/" in path else path.split("/")[-1]
            media = drive.get("Media", "no disc")
            optical.append((dev, drive.get("Model", "?"), media))

    if optical:
        print(f"\nOptical drives ({len(optical)}):")
        for dev, model, media in optical:
            print(f"  {dev}  {model}  [{media}]")
    else:
        print("\nNo optical drives found.")


# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_drives()
    else:
        monitor()


if __name__ == "__main__":
    main()
