"""Media Backup -- the tkinter front end.

The window is driven by two event sources and nothing else. :class:`DriveMonitor`
says what is physically in the drives, over udisks2 D-Bus signals with no
polling; :class:`JobManager` says what the backup jobs are doing, over its own
``on_change``. Both deliver on the tkinter main thread, so every handler below
can touch widgets directly.

The split on screen matches the split in the code. A drive card says what is
in a drive and offers the one action that makes sense for it. The collection
panel owns the jobs -- their state, their progress and their retries -- because
a backup belongs to a disc in a collection, not to the tray it happens to be
sitting in while it runs.

Nothing here decides anything. Whether a disc may start, what a failure means,
whether a retry is allowed: all of that lives in :mod:`jobs`, :mod:`model` and
:mod:`makemkv.outcome`, and this file asks. A window that made its own rulings
would be a second policy nobody tests.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk

from media_backup import config, jobs, model
from media_backup.drive_monitor import DriveMonitor
from media_backup.drives import DriveState
from media_backup.store import CollectionStore, StoreError

logger = logging.getLogger(__name__)

#: What each disc state is called on screen. The model's own names are for the
#: JSON and the logs; an operator reads "Waiting for the drive".
STATE_TEXT = {
    model.PENDING: "Not started",
    model.QUEUED: "Waiting for the drive",
    model.RESOLVING: "Identifying the disc",
    model.SCANNING: "Reading the disc",
    model.COPYING: "Copying",
    model.VERIFYING: "Checking the copy",
    model.EJECTING: "Ejecting",
    model.DONE: "Backed up",
    model.FAILED: "Failed",
    model.ABANDONED: "Given up",
}

#: Tallest the drive list may ask to be, in multiples of a text line. Two
#: full cards fit -- the common case, and every card carries a button, so a
#: card half off the bottom is a button the operator cannot reach. Past this
#: it scrolls, so a machine with five drives does not push the disc list out
#: of the window. Counted in lines rather than pixels for the reason every
#: other measurement in this file now is: see :func:`ui_font`.
DRIVES_MAX_LINES = 11


def ui_font(name: str, scale: float = 1.0, bold: bool = False) -> str:
    """A named font derived from the desktop's own interface font.

    Nothing here may hardcode a point size. This machine is 8192x2880 with Tk
    scaling 1.33, where ``TkDefaultFont`` has a 37px linespace against the 20
    a normal-DPI desktop gives it -- and a hardcoded ``("Helvetica", 10,
    "bold")`` card title came out at 28px, *smaller* than the body text it was
    supposed to stand out from.

    Created once and looked up by name thereafter, since a Font object per
    widget is a Tcl object per widget.
    """
    try:
        return str(tkfont.nametofont(name))
    except tk.TclError:
        pass
    actual = tkfont.nametofont("TkDefaultFont").actual()
    size = actual["size"]
    return str(tkfont.Font(
        name=name, exists=False, family=actual["family"],
        # Sizes are points when positive and pixels when negative; scaling by
        # a ratio is right either way.
        size=int(size * scale) or size,
        weight="bold" if bold else actual["weight"]))


FONT_CARD_TITLE = "MediaBackupCardTitle"
FONT_HEADER = "MediaBackupHeader"
FONT_PROBLEM = "MediaBackupProblem"

#: Failures the operator can actually do something about, as opposed to ones
#: that just need retrying. A disc whose feature cannot be told from its
#: decoys is not broken -- it needs a person, and it should not read the same
#: as a disc that failed to copy.
NEEDS_OPERATOR = frozenset({model.ERR_DECOY_TITLES, model.ERR_AMBIGUOUS_TITLES})

COLOUR_GOOD = "#2d932d"
COLOUR_BAD = "#b3261e"
COLOUR_BUSY = "#1a6fb5"
COLOUR_ATTENTION = "#b06000"
COLOUR_IDLE = "#999"
COLOUR_DETAIL = "#555"


def grid_shown(widget: tk.Widget, shown: bool) -> None:
    """Show or hide a *gridded* widget, keeping its place for later.

    Only ever call this on a widget managed by ``grid``. Tk refuses to mix
    geometry managers inside one container, and the failure is an exception at
    the moment of hiding, not at the moment of the mistake.

    Does nothing when the widget is already in the state asked for. These
    handlers run on every job event -- four times a second, for hours -- and
    re-running the geometry manager on something that has not moved is how a
    steady window turns into a flickering one.
    """
    if bool(widget.winfo_manager()) == shown:
        return
    if shown:
        widget.grid()
    else:
        widget.grid_remove()


def grid_text(label: ttk.Label, text: str) -> None:
    """Set a gridded label's text, collapsing its row when it is empty."""
    if label.cget("text") != text:
        label.config(text=text)
    grid_shown(label, bool(text))


