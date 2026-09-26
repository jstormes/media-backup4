#!/bin/bash
#
# Media to Backup Drive Script
# Mirrors each MediaN drive to its matching BackupN drive. Each Media drive
# has two backup copies, told apart by a letter: Backup2A and Backup2B both
# back up Media2. A label without a letter (Backup1) backs up Media1 the same way.
#
# Backup drives are cold storage, connected on demand, so a run with no
# Backup drive plugged in is normal and exits quietly. Every Backup drive
# that is plugged in gets backed up in the same run.
#
# Usage: media-backup.sh [--verify] [--dry-run]
#   --verify   After the copy, re-read both drives in full and compare file
#              contents (rsync --checksum). Hours per drive on spinning disks.
#   --dry-run  Show what would be copied and deleted; change nothing.
#
# Files deleted or replaced on a Media drive are not erased from its backup.
# They move to .backup-deleted/<run date>/ on the Backup drive and are pruned
# after DELETED_KEEP_DAYS days.
#
# Exit status is 0 when every plugged-in Backup drive was backed up (or none
# was plugged in), 1 when any drive failed or was skipped.

set -u
set -o pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

LOG_FILE="${LOG_FILE:-/var/log/media-backup.log}"
MOUNT_BASE="${MOUNT_BASE:-/mnt/backup_temp}"
LOCK_FILE="/run/lock/media-backup.lock"
DELETED_DIR=".backup-deleted"
DELETED_KEEP_DAYS=90
# A run that would delete more files than this is more likely an accident on
# the Media drive than intent, so rsync stops deleting and the run fails.
MAX_DELETE=500

RSYNC_EXCLUDES=(
    --exclude='$RECYCLE.BIN'
    --exclude='System Volume Information'
    --exclude="/$DELETED_DIR"
)

VERIFY=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --verify) VERIFY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        *) echo "Usage: $0 [--verify] [--dry-run]" >&2; exit 2 ;;
    esac
done

# All output goes to the log file, and to the terminal too when run by hand.
# The script owns its log, so the cron line needs no redirect.
if [ -t 1 ]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
else
    exec >>"$LOG_FILE" 2>&1
fi

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# A full first copy can outlast the 24h cron interval; never run twice at once.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "Another media backup is still running. Exiting."
    exit 0
fi

# Unmount a Backup drive left mounted if the script dies mid-copy.
MOUNTED=""
cleanup() {
    if [ -n "$MOUNTED" ] && mountpoint -q "$MOUNTED"; then
        log "  Unmounting $MOUNTED on exit..."
        sync
        umount "$MOUNTED" || log "  WARNING: Failed to unmount $MOUNTED. Unmount it by hand before unplugging."
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

# Every device carrying a filesystem label, one per line.
# lsblk -r escapes spaces in labels, so the columns never shift.
devices_for_label() {
    lsblk -n -r -p -o NAME,LABEL | awk -v label="$1" '$2 == label {print $1}'
}

mirror() {
    local src=$1 dst=$2 rc
    local opts=(-rtLv --stats --delete --modify-window=1 --max-delete="$MAX_DELETE"
                --backup --backup-dir="$DELETED_DIR/$(date +%F_%H%M%S)")
    [ "$DRY_RUN" -eq 1 ] && opts+=(--dry-run)

    log "  Starting rsync from $src to $dst..."
    rsync "${opts[@]}" "${RSYNC_EXCLUDES[@]}" "$src/" "$dst/"
    rc=$?
    case $rc in
        0)  log "  Rsync completed." ;;
        # Files vanished mid-copy: the 03:30 Jellyfin backup rewrites
        # Backups/jellyfin_cache on the Media drive. Everything else copied;
        # the next run picks up the new files.
        24) log "  WARNING: Some source files vanished during the copy (rsync code 24). The rest copied; the next run catches up."
            rc=0 ;;
        25) log "  ERROR: Rsync stopped after $MAX_DELETE deletions. Check $src for an accidental mass deletion; if it was intended, raise MAX_DELETE and rerun." ;;
        *)  log "  ERROR: Rsync failed with exit code $rc." ;;
    esac
    return $rc
}

verify() {
    local src=$1 dst=$2 out diffs rc
    log "  Verifying file contents (reads both drives in full)..."
    out=$(rsync -rtL --checksum --dry-run --delete --itemize-changes --modify-window=1 \
        "${RSYNC_EXCLUDES[@]}" "$src/" "$dst/")
    rc=$?
    if [ $rc -eq 24 ]; then
        log "  WARNING: Some source files vanished during the verify (rsync code 24)."
    elif [ $rc -ne 0 ]; then
        log "  ERROR: Verify failed with rsync exit code $rc."
        return 1
    fi
    # Lines starting with "." are attribute-only (directory times), not content.
    diffs=$(grep -v '^\.' <<<"$out")
    if [ -n "$diffs" ]; then
        log "  ERROR: $(wc -l <<<"$diffs") entries differ between $src and $dst:"
        echo "$diffs"
        return 1
    fi
    log "  Verified: all files match."
}

