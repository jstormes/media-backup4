"""Tests for D-Bus signal classification and monitor state transitions."""

import unittest
from unittest import mock

from gi.repository import GLib

from media_backup.drive_monitor import DriveMonitor, _DBusSignalThread
from media_backup.drives import DriveState

from . import fixtures as fx

SR1 = fx.BLOCK_PREFIX + "sr1"
IFACE_BLOCK = fx.IFACE_BLOCK
IFACE_FILESYSTEM = fx.IFACE_FILESYSTEM


def added(path, interfaces=(IFACE_FILESYSTEM,)):
    """A real InterfacesAdded payload: ``(oa{sa{sv}})``."""
    return GLib.Variant("(oa{sa{sv}})", (path, {name: {} for name in interfaces}))


def removed(path, interfaces):
    """A real InterfacesRemoved payload: ``(oas)``."""
    return GLib.Variant("(oas)", (path, list(interfaces)))


def state(device, has_media=False):
    return DriveState(device=device, model="BD-RE BU40N", vendor="HL-DT-ST",
                      has_media=has_media, media_compatibility=["optical_bd"])


class TestSignalClassification(unittest.TestCase):
    """The two meanings of InterfacesRemoved must be told apart."""

    def setUp(self):
        self.seen = []
        self.thread = _DBusSignalThread()
        self.thread._on_signal = lambda path, event: self.seen.append((path, event))

    def emit(self, signal_name, payload):
        self.thread._on_bus_signal(None, None, None, None, signal_name, payload)
        return self.seen[-1][1] if self.seen else None

    def test_interfaces_added_is_an_add(self):
        self.assertEqual(self.emit("InterfacesAdded", added(SR1)), "added")

    def test_filesystem_removed_is_a_disc_eject(self):
        """The drive stays put; only its disc went away."""
        event = self.emit("InterfacesRemoved", removed(SR1, [IFACE_FILESYSTEM]))
        self.assertEqual(event, "disc-removed")

    def test_block_removed_is_a_drive_unplug(self):
        event = self.emit("InterfacesRemoved", removed(SR1, [IFACE_BLOCK, IFACE_FILESYSTEM]))
        self.assertEqual(event, "drive-removed")

    def test_empty_removal_list_is_treated_as_a_disc_eject(self):
        """Never drop a drive on an ambiguous signal."""
        self.assertEqual(self.emit("InterfacesRemoved", removed(SR1, [])), "disc-removed")

    def test_non_block_paths_are_ignored(self):
        self.thread._on_bus_signal(
            None, None, None, None, "InterfacesRemoved",
            removed(fx.DRIVE_BU40N, [IFACE_BLOCK]),
        )
        self.assertEqual(self.seen, [])

    def test_malformed_payload_is_ignored(self):
        self.thread._on_bus_signal(
            None, None, None, None, "InterfacesAdded", GLib.Variant("(s)", ("junk",)),
        )
        self.assertEqual(self.seen, [])

    def test_no_callback_before_start_listening(self):
        _DBusSignalThread()._on_bus_signal(
            None, None, None, None, "InterfacesAdded", added(SR1),
        )  # must not raise


class TestStateTransitions(unittest.TestCase):
    def setUp(self):
        self.monitor = DriveMonitor()
        self.monitor._drives = {"/dev/sr0": state("/dev/sr0"),
                                "/dev/sr1": state("/dev/sr1", has_media=True)}
        self.rescans = 0
        self.monitor._schedule_rescan = self._count_rescan

    def _count_rescan(self):
        self.rescans += 1

    def test_disc_eject_keeps_the_drive(self):
        """Regression: an eject used to delete the whole drive from the list."""
        self.monitor._on_dbus_signal(SR1, "disc-removed")
        self.assertEqual(sorted(self.monitor.drives), ["/dev/sr0", "/dev/sr1"])
        self.assertEqual(self.rescans, 1, "eject must refresh the drive's state")

    def test_drive_unplug_drops_the_drive(self):
        self.monitor._on_dbus_signal(SR1, "drive-removed")
        self.assertEqual(sorted(self.monitor.drives), ["/dev/sr0"])

    def test_unplug_of_an_unknown_device_is_harmless(self):
        self.monitor._on_dbus_signal(fx.BLOCK_PREFIX + "sr9", "drive-removed")
        self.assertEqual(sorted(self.monitor.drives), ["/dev/sr0", "/dev/sr1"])

    def test_add_triggers_a_rescan(self):
        self.monitor._on_dbus_signal(SR1, "added")
        self.assertEqual(self.rescans, 1)


