"""Which audio track plays when nobody chooses one.

A Matroska file carries a default-track flag per track, and a player picks the
flagged one. MakeMKV sets those flags from the disc, which means the language
that plays first is whatever the disc authored first -- English on most
releases, and not on all of them.

This sets the flag so an English audio track is the one that plays. **Nothing
is removed**: every audio track the disc carried is still in the file and a
viewer can still choose any of them. That distinction is the whole reason this
module exists instead of a MakeMKV setting.

**What was tried first, and what it did.** MakeMKV can be made to keep only
preferred-language tracks, with ``app_DefaultSelectionString`` in
``~/.MakeMKV/settings.conf``; see docs/makemkv/track-selection.md. Set to
``-sel:all,+sel:video,+sel:(favlang|nolang|single),-sel:mvcvideo`` with
``app_PreferredLanguage="eng"`` it was measured on Speed Racer disc 1
(Mach GoGoGo, 2026-09-12), whose title 0 carries Japanese audio only -- a
DTS-HD MA track and the DTS core inside it -- with English PGS subtitles. It
wrote **3.85 GB of video with no audio at all**, and MakeMKV reported success.
The ``single`` clause did not save it: two audio tracks exist, so neither is
"the only one of its kind", and both were dropped for not being English.

That is the shape of the failure this module avoids. A flag is reversible, it
cannot lose content, and a file with the wrong track flagged is an annoyance
rather than a silent hole in an archive.

Subtitle and video flags are left exactly as MakeMKV wrote them. Deciding
whether subtitles should come up automatically is a separate question with a
different answer per disc, and guessing it here would put captions over English
dialogue on every DVD that carries them.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Track", "Change", "TrackError", "ENGLISH", "read_tracks", "plan",
           "argv_for", "apply_to"]

#: Matroska writes ISO 639-2/B. A track with no language element means English
#: by specification, and mkvmerge reports "eng" for it rather than "und", so
#: an untagged track needs no special case here.
ENGLISH = ("eng", "en")

AUDIO = "audio"


class TrackError(Exception):
    pass


@dataclass(frozen=True)
class Track:
    """One track, as Matroska addresses it.

    ``number`` is the Matroska TrackNumber, which is what ``mkvpropedit
    --edit track:@N`` selects. It is not the ffprobe stream index and not
    mkvmerge's ``id``; using either of those to address a track edits the
    wrong one on any file whose numbering is not 1:1.
    """

    number: int
    kind: str
    language: str
    default: bool
    forced: bool
    name: str = ""
    channels: int = 0

    @property
    def is_english(self) -> bool:
        return self.language.lower() in ENGLISH


@dataclass(frozen=True)
class Change:
    """One flag to write, and why -- the reason goes in the run's report."""

    number: int
    flag: str
    value: bool
    why: str


def read_tracks(path, run=subprocess.run) -> list[Track]:
    """The file's tracks, from ``mkvmerge -J``.

    mkvmerge rather than ffprobe because the flags being edited are Matroska's
    own and mkvmerge is the tool that writes them; its idea of which track is
    which is the one mkvpropedit shares.
    """
    proc = run(["mkvmerge", "-J", str(path)], capture_output=True, text=True)
    if proc.returncode not in (0, 1):          # 1 is "warnings", still parseable
        raise TrackError(f"mkvmerge failed on {path}: "
                         f"{(proc.stderr or proc.stdout).strip()[:200]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TrackError(f"mkvmerge gave no JSON for {path}: {exc}") from exc

    out = []
    for track in data.get("tracks", []):
        p = track.get("properties", {})
        out.append(Track(number=int(p.get("number", 0)),
                         kind=track.get("type", ""),
                         language=p.get("language", "eng"),
                         default=bool(p.get("default_track")),
                         forced=bool(p.get("forced_track")),
                         name=p.get("track_name", "") or "",
                         channels=int(p.get("audio_channels") or 0)))
    if not out:
        raise TrackError(f"{path} reports no tracks")
    return out


def plan(tracks) -> list[Change]:
    """The flag changes that make an English audio track the default one. Pure.

    Minimal intervention, in this order:

    * **An English audio track is already the default: change nothing.** The
      disc said which mix is the main one and it is English, so there is
      nothing to improve and no reason to touch the file.
    * **Otherwise the first English audio track becomes the default**, in track
      order, and the flag is cleared on the other audio tracks. First rather
      than "best": the disc's order is the authoring order, and picking by
      channel count would promote a later stereo remix over an original mono
      mix. Challenge of the Super Friends carries exactly that pair.
    * **No English audio: nothing changes.** Speed Racer's Japanese-only
      titles keep whatever MakeMKV wrote. There is no English track to prefer,
      and inventing a preference is how the selection-string attempt ended up
      writing silence.

    A forced track is never touched. On a file with English audio a forced
    subtitle track carries the translations of on-screen foreign text, and its
    flags are the author's business.
    """
    audio = [t for t in tracks if t.kind == AUDIO and not t.forced]
    english = [t for t in audio if t.is_english]
    if not english:
        return []
    if any(t.default for t in english):
        return []

    wanted = english[0]
    changes = [Change(wanted.number, "flag-default", True,
                      f"first English audio track ({wanted.language}"
                      f"{', ' + wanted.name if wanted.name else ''})")]
    for t in audio:
        if t.number != wanted.number and t.default:
            changes.append(Change(t.number, "flag-default", False,
                                  f"was the default and is {t.language}"))
    return changes


def argv_for(path, changes) -> list[str]:
    """The mkvpropedit command for those changes. Pure.

    ``--edit track:@N`` selects by Matroska TrackNumber. This rewrites a few
    bytes of the header in place: no re-encode, no re-mux, seconds per file
    whatever its size.
    """
    argv = ["mkvpropedit", str(path)]
    for change in changes:
        argv += ["--edit", f"track:@{change.number}",
                 "--set", f"{change.flag}={1 if change.value else 0}"]
    return argv


def apply_to(path, run=subprocess.run) -> list[Change]:
    """Set the flags on one file. Returns what was changed, empty if nothing.

    Raises rather than returning a failure: a file whose flags could not be
    written is worth a line in the run's report, and the caller decides
    whether that fails the disc. It does not: the copy is still correct, the
    wrong track just plays first.
    """
    changes = plan(read_tracks(path, run=run))
    if not changes:
        return []
    proc = run(argv_for(path, changes), capture_output=True, text=True)
    if proc.returncode != 0:
        raise TrackError(f"mkvpropedit failed on {path}: "
                         f"{(proc.stderr or proc.stdout).strip()[:200]}")
    return changes


def _main(argv) -> int:
    """``python3 -m media_backup.tracks FILE...`` -- for files already ripped."""
    if not argv:
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python3 -m media_backup.tracks FILE.mkv [FILE.mkv ...]")
        print("       --dry-run   say what would change and write nothing")
        return 2
    dry = "--dry-run" in argv
    paths = [Path(a) for a in argv if not a.startswith("-")]
    touched = 0
    for path in paths:
        try:
            if dry:
                changes = plan(read_tracks(path))
            else:
                changes = apply_to(path)
        except TrackError as exc:
            print(f"  FAIL  {path.name}: {exc}")
            continue
        if not changes:
            print(f"  ok    {path.name}: English audio already the default")
            continue
        touched += 1
        verb = "would set" if dry else "set"
        for change in changes:
            print(f"  {verb}  {path.name}: track {change.number} "
                  f"{change.flag}={int(change.value)} -- {change.why}")
    print(f"\n{touched} of {len(paths)} file(s) "
          f"{'would be' if dry else ''} changed")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
