# Disc Detection in Optical Drives

## Problem

The "Disc present" / "No disc" flag in the GUI was unreliable. It would incorrectly report "No disc" even when a valid disc was inserted and readable.

## Root Cause

In `src/media_backup/drives.py`, the `DriveScanner.scan()` method determines disc presence with:

```python
has_media = bool(mount_points) or "IdType" in info
```

This has two problems:

### 1. Relies on mount points (unreliable)

When a disc is inserted but not mounted (e.g., gnome desktop auto-mounts, or the user disables automount), `mount_points` is empty. Without a mount point, this condition alone gives `False` even though a disc is physically present.

### 2. Checks for a key, not a value

udisks2's `IdType` **key** is always present in the parsed output — it is simply an empty string when no disc is in the drive:

```
# No disc:
    IdType:                      # empty string, but KEY exists

# Disc present (e.g. UDF):
    IdType: udf                   # non-empty value
```

So `"IdType" in info` is always `True` when udisksctl succeeds, regardless of whether a disc is actually present. This means the second clause of the `or` expression was never actually failing — but the first clause (`bool(mount_points)`) was, leaving us with `False` when no disc was mounted.

## Solution

Check the **value** of `IdType`, not just its existence:

```python
has_media = bool(mount_points) or bool(info.get("IdType", "").strip())
```

This evaluates to `True` when:
- The drive has mount points (disc is mounted), **or**
- udisks2 reports a non-empty filesystem type (e.g., `"udf"`, `"iso9660"`, `"iso9661"`), meaning a disc is present and readable

## Metadata Extraction

Disc metadata (label, filesystem type) comes from the same udisks2 call:

| Field | udisks2 Property | Purpose |
|-------|-----------------|---------|
| Label | `IdLabel` | Disc volume name (e.g., `"DVD_VIDEO"`, `"SPIDER_MAN_ACROSS_SPIDER_VERSE"`) |
| Filesystem | `IdType` | Disc format (e.g., `"udf"`, `"iso9660"`) |
| Size | `Size` | Disc capacity in bytes |

The `DriveScanner._parse_udisksctl()` method parses `udisksctl info -b /dev/srX` output line-by-line, building a flat dict of all properties. Values are extracted from this dict in `scan()`.

## Alternative Approaches (Not Used)

| Method | Command | Notes |
|--------|---------|-------|
| `eject -i 1` | `eject -i 1 /dev/srX` | Returns 0 if disc present, 1 if not. Reliable but requires a subprocess call separate from udisks2. |
| Drive ioctl | `CDROMREADTOCHDR` | Low-level C/ctypes. Most reliable but most complex. |
| udisks2 Drive properties | `Media Available`, `Optical Disc State` | These exist in the `Drive:` section of udisksctl output but are not currently parsed. |

The chosen approach (checking `IdType` value) is simplest and already part of the data we parse for display.

## Testing Checklist

When changing disc detection logic, verify:

- [ ] Empty drive → "No disc"
- [ ] DVD with filesystem → "Disc present" + correct label + correct fs_type
- [ ] BD with filesystem → "Disc present" + correct label + correct fs_type
- [ ] Disc inserted, not mounted → "Disc present" (not dependent on mount points)
- [ ] Disc swapped between drives → each drive reports correctly
