"""Capturing what an obfuscated disc knows, so a pattern can be found later.

A disc that hides its feature among decoy playlists is resolved today by hand:
find someone else's published clip map, match it against the scan, rip that one
title (docs/makemkv/playlist-obfuscation.md). That does not scale and it does
not accumulate.

What would scale is knowing *how the disc itself decides*. A real player is
told which playlist to run, by navigation commands in ``MovieObject.bdmv`` or
by the BD-J application in ``BDMV/JAR``. That answer is on the disc, in the
clear, and it is small: 21 MB of navigation data beside 49 GB of streams,
measured on Knives Out 2026-09-11. None of it needs decrypting -- AACS covers
``BDMV/STREAM`` and nothing else.

So this captures that, plus the scan MakeMKV produced, plus -- once a human has
worked out which playlist was real -- the answer. One disc proves nothing. A
dozen, each with its navigation data and its confirmed answer beside it, is a
corpus you can test a rule against before trusting it.

**What was already tried, so the next person does not repeat it.** Measured on
Knives Out, 2026-09-11:

* The JARs hold no ``#####.mpls`` string. 880 classes across two 4 MB archives,
  zero literal playlist filenames. BD-J addresses playlists numerically.
* ``MovieObject.bdmv`` is 45 KB but yielded no ``PlayPL``-shaped command whose
  operand matched a playlist on the disc. Either the parse is wrong or this
  disc drives playback from BD-J, which ``index.bdmv`` would settle.
* Five-digit ASCII runs in the BDJO files look promising and are a trap:
  ``00004.bdjo`` contains ``00006``, and ``00006`` is both a playlist *and*
  ``/BDMV/JAR/00006.jar``. Playlist ids, JAR ids and application ids share one
  five-digit namespace, so any scan that does not parse the structure it is
  reading will find coincidences.

None of that is a dead end; all of it is a reason to collect discs before
writing a detector. The capture is deliberately dumb and complete.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import model
from .config import Config
from .makemkv import command, isolation, selection
from .makemkv.records import (ATTR_CHAPTER_COUNT, ATTR_DURATION, ATTR_NAME,
                              ATTR_SEGMENTS_MAP, ATTR_SIZE_BYTES,
                              ATTR_SOURCE_FILE, Tinfo, parse_line)
from .udf import UdfError, UdfImage

#: Everything under here is the encrypted payload and is never captured.
STREAM_DIR = "/BDMV/STREAM"

#: Captured whole, whatever their size: this is the navigation logic.
CODE_SUFFIXES = {".bdmv", ".bdjo", ".mpls", ".clpi", ".jar", ".class",
                 ".perm", ".xml", ".properties", ".otf", ".ttf"}

#: Presentation assets. Recorded in the manifest, copied only on request --
#: the exploded BD-J application directory on Knives Out is 55 MB of these
#: against 21 MB of everything else.
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".pcm", ".mp3",
                  ".wav", ".ac3", ".m2ts", ".bin", ".dat"}

#: A file bigger than this is recorded but not copied unless it is code.
DEFAULT_FILE_CAP = 32 * 1024 * 1024


@dataclass
class CapturedFile:
    path: str
    size: int
    sha256: str = ""
    copied: bool = False
    note: str = ""


@dataclass
class Capture:
    """One disc's forensic record."""

    device: str
    volume_id: str
    captured_at: str = field(default_factory=model.now)
    fingerprint: str = ""
    bytes_copied: int = 0
    files: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["files"] = [asdict(f) if not isinstance(f, dict) else f
                      for f in self.files]
        return d



