#!/bin/bash
# Build and install MakeMKV from source for media-backup.
#
#   sudo ./install-makemkv.sh [options]
#
#     --no-gui           build makemkvcon only, skip the Qt5 GUI (drops
#                        qtbase5-dev and libgl1-mesa-dev from the deps)
#     --version VER      MakeMKV version to build       (default 1.18.4)
#     --prefix DIR       install prefix                 (default /usr/local)
#     --src-dir DIR      look here for the tarballs before downloading
#     --accept-eula      accept the MakeMKV EULA non-interactively
#     --keep-build       do not delete the build tree on success
#
# MakeMKV ships as two tarballs that must be the same version: -oss compiles
# libmakemkv.so.1 and libdriveio.so.0, which the proprietary makemkvcon in
# -bin links against. -bin cannot run on its own. See docs/makemkv/README.md.
#
# This installs the engine, not a licence key. MakeMKV is proprietary and
# needs your own key; the script never writes one.
set -euo pipefail

VERSION=1.18.4
PREFIX=/usr/local
BUILD_GUI=1
ACCEPT_EULA=0
KEEP_BUILD=0
SRC_DIR=""

# Checksums for the 1.18.4 tarballs as served by makemkv.com. Only checked
# when building that version; any other version is built unverified.
SHA256_OSS=8590063648d42ec2a958b74573d7022f0f4c334e4e4fe7dd53b70c6e748ba453
SHA256_BIN=cee56de0baa5531abed16bd862742d308d772b4ab4dae16ee865bf74f04a1608

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-gui)      BUILD_GUI=0; shift ;;
        --accept-eula) ACCEPT_EULA=1; shift ;;
        --keep-build)  KEEP_BUILD=1; shift ;;
        --version)     VERSION="${2:?--version needs a value}"; shift 2 ;;
        --prefix)      PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
        --src-dir)     SRC_DIR="${2:?--src-dir needs a value}"; shift 2 ;;
        -h|--help)     sed -n '2,/^[^#]/p' "$0" | sed '$d'; exit 0 ;;
        *)             die "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run this with sudo: sudo $0 $*"

# Compile as the invoking user; only the install steps need root.
BUILD_USER="${SUDO_USER:-root}"
BUILD_HOME=$(getent passwd "$BUILD_USER" | cut -d: -f6)

OSS="makemkv-oss-$VERSION"
BIN="makemkv-bin-$VERSION"

say "Build dependencies"
PACKAGES=(build-essential pkg-config libc6-dev libssl-dev libexpat1-dev
          libavcodec-dev zlib1g-dev less curl)
# The Qt5 GUI is optional: media-backup only ever runs makemkvcon. It is built
# by default because it is the easiest place to enter the licence key.
if [[ $BUILD_GUI -eq 1 ]]; then
    PACKAGES+=(qtbase5-dev libgl1-mesa-dev)
fi

missing=()
for pkg in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "^install ok installed$" \
        || missing+=("$pkg")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "  installing: ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y "${missing[@]}"
else
    ok "all present"
fi

say "Source tarballs"
BUILD_DIR=$(mktemp -d /var/tmp/makemkv-build-XXXXXX)
chown "$BUILD_USER" "$BUILD_DIR"

# Look where a tarball may already sit before pulling 24M over the network.
find_tarball() {
    local name="$1" dir
    for dir in "$SRC_DIR" "$(dirname "$(realpath "$0")")" "$BUILD_HOME/Downloads" "$PWD"; do
        [[ -n "$dir" && -f "$dir/$name" ]] && { echo "$dir/$name"; return 0; }
    done
    return 1
}

for name in "$OSS.tar.gz" "$BIN.tar.gz"; do
    if found=$(find_tarball "$name"); then
        cp "$found" "$BUILD_DIR/$name"
        ok "$name (from $(dirname "$found"))"
    else
        echo "  downloading $name"
        curl -fL --progress-bar -o "$BUILD_DIR/$name" \
            "https://www.makemkv.com/download/$name" \
            || die "download failed: $name -- check the version number"
        ok "$name (downloaded)"
    fi
done

if [[ "$VERSION" == "1.18.4" ]]; then
    echo "$SHA256_OSS  $BUILD_DIR/$OSS.tar.gz" | sha256sum -c --quiet - \
        || die "checksum mismatch on $OSS.tar.gz"
    echo "$SHA256_BIN  $BUILD_DIR/$BIN.tar.gz" | sha256sum -c --quiet - \
        || die "checksum mismatch on $BIN.tar.gz"
    ok "checksums verified"
else
    warn "no known checksums for $VERSION -- tarballs not verified"
