#!/bin/bash
#
# Check that a cold backup drive on nas2 can be unplugged, and optionally
# spin it down and power off its USB port.
#
# Runs ON nas2. From the ripper, send it over ssh -- as a script on stdin,
# so no -n:
#
#   ssh nas2 'bash -s' -- Backup2A            < remove-backup-drive.sh   # checks only
#   ssh nas2 'bash -s' -- Backup2A --power-off < remove-backup-drive.sh
#
# Without --power-off nothing is changed. With it, the drive is spun down and
# its USB port powered off only if every check passed; the disk then drops
# out of lsblk, which is the proof it is safe to pull.
#
# Exit codes: 0 safe (and powered off, if asked); 1 not safe -- the reason is
# printed; 2 usage.

set -u

LABEL=${1:-}
POWER_OFF=0
[ "${2:-}" = "--power-off" ] && POWER_OFF=1
LOCK_FILE=/run/lock/media-backup.lock          # shared by media-backup.sh and jellyfin-backup.sh
LOG_FILE=/var/log/media-backup.log

fail() { echo "NOT SAFE: $*"; exit 1; }
ok()   { echo "  ok  $*"; }

# Only a Backup drive, by the same pattern media-backup.sh pairs on. This is
# what stops a typo from powering off Media2 under Jellyfin.
if ! [[ "$LABEL" =~ ^Backup[0-9]+[A-Z]?$ ]]; then
    echo "usage: remove-backup-drive.sh BackupN[A-Z] [--power-off]" >&2
    exit 2
fi

# 1. Find it. By label, never by /dev/sdX: those letters move on every replug.
part=$(lsblk -n -r -p -o NAME,LABEL | awk -v l="$LABEL" '$2 == l {print $1}')
[ -n "$part" ] || fail "no partition labelled $LABEL is attached (already removed?)"
[ "$(wc -l <<<"$part")" -eq 1 ] || fail "more than one partition is labelled $LABEL: $part"
disk=/dev/$(lsblk -n -o PKNAME "$part")
[ "$disk" != /dev/ ] || fail "could not find the disk holding $part"
ok "$LABEL is $part on $disk ($(lsblk -d -n -o TRAN,SIZE,MODEL "$disk" | xargs))"

# 2. Nothing on the disk may be mounted -- any partition, not just the labelled one.
mounted=$(lsblk -n -r -p -o NAME,MOUNTPOINT "$disk" | awk '$2 != ""')
[ -z "$mounted" ] || fail "mounted: $mounted"
ok "no partition of $disk is mounted"

# 3. No backup job running. The lock is flock-held for the life of a run, so
#    taking it is the test; the file itself outlives every run and means nothing.
if ! sudo flock -n "$LOCK_FILE" true; then
    fail "a backup job holds $LOCK_FILE -- let it finish ($(pgrep -af 'media-backup|jellyfin-backup' | grep -v pgrep | head -1))"
fi
ok "no backup job is running"

# 4. No process has the device open.
users=$(sudo fuser -v "$disk" ${disk}[0-9]* 2>&1 | grep -v -e '^ *USER' -e '^$')
[ -z "$users" ] || fail "in use: $users"
ok "no process has $disk open"

# 5. Powering off a USB port takes everything behind it. A two-bay dock puts
#    two disks behind one port, and the other could be a Media drive.
usbdev=$(readlink -f "/sys/block/${disk#/dev/}/device" | grep -oE '/usb[0-9]+/[0-9]+-[0-9.]+' | tail -1)
if [ -n "$usbdev" ]; then
    siblings=$(for b in /sys/block/sd*; do
                   [ "/dev/${b##*/}" = "$disk" ] && continue
                   readlink -f "$b/device" | grep -q "$usbdev/" && echo "/dev/${b##*/}"
               done)
    [ -z "$siblings" ] || fail "other disks share its USB port and would lose power too: $siblings"
    ok "nothing else is behind its USB port (${usbdev##*/})"
else
    ok "not a USB disk; spin-down only, no port to power off"
fi

# 6. Report when this drive was last written. Informational: a stale backup
#    is the operator's call, not a reason to keep a drive in.
last=$(sudo grep -E "Backup of Media[0-9]+ to $LABEL (completed|finished)" "$LOG_FILE" 2>/dev/null | tail -1)
n=$(sudo grep -n "Processing $LABEL " "$LOG_FILE" 2>/dev/null | tail -1 | cut -d: -f1)
failed=""
[ -n "$n" ] && failed=$(sudo tail -n "+$n" "$LOG_FILE" | sed '/=== Media Backup Finished/q' | grep -E 'ERROR' | tail -1)
echo "  --  last completed backup: ${last:-none found in $LOG_FILE}"
[ -z "$failed" ] || echo "  !!  last ERROR logged for $LABEL: $failed"

if [ "$POWER_OFF" -eq 0 ]; then
    echo "SAFE to power off $LABEL ($disk). Nothing was changed; rerun with --power-off."
    exit 0
fi

# 7. Flush, spin down, power off the port. udisksctl power-off stops the
#    motor itself before cutting the port; the sdparm stop first is so the
#    heads are parked even if the port cannot be powered off.
sync
sudo sdparm --command=stop "$disk" >/dev/null 2>&1 \
    && ok "spun down $disk" || echo "  --  sdparm stop did not answer; relying on power-off"
if [ -n "$usbdev" ]; then
    sudo udisksctl power-off -b "$disk" || fail "udisksctl power-off failed; the drive is spun down but still powered"
fi

# 8. Prove it: a powered-off USB disk leaves the block layer.
sleep 2
if lsblk -n -r -o LABEL | grep -qx "$LABEL"; then
    [ -z "$usbdev" ] && { echo "SPUN DOWN: $LABEL is stopped; it is not USB, so its port stays powered."; exit 0; }
    fail "$LABEL is still listed after power-off -- do not unplug yet"
fi
echo "POWERED OFF: $LABEL is spun down and gone from the system. Safe to unplug."