def titles_from_scan(text: str) -> list[model.Title]:
    """Parse a saved ``makemkvcon info`` transcript into titles.

    The runner has its own copy of this mapping because it reads a live
    process and fills in stream counts as it goes. This one reads a file that
    is already on disk, and only needs the fields the obfuscation analysis
    looks at. ``tests.test_forensics`` pins the two against one transcript so
    they cannot drift apart unnoticed.
    """
    found: dict[int, model.Title] = {}
    for line in text.splitlines():
        record = parse_line(line)
        if not isinstance(record, Tinfo):
            continue
        title = found.setdefault(record.title, model.Title(index=record.title))
        if record.id == ATTR_NAME:
            title.name = record.value
        elif record.id == ATTR_DURATION:
            title.duration = record.value
        elif record.id == ATTR_SIZE_BYTES:
            try:
                title.size_bytes = int(record.value)
            except ValueError:
                pass
        elif record.id == ATTR_SOURCE_FILE:
            title.source = record.value
        elif record.id == ATTR_SEGMENTS_MAP:
            title.segments = record.value
        elif record.id == ATTR_CHAPTER_COUNT:
            try:
                title.chapters = int(record.value)
            except ValueError:
                pass
    return [found[k] for k in sorted(found)]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(img: UdfImage) -> str:
    """A stable id for this *pressing*, not this copy of it.

    Two discs of the same release give the same value; a different region or a
    rental variant gives a different one, which is the distinction that
    matters -- published clip maps are per pressing, and Power Rangers' retail
    and Redbox maps share no segments at all.
    """
    parts = [b"INDX", b"MOBJ"]
    for path in ("/BDMV/index.bdmv", "/BDMV/MovieObject.bdmv"):
        try:
            parts.append(img.read(path))
        except UdfError:
            parts.append(b"")
    names = sorted(e.name for e in img.listdir("/BDMV/PLAYLIST"))
    parts.append("\n".join(names).encode())
    return _sha(b"\0".join(parts))[:16]


def _should_copy(path: str, size: int, cap: int, assets: bool) -> tuple[bool, str]:
    suffix = os.path.splitext(path)[1].lower()
    if path.startswith(STREAM_DIR + "/"):
        return False, "stream payload"
    if suffix in CODE_SUFFIXES:
        return True, ""
    if suffix in ASSET_SUFFIXES and not assets:
        return False, "presentation asset"
    if size > cap:
        return False, f"over the {cap} byte cap"
    return True, ""


def capture_disc(device: str, out_root: Path, *, assets: bool = False,
                 cap: int = DEFAULT_FILE_CAP) -> Path:
    """Copy a disc's navigation data into a new capture directory."""
    with UdfImage(device) as img:
        print_ = print
        tty = sys.stdout.isatty()
        fp = fingerprint(img)
        label = re.sub(r"[^A-Za-z0-9_.-]", "_", img.volume_id or "UNKNOWN")
        out = out_root / f"{label}-{fp}"
        out.mkdir(parents=True, exist_ok=True)

        record = Capture(device=device, volume_id=img.volume_id, fingerprint=fp)
        for entry in img.walk("/"):
            copy, note = _should_copy(entry.path, entry.size, cap, assets)
            item = CapturedFile(entry.path, entry.size, note=note)
            if copy:
                try:
                    data = img.read(entry.path)
                except UdfError as exc:
                    item.note = f"unreadable: {exc}"
                else:
                    item.sha256 = _sha(data)
                    item.copied = True
                    dest = out / "disc" / entry.path.lstrip("/")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    record.bytes_copied += len(data)
            record.files.append(item)
            if tty:
                print_(f"\r  {len(record.files):>5} files, "
                       f"{record.bytes_copied / 1e6:>7.1f} MB copied",
                       end="", flush=True)
        if tty:
            print_()
        print_(f"  {len(record.files)} files, "
               f"{record.bytes_copied / 1e6:.1f} MB copied")

    (out / "capture.json").write_text(json.dumps(record.to_dict(), indent=2))
    _index_jars(out)
    return out


def _index_jars(out: Path) -> None:
    """List what is inside each captured JAR, without unpacking it.

    The archives stay verbatim -- they are the evidence. This is the index you
    read first: 880 classes across two archives on Knives Out, and knowing
    which are the disc author's and which are a shipped framework is most of
    the triage.
    """
    # By magic, not by name. Knives Out keeps a second copy of its
    # application at /BDMV/JAR/03000/809ad4ac00000 -- no extension, 707
    # classes -- and a *.jar glob walks straight past it.
    jars = sorted(f for f in (out / "disc").rglob("*")
                  if f.is_file() and f.read_bytes()[:4] == b"PK\x03\x04")
    index = {}
    for jar in jars:
        try:
            with zipfile.ZipFile(jar) as z:
                entries = []
                for info in z.infolist():
                    blob = z.read(info.filename)
                    entries.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "sha256": _sha(blob),
                    })
        except zipfile.BadZipFile as exc:
            index[str(jar.relative_to(out))] = {"error": str(exc)}
            continue
        classes = [e["name"] for e in entries if e["name"].endswith(".class")]
        packages = sorted({c.rsplit("/", 1)[0] for c in classes if "/" in c})
        # Short meaningless class names mean the application was put through
        # an obfuscator before pressing. Knives Out ships 707 classes called
        # a, aa, ab... Worth knowing before anyone plans to read it: the
        # names carry nothing and the structure is all there is.
        stems = [c.rsplit("/", 1)[-1][:-6] for c in classes]
        short = sum(1 for st in stems if len(st) <= 3)
        index[str(jar.relative_to(out))] = {
            "entries": len(entries),
            "classes": len(classes),
            "packages": packages,
            "name_obfuscated": bool(stems) and short / len(stems) > 0.5,
            "short_name_ratio": round(short / len(stems), 3) if stems else 0.0,
            "files": entries,
        }
    (out / "jars.json").write_text(json.dumps(index, indent=2))


