"""Tests for udisks2 ejection. The bus is mocked; no disc is touched."""

import unittest
from unittest import mock

from gi.repository import GLib

from media_backup import eject as ejector
from media_backup.drives import DriveState


class FakeBus:
    """Records D-Bus calls and can be told which ones fail."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def call_sync(self, bus, path, interface, method, args, *rest):
        self.calls.append((path, interface, method, args))
        if method in self.fail_on:
            raise GLib.GError(f"simulated {method} failure")

    def methods(self):
        return [c[2] for c in self.calls]


DRIVE = "/org/freedesktop/UDisks2/drives/HL_DT_ST_BD_RE_BU40N_123"
BLOCK = "/org/freedesktop/UDisks2/block_devices/sr1"


class TestEject(unittest.TestCase):
    def test_unmounted_disc_is_ejected_directly(self):
        bus = FakeBus()
        ejector.eject(DRIVE, BLOCK, mounted=False, connection=bus)
        self.assertEqual(bus.methods(), ["Eject"])
        self.assertEqual(bus.calls[0][0], DRIVE)
        self.assertEqual(bus.calls[0][1], ejector.IFACE_DRIVE)

    def test_mounted_disc_is_unmounted_first(self):
        """The kernel will not eject a mounted device."""
        bus = FakeBus()
        ejector.eject(DRIVE, BLOCK, mounted=True, connection=bus)
        self.assertEqual(bus.methods(), ["Unmount", "Eject"])
        self.assertEqual(bus.calls[0][0], BLOCK)

    def test_failed_unmount_is_retried_forced(self):
        bus = FakeBus()
        original = bus.call_sync
        state = {"first": True}

        def once(*args, **kw):
            if args[3] == "Unmount" and state["first"]:
                state["first"] = False
                raise GLib.GError("busy")
            return original(*args, **kw)

        with mock.patch.object(bus, "call_sync", side_effect=once):
            ejector.eject(DRIVE, BLOCK, mounted=True, connection=bus)
        # The first Unmount raised before reaching the bus, so what was
        # recorded is the forced retry and then the eject.
        self.assertEqual(bus.methods(), ["Unmount", "Eject"])
        retry_options = bus.calls[0][3].unpack()[0]
        self.assertEqual(retry_options.get("force"), True,
                         "the retry must actually pass force")

    def test_unmount_failing_twice_raises(self):
        bus = FakeBus(fail_on={"Unmount"})
        with self.assertRaises(ejector.EjectError) as ctx:
            ejector.eject(DRIVE, BLOCK, mounted=True, connection=bus)
        self.assertIn("unmount", str(ctx.exception))
        self.assertNotIn("Eject", bus.methods())

    def test_eject_failure_raises_with_the_reason(self):
        bus = FakeBus(fail_on={"Eject"})
        with self.assertRaises(ejector.EjectError) as ctx:
            ejector.eject(DRIVE, BLOCK, connection=bus)
        self.assertIn("could not eject", str(ctx.exception))

    def test_missing_drive_path_is_refused_before_any_call(self):
        bus = FakeBus()
        with self.assertRaises(ejector.EjectError):
            ejector.eject("", BLOCK, connection=bus)
        self.assertEqual(bus.calls, [])


class TestEjectDriveState(unittest.TestCase):
    def test_uses_the_object_paths_carried_on_the_state(self):
        bus = FakeBus()
        drive = DriveState(device="/dev/sr1", model="BD-RE",
                           object_path=BLOCK, drive_object_path=DRIVE)
        ejector.eject_drive_state(drive, connection=bus)
        self.assertEqual(bus.methods(), ["Eject"])

    def test_a_mounted_disc_is_detected_from_its_mount_points(self):
        bus = FakeBus()
        drive = DriveState(device="/dev/sr1", model="BD-RE",
                           object_path=BLOCK, drive_object_path=DRIVE,
                           mount_points=["/run/media/user/DVD"])
        ejector.eject_drive_state(drive, connection=bus)
        self.assertEqual(bus.methods(), ["Unmount", "Eject"])


if __name__ == "__main__":
    unittest.main()
