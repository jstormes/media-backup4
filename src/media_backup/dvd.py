"""Reading a DVD's own title tables, because MakeMKV's are not enough.

On a Blu-ray, MakeMKV reports a title's clip list as global stream ids --
``510,505,513`` names three files in ``BDMV/STREAM`` -- so two titles naming
clip 510 demonstrably share content. That identity is what
:func:`makemkv.selection.classify` reasons over.

On a DVD it reports a cell range **local to its own title**. Every title
starts at 1, so four titles all reporting ``1`` say nothing about whether they
overlap. ``selection.segments`` documents this and the classification is
simply blind there.

The disc knows better. Its IFO files carry VOB and cell ids that are global to
the disc, and they are the DVD's exact equivalent of a Blu-ray clip list.
Reading them gives the classification the same footing on both formats.

**Why this is worth the parsing.** Measured on "Challenge of the Superfriends"
disc 1, 2026-09-12: the disc declares thirteen program chains, seven of which
are 21-minute episodes on their own VOBs. MakeMKV offered **four** titles. It
logged a reason for skipping the four short menus and no reason at all for the
missing episodes -- they simply never appeared. Four of the seven differ in
runtime by under two seconds from ones it kept.

Nothing here is decrypted and nothing needs to be: CSS scrambles the payload
in the VOB files, while ``VIDEO_TS.IFO`` and ``VTS_nn_0.IFO`` are plaintext.
The same is true of a Blu-ray's navigation data under AACS, which is what
makes the forensics capture cheap.

The layout is the DVD-Video specification's; the offsets below are the ones
this module depends on, named so a reader can check them against it. One trap
worth the comment: ``+0xE8`` is the cell *playback* table and ``+0xEA`` the
cell *position* table. Reading position data at 0xE8 yields plausible-looking
rubbish -- every cell comes back as VOB 512 -- rather than an error.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = ["DvdError", "Cell", "ProgramChain", "read_titles", "clip_list",
           "missing_from_scan"]

SECTOR = 2048

#: Where the ISO 9660 primary volume descriptor lives, by specification.
PVD_SECTOR = 16

#: Offsets into VIDEO_TS.IFO and VTS_nn_0.IFO that this module reads.
VMG_TT_SRPT = 0xC4        # title search pointer table, sector, big-endian
VTS_PGCIT = 0xCC          # program chain information table, sector
PGC_CELL_PLAYBACK = 0xE8  # NOT what we want -- see the module docstring
PGC_CELL_POSITION = 0xEA  # what we want


class DvdError(Exception):
    pass


@dataclass(frozen=True)
class Cell:
    """One cell, addressed the way the whole disc addresses it."""

    vob: int
    cell: int

    def __str__(self) -> str:
        return f"{self.vob}/{self.cell}"


@dataclass
class ProgramChain:
    """One PGC: what a DVD offers as a playable thing."""

    number: int
    seconds: int
    programs: int
    cells: list = field(default_factory=list)

    @property
    def clips(self) -> frozenset:
        return frozenset(self.cells)


def _bcd(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0xF)


class _Reader:
    """Sector reads against an open device, so the parsing stays testable."""

    def __init__(self, device: str) -> None:
        self.device = device

    def sectors(self, first: int, count: int = 1) -> bytes:
        try:
            with open(self.device, "rb") as handle:
                handle.seek(first * SECTOR)
                data = handle.read(count * SECTOR)
        except OSError as exc:
            raise DvdError(f"cannot read {self.device}: {exc}") from exc
        if len(data) < count * SECTOR:
            raise DvdError(f"short read at sector {first} of {self.device}")
        return data


def _iso_dir(read, extent: int, size: int) -> list:
    """One ISO 9660 directory as (name, extent, size, is_dir)."""
    data = read.sectors(extent, (size + SECTOR - 1) // SECTOR)
    out, i = [], 0
    while i < size:
        length = data[i]
        if length == 0:                      # padding to the next sector
            i = ((i // SECTOR) + 1) * SECTOR
            continue
        record = data[i:i + length]
        name_len = record[32]
        name = record[33:33 + name_len].decode("latin1")
        if name not in ("\x00", "\x01"):     # . and ..
            out.append((name.split(";")[0].upper(),
                        struct.unpack("<I", record[2:6])[0],
                        struct.unpack("<I", record[10:14])[0],
                        bool(record[25] & 2)))
        i += length
    return out


def _video_ts(read) -> dict:
    """The VIDEO_TS directory, as name -> (extent, size)."""
    pvd = read.sectors(PVD_SECTOR)
    if pvd[1:6] != b"CD001":
        raise DvdError(f"{read.device} is not an ISO 9660 disc "
                       "(an empty drive and a disc still spinning up look "
                       "like this too)")
    root = pvd[156:190]
    entries = _iso_dir(read, struct.unpack("<I", root[2:6])[0],
                       struct.unpack("<I", root[10:14])[0])
    for name, extent, size, is_dir in entries:
        if is_dir and name.startswith("VIDEO_TS"):
            return {n: (e, s) for n, e, s, _ in _iso_dir(read, extent, size)}
    raise DvdError(f"{read.device} has no VIDEO_TS directory; "
                   "it is not a DVD-Video disc")


def _program_chains(read, extent: int, size: int) -> list:
    """Every PGC in one VTS_nn_0.IFO, with its duration and its cells."""
    ifo = read.sectors(extent, (size + SECTOR - 1) // SECTOR)
    if not ifo.startswith(b"DVDVIDEO-VTS"):
        raise DvdError("not a VTS IFO")
    base = struct.unpack(">I", ifo[VTS_PGCIT:VTS_PGCIT + 4])[0] * SECTOR
    count = struct.unpack(">H", ifo[base:base + 2])[0]
    chains = []
    for i in range(count):
        entry = base + 8 + i * 8
        offset = struct.unpack(">I", ifo[entry + 4:entry + 8])[0]
        pgc = base + offset
        programs, cell_count = ifo[pgc + 2], ifo[pgc + 3]
        seconds = (_bcd(ifo[pgc + 4]) * 3600
                   + _bcd(ifo[pgc + 5]) * 60
                   + _bcd(ifo[pgc + 6]))
        position = struct.unpack(">H", ifo[pgc + PGC_CELL_POSITION:
                                           pgc + PGC_CELL_POSITION + 2])[0]
        cells = []
        for c in range(cell_count):
            at = pgc + position + c * 4
            cells.append(Cell(struct.unpack(">H", ifo[at:at + 2])[0], ifo[at + 3]))
        chains.append(ProgramChain(i + 1, seconds, programs, cells))
    return chains


def read_titles(device: str) -> list:
    """Every program chain the disc declares, across every title set.

    Cells are numbered per title set, so a disc with more than one is
    disambiguated by prefixing the VOB id -- two title sets both holding a
    "VOB 2" must not read as shared content.
    """
    read = _Reader(device)
    files = _video_ts(read)
    chains = []
    for name in sorted(files):
        if not (name.startswith("VTS_") and name.endswith("_0.IFO")):
            continue
        try:
            vts = int(name[4:6])
        except ValueError:
            continue
        extent, size = files[name]
        for chain in _program_chains(read, extent, size):
            chain.cells = [Cell(vts * 1000 + c.vob, c.cell) for c in chain.cells]
            chain.number = vts * 100 + chain.number
            chains.append(chain)
    if not chains:
        raise DvdError(f"{device} declares no program chains")
    return chains


def clip_list(chain: ProgramChain) -> str:
    """The chain's cells in the spelling ``model.Title.segments`` uses.

    So a DVD chain can be handed to ``selection.classify`` exactly as a
    Blu-ray title is, and the same reasoning applies to both.
    """
    return ",".join(str(c) for c in chain.cells)


def missing_from_scan(chains, scanned, kind: str = "", min_seconds: int = 120,
                      tolerance: int = 3) -> list:
    """Disc content that the scan never offered. Pure.

    The check the Superfriends disc earned. MakeMKV enumerated four titles
    from a disc carrying seven episodes, logged a reason for skipping the four
    short menus and none at all for the missing episodes. Four of the seven
    differ in runtime by under two seconds from ones it kept, which is the
    only signal the scan has on a DVD -- there is no global clip identity in
    what it reports.

    So the comparison is on duration, and it is deliberately one-directional:
    a disc chain with no scanned title to account for it is reported. The
    reverse -- a scanned title with no chain -- is not, because MakeMKV
    legitimately presents things the PGC table does not enumerate that way.

    Only chains :func:`selection.classify` calls content are considered, so a
    play-all, an alternate-audio duplicate, a one-clip-repeated filler and the
    disc's menus are all expected to be absent and none of them raises this.

    ``kind`` is the collection's, passed through to
    :func:`selection.classify` -- on a season disc it is what makes the disc's
    play-all read as a play-all rather than as content to go looking for.

    ``min_seconds`` mirrors MakeMKV's own minimum title length: content below
    it was never going to be offered and its absence means nothing.

    **The risk is a false positive**, which stops a disc that was fine. That
    is why the tolerance is generous and why only content counts. A disc
    stopped wrongly costs an operator a minute; a season published three
    episodes short is discovered years later.
    """
    from . import model
    from .makemkv import selection

    titles = []
    for chain in chains:
        titles.append(model.Title(index=chain.number,
                                  duration=_clock(chain.seconds),
                                  segments=clip_list(chain),
                                  size_bytes=chain.seconds * 3_000_000))
    # Cells read from the IFO are addressed by the whole disc -- that is the
    # entire point of this module -- so the classification is told so rather
    # than left to infer it from a spelling it has never seen.
    verdict = selection.classify(titles, kind, clips_global=True)

    expected = [c for c in chains
                if verdict.get(c.number) == selection.CONTENT
                and c.seconds >= min_seconds]

    unclaimed = sorted(t.seconds for t in scanned if t.seconds > 0)
    missing = []
    for chain in sorted(expected, key=lambda c: -c.seconds):
        hit = next((s for s in unclaimed if abs(s - chain.seconds) <= tolerance), None)
        if hit is None:
            missing.append(chain)
        else:
            unclaimed.remove(hit)
    return sorted(missing, key=lambda c: c.number)


def _clock(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