prune_deleted() {
    local dir="$1/$DELETED_DIR" cutoff old name
    [ -d "$dir" ] || return 0
    cutoff=$(date -d "-$DELETED_KEEP_DAYS days" +%F)
    for old in "$dir"/*; do
        [ -d "$old" ] || continue
        name=$(basename "$old")
        [[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]] || continue
        if [[ "$name" < "$cutoff" ]]; then
            log "  Pruning $DELETED_DIR/$name (older than $DELETED_KEEP_DAYS days)..."
            rm -rf -- "$old"
        fi
    done
}

# Back up one Backup drive from its matching Media drive. Returns 1 on any
# failure or skip.
backup_drive() {
    local label=$1
    local num=${label#Backup}
    local media_label="Media${num%[A-Z]}"
    local -a devs
    local backup_dev media_dev media_mount media_fstype backup_fstype
    local media_used backup_size target ok=1

    log "Processing $label (matching $media_label)..."

    mapfile -t devs < <(devices_for_label "$label")
    if [ ${#devs[@]} -ne 1 ]; then
        log "  ERROR: Label $label is on ${#devs[@]} devices (${devs[*]}). Skipping."
        return 1
    fi
    backup_dev=${devs[0]}

    if findmnt -n --source "$backup_dev" >/dev/null; then
        log "  ERROR: $label ($backup_dev) is already mounted at $(findmnt -n -f -o TARGET --source "$backup_dev"). Skipping."
        return 1
    fi

    mapfile -t devs < <(devices_for_label "$media_label")
    if [ ${#devs[@]} -eq 0 ]; then
        log "  ERROR: No matching $media_label drive found. Skipping."
        return 1
    elif [ ${#devs[@]} -gt 1 ]; then
        log "  ERROR: Label $media_label is on ${#devs[@]} devices (${devs[*]}). Skipping."
        return 1
    fi
    media_dev=${devs[0]}

    media_mount=$(findmnt -n -f -o TARGET --source "$media_dev")
    if [ -z "$media_mount" ]; then
        log "  ERROR: $media_label ($media_dev) is not mounted. Skipping."
        return 1
    fi

    media_fstype=$(lsblk -n -d -o FSTYPE "$media_dev")
    backup_fstype=$(lsblk -n -d -o FSTYPE "$backup_dev")
    if [ "$media_fstype" != "$backup_fstype" ]; then
        log "  ERROR: Filesystem mismatch. $media_label=$media_fstype, $label=$backup_fstype. Skipping."
        return 1
    fi
    log "  Filesystem match: $media_fstype"

    media_used=$(df -B1 --output=used "$media_mount" | tail -1 | tr -d ' ')
    backup_size=$(lsblk -b -n -d -o SIZE "$backup_dev" | tr -d ' ')
    if ! [[ "$media_used" =~ ^[0-9]+$ && "$backup_size" =~ ^[0-9]+$ ]]; then
        log "  ERROR: Could not read drive sizes (Media used: '$media_used', Backup size: '$backup_size'). Skipping."
        return 1
    fi
    if [ "$backup_size" -lt "$media_used" ]; then
        log "  ERROR: Backup drive too small. Media used: $media_used, Backup size: $backup_size. Skipping."
        return 1
    fi
    log "  Size OK: Media used $(numfmt --to=iec "$media_used"), Backup size $(numfmt --to=iec "$backup_size")"

    # One mount point per drive, so a stuck mount never gets another drive stacked on it.
    target="$MOUNT_BASE/$label"
    mkdir -p "$target"
    if mountpoint -q "$target"; then
        log "  ERROR: Something is already mounted at $target. Skipping."
        return 1
    fi
    log "  Mounting $backup_dev at $target..."
    if ! mount "$backup_dev" "$target"; then
        log "  ERROR: Failed to mount $backup_dev. Skipping."
        return 1
    fi
    MOUNTED=$target

    mirror "$media_mount" "$target" || ok=0
    if [ "$ok" -eq 1 ] && [ "$VERIFY" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
        verify "$media_mount" "$target" || ok=0
    fi
    if [ "$ok" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
        prune_deleted "$target"
    fi

    log "  Unmounting $target..."
    sync
    if umount "$target"; then
        MOUNTED=""
        log "  Successfully unmounted $label."
    else
        log "  ERROR: Failed to unmount $target. Unmount it by hand before unplugging."
        ok=0
    fi

    if [ "$ok" -eq 1 ]; then
        log "  Backup of $media_label to $label completed."
        return 0
    fi
    log "  Backup of $media_label to $label FAILED."
    return 1
}

log "=== Media Backup Started ==="
[ "$DRY_RUN" -eq 1 ] && log "Dry run: nothing will be changed."
[ "$VERIFY" -eq 1 ] && log "Verify enabled: file contents will be compared after the copy."

mapfile -t BACKUP_LABELS < <(lsblk -n -r -o LABEL | grep -E '^Backup[0-9]+[A-Z]?$' | sort -u -V)

if [ ${#BACKUP_LABELS[@]} -eq 0 ]; then
    log "No Backup drives found. Exiting."
    exit 0
fi

FAILED=()
for LABEL in "${BACKUP_LABELS[@]}"; do
    backup_drive "$LABEL" || FAILED+=("$LABEL")
done

if [ ${#FAILED[@]} -gt 0 ]; then
    log "=== Media Backup Finished WITH ERRORS (failed: ${FAILED[*]}) ==="
    exit 1
fi
log "=== Media Backup Finished ==="