fi

tar xzf "$BUILD_DIR/$OSS.tar.gz" -C "$BUILD_DIR"
tar xzf "$BUILD_DIR/$BIN.tar.gz" -C "$BUILD_DIR"
chown -R "$BUILD_USER" "$BUILD_DIR"

as_user() { runuser -u "$BUILD_USER" -- "$@"; }

say "Building $OSS (as $BUILD_USER)"
# Both halves default to --prefix=/usr; media-backup's config.py expects
# /usr/local/bin/makemkvcon, and /usr/local keeps this clear of dpkg's files.
CONFIGURE_ARGS=(--prefix="$PREFIX")
[[ $BUILD_GUI -eq 0 ]] && CONFIGURE_ARGS+=(--disable-gui)
as_user bash -c "cd '$BUILD_DIR/$OSS' && ./configure ${CONFIGURE_ARGS[*]}" \
    || die "configure failed -- see the output above"
as_user make -C "$BUILD_DIR/$OSS" -j"$(nproc)" || die "make failed for $OSS"

say "Installing $OSS"
make -C "$BUILD_DIR/$OSS" install
ldconfig
ok "libraries installed to $PREFIX/lib"

say "Building $BIN"
# `make` here shows the EULA in less and waits for you to type "yes".
if [[ $ACCEPT_EULA -eq 1 ]]; then
    as_user mkdir -p "$BUILD_DIR/$BIN/tmp"
    as_user bash -c "echo accepted > '$BUILD_DIR/$BIN/tmp/eula_accepted'"
    warn "EULA accepted via --accept-eula (see $BIN/src/eula_en_linux.txt)"
elif [[ ! -t 0 ]]; then
    die "the EULA prompt needs a terminal; re-run interactively or pass --accept-eula"
fi
as_user make -C "$BUILD_DIR/$BIN" PREFIX="$PREFIX" || die "EULA declined or make failed"

say "Installing $BIN"
make -C "$BUILD_DIR/$BIN" PREFIX="$PREFIX" install
ldconfig

say "Verifying"
CON="$PREFIX/bin/makemkvcon"
[[ -x "$CON" ]] || die "$CON is missing after install"
ok "$CON installed"

# The oss/bin split fails here if the halves are mismatched or ldconfig
# has not picked up $PREFIX/lib.
if ldd "$CON" | grep -q "not found"; then
    ldd "$CON" | grep "not found" | sed 's/^/        /'
    die "makemkvcon has unresolved libraries"
fi
ok "all shared libraries resolve"

# makemkvcon has no --version; the banner comes back as a MSG:1005 record.
# disc:9999 is a deliberately absent disc, so this only enumerates drives.
version_line=$(timeout 30 "$CON" -r --cache=1 info disc:9999 2>&1 \
    | grep -m1 -o 'MakeMKV v[^"]*' || true)
if [[ -n "$version_line" ]]; then
    ok "$version_line"
    drives=$(timeout 30 "$CON" -r --cache=1 info disc:9999 2>&1 \
        | grep -c '^DRV:.*,"/dev/' || true)
    ok "makemkvcon sees $drives optical drive(s)"
else
    warn "makemkvcon installed but did not report a version"
fi

if [[ -f "$BUILD_HOME/.MakeMKV/settings.conf" ]] \
   && grep -q "app_Key" "$BUILD_HOME/.MakeMKV/settings.conf" 2>/dev/null; then
    ok "licence key found in $BUILD_HOME/.MakeMKV/settings.conf"
else
    warn "no licence key yet -- makemkvcon will not read discs until you add one"
fi

if [[ $KEEP_BUILD -eq 1 ]]; then
    echo "  build tree kept at $BUILD_DIR"
else
    rm -rf "$BUILD_DIR"
fi

say "Next steps"
if [[ ! -f "$BUILD_HOME/.MakeMKV/settings.conf" ]]; then
    cat <<EOF
  Enter your purchased key, either in the GUI ($PREFIX/bin/makemkv →
  Help → Register), or by hand as $BUILD_USER:

      mkdir -p ~/.MakeMKV
      printf 'app_Key = "YOUR-KEY-HERE"\n' >> ~/.MakeMKV/settings.conf

EOF
fi
if [[ "$PREFIX" != "/usr/local" ]]; then
    echo "  config.py defaults makemkvcon to /usr/local/bin/makemkvcon, so set"
    echo "  \"makemkvcon\": \"$CON\" in config.json."
else
    echo "  Nothing else to configure -- config.py already defaults to $CON."
fi
echo "  Verify the app agrees: /usr/bin/python3 -m media_backup"