class TestDebounce(unittest.TestCase):
    """udisks2 emits several signals per physical event; they coalesce into one."""

    def setUp(self):
        self.monitor = DriveMonitor()

    def test_a_burst_schedules_one_rescan(self):
        with mock.patch("media_backup.drive_monitor.GLib.timeout_add") as timeout_add:
            for _ in range(5):
                self.monitor._schedule_rescan()
            self.assertEqual(timeout_add.call_count, 1)

    def test_rescanning_rearms_the_debounce(self):
        with mock.patch("media_backup.drive_monitor.GLib.timeout_add") as timeout_add:
            self.monitor._schedule_rescan()
            with mock.patch.object(type(self.monitor), "_notify"):
                with mock.patch("media_backup.drive_monitor.DriveScanner.scan", return_value=[]):
                    self.monitor._rescan()
            self.monitor._schedule_rescan()
            self.assertEqual(timeout_add.call_count, 2)

    def test_rescan_replaces_state_and_keys_by_device_path(self):
        found = [state("/dev/sr0"), state("/dev/sr1", has_media=True)]
        with mock.patch("media_backup.drive_monitor.DriveScanner.scan", return_value=found):
            self.monitor._rescan()
        self.assertEqual(sorted(self.monitor.drives), ["/dev/sr0", "/dev/sr1"])
        self.assertTrue(self.monitor.drives["/dev/sr1"].has_media)


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.monitor = DriveMonitor()
        self.calls = []
        self.monitor._on_changed = self.calls.append

    def test_notifies_when_state_changes(self):
        self.monitor._drives = {"/dev/sr0": state("/dev/sr0")}
        self.monitor._notify()
        self.assertEqual(len(self.calls), 1)

    def test_does_not_notify_when_nothing_changed(self):
        self.monitor._drives = {"/dev/sr0": state("/dev/sr0")}
        self.monitor._notify()
        self.monitor._notify()
        self.assertEqual(len(self.calls), 1)

    def test_notifies_when_a_disc_appears_in_a_known_drive(self):
        """The device set is unchanged here -- the state behind it is not."""
        self.monitor._drives = {"/dev/sr0": state("/dev/sr0")}
        self.monitor._notify()
        self.monitor._drives = {"/dev/sr0": state("/dev/sr0", has_media=True)}
        self.monitor._notify()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.calls[-1][0].has_media)

    def test_delivers_a_sorted_list(self):
        self.monitor._drives = {"/dev/sr1": state("/dev/sr1"), "/dev/sr0": state("/dev/sr0")}
        self.monitor._notify()
        self.assertEqual([d.device for d in self.calls[0]], ["/dev/sr0", "/dev/sr1"])

    def test_marshals_through_tkinter_when_attached(self):
        root = mock.Mock()
        self.monitor.attach_to_tkinter(root)
        self.monitor._drives = {"/dev/sr0": state("/dev/sr0")}
        self.monitor._notify()
        self.assertEqual(self.calls, [], "callback must not run on the D-Bus thread")
        root.after.assert_called_once()
        self.assertEqual(root.after.call_args[0][0], 0)

    def test_without_a_callback_it_is_a_no_op(self):
        DriveMonitor()._notify()  # must not raise


class TestConnect(unittest.TestCase):
    def test_connect_is_idempotent(self):
        monitor = DriveMonitor()
        with mock.patch("media_backup.drive_monitor._DBusSignalThread") as thread_cls:
            with mock.patch.object(DriveMonitor, "_rescan"):
                monitor.connect(on_changed=lambda drives: None)
                monitor.connect(on_changed=lambda drives: None)
        self.assertEqual(thread_cls.call_count, 1)

    def test_disconnect_stops_the_thread(self):
        monitor = DriveMonitor()
        with mock.patch("media_backup.drive_monitor._DBusSignalThread") as thread_cls:
            with mock.patch.object(DriveMonitor, "_rescan"):
                monitor.connect(on_changed=lambda drives: None)
            monitor.disconnect()
        thread_cls.return_value.stop.assert_called_once()
        monitor.disconnect()  # second call must be harmless


if __name__ == "__main__":
    unittest.main()
