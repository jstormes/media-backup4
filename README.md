# Media Backup

A Python GUI application for managing media backups.

## Quick Start

```bash
# Run the app
python3 gui_app.py
# or
python3 -m media_backup
```

## Requirements

- **Python 3.14+**
- **tkinter** (included with Python on most systems)
- **lsblk** + **udisksctl** (for drive detection)

No external Python dependencies — pure standard library.

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
