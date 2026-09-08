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
├── install-requirements.sh # System setup: packages, then verification
├── install-makemkv.sh      # System setup: builds MakeMKV from source
├── setup-backup-drive.sh   # System setup: mounts media_path by UUID
├── verify-setup.sh         # Read-only check of the whole setup
├── *.rules                 # udev rules; copy to /etc/udev/rules.d/
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

## System setup

Four scripts in the repo root bring a fresh machine up; `verify-setup.sh`
checks the result and is read-only, so it is the one to run after a reboot.

| Script | Needs root | What it does |
|--------|-----------|--------------|
| `install-requirements.sh` | yes | `python3-tk`, a JRE, then verifies every documented requirement |
| `install-makemkv.sh` | yes | builds MakeMKV from both tarballs into `/usr/local`, checks the link |
| `setup-backup-drive.sh` | yes | mounts the backup partition at `media_path` by UUID, `nofail`, owned by the app user |
| `verify-setup.sh` | no | checks all of the above plus automount and drive identity |

Two udev rules go in `/etc/udev/rules.d/`:

- `99-media-backup-no-automount.rules` -- `UDISKS_AUTO=0` on `sr*`, so a disc
  is never mounted under a running `makemkvcon`. It clears `HintAuto` without
  hiding the drive; `UDISKS_IGNORE=1` would hide it from this app too. GNOME
  needs `gsettings set org.gnome.desktop.media-handling automount false` as
  well, which is per-user and not covered by the rule.
- `98-media-backup-optical-serial.rules` -- unique serials for drives that
  report none. See the drives section below for why this matters.

Both rules are read at boot, before udisks2 starts. Installing one on a running
system needs a reboot rather than `udevadm trigger`: udev persists the
properties in `/run/udev/data/`, so a guard like `ENV{ID_SERIAL_SHORT}==""`
will not fire again until `/run` is cleared.

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
- **Two identical drives can collapse into one udisks2 drive object**, and then
  `Drive.MediaAvailable` describes whichever drive was probed last -- a drive
  holding a disc reports `has_media=False`. udisks2 derives a drive object from
  udev: `ID_SERIAL` decides *which* block devices group into one object, while
  `ID_SERIAL_SHORT` only *names* it. Drives that report no serial get
  `ID_SERIAL` set to their model string, so two of the same model collide.
  `98-media-backup-optical-serial.rules` synthesises both properties from
  `ID_PATH` for serial-less optical drives. Setting only `ID_SERIAL_SHORT`
  renames the merged object without splitting it and does not fix anything.
  The block-level `Size` and `IdLabel` are never merged, which is what
  `verify-setup.sh` cross-checks `has_media` against.

### Reading a makemkvcon process (`runner.py`)

- Every read goes through `_iter_lines`, which pumps the pipe on a reader
  thread and waits on a bounded queue, so the loop wakes every second even
  when **no output arrives**. Iterating the pipe directly blocks forever, and
  a drive that hangs MakeMKV's probe emits nothing at all -- so watchdogs
  driven by arriving lines never ran in exactly the case they exist for. An
  LG GHA2N did this on 2026-09-07: 100% CPU, zero DRV rows, indefinitely.
- The queue is bounded at one item deliberately. Unbounded, the reader races
  ahead and `_last_activity_at` lags the lines actually consumed, which is
  what the watchdogs measure.
- `probe_timeout_s` bounds the two short commands, enumerate and scan. They
  take seconds on healthy hardware (14s for four loaded drives), and
  `stall_timeout_s` cannot bound them because it needs output to notice.

### Drive enumeration is global (`enumeration.py`)

- `info disc:9999` lists *every* drive, and opens every drive to do it. One
  job per drive therefore means N identical probes, and a single drive that
  hangs the probe stalls **all** of them, not just its own job.
- `DriveIndex` gives one enumeration to everyone who asks at once: a lock
  serialises the probes, and a short TTL lets a burst of job starts share
  one result. `JobManager` owns one; a runner built standalone gets a
  private one and behaves as it always did.
- The TTL stays short and `gui_app` calls `JobManager.drives_changed()` on
  every insert or eject, because `disc:N` indices move when a drive is
  hotplugged, and `resolve()`'s label check cannot catch two discs sharing a
  label.

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
