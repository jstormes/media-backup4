#!/bin/bash
# Install the tools that identify and inspect a rip after it is made.
#
#   sudo ./install-media-tools.sh
#
# None of this is needed to *make* a backup -- install-requirements.sh covers
# that. This is for the step after: working out what the files on disk
# actually are, which since 2026-09-08 is a real job rather than a formality.
#
# The ripper now copies every title MakeMKV reports and makes no judgement
# about any of them, so a Blu-ray arrives as up to thirty-nine files that are
# a mix of the feature, alternate cuts, extras, the same content authored
# twice, and individual clips of the film offered as titles in their own
# right. Sorting that pile is the publish step's job and it needs to be able
# to look inside a file.
#
# The case that prompted this: the "Firehead and Last Lives" DVD carries two
# films and names neither. Its MKV headers hold nothing but "Chapter 01"
# through "Chapter 12" and an empty title, and the scan records no name
# either, so which film had been ripped came down to comparing its runtime
# against published ones -- 95:43 against 92 and 88 minutes, which picks an
# answer without settling it. A single frame of the title sequence would have
# read the name off the screen. There was no tool on the box to extract one.
set -euo pipefail

PACKAGES=(
    ffmpeg      # ffmpeg: pull a frame of the title card and read the name off
                # it. ffprobe: exact durations and stream inventories, which
                # is how the publish step tells a feature from a slice of one.
                # The publish skill already instructs a reader to run ffprobe
                # when comparing two authorings of the same title; without
                # this package that instruction cannot be followed.
    mediainfo   # One readable summary per file, including the things ffprobe
                # buries: scan type, encoding library, subtitle track names.
                # Used to pick the richer of two copies of the same content --
                # Hancock's pair differ by eight subtitle languages.
    mkvtoolnix  # mkvpropedit --set title=... stamps the film's name into the
                # file, so a rip stops depending on its folder for identity.
                # mkvmerge -J gives structured JSON instead of scraped output.
    lsdvd       # Reads a DVD's title sets straight off the disc. On a double
                # feature the VTS layout separates two films more directly
                # than MakeMKV's cell ranges, which carry no meaning across
                # titles on a DVD at all.
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

# Presence is not capability. Every check below runs the tool against a real
# file this script generates, so a package that installed but cannot do the
# one thing it was installed for is reported rather than assumed working.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

say "Verifying the tools do the job they are here for"

if command -v ffmpeg >/dev/null; then
    ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
    if ffmpeg -nostdin -loglevel error -f lavfi \
              -i "testsrc=duration=2:size=320x240:rate=5" \
              -c:v libx264 -pix_fmt yuv420p "$WORK/sample.mkv" -y 2>/dev/null; then
        ok "  encodes video"
    else
        bad "  cannot encode -- the rest of these checks will not mean much"
    fi
    # The thing that was actually missing: one frame, as a PNG, to look at.
    if ffmpeg -nostdin -loglevel error -ss 1 -i "$WORK/sample.mkv" \
              -frames:v 1 -vf scale=160:-1 "$WORK/frame.png" -y 2>/dev/null \
       && [[ -s "$WORK/frame.png" ]]; then
        ok "  extracts a frame to PNG ($(stat -c %s "$WORK/frame.png") bytes)"
    else
        bad "  cannot extract a frame -- title cards stay unreadable"
    fi
else
    bad "ffmpeg not on PATH"
fi

if command -v ffprobe >/dev/null; then
    ok "ffprobe $(ffprobe -version 2>/dev/null | head -1 | awk '{print $3}')"
    # Verbatim the command the publish skill tells a reader to run.
    kinds=$(ffprobe -v error -show_entries stream=codec_type \
                    -of csv=p=0 "$WORK/sample.mkv" 2>/dev/null | tr -d '\r' | paste -sd, -)
    [[ -n "$kinds" ]] \
        && ok "  the publish skill's stream query works ($kinds)" \
        || bad "  the publish skill's ffprobe command returned nothing"
    secs=$(ffprobe -v error -show_entries format=duration \
                   -of default=nw=1:nk=1 "$WORK/sample.mkv" 2>/dev/null)
    [[ -n "$secs" ]] \
        && ok "  reads duration (${secs}s of an expected 2)" \
        || bad "  cannot read a duration"
else
    bad "ffprobe not on PATH -- it ships with ffmpeg, so something is wrong"
fi

if command -v mediainfo >/dev/null; then
    ok "mediainfo $(mediainfo --Version 2>/dev/null | tail -1 | awk '{print $NF}')"
    mediainfo --Output=JSON "$WORK/sample.mkv" 2>/dev/null | grep -q '"@type"' \
        && ok "  reports JSON" \
        || warn "  no JSON output -- an older build; the plain report still works"
else
    bad "mediainfo not on PATH"
fi

if command -v mkvmerge >/dev/null; then
    ok "mkvmerge $(mkvmerge --version 2>/dev/null | awk '{print $2}')"
    mkvmerge -J "$WORK/sample.mkv" 2>/dev/null | grep -q '"tracks"' \
        && ok "  reports JSON" \
        || bad "  -J returned nothing usable"
else
    bad "mkvmerge not on PATH"
fi

if command -v mkvpropedit >/dev/null; then
    ok "mkvpropedit $(mkvpropedit --version 2>/dev/null | awk '{print $2}')"
    # The point of this one: give a file an identity that survives a rename.
    if mkvpropedit "$WORK/sample.mkv" \
                   --edit info --set "title=Last Lives (1997)" >/dev/null 2>&1; then
        got=$(mkvmerge -J "$WORK/sample.mkv" 2>/dev/null \
              | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["container"]["properties"].get("title",""))' 2>/dev/null)
        [[ "$got" == "Last Lives (1997)" ]] \
            && ok "  writes a title into the file and it reads back" \
            || bad "  set a title but read back '$got'"
    else
        bad "  cannot set a title"
    fi
else
    bad "mkvpropedit not on PATH"
fi

# Needs a disc in a drive, so this is the one that cannot be proved here.
if command -v lsdvd >/dev/null; then
    ok "lsdvd present (needs a DVD in a drive to do anything; not tested here)"
else
    bad "lsdvd not on PATH"
fi

say "Result"
if [[ $failed -eq 0 ]]; then
    echo "  All of it works. What each one is for, in the order you will want them:"
    echo
    echo "  Which film is this?   ffmpeg -ss 90 -i FILE -frames:v 1 -vf scale=640:-1 card.png"
    echo "                        then look at card.png -- the title card names it."
    echo "  How long is it?       ffprobe -v error -show_entries format=duration \\"
    echo "                            -of default=nw=1:nk=1 FILE"
    echo "  What is in it?        mediainfo FILE"
    echo "  Name the file itself: mkvpropedit FILE --edit info --set title='Film (Year)'"
    echo "  Read the disc:        lsdvd /dev/sr0"
else
    echo "  Some tools are missing or not working -- see the FAIL lines above."
    exit 1
fi
