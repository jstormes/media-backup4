"""Media Backup — State-driven GUI application.

The UI is driven entirely by a :class:`DriveMonitor` that listens to
udisks2 D-Bus signals.  No polling ever — the GUI only updates when
the monitor fires a callback on insert or eject.
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import ttk

from media_backup.drive_monitor import DriveMonitor
from media_backup.drives import DriveState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DriveFrame — single drive card
# ---------------------------------------------------------------------------


class DriveFrame(ttk.Frame):
    """Display the state of a single drive.

    The widgets are built once and re-used; :meth:`update_drive` re-renders
    the card in place so that a disc appearing in an already-listed drive
    is reflected without rebuilding (or reordering) the list.
    """

    STATUS_PRESENT = "#2d932d"
    STATUS_EMPTY = "#999"
    DETAIL = "#555"

    def __init__(self, parent: tk.Widget, drive: DriveState) -> None:
        super().__init__(parent, padding=6)
        self.columnconfigure(0, weight=1)

        self._drive: DriveState | None = None

        self._title = ttk.Label(self, font=("Helvetica", 10, "bold"))
        self._title.grid(row=0, column=0, columnspan=2, sticky="w")

        self._device = ttk.Label(self)
        self._device.grid(row=1, column=0, sticky="w", padx=(12, 0))

        self._status = ttk.Label(self)
        self._status.grid(row=1, column=1, sticky="e")

        self._info = ttk.Label(self, foreground=self.DETAIL)
        self._info.grid(row=2, column=0, columnspan=2, sticky="w", padx=(12, 0))

        self._mounts = ttk.Label(self, foreground=self.DETAIL, justify="left")
        self._mounts.grid(row=3, column=0, columnspan=2, sticky="w", padx=(12, 0))

        self.update_drive(drive)

    def update_drive(self, drive: DriveState) -> None:
        """Re-render this card from ``drive``.  A no-op if nothing changed."""
        if drive == self._drive:
            return
        self._drive = drive

        self._title.config(text=f"{drive.display_name} ({drive.drive_type})")
        self._device.config(text=drive.device)

        if drive.has_media:
            self._status.config(text="Disc present", foreground=self.STATUS_PRESENT)
        else:
            self._status.config(text="No disc", foreground=self.STATUS_EMPTY)

        details = [part for part in (drive.disc_description, drive.label, drive.fs_type) if part]
        self._set_text(self._info, ", ".join(details))
        self._set_text(self._mounts, "\n".join(f"  📁 {mp}" for mp in drive.mount_points))

    @staticmethod
    def _set_text(label: ttk.Label, text: str) -> None:
        """Set a label's text, hiding the row entirely when it is empty."""
        label.config(text=text)
        if text:
            label.grid()
        else:
            label.grid_remove()


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow:
    """Main application window — drives list driven by DriveMonitor."""

    TITLE = "Media Backup"
    WIDTH = 700
    HEIGHT = 500

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(self.TITLE)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.resizable(True, True)
        self.root.minsize(400, 300)

        # Throttling helper for scrollregion recalculation
        self._scroll_dirty = False

        # Mapping: device path → DriveFrame widget
        self._drive_frames: dict[str, DriveFrame] = {}

        self._build_ui()

        # Connect monitor — initial scan triggers first update
        self._monitor = DriveMonitor()
        self._monitor.attach_to_tkinter(self.root)
        self._monitor.connect(on_changed=self._on_drives_changed)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI layout ----------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the main layout."""
        # Title header
        header = ttk.Frame(self.root, padding=10)
        header.pack(fill="x")

        ttk.Label(
            header, text=self.TITLE,
            font=("Helvetica", 18, "bold"),
        ).pack(side="left")

        # Drive list area
        drives_frame = ttk.LabelFrame(self.root, text="Optical Drives", padding=8)
        drives_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self._canvas = tk.Canvas(drives_frame, highlightthickness=0)
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

    def _on_close(self) -> None:
        """Stop the D-Bus thread before tearing down the window."""
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

        # Remove drives that disappeared
        for device in set(self._drive_frames) - set(drive_map):
            self._drive_frames.pop(device).destroy()

        # Add drives that appeared, refresh the ones that were already there
        for device, drive in sorted(drive_map.items()):
            logger.debug("  drive: %s media=%s", drive, drive.has_media)
            frame = self._drive_frames.get(device)
            if frame is None:
                frame = DriveFrame(self._drives_container, drive)
                frame.pack(fill="x", pady=2)
                self._drive_frames[device] = frame
            else:
                frame.update_drive(drive)

        # Update title
        count = len(self._drive_frames)
        self.root.title(
            f"{self.TITLE} — {count} drive{'s' if count != 1 else ''}"
        )

        # Refresh scroll region
        self._schedule_scroll_update()

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
    app = MainWindow()
    app.run()


if __name__ == "__main__":
    main()
