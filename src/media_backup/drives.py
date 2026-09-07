"""Optical drive discovery via the udisks2 D-Bus API.

Everything the UI needs -- model, vendor, serial, disc presence, label,
filesystem and mount points -- comes from a single
``ObjectManager.GetManagedObjects`` round trip.  No subprocesses.

Two udisks2 properties matter more than they look:

* ``Drive.MediaCompatibility`` stays populated when the tray is empty,
  so it is what identifies a drive as optical.  ``Drive.Optical`` is
  ``False`` on a drive with no disc in it and cannot be used for this.
* ``Drive.MediaAvailable`` is the authoritative disc-presence flag.  It
  is true for audio CDs and blank discs, which carry no filesystem and
  so report an empty ``Block.IdType``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

try:
    from gi.repository import Gio, GLib
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    raise ImportError("PyGObject is required for drive scanning") from exc

logger = logging.getLogger(__name__)

UDISKS2_BUS = "org.freedesktop.UDisks2"
UDISKS2_PATH = "/org/freedesktop/UDisks2"
IFACE_BLOCK = "org.freedesktop.UDisks2.Block"
IFACE_DRIVE = "org.freedesktop.UDisks2.Drive"
IFACE_FILESYSTEM = "org.freedesktop.UDisks2.Filesystem"
IFACE_PARTITION = "org.freedesktop.UDisks2.Partition"
IFACE_OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

_MANAGED_OBJECTS_TYPE = "(a{oa{sa{sv}}})"

# Pretty names for udisks2 ``Drive.Media`` / ``MediaCompatibility`` values.
_MEDIA_NAMES = {
    "optical_cd": "CD",
    "optical_cd_r": "CD-R",
    "optical_cd_rw": "CD-RW",
    "optical_dvd": "DVD",
    "optical_dvd_r": "DVD-R",
    "optical_dvd_rw": "DVD-RW",
    "optical_dvd_ram": "DVD-RAM",
    "optical_dvd_plus_r": "DVD+R",
    "optical_dvd_plus_rw": "DVD+RW",
    "optical_dvd_plus_r_dl": "DVD+R DL",
    "optical_bd": "Blu-ray",
    "optical_bd_r": "BD-R",
    "optical_bd_re": "BD-RE",
    "optical_hddvd": "HD DVD",
}


def _decode_ay(value) -> str:
    """Decode a udisks2 byte array (``ay``) into a string.

    Device paths and mount points come back as NUL-terminated arrays of
    bytes, which ``GLib.Variant.unpack()`` renders as ``list[int]``.
    """
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, (list, tuple)):
        try:
            raw = bytes(value)
        except (TypeError, ValueError):
            return ""
    else:
        return str(value or "")
    return raw.decode("utf-8", "replace").rstrip("\x00")


def _media_label(media: str) -> str:
    """``optical_dvd_r`` -> ``DVD R``; falls back to the raw value."""
    if not media.startswith("optical_"):
        return media
    if media in _MEDIA_NAMES:
        return _MEDIA_NAMES[media]
    return media[len("optical_"):].replace("_", " ").upper()


@dataclass
class DriveState:
    """State for a single optical drive."""

    device: str
    model: str
    #: udisks2 object paths. Needed to call Drive.Eject, which is why they are
    #: carried on the state rather than discarded after the scan.
    object_path: str = ""
    drive_object_path: str = ""
    vendor: str = ""
    serial: str | None = None
    size: int = 0  # bytes; the size of the disc, 0 when the drive is empty
    bus: str = ""
    label: str = ""
    fs_type: str = ""
    mount_points: list[str] = field(default_factory=list)
    has_media: bool = False
    is_readonly: bool = True
    media: str = ""  # udisks2 Drive.Media, e.g. "optical_dvd"
    media_compatibility: list[str] = field(default_factory=list)
    audio_tracks: int = 0
    is_blank: bool = False

    @property
    def display_name(self) -> str:
        """Vendor and model, e.g. ``HL-DT-ST BD-RE BU40N``."""
        return f"{self.vendor} {self.model}".strip()

    @property
    def drive_type(self) -> str:
        """Best media class the *drive* supports ('BD', 'DVD', 'CD')."""
        compat = " ".join(self.media_compatibility)
        for prefix, name in (("optical_bd", "BD"), ("optical_dvd", "DVD"), ("optical_cd", "CD")):
            if prefix in compat:
                return name

        # Some drives report no compatibility list; fall back to the model.
        model = self.model.lower()
        if "bd" in model or "blu" in model:
            return "BD"
        if "dvd" in model:
            return "DVD"
        if "cd" in model:
            return "CD"
        return "Unknown"

    @property
    def disc_description(self) -> str:
        """Human summary of the disc currently loaded, or '' if empty.

        Covers the cases a filesystem check misses: audio CDs and blank
        discs are present but carry no ``IdType``.
        """
        if not self.has_media:
            return ""
        if self.is_blank:
            return f"Blank {_media_label(self.media) if self.media else self.drive_type}"
        if self.audio_tracks:
            plural = "s" if self.audio_tracks != 1 else ""
            return f"Audio CD, {self.audio_tracks} track{plural}"
        return _media_label(self.media) if self.media else ""

    def __str__(self) -> str:
        media = f" — {self.label}" if self.label else ""
        return f"{self.device} ({self.display_name}){media}"


class DriveScanner:
    """Scan the system for optical drives using the udisks2 D-Bus API."""

    _connection: Gio.DBusConnection | None = None

    @classmethod
    def _bus(cls) -> Gio.DBusConnection:
        if cls._connection is None:
            cls._connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return cls._connection

    @classmethod
    def _managed_objects(cls) -> dict:
        """Return udisks2's whole object tree: ``{path: {interface: props}}``."""
        result = cls._bus().call_sync(
            UDISKS2_BUS,
            UDISKS2_PATH,
            IFACE_OBJECT_MANAGER,
            "GetManagedObjects",
            None,
            GLib.VariantType(_MANAGED_OBJECTS_TYPE),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return result.unpack()[0]

    @staticmethod
    def _is_optical(drive_props: dict) -> bool:
        """Return True for an optical drive, disc loaded or not."""
        if drive_props.get("Optical"):
            return True
        compat = drive_props.get("MediaCompatibility") or []
        return any(str(media).startswith("optical") for media in compat)

    @staticmethod
    def _build_state(block: dict, drive: dict, filesystem: dict | None,
                     object_path: str = "") -> DriveState:
        mount_points = [_decode_ay(mp) for mp in (filesystem or {}).get("MountPoints", [])]
        mount_points = [mp for mp in mount_points if mp]

        num_tracks = int(drive.get("OpticalNumTracks") or 0)
        has_media = (
            bool(drive.get("MediaAvailable"))
            or num_tracks > 0
            or bool(mount_points)
        )

        return DriveState(
            device=_decode_ay(block.get("Device")),
            model=str(drive.get("Model") or "").strip() or "Unknown",
            object_path=object_path,
            drive_object_path=str(block.get("Drive") or ""),
            vendor=str(drive.get("Vendor") or "").strip(),
            serial=str(drive.get("Serial") or "").strip() or None,
            size=int(block.get("Size") or drive.get("Size") or 0),
            bus=str(drive.get("ConnectionBus") or ""),
            label=str(block.get("IdLabel") or ""),
            fs_type=str(block.get("IdType") or ""),
            mount_points=mount_points,
            has_media=has_media,
            is_readonly=bool(block.get("ReadOnly", True)),
            media=str(drive.get("Media") or ""),
            media_compatibility=[str(m) for m in (drive.get("MediaCompatibility") or [])],
            audio_tracks=int(drive.get("OpticalNumAudioTracks") or 0),
            is_blank=bool(drive.get("OpticalBlank")),
        )

    @classmethod
    def scan(cls) -> list[DriveState]:
        """Scan for all optical drives and return their state, sorted by device."""
        try:
            objects = cls._managed_objects()
        except GLib.GError:
            logger.exception("udisks2 GetManagedObjects failed")
            return []

        drives_by_path = {
            path: ifaces[IFACE_DRIVE]
            for path, ifaces in objects.items()
            if IFACE_DRIVE in ifaces
        }

        states: list[DriveState] = []
        for path, ifaces in objects.items():
            block = ifaces.get(IFACE_BLOCK)
            if block is None or IFACE_PARTITION in ifaces:
                continue
            drive = drives_by_path.get(block.get("Drive", ""))
            if drive is None or not cls._is_optical(drive):
                continue
            states.append(cls._build_state(
                block, drive, ifaces.get(IFACE_FILESYSTEM), path))

        return sorted(states, key=lambda d: d.device)


def scan_drives() -> list[DriveState]:
    """Convenience function to scan for all optical drives."""
    return DriveScanner.scan()
