#!/bin/bash
# Mount the backup drive at media_path and make it writable by the app.
#
#   sudo ./setup-backup-drive.sh [options]
#
#     --device DEV     partition to use        (default /dev/nvme0n1p1)
#     --mount DIR      mount point             (default /srv/media-backup)
#     --label NAME     ext4 label if unset     (default media-backup)
#     --no-label       leave the label alone
#     --yes            skip the confirmation prompt
#
# This script NEVER formats, erases, or deletes anything. It mounts the
# partition read-only first, shows you what is on it, and asks before
# making the mount permanent. If the drive holds data you want, nothing
# here disturbs it -- the data simply appears under the mount point.
#
# config.py wants media_path to exist, be a directory, and be writable by
# the user running the app. finished/ and cancelled/ must live on the same
# filesystem, because finishing a collection is a rename, not a copy.
set -euo pipefail

DEVICE=/dev/nvme0n1p1
MOUNT=/srv/media-backup
LABEL=media-backup
SET_LABEL=1
ASSUME_YES=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device)   DEVICE="${2:?--device needs a value}"; shift 2 ;;
        --mount)    MOUNT="${2:?--mount needs a value}"; shift 2 ;;
        --label)    LABEL="${2:?--label needs a value}"; shift 2 ;;
        --no-label) SET_LABEL=0; shift ;;
        --yes)      ASSUME_YES=1; shift ;;
        -h|--help)  sed -n '2,/^[^#]/p' "$0" | sed '$d'; exit 0 ;;
        *)          die "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run this with sudo: sudo $0 $*"

TARGET_USER="${SUDO_USER:-root}"
TARGET_GROUP=$(id -gn "$TARGET_USER")

say "Checking $DEVICE"
[[ -b "$DEVICE" ]] || die "$DEVICE is not a block device"

FSTYPE=$(blkid -o value -s TYPE "$DEVICE" || true)
UUID=$(blkid -o value -s UUID "$DEVICE" || true)
CURRENT_LABEL=$(blkid -o value -s LABEL "$DEVICE" || true)
[[ -n "$UUID" ]] || die "$DEVICE has no filesystem UUID -- is it formatted?"
[[ "$FSTYPE" == "ext4" ]] || die "$DEVICE is $FSTYPE, expected ext4 (this script will not reformat it)"
ok "ext4, UUID=$UUID, label=${CURRENT_LABEL:-(none)}"

if findmnt -S "$DEVICE" >/dev/null 2>&1; then
    die "$DEVICE is already mounted at $(findmnt -n -o TARGET -S "$DEVICE") -- unmount it first"
fi

say "Inspecting contents (read-only, nothing is modified)"
PROBE=$(mktemp -d /tmp/backup-drive-probe-XXXXXX)
cleanup() { mountpoint -q "$PROBE" && umount "$PROBE"; rmdir "$PROBE" 2>/dev/null || true; }
trap cleanup EXIT
mount -o ro "$DEVICE" "$PROBE" || die "could not mount $DEVICE read-only"

entries=$(find "$PROBE" -mindepth 1 -maxdepth 1 ! -name 'lost+found' | wc -l)
df -h --output=size,used,avail,pcent "$PROBE" | sed 's/^/        /'
if [[ "$entries" -eq 0 ]]; then
    ok "filesystem is empty (apart from lost+found)"
else
    warn "filesystem has $entries top-level entries:"
    ls -la "$PROBE" | sed 's/^/        /' | head -20
fi
umount "$PROBE"
trap - EXIT
rmdir "$PROBE"

if [[ $ASSUME_YES -eq 0 ]]; then
    echo
    echo "  About to mount $DEVICE at $MOUNT permanently (fstab entry by UUID)"
    echo "  and hand ownership of the mount root to $TARGET_USER."
    echo "  Nothing on the drive will be erased."
    read -r -p "  Type yes to continue: " answer
    [[ "$answer" == "yes" ]] || die "aborted"
fi

if [[ $SET_LABEL -eq 1 && -z "$CURRENT_LABEL" ]]; then
    say "Labelling"
    e2label "$DEVICE" "$LABEL"
    ok "label set to $LABEL"
elif [[ -n "$CURRENT_LABEL" ]]; then
    ok "keeping existing label: $CURRENT_LABEL"
fi

say "Mounting at $MOUNT"
mkdir -p "$MOUNT"

# By UUID, so it survives the drive moving to another slot. nofail keeps a
# missing drive from holding up boot -- the app reports the absent
# media_path far more clearly than an emergency shell does.
FSTAB_LINE="UUID=$UUID  $MOUNT  ext4  defaults,nofail  0  2"
if grep -q "^UUID=$UUID[[:space:]]" /etc/fstab; then
    ok "fstab already has an entry for this UUID"
else
    cp /etc/fstab /etc/fstab.bak.$(date +%Y%m%d%H%M%S)
    printf '\n# media-backup target\n%s\n' "$FSTAB_LINE" >> /etc/fstab
    ok "added to /etc/fstab (previous copy saved as /etc/fstab.bak.*)"
fi

systemctl daemon-reload
mountpoint -q "$MOUNT" || mount "$MOUNT"
mountpoint -q "$MOUNT" || die "mount failed -- check 'journalctl -xe'"
ok "mounted"

say "Ownership"
# The app runs as a normal user; config.validate() checks W_OK on media_path.
chown "$TARGET_USER:$TARGET_GROUP" "$MOUNT"
chmod 755 "$MOUNT"
ok "$MOUNT owned by $TARGET_USER:$TARGET_GROUP"

# Created as the user so nothing under media_path ends up root-owned.
for sub in collections finished cancelled; do
    runuser -u "$TARGET_USER" -- mkdir -p "$MOUNT/$sub"
done
ok "created collections/ finished/ cancelled/"

say "Verifying"
findmnt -o SOURCE,TARGET,FSTYPE,OPTIONS "$MOUNT" | sed 's/^/        /'
runuser -u "$TARGET_USER" -- test -w "$MOUNT" \
    && ok "writable by $TARGET_USER" \
    || die "$MOUNT is not writable by $TARGET_USER"
avail=$(df -h --output=avail "$MOUNT" | tail -1 | tr -d ' ')
ok "$avail free"

REPO=$(dirname "$(realpath "$0")")
if [[ -f "$REPO/src/media_backup/config.py" ]]; then
    say "App config check"
    runuser -u "$TARGET_USER" -- env PYTHONPATH="$REPO/src" /usr/bin/python3 -c "
from media_backup import config
cfg = config.load()
probs = config.validate(cfg)
print('        media_path:', cfg.media_path)
print('        makemkvcon:', cfg.makemkvcon)
if not probs:
    print('        no problems')
for p in probs:
    print(f'        {p.level}: ' + p.text.splitlines()[0])
" || warn "could not run the config check"
fi

say "Done"
echo "  The drive remounts automatically at boot."
echo "  To undo: remove the UUID=$UUID line from /etc/fstab and 'sudo umount $MOUNT'."
