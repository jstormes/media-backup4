"""Media Backup — Main GUI application."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from media_backup.drives import DriveState, scan_drives


class DriveFrame(ttk.Frame):
    """Display the state of a single drive."""

    def __init__(self, parent: tk.Widget, drive: DriveState) -> None:
        super().__init__(parent, padding=6)

        # Model + type
        type_label = drive.drive_type
        model = f"{drive.model} ({type_label})"
        ttk.Label(self, text=model, font=("Helvetica", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w",
        )

        # Device path
        ttk.Label(self, text=drive.device).grid(
            row=1, column=0, sticky="w", padx=(12, 0),
        )

        # Status
        status = "Disc present" if drive.has_media else "No disc"
        status_color = "#2d932d" if drive.has_media else "#999"
        ttk.Label(self, text=status, foreground=status_color).grid(
            row=1, column=1, sticky="e",
        )

        # Label + filesystem
        info_parts = []
        if drive.label:
            info_parts.append(drive.label)
        if drive.fs_type:
            info_parts.append(drive.fs_type)
        if info_parts:
            ttk.Label(self, text=", ".join(info_parts), foreground="#555").grid(
                row=2, column=0, columnspan=2, sticky="w", padx=(12, 0),
            )

        # Mount points
        for mp in drive.mount_points:
            ttk.Label(self, text=f"  📁 {mp}", foreground="#555").grid(
                row=3, column=0, columnspan=2, sticky="w", padx=(12, 0),
            )


class MainWindow:
    """Main application window."""

    TITLE = "Media Backup"
    WIDTH = 700
    HEIGHT = 500

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(self.TITLE)
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.resizable(True, True)
        self.root.minsize(400, 300)

        # State for throttling and change detection
        self._resize_pending = False
        self._scroll_dirty = False
        self._first_map_done = False
        self._current_drives: list[str] = []

        self._build_ui()
        # Defer scan until window is fully mapped
        self.root.after_idle(self._scan_and_show_drives)

    def _build_ui(self) -> None:
        """Build the main layout."""
        # Title header
        header = ttk.Frame(self.root, padding=10)
        header.pack(fill="x")

        ttk.Label(
            header, text=self.TITLE,
            font=("Helvetica", 18, "bold"),
        ).pack(side="left")

        ttk.Button(header, text="⟳ Refresh", command=self._scan_and_show_drives).pack(
            side="right",
        )

        # Drive list frame
        drives_frame = ttk.LabelFrame(self.root, text="Optical Drives", padding=8)
        drives_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Canvas + scrollbar for scrollable drive list
        canvas = tk.Canvas(drives_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(drives_frame, orient="vertical", command=canvas.yview)
        self._drives_container = ttk.Frame(canvas)

        self._drives_container.bind(
            "<Configure>",
            lambda e: self._schedule_scroll_update(canvas),
        )

        canvas.create_window((0, 0), window=self._drives_container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Event bindings
        self.root.bind("<Configure>", self._on_resize)
        self.root.bind("<Map>", self._on_map)

    def _on_map(self, event: tk.Event) -> None:
        """Window has been mapped — layout is now stable."""
        self._first_map_done = True

    def _on_resize(self, event: tk.Event) -> None:
        """Adjust canvas width on resize (throttled, only after first map)."""
        if not self._first_map_done or self._resize_pending:
            return
        self._resize_pending = True
        self.root.after(50, self._apply_resize, event.width)

    def _apply_resize(self, width: int) -> None:
        """Apply the resized width (only if changed)."""
        self._resize_pending = False
        if width > 0:
            new_width = width - 50
            current = self._drives_container.cget('width')
            if current != new_width:
                self._drives_container.config(width=new_width)

    def _schedule_scroll_update(self, canvas: tk.Canvas) -> None:
        """Throttle canvas scrollregion updates."""
        if self._scroll_dirty:
            return
        self._scroll_dirty = True
        self.root.after(50, lambda: self._update_scroll(canvas))

    def _update_scroll(self, canvas: tk.Canvas) -> None:
        """Update canvas scroll region."""
        self._scroll_dirty = False
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _scan_and_show_drives(self) -> None:
        """Scan drives and update the UI."""
        drives = scan_drives()
        drive_ids = [d.device for d in drives]
        self._current_drives = drive_ids

        # Clear existing drive frames
        for widget in self._drives_container.winfo_children():
            widget.destroy()

        if not drives:
            ttk.Label(
                self._drives_container,
                text="No optical drives found.",
                foreground="#999",
            ).pack(pady=20)
            return

        for drive in drives:
            DriveFrame(self._drives_container, drive).pack(fill="x", pady=2)

        # Update window title with drive count
        self.root.title(f"{self.TITLE} — {len(drives)} drive{'s' if len(drives) != 1 else ''}")

    def run(self) -> None:
        """Start the main event loop."""
        self.root.mainloop()


def create_main_window(title: str = "Media Backup") -> tk.Tk:
    """Backwards-compatible function to create the root window."""
    return tk.Tk()


def main() -> None:
    """Entry point for the application."""
    app = MainWindow()
    app.run()


if __name__ == "__main__":
    main()
