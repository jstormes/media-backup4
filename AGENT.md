# Media Backup — Project Agent Notes

## Tech Stack

- **Language**: Python 3 (python3, v3.14.4)
- **GUI Framework**: tkinter (standard library)
- **Drive Detection**: `lsblk` + `udisksctl`

## Project Structure

```
media-backup4/
├── src/
│   └── media_backup/
│       ├── __init__.py     # Package init
│       ├── __main__.py     # Run via `python -m media_backup`
│       ├── gui_app.py      # Main GUI logic
│       └── drives.py       # Drive scanning & state management
├── tests/                  # Unit & integration tests
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
| lsblk    | /usr/bin/lsblk      |
| udisksctl| /usr/bin/udisksctl  |

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

- `DriveState` — dataclass holding drive info (device, model, serial, size, label, mount points, etc.)
- `DriveScanner` — two-tool approach:
  - `lsblk` (propertified output) for model, serial, size, bus type
  - `udisksctl info` for filesystem type, disc label, mount points
- Handles drives even if `udisksctl` fails (shows what we have from `lsblk`)

### Drive Type Detection

Drive type is inferred from the model string:
- `BD-RE`, `BD-ROM` → "BD"
- `DVD-R`, `DVD-RAM` → "DVD-RAM"
- `DVD` → "DVD"
- `CD-RW`, `CD-ROM` → "CD"
