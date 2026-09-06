#!/usr/bin/env python3
"""
Raw D-Bus signal dumper for udisks2.

Useful for debugging which signals fire when you plug/unplug drives
or insert/eject discs.

Usage:
    python3 test_dbus_signals.py
"""

import signal
import sys
from datetime import datetime

try:
    from gi.repository import GLib, Gio
except ImportError:
    print("ERROR: PyGObject not installed.")
    sys.exit(1)


def _variant_to_str(v):
    """Convert a GLib.Variant to a readable string."""
    t = v.get_type_string()
    if t in ("s", "o"):
        return v.get_string().rstrip("\x00")
    elif t == "b":
        return str(v.get_boolean())
    elif t in ("i", "x", "d", "t", "u"):
        return str(v.unpack())
    elif t == "as":
        return [v.get_child_value(i).get_string().rstrip("\x00") for i in range(v.n_children())]
    elif t == "a{sv}":
        d = {}
        for i in range(v.n_children()):
            e = v.get_child_value(i)
            d[e.get_child_value(0).get_string()] = _variant_to_str(e.get_child_value(1))
        return d
    elif t == "ay":
        return bytes(v.get_data()).decode("utf-8", errors="replace").rstrip("\x00")
    else:
        return str(v.unpack())


def _params_to_str(params):
    """Convert signal params to a dict of arg names."""
    if not params:
        return {}
    result = {}
    for i in range(params.n_children()):
        child = params.get_child_value(i)
        result[f"arg{i} ({child.get_type_string()})"] = _variant_to_str(child)
    return result


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def on_signal(connection, sender, object_path, interface, signal_name, params, user_data=None):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\n[{ts}] {sender} -> {object_path}")
        print(f"       Interface: {interface}")
        print(f"       Signal:    {signal_name}")
        for k, v in _params_to_str(params).items():
            print(f"       {k}: {v}")
        print("-" * 60)

    # Subscribe to ALL udisks2 signals
    for interface, member, path in [
        ("org.freedesktop.UDisks2.Drive", None, "/org/freedesktop/UDisks2/drives"),
        ("org.freedesktop.UDisks2.Block", None, "/org/freedesktop/UDisks2/block_devices"),
        ("org.freedesktop.DBus.ObjectManager", None, "/org/freedesktop/UDisks2"),
    ]:
        bus.signal_subscribe(
            "org.freedesktop.UDisks2", interface, member, path, None,
            Gio.DBusSignalFlags.NONE, on_signal,
        )

    print("Listening for ALL udisks2 D-Bus signals...")
    print("Plug/unplug drives, insert/eject discs, open/close trays.\n")
    print("Press Ctrl+C to stop\n")

    loop = GLib.MainLoop()

    def shutdown(sig, frame):
        print("\nDone.")
        loop.quit()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    loop.run()


if __name__ == "__main__":
    main()
