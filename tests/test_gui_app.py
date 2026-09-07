"""Tests for the tkinter layer: card updates, canvas resizing, and wiring.

Skipped without a display, since tkinter needs a real X connection.

The window is built against a real store in a temp directory and a real
:class:`JobManager` whose runners are fakes, so what is exercised is the actual
path from a button to a queued job to a re-rendered row -- not a mock agreeing
with itself. Nothing spawns a process or touches D-Bus.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_backup import config, events, jobs, model
from media_backup.config import Config
from media_backup.drives import DriveState
from media_backup.store import CollectionStore

from .test_jobs import FakeRunner

if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
    import tkinter as tk
    from tkinter import ttk
    from media_backup.gui_app import (STATE_TEXT, DriveFrame, MainWindow,
                                      ProblemWindow, state_text)
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
class WindowTestCase(unittest.TestCase):
    """A real window over a real store in a temp directory."""

    #: Geometry tests need a mapped window -- an unmapped one is never laid
    #: out, so every widget reports a width of 1. Everything else hides it,
    #: so a test run does not throw windows at whoever is at the keyboard.
    withdraw = True

    def setUp(self):
        patcher = mock.patch("media_backup.gui_app.DriveMonitor")
        self.monitor_cls = patcher.start()
        self.addCleanup(patcher.stop)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(media_path=Path(self._tmp.name), min_free_margin_bytes=0, use_stdbuf=False)
        config.ensure_directories(self.cfg)
        self.store = CollectionStore(self.cfg)
        self.seed_store()
        self.runners = []
        self.ejects = []
        self.confirm = True

        self.window = self.make_window()
        if self.withdraw:
            self.window.root.withdraw()
        self.addCleanup(self.window.root.destroy)

    def make_window(self, **kw):
        manager = jobs.JobManager(self.cfg, self.store,
                                  runner_factory=self.runner_factory,
                                  ejector=self.ejects.append)
        window = MainWindow(self.cfg, self.store, manager=manager, **kw)
        # The two modal dialogs, replaced so a test never blocks on one.
        self.errors = []
        window._confirm = lambda title, message: self.confirm
        window._error = lambda title, message: self.errors.append((title, message))
        return window

    def seed_store(self):
        """Put collections on disk before the window loads them. Override me."""

    def runner_factory(self, request, emit, *, ejector=None):
        runner = FakeRunner(request, emit, ejector=ejector)
        self.runners.append(runner)
        return runner

    def flush(self):
        """Run the pending ``after(0)`` callbacks the manager dispatched."""
        self.window.root.update()

    # -- convenience --------------------------------------------------------

    def insert(self, *drives):
        self.window._on_drives_changed(list(drives))

    def row(self, disc, column):
        return self.window._tree.set(disc.disc_id, column)


@unittest.skipIf(tk is None, "no display available")
class TestMainWindow(WindowTestCase):
    """The drive list: cards added, updated in place and dropped."""

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
class TestHiDpiLayout(WindowTestCase):
    """Regression: on a HiDPI desktop the disc list clipped its own text.

    Measured 2026-09-07 on 8192x2880 with Tk scaling 1.33: TkDefaultFont has a
    37px linespace, ttk's Treeview keeps its ~20px default row height whatever
    the font does, and every column width in this file was a pixel constant
    written for a 96-dpi desktop. Rows clipped top and bottom and every column
    clipped its text -- "the box is too short".

    These assertions are relative to the font, so they hold on any display.
    """

    withdraw = False

    def test_rows_are_tall_enough_for_their_text(self):
        rowheight = int(ttk.Style(self.window.root).lookup("Treeview", "rowheight"))
        self.assertGreaterEqual(rowheight, self.window.line)

    def test_no_column_clips_the_widest_text_it_must_hold(self):
        font = self.window.font
        widest_state = max(list(STATE_TEXT.values())
                           + [state_text(model.COPYING, 100.0)], key=font.measure)
        for column, worst in (("ordinal", "99"),
                              ("state", widest_state),
                              ("progress", "100%")):
            with self.subTest(column=column):
                self.assertGreaterEqual(self.window._tree.column(column, "width"),
                                        font.measure(worst))

    def test_a_long_disc_name_fits(self):
        name = "Spider-Man: Across The Spider-Verse"
        self.assertGreaterEqual(self.window._tree.column("name", "width"),
                                self.window.font.measure(name))

    def test_lengths_are_scaled_from_the_font(self):
        """A pixel written for a 96-dpi desktop is not a pixel here."""
        window = self.window
        self.assertAlmostEqual(
            window.scale, max(1.0, window.line / MainWindow.NOMINAL_LINE))
        self.assertGreaterEqual(window.scaled(100), 100,
                                "never shrinks below what was written")
        if window.line > MainWindow.NOMINAL_LINE:
            self.assertGreater(window.scaled(100), 100,
                               "and grows with the interface font")

    def test_the_action_row_is_never_starved(self):
        """It is fixed chrome packed before the lists, so it always fits."""
        for geometry in ("860x680", "700x500", "560x420"):
            with self.subTest(geometry=geometry):
                self.window.root.geometry(geometry)
                self.window.root.update()
                self.window.root.update_idletasks()
                row = self.window._actions
                self.assertGreaterEqual(row.winfo_height(), row.winfo_reqheight())


@unittest.skipIf(tk is None, "no display available")
class TestScrollRegion(WindowTestCase):
    withdraw = False

    def setUp(self):
        super().setUp()
        self.window.root.geometry("700x500")

    def settle(self):
        """Let the throttled after(50) fire and geometry reflow."""
        for _ in range(6):
            self.window.root.update_idletasks()
            self.window.root.after(30, self.window.root.quit)
            self.window.root.mainloop()

    def region(self):
        return [int(float(n)) for n in self.window._canvas.cget("scrollregion").split()]

    def test_region_grows_with_the_card_list(self):
        self.window._on_drives_changed([EMPTY, state("/dev/sr1"), state("/dev/sr2")])
        self.settle()
        tall = self.region()[3]
        self.assertGreater(tall, 0)
        self.window._on_drives_changed([EMPTY])
        self.settle()
        self.assertLess(self.region()[3], tall)

    def test_region_is_empty_when_no_drives_remain(self):
        """Pack stops propagating with no slaves, so the frame keeps its height."""
        self.window._on_drives_changed([EMPTY])
        self.settle()
        self.assertGreater(self.region()[3], 0)
        self.window._on_drives_changed([])
        self.settle()
        self.assertEqual(self.region(), [0, 0, 0, 0])

    def test_scheduling_is_throttled(self):
        with mock.patch.object(self.window.root, "after") as after:
            for _ in range(5):
                self.window._schedule_scroll_update()
            self.assertEqual(after.call_count, 1)

    def test_container_configure_triggers_a_scroll_update(self):
        self.window._scroll_dirty = False
        with mock.patch.object(self.window.root, "after") as after:
            self.window._drives_container.event_generate("<Configure>")
            self.window.root.update()
        after.assert_called_once()


@unittest.skipIf(tk is None, "no display available")
class TestCanvasResize(WindowTestCase):
    withdraw = False

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



# ---------------------------------------------------------------------------
# Wiring: startup, collections, and the path from a button to a running job
# ---------------------------------------------------------------------------


@unittest.skipIf(tk is None, "no display available")
class TestStateText(unittest.TestCase):
    def test_copying_carries_its_percentage(self):
        self.assertEqual(state_text(model.COPYING, 47.4), "Copying — 47%")

    def test_other_states_do_not(self):
        """A queued job has no progress; showing 0% would suggest it started."""
        self.assertEqual(state_text(model.QUEUED, 47.0), "Waiting for the drive")

    def test_an_unknown_state_is_shown_verbatim(self):
        self.assertEqual(state_text("something new"), "something new")


@unittest.skipIf(tk is None, "no display available")
class TestStartup(WindowTestCase):
    """What the window does with what it finds on disk."""

    def seed_store(self):
        self.seeded = self.store.create(identifier="seed")
        disc = self.store.add_disc(self.seeded, DVD)
        disc.state = model.COPYING  # as if the app died mid-copy
        self.store.save(self.seeded)

    def test_open_collections_are_loaded(self):
        self.assertEqual([c.collection_id for c in self.window.collections],
                         [self.seeded.collection_id])
        self.assertEqual(self.window.active.collection_id,
                         self.seeded.collection_id)

    def test_the_discs_are_listed(self):
        self.assertEqual(len(self.window._tree.get_children()), 1)

    def test_an_interrupted_disc_is_recovered(self):
        disc = self.window.active.discs[0]
        self.assertEqual(disc.state, model.FAILED)
        self.assertEqual(disc.last_attempt.error_kind, model.ERR_INTERRUPTED)

    def test_the_operator_is_told_why(self):
        self.assertIn("cannot resume", self.window._notice.cget("text"))

    def test_a_recovered_disc_offers_a_retry(self):
        disc = self.window.active.discs[0]
        self.window._tree.selection_set(disc.disc_id)
        self.flush()  # <<TreeviewSelect>> is a queued virtual event
        self.assertTrue(self.window._disc_buttons["retry"].winfo_manager())
        self.assertFalse(self.window._disc_buttons["cancel"].winfo_manager())


@unittest.skipIf(tk is None, "no display available")
class TestCollections(WindowTestCase):
    def test_creating_one_makes_it_active_and_selectable(self):
        collection = self.window.create_collection("Alien", "794", 4)
        self.assertIs(self.window.active, collection)
        self.assertIn("Alien", self.window._collection_box.get())
        self.assertEqual(collection.expected_disc_count, 4)

    def test_it_is_on_disk_immediately(self):
        self.window.create_collection("Alien", "794", 4)
        reloaded = CollectionStore(self.cfg).load_all()
        self.assertEqual([c.title for c in reloaded], ["Alien"])
        self.assertEqual(reloaded[0].expected_disc_count, 4)

    def test_finishing_is_refused_when_the_operator_declines(self):
        collection = self.window.create_collection("Alien")
        self.confirm = False
        self.window._finish_collection()
        self.assertIn(collection, self.window.collections)

    def test_finishing_moves_it_into_finished(self):
        collection = self.window.create_collection("Alien")
        self.window._finish_collection()
        self.assertEqual(self.window.collections, [])
        self.assertTrue((self.cfg.finished_dir / collection.collection_id).is_dir())

    def test_cancelling_moves_it_aside_rather_than_deleting_it(self):
        collection = self.window.create_collection("Alien")
        self.window._cancel_collection()
        self.assertTrue((self.cfg.cancelled_dir / collection.collection_id).is_dir())

    def test_the_collection_buttons_are_dead_without_one(self):
        self.assertIn("disabled", self.window._finish_button.state())
        self.window.create_collection("Alien")
        self.assertNotIn("disabled", self.window._finish_button.state())


@unittest.skipIf(tk is None, "no display available")
class TestNoCollection(WindowTestCase):
    def test_backing_up_offers_to_create_one(self):
        self.insert(DVD)
        self.confirm = False
        self.window.backup_drive("/dev/sr0")
        self.assertEqual(self.window.collections, [])
        self.assertEqual(self.runners, [], "nothing was started")

    def test_accepting_creates_one_and_carries_on(self):
        self.insert(DVD)
        self.window._new_collection = lambda: self.window.create_collection("Ad hoc")
        self.window.backup_drive("/dev/sr0")
        self.assertEqual(len(self.window.collections), 1)
        self.assertEqual(len(self.runners), 1)


@unittest.skipIf(tk is None, "no display available")
class TestBackupFlow(WindowTestCase):
    """Button to queued job to re-rendered row, over a real JobManager."""

    def setUp(self):
        super().setUp()
        self.insert(DVD)
        self.collection = self.window.create_collection("Spider-Man", "0123", 1)
        self.window.backup_drive("/dev/sr0")
        self.disc = self.collection.discs[0]
        self.runner = self.runners[0]

    def card(self, device="/dev/sr0"):
        return self.window._drive_frames[device]

    def test_the_disc_is_added_to_the_collection_and_started(self):
        self.assertEqual(len(self.collection.discs), 1)
        self.assertEqual(self.disc.state, model.RESOLVING)
        self.assertEqual(self.runner.request.device, "/dev/sr0")
        self.assertEqual(self.runner.request.expected_label, "DVD_VIDEO")

    def test_the_row_shows_what_the_job_is_doing(self):
        self.assertEqual(self.row(self.disc, "state"), "Identifying the disc")

    def test_the_card_shows_the_job_and_withdraws_its_button(self):
        self.assertIn("DVD_VIDEO", self.card()._job.cget("text"))
        self.assertFalse(self.card()._backup.winfo_manager(),
                         "the only thing that may happen to this disc is "
                         "the backup already running")

    def test_progress_reaches_the_row(self):
        self.runner.state(model.COPYING, "copying")
        self.runner.progress(47.0)
        self.flush()
        self.assertEqual(self.row(self.disc, "state"), "Copying — 47%")
        self.assertEqual(self.row(self.disc, "progress"), "47%")

    def test_progress_reaches_the_drive_card(self):
        self.runner.state(model.COPYING, "copying")
        self.runner.progress(47.0)
        self.flush()
        self.assertIn("Copying — 47%", self.card()._job.cget("text"))

    def test_a_finished_job_marks_the_row_backed_up(self):
        self.runner.finish()
        self.flush()
        self.assertEqual(self.disc.state, model.DONE)
        self.assertEqual(self.row(self.disc, "state"), "Backed up")
        self.assertEqual(self.row(self.disc, "progress"), "100%")

    def test_the_button_comes_back_when_the_drive_is_free(self):
        self.runner.finish()
        self.flush()
        self.assertTrue(self.card()._backup.winfo_manager())
        self.assertFalse(self.card()._job.winfo_manager())

    def test_the_collection_label_counts_the_finished_disc(self):
        self.runner.finish()
        self.flush()
        self.assertIn("1/1", self.window._collection_box.get())

    def test_cancelling_asks_the_runner_to_stop(self):
        self.window._cancel_disc()
        self.assertEqual(self.runner.cancel_reason, model.ERR_CANCELLED)

    def test_the_row_takes_the_name_makemkv_gives_the_disc(self):
        """A DVD's volume label is a generic stamp; MakeMKV knows the film."""
        self.assertEqual(self.row(self.disc, "name"), "DVD_VIDEO")
        self.runner.emit(events.JobEvent(
            self.runner.request.job_id, events.IDENTIFIED,
            disc_name="Fresh Horses",
            titles=(model.Title(index=0, name="Fresh Horses",
                                duration="1:42:39", size_bytes=4_245_336_064),
                    model.Title(index=1, name="Fresh Horses",
                                duration="0:02:32", size_bytes=99_866_624))))
        self.flush()

        self.assertEqual(self.row(self.disc, "name"), "Fresh Horses")
        self.assertIn("2 titles", self.window._detail.cget("text"))
        self.assertIn("main feature 1:42:39", self.window._detail.cget("text"))

    def test_the_drive_card_uses_the_real_name_too(self):
        self.runner.emit(events.JobEvent(
            self.runner.request.job_id, events.IDENTIFIED,
            disc_name="Fresh Horses"))
        self.runner.state(model.COPYING, "copying")
        self.flush()
        self.assertIn("Fresh Horses", self.card()._job.cget("text"))

    def test_an_unscanned_disc_shows_no_summary(self):
        self.assertNotIn("title", self.window._detail.cget("text"))

    def test_progress_does_not_republish_the_buttons(self):
        """Regression: the Cancel button flashed four times a second.

        _render_disc_actions unpacked and repacked every button on every job
        event, and a running job emits progress every PROGRESS_INTERVAL_S --
        four times a second, for the whole length of a multi-hour copy.
        """
        self.runner.state(model.COPYING, "copying")
        self.flush()
        cancel = self.window._disc_buttons["cancel"]
        with mock.patch.object(cancel, "pack_forget") as forget, \
                mock.patch.object(cancel, "pack") as pack:
            for pct in (10.0, 20.0, 30.0, 40.0):
                self.runner.progress(pct)
            self.flush()
            forget.assert_not_called()
            pack.assert_not_called()
        self.assertTrue(cancel.winfo_manager(), "and it is still on screen")
        self.assertEqual(self.row(self.disc, "progress"), "40%",
                         "while the row itself still tracks progress")

    def test_the_buttons_are_republished_when_the_state_changes(self):
        """The cheap path must not become a stuck one."""
        self.runner.state(model.COPYING, "copying")
        self.flush()
        self.assertTrue(self.window._disc_buttons["cancel"].winfo_manager())

        self.runner.finish(good=False, error_kind=model.ERR_COPY)
        self.flush()
        self.assertFalse(self.window._disc_buttons["cancel"].winfo_manager())
        self.assertTrue(self.window._disc_buttons["retry"].winfo_manager())

    def test_a_disc_that_needs_a_person_says_so_rather_than_failed(self):
        """"Failed" is wrong: nothing is broken and retrying changes nothing."""
        self.runner.finish(good=False, error_kind=model.ERR_DECOY_TITLES,
                           reason="9 titles are all about the same length")
        self.flush()
        self.assertEqual(self.row(self.disc, "state"), "Needs you")
        self.assertEqual(self.window._tree.item(self.disc.disc_id, "tags"),
                         ("attention",))

    def test_an_ordinary_failure_still_reads_as_failed(self):
        self.runner.finish(good=False, error_kind=model.ERR_COPY,
                           reason="only 20% of the disc was written")
        self.flush()
        self.assertEqual(self.row(self.disc, "state"), "Failed")
        self.assertEqual(self.window._tree.item(self.disc.disc_id, "tags"),
                         ("bad",))

    def test_the_candidate_list_reaches_the_operator(self):
        self.runner.finish(
            good=False, error_kind=model.ERR_DECOY_TITLES,
            reason=("9 titles are all about the same length.\n\nBack this "
                    "disc up by hand in MakeMKV. The candidates are:\n"
                    "  00800.mpls -- 1:50:00, 12 chapters"))
        self.flush()
        detail = self.window._detail.cget("text")
        self.assertIn("00800.mpls", detail)
        self.assertIn("by hand", detail)

    def test_a_running_disc_offers_only_cancel(self):
        buttons = self.window._disc_buttons
        self.assertTrue(buttons["cancel"].winfo_manager())
        self.assertFalse(buttons["retry"].winfo_manager())
        self.assertFalse(buttons["start"].winfo_manager())

    def test_a_finished_disc_offers_nothing(self):
        self.runner.finish()
        self.flush()
        self.assertFalse(any(b.winfo_manager()
                             for b in self.window._disc_buttons.values()))

    def test_a_failed_disc_offers_retry_and_giving_up(self):
        self.runner.finish(good=False, reason="only 20% of the disc was written",
                           error_kind=model.ERR_COPY)
        self.flush()
        buttons = self.window._disc_buttons
        self.assertEqual(self.row(self.disc, "state"), "Failed")
        self.assertTrue(buttons["retry"].winfo_manager())
        self.assertTrue(buttons["abandon"].winfo_manager())
        self.assertIn("only 20%", self.window._detail.cget("text"))

    def test_retrying_starts_a_second_attempt_in_the_same_drive(self):
        self.runner.finish(good=False, error_kind=model.ERR_COPY)
        self.flush()
        self.window._retry_disc()
        self.assertEqual(len(self.runners), 2)
        self.assertEqual(self.runners[1].request.device, "/dev/sr0")
        self.assertEqual(self.runners[1].request.attempt, 2)
        self.assertEqual(self.disc.state, model.RESOLVING)

    def test_retrying_with_an_empty_drive_says_so(self):
        self.runner.finish(good=False, error_kind=model.ERR_COPY)
        self.flush()
        self.insert(EMPTY)
        self.window._retry_disc()
        self.assertEqual(len(self.runners), 1, "nothing was started")
        self.assertIn("No disc to copy", [title for title, _ in self.errors])

    def test_giving_up_is_confirmed_first(self):
        self.runner.finish(good=False, error_kind=model.ERR_COPY)
        self.flush()
        self.confirm = False
        self.window._abandon_disc()
        self.assertEqual(self.disc.state, model.FAILED)

        self.confirm = True
        self.window._abandon_disc()
        self.assertEqual(self.disc.state, model.ABANDONED)
        self.assertEqual(self.row(self.disc, "state"), "Given up")

    def test_giving_up_is_refused_while_the_job_runs(self):
        self.window._abandon_disc()
        self.assertEqual(self.disc.state, model.RESOLVING)
        self.assertIn("Cannot give up on this disc yet",
                      [title for title, _ in self.errors])

    def test_a_second_disc_for_a_busy_drive_waits(self):
        self.window.backup_drive("/dev/sr0")
        second = self.collection.discs[1]
        self.assertEqual(second.state, model.QUEUED)
        self.assertEqual(self.row(second, "state"), "Waiting for the drive")
        self.assertEqual(len(self.runners), 1)