def wrap_to_width(holder: tk.Widget, label: ttk.Label) -> None:
    """Keep ``label`` wrapping at the width of ``holder``.

    A wraplength fixed at build time is a guess about the window, and the
    guess is wrong the moment anyone drags an edge: too small wastes the
    window, too large clips the text against the side of it.

    The width is only written when it actually changes. Setting it re-requests
    the label's height, which fires <Configure> again -- an unguarded handler
    here oscillates instead of settling.
    """

    def resize(event: tk.Event) -> None:
        want = max(200, event.width - 8)  # px; a lower bound, not a layout
        if label.cget("wraplength") != want:
            label.configure(wraplength=want)

    holder.bind("<Configure>", resize)


def state_text(state: str, total_pct: float = 0.0) -> str:
    text = STATE_TEXT.get(state, state)
    if state == model.COPYING and total_pct:
        return f"{text} — {total_pct:.0f}%"
    return text


def needs_operator(disc: model.Disc) -> bool:
    """True if this disc is waiting on a person rather than on a retry."""
    attempt = disc.last_attempt
    return bool(disc.state == model.FAILED and attempt
                and attempt.error_kind in NEEDS_OPERATOR)


def disc_state_text(disc: model.Disc, total_pct: float = 0.0) -> str:
    """What to call this disc's state on screen.

    "Failed" is wrong for a disc that copied nothing because nobody could tell
    which title to copy. Nothing is broken and retrying changes nothing; it is
    waiting on a person.
    """
    if needs_operator(disc):
        return "Needs you"
    return state_text(disc.state, total_pct)


# ---------------------------------------------------------------------------
# DriveFrame -- single drive card
# ---------------------------------------------------------------------------


class DriveFrame(ttk.Frame):
    """Display the state of a single drive.

    The widgets are built once and re-used; :meth:`update_drive` re-renders
    the card in place so that a disc appearing in an already-listed drive
    is reflected without rebuilding (or reordering) the list.

    ``job_line`` is a one-line summary of the job running in this drive, if
    any, rendered by the caller -- the card knows about drives, not jobs. A
    drive with a job running in it offers no button: the backup that is
    already going is the only thing that may happen to that disc.
    """

    STATUS_PRESENT = COLOUR_GOOD
    STATUS_EMPTY = COLOUR_IDLE
    DETAIL = COLOUR_DETAIL

    def __init__(self, parent: tk.Widget, drive: DriveState,
                 on_backup=None) -> None:
        super().__init__(parent, padding=6)
        self.columnconfigure(0, weight=1)

        self._drive: DriveState | None = None
        self._on_backup = on_backup
        #: The facts the card is currently showing. A sentinel rather than
        #: None, so the first render always happens.
        self._rendered: object = object()

        self._title = ttk.Label(self, font=ui_font(FONT_CARD_TITLE, bold=True))
        self._title.grid(row=0, column=0, columnspan=2, sticky="w")

        self._device = ttk.Label(self)
        self._device.grid(row=1, column=0, sticky="w", padx=(12, 0))

        self._status = ttk.Label(self)
        self._status.grid(row=1, column=1, sticky="e")

        self._info = ttk.Label(self, foreground=self.DETAIL)
        self._info.grid(row=2, column=0, columnspan=2, sticky="w", padx=(12, 0))

        self._mounts = ttk.Label(self, foreground=self.DETAIL, justify="left")
        self._mounts.grid(row=3, column=0, columnspan=2, sticky="w", padx=(12, 0))

        self._job = ttk.Label(self, foreground=COLOUR_BUSY)
        self._job.grid(row=4, column=0, columnspan=2, sticky="w", padx=(12, 0))

        self._backup = ttk.Button(self, text="Back up this disc",
                                  command=self._fire_backup)
        self._backup.grid(row=5, column=0, columnspan=2, sticky="w",
                          padx=(12, 0), pady=(4, 0))

        self.update_drive(drive)

    def update_drive(self, drive: DriveState, job_line: str = "") -> None:
        """Re-render this card.  A no-op if nothing on it changed."""
        if (drive, job_line) == self._rendered:
            return
        self._rendered = (drive, job_line)
        self._drive = drive

        self._title.config(text=f"{drive.display_name} ({drive.drive_type})")
        self._device.config(text=drive.device)

        if drive.has_media:
            self._status.config(text="Disc present", foreground=self.STATUS_PRESENT)
        else:
            self._status.config(text="No disc", foreground=self.STATUS_EMPTY)

        details = [part for part in (drive.disc_description, drive.label, drive.fs_type) if part]
        grid_text(self._info, ", ".join(details))
        grid_text(self._mounts, "\n".join(f"  📁 {mp}" for mp in drive.mount_points))
        grid_text(self._job, job_line)

        offer_backup = bool(drive.has_media and not job_line and self._on_backup)
        grid_shown(self._backup, offer_backup)

    def _fire_backup(self) -> None:
        if self._on_backup is not None and self._drive is not None:
            self._on_backup(self._drive.device)


# ---------------------------------------------------------------------------
# New collection dialog
# ---------------------------------------------------------------------------


