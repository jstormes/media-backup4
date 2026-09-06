"""Drive scanning and state management for optical drives."""

from __future__ import annotations

import dataclasses
import os
import subprocess
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class DriveState:
    """State for a single optical drive."""

    device: str
    model: str
    serial: str | None = None
    size: int = 0  # bytes
    bus: str = ""
    label: str = ""
    fs_type: str = ""
    mount_points: list[str] = field(default_factory=list)
    has_media: bool = False
    is_readonly: bool = True

    @property
    def drive_type(self) -> str:
        """Return the drive type (e.g. 'CD-RW', 'DVD-ROM', 'BD-RE')."""
        model_lower = self.model.lower()
        if "bd-re" in model_lower or "bd-rom" in model_lower:
            return "BD"
        if "dvdr" in model_lower or "dvd-ram" in model_lower:
            return "DVD-RAM"
        if "dvd" in model_lower:
            return "DVD"
        if "cd-rw" in model_lower or "cd-rom" in model_lower:
            return "CD"
        return "Unknown"

    @property
    def has_disc(self) -> bool:
        """Return True if a disc is present."""
        return self.has_media

    def __str__(self) -> str:
        media = f" — {self.label}" if self.label else ""
        return f"{self.device} ({self.model}){media}"


class DriveScanner:
    """Scan the system for optical drives using udisks2 and lsblk."""

    @staticmethod
    def _lsblk_optical_drives() -> list[str]:
        """Return list of /dev/srX devices."""
        try:
            result = subprocess.run(
                ["lsblk", "-d", "-n", "-o", "NAME,TYPE"],
                capture_output=True, text=True, check=True,
            )
            devices = []
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "rom":
                    devices.append(f"/dev/{parts[0]}")
            return devices
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

    @staticmethod
    def _lsblk_drive_info(device: str) -> dict[str, str]:
        """Get model, serial, size from lsblk for a single device (propertified output)."""
        try:
            result = subprocess.run(
                ["lsblk", "-d", "-n", "-o", "NAME,MODEL,SERIAL,SIZE,TRAN", "-P", device],
                capture_output=True, text=True, check=True,
            )
            info: dict[str, str] = {}
            for field_str in result.stdout.strip().split():
                if "=" in field_str:
                    key, _, value = field_str.partition("=")
                    info[key] = value.strip('"')
            return info
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {}

    @staticmethod
    def _udisksctl_info(device: str) -> str | None:
        """Get udisks2 info for a device."""
        if not os.path.exists(device):
            return None
        try:
            result = subprocess.run(
                ["udisksctl", "info", "-b", device],
                capture_output=True, text=True, check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def _parse_udisksctl(output: str) -> dict[str, str | list[str]]:
        """Parse udisksctl --info output into a dict."""
        info: dict[str, str | list[str]] = {}
        current_section = None

        for line in output.splitlines():
            stripped = line.strip()
            # Detect section headers like "org.freedesktop.UDisks2.Block:"
            if stripped.startswith("org.") and stripped.endswith(":"):
                current_section = stripped.rstrip(":")
                continue
            # Parse key: value pairs within sections
            if ":" in stripped and not stripped.startswith("/"):
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()
                if key in info:
                    # Already has this key — it's a multi-value entry (e.g. Symlinks)
                    existing = info[key]
                    if isinstance(existing, list):
                        if value:
                            existing.append(value)
                    else:
                        info[key] = [existing, value] if existing else [value]
                else:
                    info[key] = value

        return info

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """Convert a human-readable size string to bytes."""
        units: dict[str, int] = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
        size_str = size_str.strip()
        if not size_str:
            return 0
        # Try parsing as plain bytes first
        try:
            return int(size_str)
        except ValueError:
            pass
        # Parse with unit suffix
        for suffix, multiplier in sorted(units.items(), key=lambda x: -len(x[0])):
            if size_str.endswith(suffix) and suffix:
                try:
                    return int(float(size_str[:-len(suffix)]) * multiplier)
                except ValueError:
                    return 0
        return 0

    @classmethod
    def scan(cls) -> list[DriveState]:
        """Scan for all optical drives and return their state."""
        devices = cls._lsblk_optical_drives()
        drives: list[DriveState] = []

        for device in devices:
            # Get model/serial/size from lsblk
            lsblk_info = cls._lsblk_drive_info(device)
            model = lsblk_info.get("MODEL", "Unknown").strip() or "Unknown"
            serial = lsblk_info.get("SERIAL", "").strip() or None
            size = cls._parse_size(lsblk_info.get("SIZE", "0"))

            # Get filesystem/mount info from udisksctl
            output = cls._udisksctl_info(device)
            if output is None:
                drives.append(DriveState(
                    device=device,
                    model=model,
                    serial=serial,
                    size=size,
                    bus=lsblk_info.get("TRAN", ""),
                ))
                continue

            info = cls._parse_udisksctl(output)

            # Parse mount points
            mount_points = []
            mps = info.get("MountPoints", "")
            if isinstance(mps, list):
                mount_points = [m.strip() for m in mps if m.strip()]
            elif isinstance(mps, str) and mps:
                mount_points = [m.strip() for m in mps.split(",") if m.strip()]

            # Determine if media is present (non-empty IdType or mount points)
            has_media = bool(mount_points) or bool(info.get("IdType", "").strip())

            drives.append(DriveState(
                device=device,
                model=model,
                serial=serial,
                size=size,
                bus=lsblk_info.get("TRAN", ""),
                label=str(info.get("IdLabel", "")),
                fs_type=str(info.get("IdType", "")),
                mount_points=mount_points,
                has_media=has_media,
            ))

        return drives


def scan_drives() -> list[DriveState]:
    """Convenience function to scan for all optical drives."""
    return DriveScanner.scan()