@unittest.skipIf(tk is None, "no display available")
class TestShutdown(WindowTestCase):
    def test_jobs_are_stopped_before_the_d_bus_thread(self):
        """Both orders 'work'; only this one lets a runner report its end."""
        order = []
        self.window.manager.shutdown = lambda *a, **kw: order.append("jobs")
        self.window._monitor.disconnect.side_effect = lambda: order.append("monitor")
        with mock.patch.object(self.window.root, "destroy"):
            self.window._on_close()
        self.assertEqual(order, ["jobs", "monitor"])


@unittest.skipIf(tk is None, "no display available")
class TestProblemWindow(unittest.TestCase):
    def labels(self, widget):
        found = []
        for child in widget.winfo_children():
            try:
                found.append(child.cget("text"))
            except tk.TclError:
                pass
            found.extend(self.labels(child))
        return found

    def test_every_problem_is_on_screen(self):
        window = ProblemWindow([
            config.Problem(config.ERROR, "media_path does not exist: /srv/x"),
            config.Problem(config.WARNING, "stdbuf is not on PATH"),
        ])
        self.addCleanup(window.root.destroy)
        texts = self.labels(window.root)
        self.assertIn("media_path does not exist: /srv/x", texts)
        self.assertIn("stdbuf is not on PATH", texts)