def scan_disc(device: str, out: Path, cfg: Config | None = None) -> Path:
    """Run ``makemkvcon info`` and keep the raw output beside the capture.

    Kept raw and whole. ``collection.json`` records a parsed title inventory
    but not this, so whether MakeMKV resolved an ``FPL_MainFeature`` -- the
    engine's own answer to the question this whole exercise is about -- has
    been leaving no trace.
    """
    cfg = cfg or Config()
    argv = command.info_argv(cfg, 0, device)
    (out / "scan").mkdir(exist_ok=True)
    raw = out / "scan" / "info.txt"
    with raw.open("w", errors="replace") as handle:
        handle.write("# " + " ".join(argv) + "\n")
        handle.flush()
        subprocess.run(argv, stdout=handle, stderr=subprocess.STDOUT, check=False)
    return raw


def analyse(out: Path) -> dict:
    """What the scan and the disc say about the obfuscation, side by side."""
    raw = out / "scan" / "info.txt"
    titles = titles_from_scan(raw.read_text(errors="replace")) if raw.exists() else []

    capture = json.loads((out / "capture.json").read_text())
    on_disc = [f["path"] for f in capture["files"]
               if f["path"].startswith("/BDMV/PLAYLIST/")
               and f["path"].endswith(".mpls")]

    found = selection.obfuscation(titles) if titles else None
    classes = selection.permutation_classes(titles) if titles else {}
    result = {
        "playlists_on_disc": len(on_disc),
        "titles_from_scan": len(titles),
        "permutation_classes": len(classes),
        "largest_pool": 0,
        "pool_clip_count": 0,
        "pool_duration": "",
        "obfuscated": bool(found),
        "fpl_main_feature": any("FPL_MainFeature" in (t.name or "")
                                for t in titles),
        "pool_members": [],
    }
    if found:
        members, orderings = found
        result["largest_pool"] = orderings
        result["pool_clip_count"] = len(selection.segments(members[0]))
        result["pool_duration"] = members[0].duration
        result["pool_members"] = [
            {"index": t.index, "source": t.source, "segments": t.segments}
            for t in sorted(members, key=lambda t: t.index)]
    outside = [t for t in titles
               if not found or t not in found[0]]
    result["titles_outside_the_pool"] = [
        {"index": t.index, "source": t.source, "duration": t.duration,
         "size_bytes": t.size_bytes, "segments": t.segments}
        for t in sorted(outside, key=lambda t: t.index)]
    (out / "analysis.json").write_text(json.dumps(result, indent=2))
    return result


def record_truth(out: Path, playlist: str, how: str, evidence: str,
                 segments_map: str = "") -> dict:
    """Write down which playlist turned out to be the film, and why we believe it.

    This is the half that cannot be automated and the half the corpus is
    worthless without. A hundred captured discs with no confirmed answers
    teach nothing; a dozen with answers can be tested against.

    ``how`` should say where the answer came from -- a forum clip map, the
    engine's own ``FPL_MainFeature``, or watching it -- because the answers
    are not equally strong and a rule trained on the weak ones is a rule that
    will lose a film.
    """
    truth = {
        "playlist": playlist,
        "segments": segments_map,
        "how": how,
        "evidence": evidence,
        "recorded_at": model.now(),
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=2))
    return truth


