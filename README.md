# Media Backup

A Python GUI application for managing media backups.

## Quick Start

```bash
# Run the app
/usr/bin/python3 gui_app.py
# or
/usr/bin/python3 -m media_backup
```

Logging goes to stderr at INFO. Override with `MEDIA_BACKUP_LOG=DEBUG`,
or send it to a file with `MEDIA_BACKUP_LOG_FILE=app.log`.

## Requirements

- **Python 3.14+**
- **tkinter** (included with Python on most systems)
- **PyGObject** (`gi`) — used for the udisks2 D-Bus API
- **udisks2** running on the system

PyGObject is a system package, not a pip install. On Debian/Ubuntu:
`sudo apt install python3-gi`. Note that a plain virtualenv will *not*
see it — create the venv with `--system-site-packages`, or run the app
with `/usr/bin/python3` directly.

## Features

- **Auto-discover optical drives** on startup (CD, DVD, Blu-Ray)
- **Drive cards** showing model, serial, disc label, filesystem, and mount status
- **Refresh button** to re-scan for changed drives
- **Scrollable list** for multiple drives
- **Change detection** — only rebuilds UI when drives actually change

## Structure

```
media-backup4/
├── src/
│   └── media_backup/
│       ├── __init__.py
│       ├── __main__.py
│       ├── gui_app.py      # Main GUI
│       └── drives.py       # Drive scanning & state
├── tests/
├── docs/
├── gui_app.py
├── AGENT.md
└── README.md
```

## GUI Notes

Runs on X11. On Wayland sessions, XWayland handles rendering. If the window doesn't appear:

```bash
DISPLAY=:0 python3 gui_app.py
```

## Development

See [AGENT.md](AGENT.md) for project notes, architecture details, and tooling.