@unittest.skipIf(tk is None, "no display available")
class TestEntryPoint(unittest.TestCase):
    """main() decides which of the two windows the operator gets."""

    def run_main(self, problems):
        from media_backup import gui_app
        with mock.patch.object(gui_app, "configure_logging"), \
                mock.patch.object(gui_app.config, "load"), \
                mock.patch.object(gui_app.config, "validate", return_value=problems), \
                mock.patch.object(gui_app.config, "ensure_directories") as ensure, \
                mock.patch.object(gui_app, "ProblemWindow") as problem, \
                mock.patch.object(gui_app, "MainWindow") as window:
            gui_app.main()
        return problem, window, ensure

    def test_a_fatal_problem_stops_the_app_before_it_touches_anything(self):
        problem, window, ensure = self.run_main(
            [config.Problem(config.ERROR, "media_path does not exist")])
        problem.assert_called_once()
        window.assert_not_called()
        ensure.assert_not_called()

    def test_a_warning_is_carried_into_the_app(self):
        warning = config.Problem(config.WARNING, "stdbuf is not on PATH")
        problem, window, ensure = self.run_main([warning])
        problem.assert_not_called()
        ensure.assert_called_once()
        self.assertEqual(window.call_args.kwargs["problems"], [warning])


if __name__ == "__main__":
    unittest.main()
