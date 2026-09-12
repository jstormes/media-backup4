#!/bin/bash
# Check every piece of the media-backup setup. Read-only: installs nothing,
# mounts nothing, changes nothing. No sudo needed.
#
#   ./verify-setup.sh
#
# Written to be run after a reboot, to confirm the things that only a cold
# boot exercises: the fstab mount, the udev rule applied from scratch, and
# whether all the optical drives come back.
set -uo pipefail

pass=0; fail=0; warn=0
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; pass=$((pass+1)); }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; warn=$((warn+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

REPO=$(dirname "$(realpath "$0")")
MOUNT=/srv/media-backup
CON=/usr/local/bin/makemkvcon

say "Python environment"
/usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,14) else 1)' \
    && ok "python3 $(/usr/bin/python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')" \
    || bad "python3 older than 3.14"
/usr/bin/python3 -c 'import tkinter' 2>/dev/null && ok "tkinter" || bad "tkinter missing"
/usr/bin/python3 -c 'import gi' 2>/dev/null && ok "PyGObject" || bad "PyGObject missing"
systemctl is-active --quiet udisks2 && ok "udisks2 running" || bad "udisks2 not running"

say "Backup drive"
if mountpoint -q "$MOUNT"; then
    ok "$MOUNT mounted from $(findmnt -n -o SOURCE "$MOUNT")"
    [[ -w "$MOUNT" ]] && ok "writable by $USER" || bad "$MOUNT not writable by $USER"
    ok "$(df -h --output=avail "$MOUNT" | tail -1 | tr -d ' ') free"
    for sub in collections finished cancelled; do
        [[ -d "$MOUNT/$sub" ]] || warn "$MOUNT/$sub is missing"
    done
else
    bad "$MOUNT is not mounted (fstab entry did not take)"
fi

say "MakeMKV"
if [[ -x "$CON" ]]; then
    ldd "$CON" 2>/dev/null | grep -q "not found" \
        && bad "makemkvcon has unresolved libraries" \
        || ok "makemkvcon links cleanly"
    [[ -f "$HOME/.MakeMKV/settings.conf" ]] && grep -q app_Key "$HOME/.MakeMKV/settings.conf" 2>/dev/null \
        && ok "licence key present" || bad "no licence key in ~/.MakeMKV/settings.conf"
else
    bad "$CON not found"
fi

