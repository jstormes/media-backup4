---
name: backup-drive-remove
description: Check that a cold backup drive (Backup1, Backup2A, …) on nas2 can be unplugged, then spin it down and power off its USB port so it can be pulled safely. Use when asked whether a backup drive is safe to remove, to eject, unplug, undock, spin down or power off a backup drive, or before swapping backup drives.
---

# Removing a backup drive from nas2

The backup drives are cold storage: docked for a `media-backup.sh` run, then
put away. See `docs/jellyfin/backup-strategy.md`. Pulling one is safe only when
nothing is writing to it, and it is *kind* only when the heads are parked and
the platters stopped first. A USB dock that loses power mid-spin does an
emergency head retract, which the drive counts and wears on.

This skill does both: prove it is idle, then stop it and cut the port.

## Run it

The script runs **on nas2**; send it over ssh as a script on stdin, so
**no `-n`** (that would hand the remote shell an empty script and it would
exit 0 having checked nothing):

```bash
SKILL=.claude/skills/backup-drive-remove

# Checks only. Changes nothing.
ssh nas2 'bash -s' -- Backup2A < $SKILL/remove-backup-drive.sh

# Checks, then spin down and power off.
ssh nas2 'bash -s' -- Backup2A --power-off < $SKILL/remove-backup-drive.sh
```

Exit 0 is safe (and powered off, if asked). Exit 1 prints `NOT SAFE:` and the
reason, and powers nothing off. Exit 2 is a bad label.

Run the check first and show the operator the output, then power off. A
request to remove the drive is the go-ahead for `--power-off`; a question
("is it safe to remove?") gets the check only, and an offer.

## What it checks, in order

1. **The label is a backup drive** -- `^Backup[0-9]+[A-Z]?$`, the pattern
   `media-backup.sh` pairs on. A Media drive is refused before anything else
   runs, so a typo cannot power off the library under Jellyfin.
2. **Found by label, never `/dev/sdX`.** Device letters move on every replug.
3. **No partition of the disk is mounted.** `media-backup.sh` mounts the
   drive only for its run, so a mounted backup drive means a run is live or
   one died without its cleanup.
4. **No backup job holds `/run/lock/media-backup.lock`.** Both
   `media-backup.sh` and `jellyfin-backup.sh` take it with `flock`. The
   *file* outlives every run and proves nothing; taking the lock is the test.
5. **No process has the device open** (`fuser`).
6. **Nothing else shares its USB port.** Powering off a port powers off
   everything behind it, and a two-bay dock puts two disks behind one port.
7. Prints the last completed backup of that drive, and the last `ERROR` from
   its last run if there was one. Informational: whether a stale backup is
   worth keeping in is the operator's call.

## How it powers off

```bash
sync
sudo sdparm --command=stop /dev/sdX       # spin down: park heads, stop platters
sudo udisksctl power-off -b /dev/sdX      # power off the USB port (also stops the motor)
```

`hdparm` is **not installed** on nas2; `sdparm` is, and speaks SCSI, which is
what a USB-SATA bridge (the dock is an ASMedia ASM235) presents anyway.
`udisksctl power-off` works on the **whole disk**, not a partition, and
refuses while anything on it is mounted. After it, the disk leaves `lsblk`
entirely -- the script checks that, and only then says `Safe to unplug`.
A drive still listed afterwards is a `NOT SAFE`, not a warning.

Passwordless `sudo` for the operator's account is what makes this work over
ssh; `udisksctl` alone would ask polkit, which has no session to ask.

## After a drive goes back in

A drive plugged into the dock is not necessarily the one that came out. The
label is what `media-backup.sh` trusts, so **a blank drive has no label and is
simply ignored** -- nothing is lost, but nothing is backed up either. When a
replug shows a disk with no partitions, identify it by serial before anyone
formats it:

```bash
ssh -n nas2 'udevadm info -q property -n /dev/sdX | grep ID_SERIAL_SHORT'
```

and compare against the serials recorded in project memory for each backup
drive. Seen 2026-09-30: Backup2A was pulled unpowered at 08:40 and a
same-model drive reading all zeros went into the same dock at 08:43. Its
serial (WSC313F7) did not match the one recorded for Backup2A (ZR16HXZR). Never write to an unlabelled backup-sized
drive on the strength of it being "probably the new one".

## Report

Say which drive, what the checks found, and -- when powered off -- that it is
gone from the system and safe to unplug. Mention what the next nightly run
will do without it: log `No Backup drives found` and exit, which is normal
for cold storage, and anything published from now on has no backup copy until
a drive for that Media drive is docked again.