class NewCollectionDialog(simpledialog.Dialog):
    """Ask for the three things a collection cannot infer for itself."""

    def __init__(self, parent) -> None:
        self.result = None
        super().__init__(parent, "New collection")

    def body(self, master):
        ttk.Label(master, text="Title").grid(row=0, column=0, sticky="w", pady=2)
        self._title = ttk.Entry(master, width=34)
        self._title.grid(row=0, column=1, pady=2)

        ttk.Label(master, text="Identifier (UPC/SKU)").grid(row=1, column=0,
                                                            sticky="w", pady=2)
        self._identifier = ttk.Entry(master, width=34)
        self._identifier.grid(row=1, column=1, pady=2)

        ttk.Label(master, text="Discs in the set").grid(row=2, column=0,
                                                        sticky="w", pady=2)
        self._count = ttk.Entry(master, width=8)
        self._count.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(master, foreground=COLOUR_DETAIL,
                  text="Optional. The only guard against filing an incomplete "
                       "box set.").grid(row=3, column=0, columnspan=2, sticky="w")
        return self._title

    def validate(self) -> bool:
        count = self._count.get().strip()
        if count and not count.isdigit():
            messagebox.showwarning("New collection",
                                   "The disc count has to be a number.",
                                   parent=self)
            return False
        return True

    def apply(self) -> None:
        count = self._count.get().strip()
        self.result = (self._title.get().strip(),
                       self._identifier.get().strip(),
                       int(count) if count else None)


# ---------------------------------------------------------------------------
# Problem window -- shown instead of the app when the config is unusable
# ---------------------------------------------------------------------------


