"""Tests for the tkinter layer: in-place card updates and canvas resizing.

Skipped without a display, since tkinter needs a real X connection.
"""

import os
import unittest
from unittest import mock

from media_backup.drives import DriveState

if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
    import tkinter as tk
    from media_backup.gui_app import DriveFrame, MainWindow
else:  # pragma: no cover - depends on the environment
    tk = None


def state(device="/dev/sr0", **kw):
    base = dict(device=device, model="BD-RE  WH16NS40", vendor="HL-DT-ST",
                media_compatibility=["optical_bd", "optical_dvd", "optical_cd"])
    base.update(kw)
    return DriveState(**base)


EMPTY = state()
DVD = state(has_media=True, label="DVD_VIDEO", fs_type="udf", media="optical_dvd")
AUDIO = state(has_media=True, media="optical_cd", audio_tracks=12)
MOUNTED = state(has_media=True, label="DVD_VIDEO", fs_type="udf", media="optical_dvd",
                mount_points=["/run/media/user/DVD_VIDEO"])


@unittest.skipIf(tk is None, "no display available")
class TkTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)


class TestDriveFrame(TkTestCase):
    def visible_text(self, frame):
        return [w.cget("text") for w in frame.winfo_children() if w.winfo_manager()]

    def test_renders_empty_drive(self):
        frame = DriveFrame(self.root, EMPTY)
        self.assertIn("No disc", self.visible_text(frame))
        self.assertIn("HL-DT-ST BD-RE  WH16NS40 (BD)", self.visible_text(frame))

    def test_update_reflects_an_inserted_disc(self):
        """Regression: the card kept showing 'No disc' after an insert."""
        frame = DriveFrame(self.root, EMPTY)
        frame.update_drive(DVD)
        text = self.visible_text(frame)
        self.assertIn("Disc present", text)
        self.assertIn("DVD, DVD_VIDEO, udf", text)
        self.assertNotIn("No disc", text)

    def test_update_reflects_an_eject(self):
        frame = DriveFrame(self.root, DVD)
        frame.update_drive(EMPTY)
        text = self.visible_text(frame)
        self.assertIn("No disc", text)
        self.assertNotIn("DVD, DVD_VIDEO, udf", text)

    def test_empty_detail_rows_are_hidden(self):
        frame = DriveFrame(self.root, EMPTY)
        self.assertFalse(frame._info.winfo_manager(), "detail row should be ungridded")
        frame.update_drive(DVD)
        self.assertTrue(frame._info.winfo_manager())

    def test_mount_points_share_one_row(self):
        """Regression: each mount point was gridded at row 3, overwriting the last."""
        frame = DriveFrame(self.root, state(
            has_media=True, mount_points=["/run/media/user/A", "/run/media/user/B"]))
        self.assertIn("/run/media/user/A", frame._mounts.cget("text"))
        self.assertIn("/run/media/user/B", frame._mounts.cget("text"))

    def test_audio_cd_shows_as_present(self):
        frame = DriveFrame(self.root, AUDIO)
        text = self.visible_text(frame)
        self.assertIn("Disc present", text)
        self.assertIn("Audio CD, 12 tracks", text)

    def test_identical_state_is_a_no_op(self):
        frame = DriveFrame(self.root, DVD)
        with mock.patch.object(frame._title, "config") as config:
            frame.update_drive(state(has_media=True, label="DVD_VIDEO",
                                     fs_type="udf", media="optical_dvd"))
            config.assert_not_called()


@unittest.skipIf(tk is None, "no display available")
class TestMainWindow(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("media_backup.gui_app.DriveMonitor")
        self.monitor_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow()
        self.window.root.withdraw()
        self.addCleanup(self.window.root.destroy)

    def card(self, device="/dev/sr0"):
        return self.window._drive_frames[device]

    def test_initial_render(self):
        self.window._on_drives_changed([EMPTY])
        self.assertEqual(list(self.window._drive_frames), ["/dev/sr0"])
        self.assertIn("1 drive", self.window.root.title())

    def test_disc_insert_updates_the_existing_card(self):
        """Regression: only the key set was diffed, so the card never re-rendered."""
        self.window._on_drives_changed([EMPTY])
        original = self.card()
        self.window._on_drives_changed([DVD])
        self.assertIs(self.card(), original, "card must be updated, not rebuilt")
        self.assertEqual(original._status.cget("text"), "Disc present")

    def test_disc_eject_updates_rather_than_removing(self):
        self.window._on_drives_changed([DVD])
        self.window._on_drives_changed([EMPTY])
        self.assertEqual(list(self.window._drive_frames), ["/dev/sr0"])
        self.assertEqual(self.card()._status.cget("text"), "No disc")

    def test_new_drive_is_added(self):
        self.window._on_drives_changed([EMPTY])
        self.window._on_drives_changed([EMPTY, state("/dev/sr1")])
        self.assertEqual(sorted(self.window._drive_frames), ["/dev/sr0", "/dev/sr1"])
        self.assertIn("2 drives", self.window.root.title())

    def test_unplugged_drive_is_removed(self):
        self.window._on_drives_changed([EMPTY, state("/dev/sr1")])
        self.window._on_drives_changed([EMPTY])
        self.assertEqual(list(self.window._drive_frames), ["/dev/sr0"])

    def test_empty_list_clears_every_card(self):
        self.window._on_drives_changed([EMPTY])
        self.window._on_drives_changed([])
        self.assertEqual(self.window._drive_frames, {})
        self.assertIn("0 drives", self.window.root.title())

    def test_monitor_is_attached_before_connecting(self):
        monitor = self.monitor_cls.return_value
        monitor.attach_to_tkinter.assert_called_once_with(self.window.root)
        monitor.connect.assert_called_once()

    def test_closing_the_window_disconnects(self):
        self.window._monitor.disconnect.assert_not_called()
        with mock.patch.object(self.window.root, "destroy"):
            self.window._on_close()
        self.window._monitor.disconnect.assert_called_once()


@unittest.skipIf(tk is None, "no display available")
class TestCanvasResize(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("media_backup.gui_app.DriveMonitor")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow()
        self.addCleanup(self.window.root.destroy)

    def test_container_tracks_the_canvas_width(self):
        self.window._on_canvas_resize(mock.Mock(width=640))
        item = self.window._canvas.itemcget(self.window._container_id, "width")
        self.assertEqual(int(float(item)), 640)

    def test_configure_is_bound_to_the_canvas_not_the_toplevel(self):
        """Regression: a root binding also fires for every descendant's resize."""
        self.assertFalse(self.window.root.bind("<Configure>"))
        self.assertTrue(self.window._canvas.bind("<Configure>"))

    def test_container_matches_the_canvas_after_a_real_resize(self):
        """The width applied must be the canvas viewport, not some other widget's.

        A toplevel binding receives a Configure event per descendant, so the
        width that reached the handler used to be whichever child fired first.
        """
        self.window._on_drives_changed([EMPTY, state("/dev/sr1")])
        for geometry in ("900x600", "500x600"):
            with self.subTest(geometry=geometry):
                self.window.root.geometry(geometry)
                self.window.root.update()
                self.window.root.update_idletasks()
                applied = int(float(
                    self.window._canvas.itemcget(self.window._container_id, "width")))
                self.assertEqual(applied, self.window._canvas.winfo_width())
                self.assertGreater(applied, 0)


if __name__ == "__main__":
    unittest.main()
