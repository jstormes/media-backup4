# Media Backup — Project Agent Notes

## Tech Stack

- **Language**: Python 3 (python3, v3.14.4)
- **GUI Framework**: tkinter (standard library)
- **Drive Detection**: udisks2 D-Bus API via PyGObject (`gi`)

## Project Structure

```
media-backup4/
├── src/
│   └── media_backup/
│       ├── __init__.py     # Package init
│       ├── __main__.py     # Run via `python -m media_backup`
│       ├── gui_app.py      # Main GUI logic
│       └── drives.py       # Drive scanning & state management
├── tests/                  # Unit tests (stdlib unittest)
├── docs/
│   └── makemkv/            # MakeMKV-related documentation
├── gui_app.py              # Entry point (also run as module)
├── README.md               # Project overview
├── AGENT.md                # Agent notes
└── pyproject.toml          # Project config (TBD)
```

## CLI Tools

| Tool     | Version / Path       |
|----------|---------------------|
| python3  | /usr/bin/python3 (3.14.4) |
| udisks2  | system service, reached over D-Bus |

`gi` (PyGObject) is only present on `/usr/bin/python3`, not in a plain
virtualenv. `udisksctl` and `lsblk` are no longer used by the app.

## Running the App

```bash
# Via entry point script
python3 gui_app.py

# Via module
python3 -m media_backup
```

## GUI Notes

- Runs on X11 (not native Wayland). Set `DISPLAY=:0` if needed.
- On Wayland sessions, XWayland handles the rendering.
- If the window doesn't appear, try: `DISPLAY=:0 python3 gui_app.py`

## Architecture

### GUI (`gui_app.py`)

- `MainWindow` — top-level window with title bar, refresh button, scrollable drive list
- `DriveFrame` — card for a single drive showing model, serial, disc status, label, filesystem
- Event throttling (`after_idle`, `after(50ms)`) prevents rapid-fire UI updates
- Change detection (`_current_drives`) skips rebuilds when nothing has changed

### Drives (`drives.py`)

- `DriveState` — dataclass holding drive info (device, model, vendor, serial, size, label, mount points, media, etc.)
- `DriveScanner` — one `ObjectManager.GetManagedObjects` D-Bus call returns
  the whole udisks2 object tree; no subprocesses.
- A drive is identified as optical by `Drive.MediaCompatibility`, which stays
  populated when the tray is empty. `Drive.Optical` is **False** on an empty
  drive and must not be used for this.
- Disc presence comes from `Drive.MediaAvailable`, not from `Block.IdType`.
  Audio CDs and blank discs have no filesystem and report an empty `IdType`.

### Drive monitoring (`drive_monitor.py`)

- Subscribes to udisks2 `InterfacesAdded` / `InterfacesRemoved`; each relevant
  signal triggers a full rescan (one D-Bus round trip), debounced by 150 ms.
- `InterfacesRemoved` fires for **both** a disc eject (the `Filesystem`
  interface goes away) and a drive unplug (the `Block` interface goes away).
  Only the latter may drop a drive from the list.

### Drive Type Detection

Drive type comes from `Drive.MediaCompatibility`, best class first:
`optical_bd*` → "BD", `optical_dvd*` → "DVD", `optical_cd*` → "CD".
The model string is only a fallback for drives that report no
compatibility list.

## Tests

```bash
/usr/bin/python3 -m unittest discover -s tests -t .
```

- Stdlib `unittest` — pytest is not installed on `/usr/bin/python3`, which is
  the only interpreter with `gi`. The tests are pytest-compatible anyway.
- `tests/fixtures.py` holds udisks2 `GetManagedObjects` payloads captured from
  a live system, with the real D-Bus types (`Device` as a NUL-terminated
  `list[int]`, `MountPoints` as a list of those). Audio-CD and blank-disc
  cases are hand-built to the same shape, since they need physical media.
- Nothing contacts D-Bus; `tests/test_gui_app.py` skips without a display.

## Logging

`MEDIA_BACKUP_LOG` sets the level (default `INFO`); `MEDIA_BACKUP_LOG_FILE`
redirects output to a file. Without `configure_logging()` in `main()`, the
root logger's WARNING default silently discards every `logger.info` call.