def summarise(root: Path) -> list[dict]:
    """One row per captured disc, for looking across them."""
    rows = []
    for analysis in sorted(root.glob("*/analysis.json")):
        out = analysis.parent
        data = json.loads(analysis.read_text())
        capture = json.loads((out / "capture.json").read_text())
        truth_path = out / "truth.json"
        truth = json.loads(truth_path.read_text()) if truth_path.exists() else {}
        rows.append({
            "disc": out.name,
            "volume_id": capture.get("volume_id", ""),
            "playlists": data.get("playlists_on_disc", 0),
            "titles": data.get("titles_from_scan", 0),
            "pool": data.get("largest_pool", 0),
            "clips": data.get("pool_clip_count", 0),
            "fpl": data.get("fpl_main_feature", False),
            "answer": truth.get("playlist", ""),
            "how": truth.get("how", ""),
        })
    return rows


def drive_is_busy(device: str) -> bool:
    """True if some process already has this drive's SCSI generic node open.

    Reading the disc competes with a running job for the same head. Worth one
    check: a capture that thrashes a four-hour rip is a poor trade for 21 MB.
    """
    sg = isolation.sg_name(device)
    if not sg:
        return False
    target = f"/dev/{sg}"
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                if os.readlink(fd) == target:
                    return True
        except (OSError, PermissionError):
            continue
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m media_backup.forensics",
        description="Capture an obfuscated disc's navigation data for study.")
    parser.add_argument("--root", type=Path, default=None,
                        help="where captures are kept "
                             "(default: the config's forensics_dir)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="copy one disc's navigation data")
    cap.add_argument("device")
    cap.add_argument("--assets", action="store_true",
                     help="also copy images, fonts and audio")
    cap.add_argument("--no-scan", action="store_true",
                     help="skip the makemkvcon info run")
    cap.add_argument("--force", action="store_true",
                     help="capture even if the drive looks busy")

    tru = sub.add_parser("truth", help="record which playlist was the film")
    tru.add_argument("capture_dir", type=Path)
    tru.add_argument("--playlist", required=True, help="e.g. 00988.mpls")
    tru.add_argument("--how", required=True,
                     help="forum-map | fpl-main-feature | watched | other")
    tru.add_argument("--evidence", required=True)
    tru.add_argument("--segments", default="")

    sub.add_parser("summary", help="one row per captured disc")

    args = parser.parse_args(argv)
    if args.root is None:
        args.root = Config().forensics_dir

    if args.cmd == "capture":
        if not args.force and drive_is_busy(args.device):
            print(f"{args.device} is in use by another process. A capture "
                  f"would make both slower. Use --force to do it anyway.",
                  file=sys.stderr)
            return 2
        try:
            out = capture_disc(args.device, args.root, assets=args.assets)
        except UdfError as exc:
            print(f"cannot read {args.device}: {exc}", file=sys.stderr)
            return 1
        print(f"captured to {out}")
        if not args.no_scan:
            scan_disc(args.device, out)
        result = analyse(out)
        print(f"  playlists on disc     {result['playlists_on_disc']}")
        print(f"  titles from the scan  {result['titles_from_scan']}")
        print(f"  largest decoy pool    {result['largest_pool']} "
              f"orderings of {result['pool_clip_count']} clips")
        print(f"  MakeMKV resolved it   {result['fpl_main_feature']}")
        if not (out / "truth.json").exists():
            print("\nNo answer recorded yet. Once the real playlist is known:")
            print(f"  python3 -m media_backup.forensics truth {out} \\")
            print("      --playlist 00988.mpls --how forum-map --evidence '...'")
        return 0

    if args.cmd == "truth":
        truth = record_truth(args.capture_dir, args.playlist, args.how,
                             args.evidence, args.segments)
        print(json.dumps(truth, indent=2))
        return 0

    rows = summarise(args.root)
    if not rows:
        print(f"no captures under {args.root}")
        return 0
    head = f"{'disc':<34} {'plists':>6} {'titles':>6} {'pool':>5} {'clips':>5} {'fpl':>5}  answer"
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['disc'][:34]:<34} {r['playlists']:>6} {r['titles']:>6} "
              f"{r['pool']:>5} {r['clips']:>5} {str(r['fpl']):>5}  "
              f"{r['answer'] or '-'} {('(' + r['how'] + ')') if r['how'] else ''}")
    answered = sum(1 for r in rows if r["answer"])
    print(f"\n{answered} of {len(rows)} captures have a confirmed answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