say "Optical drives"
mapfile -t SR < <(ls /dev/sr* 2>/dev/null)
[[ ${#SR[@]} -gt 0 ]] && ok "${#SR[@]} drives: ${SR[*]}" || bad "no /dev/sr* devices"
id -nG | tr ' ' '\n' | grep -qx cdrom && ok "in the cdrom group" || bad "not in the cdrom group"

# Each makemkvcon run is wrapped so it can only open its own drive's sg node;
# MakeMKV probes every drive otherwise, mid-rip or not. See
# src/media_backup/makemkv/isolation.py.
if command -v bwrap >/dev/null && bwrap --dev-bind / / -- true 2>/dev/null; then
    ok "bwrap works -- runs are confined to one drive"
else
    warn "no working bwrap -- every run will probe every drive"
fi

# Two identical drives with no real serial collapse into one udisks2 drive
# object, and then Drive.MediaAvailable reports the wrong drive's state.
say "Drive object collision"
declare -A seen=()
collision=0
for dev in "${SR[@]}"; do
    obj=$(gdbus call --system --dest org.freedesktop.UDisks2 \
        --object-path /org/freedesktop/UDisks2/block_devices/$(basename "$dev") \
        --method org.freedesktop.DBus.Properties.Get org.freedesktop.UDisks2.Block Drive 2>/dev/null)
    if [[ -n "${seen[$obj]:-}" ]]; then
        bad "$dev shares a udisks2 drive object with ${seen[$obj]} -- has_media will be wrong"
        collision=1
    else
        seen[$obj]="$dev"
    fi
done
[[ $collision -eq 0 ]] && ok "every drive has its own udisks2 drive object"

say "Automount is off"
auto_missing=0
for dev in "${SR[@]}"; do
    a=$(udevadm info --query=property --name="$dev" 2>/dev/null | grep -c "^UDISKS_AUTO=0")
    [[ "$a" == "1" ]] || { bad "$dev is missing UDISKS_AUTO=0 (udev rule not applied)"; auto_missing=1; }
done
[[ $auto_missing -eq 0 ]] && ok "UDISKS_AUTO=0 on all ${#SR[@]} drives"
for k in automount automount-open; do
    v=$(gsettings get org.gnome.desktop.media-handling $k 2>/dev/null)
    [[ "$v" == "false" ]] && ok "gsettings $k = false" || warn "gsettings $k = $v"
done
if mount | grep -q "/dev/sr"; then
    bad "an optical disc is MOUNTED: $(mount | grep /dev/sr)"
else
    ok "no optical disc mounted"
fi

say "Discs present"
found=0
for dev in "${SR[@]}"; do
    lbl=$(gdbus call --system --dest org.freedesktop.UDisks2 \
        --object-path /org/freedesktop/UDisks2/block_devices/$(basename "$dev") \
        --method org.freedesktop.DBus.Properties.Get org.freedesktop.UDisks2.Block IdLabel 2>/dev/null \
        | grep -oE "'[^']*'" | tr -d "'")
    [[ -n "$lbl" ]] && { echo "        $dev holds \"$lbl\""; found=1; }
done
[[ $found -eq 0 ]] && echo "        all trays empty"

# The symptom of a shared drive object: Drive.MediaAvailable (has_media)
# disagrees with the per-block Size/IdLabel, which are never merged.
say "Disc detection agrees with reality"
if [[ -f "$REPO/src/media_backup/drives.py" ]]; then
    PYTHONPATH="$REPO/src" /usr/bin/python3 -c "
from media_backup.drives import DriveScanner
bad = 0
for d in sorted(DriveScanner().scan(), key=lambda x: x.device):
    disc = bool(d.size) or bool(d.label)
    if disc != d.has_media:
        print(f'  \033[31mFAIL\033[0m  {d.device}: has_media={d.has_media} but '
              f'size={d.size} label={d.label!r}')
        bad += 1
    elif disc:
        print(f'  \033[32mok\033[0m    {d.device}: disc {d.label!r} detected, has_media=True')
if not bad:
    print('  \033[32mok\033[0m    has_media matches the block layer on every drive')
raise SystemExit(1 if bad else 0)
" || bad "has_media disagrees with the block layer"
fi

say "App config"
if [[ -f "$REPO/src/media_backup/config.py" ]]; then
    PYTHONPATH="$REPO/src" /usr/bin/python3 -c "
from media_backup import config
cfg = config.load()
probs = config.validate(cfg)
if not probs:
    print('  \033[32mok\033[0m    config.validate(): no problems')
for p in probs:
    colour = '31' if p.level == 'error' else '33'
    print(f'  \033[{colour}m{p.level}\033[0m  ' + p.text.splitlines()[0])
import sys; sys.exit(1 if any(p.is_fatal for p in probs) else 0)
" || bad "config.validate() reported a fatal problem"
fi

say "IMDb lookups"
# Publishing turns a disc into "Name (Year) [imdbid-tt...]" and gets the id
# from the mirror on nas2, because imdb.com cannot be read by a program at all
# -- 403 to a non-browser user-agent, an empty 202 to a browser one, measured
# 2026-09-11. The failure this check exists for is silent: a machine without
# the credentials publishes every film with no tag and no error, which reads as
# a style choice rather than a missing dependency and is discovered later, from
# a library. The credentials live in ~/.profile, which is login scope, so a
# desktop-launched GUI has them and a fresh ssh session may not.
if [[ -z "${MEDIA_BACKUP_IMDB_USER:-}" || -z "${MEDIA_BACKUP_IMDB_PASSWORD:-}" ]]; then
    warn "MEDIA_BACKUP_IMDB_USER/_PASSWORD not set in this shell -- publishing would
        tag nothing. They belong in ~/.profile; see AGENT.md."
else
    ok "MEDIA_BACKUP_IMDB_USER=$MEDIA_BACKUP_IMDB_USER (password set)"
    if ! command -v mariadb >/dev/null; then
        warn "no mariadb client on PATH -- cannot check the connection from here"
    elif rows=$(mariadb -h "${MEDIA_BACKUP_IMDB_HOST:-nas2}" \
                        -u "$MEDIA_BACKUP_IMDB_USER" -p"$MEDIA_BACKUP_IMDB_PASSWORD" \
                        "${MEDIA_BACKUP_IMDB_DATABASE:-imdb}" -N -B \
                        -e "select count(*) from title_basics;" 2>/dev/null); then
        ok "  ${MEDIA_BACKUP_IMDB_HOST:-nas2} answers: title_basics has $rows rows"
    else
        bad "  cannot query ${MEDIA_BACKUP_IMDB_HOST:-nas2} -- publishing would tag nothing"
    fi
fi

say "Result"
printf '  %d passed, %d warnings, %d failed\n' "$pass" "$warn" "$fail"
[[ $fail -eq 0 ]] && echo "  Setup is intact." || echo "  Something did not survive -- see the FAIL lines."
exit $(( fail > 0 ))