class ProblemWindow:
    """A window that explains why the app cannot start.

    ``media_path`` pointing at a volume that is not mounted yet is the normal
    failure here, and it has to produce something the operator can act on
    rather than a traceback into a terminal they are not looking at.
    """

    def __init__(self, problems: list[config.Problem]) -> None:
        self.root = tk.Tk()
        self.root.title("Media Backup — cannot start")
        self.root.geometry("620x360")

        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Media Backup cannot start",
                  font=ui_font(FONT_PROBLEM, 1.5, bold=True)).pack(anchor="w")
        ttk.Label(frame, foreground=COLOUR_DETAIL, justify="left",
                  text="Nothing has been written and nothing will be until "
                       "this is fixed.").pack(anchor="w", pady=(2, 12))

        for problem in problems:
            colour = COLOUR_BAD if problem.is_fatal else COLOUR_DETAIL
            ttk.Label(frame, text=problem.text, foreground=colour,
                      justify="left", wraplength=560).pack(anchor="w", pady=4)

        ttk.Button(frame, text="Quit", command=self.root.destroy).pack(
            anchor="e", pady=(16, 0))

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow:
    """Main application window: drives, collections and their jobs."""

    TITLE = "Media Backup"
    WIDTH = 860
    HEIGHT = 680

    def __init__(self, cfg: config.Config | None = None,
                 store: CollectionStore | None = None,
                 manager: jobs.JobManager | None = None,
                 problems: list[config.Problem] | None = None) -> None:
        self.cfg = cfg or config.load()
        self.store = store or CollectionStore(self.cfg)
        self.notices = [p.text for p in (problems or []) if not p.is_fatal]

        self.root = tk.Tk()
        self.root.title(self.TITLE)
        self._configure_metrics()
        self.root.geometry(f"{self.scaled(self.WIDTH)}x{self.scaled(self.HEIGHT)}")
        self.root.resizable(True, True)
        self.root.minsize(self.scaled(560), self.scaled(420))

        # Throttling helper for scrollregion recalculation
        self._scroll_dirty = False

        # Mapping: device path → DriveFrame widget
        self._drive_frames: dict[str, DriveFrame] = {}
        #: The last drive state the monitor reported, keyed by device. Needed
        #: to re-render one card without waiting for the next rescan, and to
        #: find a drive to retry a disc in.
        self._drives: dict[str, DriveState] = {}

        self.collections: list[model.Collection] = []
        self.active: model.Collection | None = None

        self._build_ui()
        self._load_collections()

        self.manager = manager or jobs.JobManager(self.cfg, self.store)
        self.manager.on_change = self._on_job_change
        # Before any enqueue: runner threads must not touch widgets.
        self.manager.attach_to_tkinter(self.root)

        self._render_notices()
        self._render_collections()

        # Connect monitor — initial scan triggers first update
        self._monitor = DriveMonitor()
        self._monitor.attach_to_tkinter(self.root)
        self._monitor.connect(on_changed=self._on_drives_changed)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- metrics ------------------------------------------------------------

    #: The linespace a 96-dpi desktop gives the interface font. Everything in
    #: this file that used to be a pixel constant was written against roughly
    #: this, so it is the yardstick the constants are rescaled by.
    NOMINAL_LINE = 20

    def _configure_metrics(self) -> None:
        """Take every measurement from the font instead of from 96 dpi.

        ttk's Treeview keeps its default row height whatever the font does, so
        on a HiDPI desktop -- 37px of text in a 20px row here -- every row of
        the disc list was clipping its own text top and bottom. That is what
        "the box is too short" looks like from the outside, and no amount of
        widening columns fixes it.
        """
        self.font = tkfont.nametofont("TkDefaultFont")
        self.line = self.font.metrics("linespace")
        self.scale = max(1.0, self.line / self.NOMINAL_LINE)

        style = ttk.Style(self.root)
        style.configure("Treeview", rowheight=self.line + self.scaled(6))
        style.configure("Treeview.Heading", font=self.font)

    def scaled(self, pixels: int) -> int:
        """A length written for a 96-dpi desktop, in this one's pixels."""
        return int(pixels * self.scale)

    def text_width(self, *samples: str) -> int:
        """Width that fits the widest of ``samples``, plus cell padding."""
        return max(self.font.measure(text) for text in samples) + self.scaled(16)

    # -- UI layout ----------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the main layout."""
        header = ttk.Frame(self.root, padding=10)
        header.pack(fill="x")
        ttk.Label(header, text=self.TITLE,
                  font=ui_font(FONT_HEADER, 1.8, bold=True)).pack(side="left")

        # Its own holder: the root is packed, and a hidden label has to be
        # ungridded from something to collapse the space it was taking.
        notice_holder = ttk.Frame(self.root)
        notice_holder.pack(fill="x", padx=10)
        self._notice = ttk.Label(notice_holder, foreground=COLOUR_BAD,
                                 justify="left", wraplength=800)
        self._notice.grid(row=0, column=0, sticky="w")
        wrap_to_width(notice_holder, self._notice)

        self._build_collection_bar()
        # The action row is fixed chrome, so it is packed -- to the bottom --
        # before the two lists. pack() hands out space in packing order and
        # only shares what is left over with the expanding widgets, so a fixed
        # row packed last is the first thing starved: this one was being given
        # a height of one pixel, which is what "the box is too short" looks
        # like from the outside.
        self._build_disc_actions()

        # Two lists that both want to grow, in one window. A paned window
        # settles it properly: the drive list keeps the height its cards ask
        # for, spare room goes to the disc list, and if the operator disagrees
        # they drag the sash instead of resizing the whole window.
        self._panes = ttk.PanedWindow(self.root, orient="vertical")
        self._panes.pack(fill="both", expand=True, padx=10, pady=5)
        self._build_drives()
        self._build_discs()

    def _build_collection_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="Collection:").pack(side="left")
        self._collection_box = ttk.Combobox(bar, state="readonly", width=42)
        self._collection_box.pack(side="left", padx=6)
        self._collection_box.bind("<<ComboboxSelected>>", self._on_collection_picked)

        ttk.Button(bar, text="New…", command=self._new_collection).pack(side="left")
        self._finish_button = ttk.Button(bar, text="Finish",
                                         command=self._finish_collection)
        self._finish_button.pack(side="left", padx=(6, 0))
        self._discard_button = ttk.Button(bar, text="Cancel collection",
                                          command=self._cancel_collection)
        self._discard_button.pack(side="left", padx=(6, 0))

    def _build_drives(self) -> None:
        drives_frame = ttk.LabelFrame(self._panes, text="Optical Drives", padding=8)
        self._drives_frame = drives_frame
        self._panes.add(drives_frame, weight=0)

        # Height 1 to start, then sized to its cards by _update_scroll. Left to
        # itself a Canvas asks for a fixed default (7cm) whatever is in it,
        # which is where the 322 pixels came from that were squeezing the disc
        # list -- a drive area that big is neither what one card needs nor
        # enough for five.
        self._canvas = tk.Canvas(drives_frame, highlightthickness=0, height=1)
        scrollbar = ttk.Scrollbar(drives_frame, orient="vertical", command=self._canvas.yview)
        self._drives_container = ttk.Frame(self._canvas)

        self._drives_container.bind("<Configure>", self._schedule_scroll_update)

        self._container_id = self._canvas.create_window(
            (0, 0), window=self._drives_container, anchor="nw",
        )
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind on the canvas, not the toplevel.  A widget's bindtags include
        # its toplevel, so a <Configure> handler on the root fires for every
        # descendant's resize as well -- with that widget's width in the
        # event.  The canvas is not a bindtag of anything, so this fires
        # only for the canvas itself.
        self._canvas.bind("<Configure>", self._on_canvas_resize)

    def _build_discs(self) -> None:
        """The disc list.

        A Treeview rather than a column of cards: it scrolls on its own, which
        keeps the one piece of hand-rolled scroll machinery in this file
        (above, for the drive cards) unduplicated.
        """
        frame = ttk.LabelFrame(self._panes, text="Discs in this collection",
                               padding=8)
        self._panes.add(frame, weight=1)

        # Widths measured from the widest text each column can be asked to
        # hold, rather than guessed in pixels. Every one of the old guesses
        # clipped on this display -- "Waiting for the drive" wants 271px and
        # had 180.
        widest_state = (tuple(STATE_TEXT.values())
                        + (state_text(model.COPYING, 100.0), "Needs you"))
        columns = (
            ("ordinal", "#", ("99",), "e", False),
            ("name", "Disc", ("Spider-Man: Across The Spider-Verse",), "w", True),
            ("state", "State", widest_state, "w", False),
            ("progress", "Progress", ("100%",), "e", False),
        )
        self._tree = ttk.Treeview(frame, columns=[c[0] for c in columns],
                                  show="headings", height=6)
        for column, heading, samples, anchor, stretch in columns:
            self._tree.heading(column, text=heading)
            self._tree.column(
                column, width=self.text_width(heading, *samples),
                # A column may be dragged narrower than its content, but never
                # so narrow that it cannot show what it is for.
                minwidth=self.text_width(heading) if stretch
                else self.text_width(heading, *samples),
                anchor=anchor, stretch=stretch)
        self._tree.tag_configure("good", foreground=COLOUR_GOOD)
        self._tree.tag_configure("bad", foreground=COLOUR_BAD)
        self._tree.tag_configure("busy", foreground=COLOUR_BUSY)
        self._tree.tag_configure("attention", foreground=COLOUR_ATTENTION)
        self._tree.tag_configure("idle", foreground=COLOUR_DETAIL)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._on_disc_selected)

    def _build_disc_actions(self) -> None:
        """The detail line and the buttons that act on the selected disc."""
        actions = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        actions.pack(side="bottom", fill="x")
        self._actions = actions
        detail_holder = ttk.Frame(actions)
        detail_holder.pack(fill="x", pady=(0, 6))
        self._detail = ttk.Label(detail_holder, foreground=COLOUR_DETAIL,
                                 justify="left", wraplength=780)
        self._detail.grid(row=0, column=0, sticky="w")
        wrap_to_width(detail_holder, self._detail)

        self._disc_buttons = {
            "start": ttk.Button(actions, text="Start", command=self._start_disc),
            "cancel": ttk.Button(actions, text="Cancel", command=self._cancel_disc),
            "retry": ttk.Button(actions, text="Retry", command=self._retry_disc),
            "abandon": ttk.Button(actions, text="Give up on this disc",
                                  command=self._abandon_disc),
        }
        #: Which buttons are packed right now. They are packed by
        #: :meth:`_show_disc_buttons` and nowhere else, so this stays true.
        self._packed_buttons: tuple[str, ...] = ()

    # -- startup ------------------------------------------------------------

    def _load_collections(self) -> None:
        """Load the open collections and settle anything the last run left mid-copy."""
        self.collections = self.store.load_all()
        for collection in self.collections:
            recovered = self.store.recover_interrupted(collection)
            for disc in recovered:
                logger.warning("recovered interrupted disc %s", disc.display_name)
                self.notices.append(
                    f"{disc.display_name} was being copied when the "
                    "application stopped. MakeMKV cannot resume, so it has to "
                    "be started again.")
        self.active = self.collections[0] if self.collections else None

    # -- rendering ----------------------------------------------------------

    def _render_notices(self) -> None:
        grid_text(self._notice, "\n".join(self.notices))

    def _collection_label(self, collection: model.Collection) -> str:
        name = (collection.title or collection.identifier
                or collection.collection_id[:8])
        total = collection.expected_disc_count or len(collection.discs)
        return f"{name} — {collection.done_count}/{total} backed up"

    def _render_collections(self) -> None:
        labels = [self._collection_label(c) for c in self.collections]
        self._collection_box.config(values=labels)
        if self.active in self.collections:
            self._collection_box.current(self.collections.index(self.active))
        else:
            self._collection_box.set("")
        has_active = self.active is not None
        for button in (self._finish_button, self._discard_button):
            button.state(["!disabled"] if has_active else ["disabled"])
        self._render_discs()

    def _render_discs(self) -> None:
        """Rebuild the disc list. Only called when the disc *set* changes."""
        selected = self.selected_disc_id
        self._tree.delete(*self._tree.get_children())
        if self.active is not None:
            for disc in self.active.discs:
                self._tree.insert("", "end", iid=disc.disc_id,
                                  values=self._disc_values(disc))
                self._tag_disc(disc)
        if selected and self._tree.exists(selected):
            self._tree.selection_set(selected)
        self._render_disc_actions()

    def _disc_values(self, disc: model.Disc) -> tuple:
        status = self.manager.status_for_disc(disc.disc_id)
        pct = status.total_pct if status else 0.0
        progress = f"{pct:.0f}%" if disc.is_active and pct else (
            "100%" if disc.is_good else "")
        return (disc.ordinal, disc.display_name,
                disc_state_text(disc, pct), progress)

    def _tag_disc(self, disc: model.Disc) -> None:
        if disc.is_good:
            tag = "good"
        elif needs_operator(disc):
            tag = "attention"
        elif disc.state == model.FAILED:
            tag = "bad"
        elif disc.is_active:
            tag = "busy"
        else:
            tag = "idle"
        self._tree.item(disc.disc_id, tags=(tag,))

    def _refresh_disc(self, disc_id: str) -> None:
        """Re-render one row in place. This runs four times a second."""
        disc = self._find_disc(disc_id)
        if disc is None or not self._tree.exists(disc_id):
            return
        self._tree.item(disc_id, values=self._disc_values(disc))
        self._tag_disc(disc)
        if self.selected_disc_id == disc_id:
            self._render_disc_actions()

    #: What each disc state lets the operator do. A state not listed here is a
    #: job that is running, and the only thing to do with one of those is stop
    #: it.
    DISC_ACTIONS = {
        model.PENDING: ("start", "abandon"),
        model.FAILED: ("retry", "abandon"),
        model.ABANDONED: ("retry",),
        model.DONE: (),
    }

    def _render_disc_actions(self) -> None:
        disc = self.selected_disc
        if disc is None:
            grid_text(self._detail, "")
            self._show_disc_buttons(())
            return

        status = self.manager.status_for_disc(disc.disc_id)
        detail = (status.step if status and status.step else disc.state_detail)
        lines = []
        if detail:
            lines.append(f"{disc.display_name}: {detail}")
        summary = self._disc_summary(disc)
        if summary:
            lines.append(summary)
        grid_text(self._detail, "\n".join(lines))
        self._show_disc_buttons(self.DISC_ACTIONS.get(disc.state, ("cancel",)))

    @staticmethod
    def _disc_summary(disc: model.Disc) -> str:
        """What is on the disc, from the scan the job already runs."""
        if not disc.titles:
            return ""
        plural = "" if len(disc.titles) == 1 else "s"
        parts = [f"{len(disc.titles)} title{plural}"]
        main = disc.main_title
        if main is not None and main.duration:
            parts.append(f"main feature {main.duration}")
        return ", ".join(parts)

    def _show_disc_buttons(self, offered: tuple[str, ...]) -> None:
        """Pack exactly ``offered`` -- and only when that set has changed.

        Repacking unconditionally made the Cancel button flash. This runs on
        every job event, and a running job emits progress every
        ``PROGRESS_INTERVAL_S``, so the button was being torn out of the
        layout and put back four times a second for the whole length of a
        multi-hour copy.

        The buttons act on whatever is selected at the moment they are
        clicked, so leaving them alone across a selection change that offers
        the same actions is correct as well as cheaper.
        """
        if offered == self._packed_buttons:
            return
        self._packed_buttons = offered
        for button in self._disc_buttons.values():
            button.pack_forget()
        for name in offered:
            self._disc_buttons[name].pack(side="left", padx=(0, 6))

    # -- selection ----------------------------------------------------------

    @property
    def selected_disc_id(self) -> str:
        selection = self._tree.selection()
        return selection[0] if selection else ""

    @property
    def selected_disc(self) -> model.Disc | None:
        return self._find_disc(self.selected_disc_id)

    def _find_disc(self, disc_id: str) -> model.Disc | None:
        for collection in self.collections:
            disc = collection.disc(disc_id)
            if disc is not None:
                return disc
        return None

    def _collection_of(self, disc_id: str) -> model.Collection | None:
        for collection in self.collections:
            if collection.disc(disc_id) is not None:
                return collection
        return None

    def _on_disc_selected(self, _event=None) -> None:
        self._render_disc_actions()

    def _on_collection_picked(self, _event=None) -> None:
        index = self._collection_box.current()
        if 0 <= index < len(self.collections):
            self.active = self.collections[index]
            self._render_discs()

    # -- collection actions -------------------------------------------------

    def _new_collection(self) -> None:
        answer = NewCollectionDialog(self.root).result
        if answer is not None:
            self.create_collection(*answer)

    def create_collection(self, title: str = "", identifier: str = "",
                          expected: int | None = None) -> model.Collection | None:
        try:
            collection = self.store.create(identifier=identifier)
            collection.title = title
            collection.expected_disc_count = expected
            self.store.save(collection)
        except StoreError as exc:
            self._error("Could not create the collection", str(exc))
            return None
        self.collections.append(collection)
        self.active = collection
        self._render_collections()
        return collection

    def _finish_collection(self) -> None:
        collection = self.active
        if collection is None:
            return
        warnings = collection.finish_warnings()
        if warnings and not self._confirm(
                "Finish this collection?",
                "\n".join(warnings) + "\n\nFinish it anyway?"):
            return
        try:
            target = self.store.finish(collection)
        except StoreError as exc:
            self._error("Could not finish the collection", str(exc))
            return
        logger.info("finished collection into %s", target)
        self._drop_collection(collection)

    def _cancel_collection(self) -> None:
        collection = self.active
        if collection is None:
            return
        if not self._confirm(
                "Cancel this collection?",
                "It is moved aside, not deleted — nothing that has already "
                "been copied is destroyed.\n\nCancel it?"):
            return
        try:
            self.store.cancel(collection)
        except StoreError as exc:
            self._error("Could not cancel the collection", str(exc))
            return
        self._drop_collection(collection)

    def _drop_collection(self, collection: model.Collection) -> None:
        self.collections.remove(collection)
        self.active = self.collections[0] if self.collections else None
        self._render_collections()

    # -- disc actions -------------------------------------------------------

    def backup_drive(self, device: str) -> None:
        """Add the disc in ``device`` to the open collection and start it."""
        drive = self._drives.get(device)
        if drive is None or not drive.has_media:
            return
        if self.active is None:
            if not self._confirm("No collection is open",
                                 "Create one now?"):
                return
            self._new_collection()
            if self.active is None:
                return

        try:
            disc = self.store.add_disc(self.active, drive)
        except StoreError as exc:
            self._error("Could not add the disc", str(exc))
            return
        self._render_discs()
        self._tree.selection_set(disc.disc_id)
        self._enqueue(self.active, disc, drive)

    def _start_disc(self) -> None:
        disc = self.selected_disc
        if disc is None:
            return
        drive = self._drive_for(disc)
        if drive is None:
            self._error("No disc to copy",
                        f"Put {disc.display_name} back in a drive first.")
            return
        self._enqueue(self._collection_of(disc.disc_id), disc, drive)

    _retry_disc = _start_disc

    def _cancel_disc(self) -> None:
        disc = self.selected_disc
        if disc is not None:
            self.manager.cancel_disc(disc.disc_id)

    def _abandon_disc(self) -> None:
        disc = self.selected_disc
        collection = self._collection_of(disc.disc_id) if disc else None
        if disc is None or collection is None:
            return
        if not self._confirm(
                f"Give up on {disc.display_name}?",
                "The collection can still be finished without it, and what "
                "has already been copied is kept."):
            return
        try:
            self.manager.abandon(collection, disc)
        except jobs.JobError as exc:
            self._error("Cannot give up on this disc yet", str(exc))
            return
        self._refresh_disc(disc.disc_id)
        self._render_collections()

    def _enqueue(self, collection, disc, drive) -> None:
        if collection is None:
            return
        try:
            self.manager.enqueue(collection, disc, drive)
        except jobs.JobError as exc:
            self._error("Cannot start this disc", str(exc))
            return
        self._refresh_disc(disc.disc_id)
        self._refresh_drive_card(drive.device)

    def _drive_for(self, disc: model.Disc) -> DriveState | None:
        """Which drive to (re)start this disc in.

        The drive it was last in, if there is a disc in it; otherwise the only
        loaded free drive, if there is exactly one. A guess is safe here:
        the runner resolves the device to a ``disc:N`` and refuses outright if
        the label does not match the one that was chosen.
        """
        last = disc.last_attempt
        if last and last.device:
            drive = self._drives.get(last.device)
            if drive is not None and drive.has_media:
                return drive
        loaded = [d for d in self._drives.values()
                  if d.has_media and not self.manager.is_busy(d.device)]
        return loaded[0] if len(loaded) == 1 else None

    # -- job events — run on the tkinter main thread ------------------------

    def _on_job_change(self, status: jobs.JobStatus) -> None:
        """One job moved. Re-render only what it touched."""
        self._refresh_disc(status.disc_id)
        self._refresh_drive_card(status.device)
        if status.state in model.TERMINAL_STATES:
            # done_count changed, so the collection's own label has to move.
            self._render_collections()

    def _job_line(self, device: str) -> str:
        """The one-line summary of the job in this drive, for its card."""
        for status in self.manager.statuses():
            if status.device != device:
                continue
            disc = self._find_disc(status.disc_id)
            name = disc.display_name if disc else "This disc"
            return f"{name} — {state_text(status.state, status.total_pct)}"
        return ""

    def _refresh_drive_card(self, device: str) -> None:
        frame = self._drive_frames.get(device)
        drive = self._drives.get(device)
        if frame is not None and drive is not None:
            frame.update_drive(drive, self._job_line(device))

    # -- lifecycle helpers --------------------------------------------------

    def _on_canvas_resize(self, event: tk.Event) -> None:
        """Keep the scrolled container as wide as the canvas viewport.

        The container has to be resized through the canvas *window item*.
        Setting ``width`` on the frame itself does nothing: geometry
        propagation from its packed children immediately overrides it.
        """
        self._canvas.itemconfigure(self._container_id, width=event.width)

    def _schedule_scroll_update(self, event: tk.Event | None = None) -> None:
        """Coalesce scroll-region recalculation; cards resize in bursts."""
        if self._scroll_dirty:
            return
        self._scroll_dirty = True
        self.root.after(50, self._update_scroll)

    def _update_scroll(self) -> None:
        """Fit the scroll region to the cards currently packed.

        With no cards the container keeps the height it last requested --
        pack stops propagating once its final slave is gone -- so the
        region has to be zeroed explicitly, or the canvas goes on scrolling
        over the space where a card used to be.
        """
        self._scroll_dirty = False
        region = self._canvas.bbox("all") if self._drive_frames else (0, 0, 0, 0)
        self._canvas.configure(scrollregion=region)

        # Ask for exactly the height the cards need, up to a cap. Every pixel
        # the drive list does not need belongs to the disc list below it, and
        # past the cap the scrollbar takes over. Written only when it changes:
        # setting the height fires <Configure> on the container, which comes
        # back here.
        cap = DRIVES_MAX_LINES * self.line
        wanted = min(region[3], cap) if self._drive_frames else 0
        if self._canvas.cget("height") != wanted:
            self._canvas.configure(height=wanted)
            self._fit_sash()

    def _fit_sash(self) -> None:
        """Put the sash where the drive cards now need it.

        A paned window sizes its panes once, from what they asked for at the
        time, so the drive pane would otherwise keep whatever height it had
        before there were any cards in it. Only called when the card list
        itself changes -- a drive appearing, a disc going in -- so between
        those the operator's own dragging of the sash stands.
        """
        # At idle, not now: the canvas height was set a moment ago and Tk has
        # not yet propagated it up to what the frame asks for, so reading that
        # here places the sash one card behind.
        self.root.after_idle(self._apply_sash)

    def _apply_sash(self) -> None:
        try:
            self._panes.sashpos(0, self._drives_frame.winfo_reqheight())
        except tk.TclError:
            pass  # window going away, or not laid out yet

    def _on_close(self) -> None:
        """Stop the workers, then the D-Bus thread, then tear the window down.

        Jobs first: they are what is holding a drive and writing to disk, and
        their FINISHED events are dispatched into a loop that is about to
        stop. Anything they do not manage to record is settled by
        ``recover_interrupted`` at the next startup.
        """
        self.manager.shutdown()
        self._monitor.disconnect()
        self.root.destroy()

    # -- DriveMonitor callback — runs on main thread ------------------------

    def _on_drives_changed(self, drives: list[DriveState]) -> None:
        """React to a state change from the monitor.

        Called on the tkinter main thread (via ``root.after``).  Adds new
        cards, drops stale ones, and re-renders the cards that are already
        on screen — a disc appearing in a known drive changes no keys, only
        the state behind one of them.
        """
        logger.info("GUI update: %d drive(s) in state", len(drives))
        drive_map = {d.device: d for d in drives}
        self._drives = drive_map

        # Remove drives that disappeared
        for device in set(self._drive_frames) - set(drive_map):
            self._drive_frames.pop(device).destroy()

        # Add drives that appeared, refresh the ones that were already there
        for device, drive in sorted(drive_map.items()):
            logger.debug("  drive: %s media=%s", drive, drive.has_media)
            frame = self._drive_frames.get(device)
            if frame is None:
                frame = DriveFrame(self._drives_container, drive,
                                   on_backup=self.backup_drive)
                frame.pack(fill="x", pady=2)
                self._drive_frames[device] = frame
            frame.update_drive(drive, self._job_line(device))

        # Update title
        count = len(self._drive_frames)
        self.root.title(
            f"{self.TITLE} — {count} drive{'s' if count != 1 else ''}"
        )

        # Refresh scroll region
        self._schedule_scroll_update()

    # -- dialogs, replaceable in tests --------------------------------------

    def _confirm(self, title: str, message: str) -> bool:
        return bool(messagebox.askyesno(title, message, parent=self.root))

    def _error(self, title: str, message: str) -> None:
        logger.warning("%s: %s", title, message)
        messagebox.showerror(title, message, parent=self.root)

    # -- entry point --------------------------------------------------------

    def run(self) -> None:
        """Start the main event loop."""
        self.root.mainloop()


def create_main_window(title: str = "Media Backup") -> tk.Tk:
    """Backwards-compatible function to create the root window."""
    return tk.Tk()


def configure_logging() -> None:
    """Send log output to stderr, or to ``$MEDIA_BACKUP_LOG_FILE`` if set.

    Level comes from ``$MEDIA_BACKUP_LOG`` (default ``INFO``).  Without
    this, every ``logger.info`` in the package is dropped by the root
    logger's default WARNING threshold.
    """
    level_name = os.environ.get("MEDIA_BACKUP_LOG", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        filename=os.environ.get("MEDIA_BACKUP_LOG_FILE") or None,
    )


def main() -> None:
    """Entry point for the application."""
    configure_logging()
    cfg = config.load()
    problems = config.validate(cfg)
    if any(problem.is_fatal for problem in problems):
        for problem in problems:
            logger.error("%s", problem.text)
        ProblemWindow(problems).run()
        return

    config.ensure_directories(cfg)
    MainWindow(cfg, problems=problems).run()


if __name__ == "__main__":
    main()
