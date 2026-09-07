#!/bin/bash
# Install the system requirements for media-backup and report what is missing.
#
#   sudo ./install-requirements.sh
#
# Everything the app imports is stdlib or a system package, so there is no
# pip step. MakeMKV is a proprietary source build and is only checked here,
# never installed -- see docs/makemkv/README.md.
set -euo pipefail

# The invoking user, not root: group membership and ~/.MakeMKV belong to them.
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

PACKAGES=(
    python3-tk            # tkinter -- the GUI will not import without it
    default-jre-headless  # MakeMKV needs a JRE for BD-J Blu-rays
)

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; failed=1; }

if [[ $EUID -ne 0 ]]; then
    echo "This script installs packages; run it with sudo:" >&2
    echo "  sudo $0" >&2
    exit 1
fi

say "Installing packages"
missing=()
for pkg in "${PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "^install ok installed$"; then
        ok "$pkg already installed"
    else
        missing+=("$pkg")
    fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "  installing: ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y "${missing[@]}"
else
    echo "  nothing to install"
fi

failed=0

say "Verifying requirements"

# /usr/bin/python3 is the only interpreter with gi; a plain venv will not see it.
ver=$(/usr/bin/python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
if /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)'; then
    ok "python3 $ver (>= 3.14)"
else
    bad "python3 $ver -- 3.14 or newer required"
fi

if /usr/bin/python3 -c 'import tkinter' 2>/dev/null; then
    ok "tkinter"
else
    bad "tkinter -- import still fails after installing python3-tk"
fi

if /usr/bin/python3 -c 'import gi' 2>/dev/null; then
    ok "PyGObject ($(/usr/bin/python3 -c 'import gi; print(gi.__version__)'))"
else
    bad "PyGObject -- install python3-gi"
fi

if systemctl is-active --quiet udisks2; then
    ok "udisks2 running"
else
    bad "udisks2 not running -- systemctl enable --now udisks2"
fi

if compgen -G '/dev/sr*' >/dev/null; then
    ok "optical devices: $(echo /dev/sr*)"
else
    warn "no /dev/sr* -- no optical drive is attached"
fi

if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx cdrom; then
    ok "$TARGET_USER is in the cdrom group"
else
    bad "$TARGET_USER is not in the cdrom group -- usermod -aG cdrom $TARGET_USER, then log out and back in"
fi

command -v stdbuf >/dev/null && ok "stdbuf" || warn "stdbuf missing -- set use_stdbuf=false in config.json"
command -v java   >/dev/null && ok "java ($(java -version 2>&1 | head -1))" || bad "java -- needed for BD-J Blu-rays"

# MakeMKV is proprietary, needs a purchased key, and is built from source.
# Not installable here; report its state so the gap is visible.
say "MakeMKV (manual install)"
if [[ -x /usr/local/bin/makemkvcon ]]; then
    ok "makemkvcon: $(/usr/local/bin/makemkvcon --version 2>&1 | head -1)"
    [[ -f "$TARGET_HOME/.MakeMKV/settings.conf" ]] \
        && ok "licence key present in $TARGET_HOME/.MakeMKV/settings.conf" \
        || warn "no $TARGET_HOME/.MakeMKV/settings.conf -- makemkvcon has no licence key yet"
else
    warn "/usr/local/bin/makemkvcon not found -- backups cannot run until it is built"
    warn "see docs/makemkv/README.md; this script cannot install it for you"
fi

say "Result"
if [[ $failed -eq 0 ]]; then
    echo "  All hard requirements are satisfied."
    echo "  Run the app:   /usr/bin/python3 gui_app.py"
    echo "  Run the tests: /usr/bin/python3 -m unittest discover -s tests -t ."
else
    echo "  Some requirements are missing -- see the FAIL lines above."
    exit 1
fi
