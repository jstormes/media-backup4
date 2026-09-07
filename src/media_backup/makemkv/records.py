"""Parser for the ``makemkvcon -r`` line protocol.

One record per line, ``PREFIX:field,field,...``, string fields double-quoted.

Two rules make this harder than it looks, and both are load-bearing:

* **Split the prefix on the first colon only.** Device paths and disc titles
  contain colons (``"Spider-Man: Across The Spider-Verse"``).
* **A naive ``split(",")`` corrupts any value containing a comma**
  (``"Episode 3, Part 2"``). The fields are CSV, so :mod:`csv` parses them.

Escaping inside a quoted value is undocumented and no disc encountered so far
has exercised it. ``csv`` configured with ``escapechar="\\\\"`` accepts *both*
plausible conventions -- C-style ``\\"`` and CSV-style ``""`` -- and leaves
every ordinary record byte-identical, so we accept either rather than betting
on one.

**Every function here is total.** An unknown prefix, a wrong field count, a
non-numeric field, or a line truncated by SIGKILL yields :class:`Unknown`
rather than raising. This code runs on the job worker thread, where an
exception would silently freeze a backup with no output.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

__all__ = [
    "Record", "Msg", "Drv", "Prgt", "Prgc", "Prgv", "Tcount",
    "Cinfo", "Tinfo", "Sinfo", "Unknown", "split_fields", "parse_line",
]


def split_fields(payload: str) -> list[str]:
    """Split one record's payload into fields, honouring quotes."""
    try:
        rows = list(csv.reader(
            io.StringIO(payload),
            quotechar='"',
            escapechar="\\",
            skipinitialspace=True,
        ))
    except csv.Error:
        return [payload]
    if not rows:
        return []
    # A value containing a newline would span rows; rejoin defensively.
    fields: list[str] = []
    for row in rows:
        fields.extend(row)
    return fields


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return default


def _is_int(value: str) -> bool:
    try:
        int(value.strip())
    except (ValueError, AttributeError):
        return False
    return True


@dataclass(frozen=True)
class Unknown:
    """A line we do not recognise. Ignore it; do not fail on it.

    New MakeMKV versions add records and attribute ids, and a truncated final
    line is normal when a process is killed mid-write.
    """

    prefix: str
    payload: str


@dataclass(frozen=True)
class Msg:
    """``MSG:code,flags,count,"text","format",params...``

    ``code`` is stable across versions and languages; ``text`` is localised.
    Always switch on the code. See ``docs/makemkv/message-codes.md``.
    """

    code: int
    flags: int
    count: int
    text: str
    fmt: str = ""
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class Drv:
    """``DRV:index,state,flags,unused,"name","disc name","device"``

    ``index`` is the ``disc:N`` index, valid only for the scan that produced
    it. ``device`` is the stable identity. 16 rows are always emitted.
    """

    index: int
    state: int
    flags: int
    unused: int
    name: str
    disc_name: str
    device: str

    #: Drive states, from apdefs.h.
    EMPTY_CLOSED = 0
    EMPTY_OPEN = 1
    INSERTED = 2
    LOADING = 3
    NO_DRIVE = 256
    UNMOUNTING = 257

    @property
    def is_drive(self) -> bool:
        """True if this row is a real drive rather than an empty slot."""
        return self.state != self.NO_DRIVE and bool(self.device)

    @property
    def has_disc(self) -> bool:
        return self.state == self.INSERTED

    @property
    def is_loading(self) -> bool:
        """Transient state right after a tray closes; poll through it."""
        return self.state == self.LOADING


@dataclass(frozen=True)
class Prgt:
    """``PRGT:code,id,"name"`` -- title of the overall operation."""

    code: int
    id: int
    name: str


@dataclass(frozen=True)
class Prgc:
    """``PRGC:code,id,"name"`` -- title of the current sub-operation."""

    code: int
    id: int
    name: str


@dataclass(frozen=True)
class Prgv:
    """``PRGV:current,total,max`` -- the only all-numeric record.

    ``max`` is ``AP_Progress_MaxValue`` (65536), *not* 100. Read it from the
    record rather than hardcoding; treating these as percentages leaves a
    progress bar pinned near zero for an entire job.
    """

    current: int
    total: int
    max: int

    @property
    def step_pct(self) -> float:
        """Progress of the current sub-operation, 0-100."""
        return (self.current * 100.0 / self.max) if self.max else 0.0

    @property
    def total_pct(self) -> float:
        """Progress of the whole job, 0-100."""
        return (self.total * 100.0 / self.max) if self.max else 0.0


@dataclass(frozen=True)
class Tcount:
    """``TCOUNT:n`` -- titles found by a scan. ``0`` means nothing usable."""

    count: int


#: makemkvcon attribute ids (its own ``AP_ItemAttributeId``), shared by the
#: CINFO, TINFO and SINFO records -- the id means the same thing whether it is
#: describing a disc, a title or a stream.
ATTR_TYPE = 1              # "Blu-ray disc", "DVD disc"
ATTR_NAME = 2
ATTR_LANG_CODE = 3
ATTR_CHAPTER_COUNT = 8
ATTR_DURATION = 9
ATTR_SIZE_HUMAN = 10       # "29.3 GB"
ATTR_SIZE_BYTES = 11
ATTR_SOURCE_FILE = 16      # "00001.mpls"; DVDs do not emit this
ATTR_VIDEO_SIZE = 19       # "1920x1080"
ATTR_METADATA_LANG = 28
ATTR_VOLUME_NAME = 32      # the raw volume label, e.g. "DVD_VIDEO"


@dataclass(frozen=True)
class Cinfo:
    """``CINFO:id,code,"value"`` -- a disc-level attribute."""

    id: int
    code: int
    value: str


@dataclass(frozen=True)
class Tinfo:
    """``TINFO:title,id,code,"value"`` -- a title-level attribute."""

    title: int
    id: int
    code: int
    value: str


@dataclass(frozen=True)
class Sinfo:
    """``SINFO:title,stream,id,code,"value"`` -- a stream-level attribute."""

    title: int
    stream: int
    id: int
    code: int
    value: str


Record = Msg | Drv | Prgt | Prgc | Prgv | Tcount | Cinfo | Tinfo | Sinfo | Unknown


def _parse_msg(f: list[str], payload: str) -> Record:
    if len(f) < 4 or not _is_int(f[0]):
        return Unknown("MSG", payload)
    return Msg(
        code=_int(f[0]), flags=_int(f[1]), count=_int(f[2]),
        text=f[3], fmt=f[4] if len(f) > 4 else "",
        params=tuple(f[5:]),
    )


def _parse_drv(f: list[str], payload: str) -> Record:
    if len(f) < 7 or not all(_is_int(x) for x in f[:4]):
        return Unknown("DRV", payload)
    return Drv(
        index=_int(f[0]), state=_int(f[1]), flags=_int(f[2]), unused=_int(f[3]),
        name=f[4], disc_name=f[5], device=f[6],
    )


def _parse_prg(cls, f: list[str], prefix: str, payload: str) -> Record:
    if len(f) < 3 or not _is_int(f[0]) or not _is_int(f[1]):
        return Unknown(prefix, payload)
    return cls(code=_int(f[0]), id=_int(f[1]), name=f[2])


def _parse_prgv(f: list[str], payload: str) -> Record:
    if len(f) < 3 or not all(_is_int(x) for x in f[:3]):
        return Unknown("PRGV", payload)
    return Prgv(current=_int(f[0]), total=_int(f[1]), max=_int(f[2]))


def _parse_tcount(f: list[str], payload: str) -> Record:
    if len(f) < 1 or not _is_int(f[0]):
        return Unknown("TCOUNT", payload)
    return Tcount(count=_int(f[0]))


def _parse_cinfo(f: list[str], payload: str) -> Record:
    if len(f) < 3 or not all(_is_int(x) for x in f[:2]):
        return Unknown("CINFO", payload)
    return Cinfo(id=_int(f[0]), code=_int(f[1]), value=f[2])


def _parse_tinfo(f: list[str], payload: str) -> Record:
    if len(f) < 4 or not all(_is_int(x) for x in f[:3]):
        return Unknown("TINFO", payload)
    return Tinfo(title=_int(f[0]), id=_int(f[1]), code=_int(f[2]), value=f[3])


def _parse_sinfo(f: list[str], payload: str) -> Record:
    if len(f) < 5 or not all(_is_int(x) for x in f[:4]):
        return Unknown("SINFO", payload)
    return Sinfo(title=_int(f[0]), stream=_int(f[1]), id=_int(f[2]),
                 code=_int(f[3]), value=f[4])


def parse_line(line: str) -> Record | None:
    """Parse one line of robot-mode output.

    Returns ``None`` only for a blank line. Anything unrecognised, malformed
    or truncated comes back as :class:`Unknown` -- this never raises.
    """
    try:
        stripped = line.strip("\r\n").strip()
        if not stripped:
            return None

        prefix, sep, payload = stripped.partition(":")
        if not sep:
            return Unknown("", stripped)

        fields = split_fields(payload)

        if prefix == "MSG":
            return _parse_msg(fields, payload)
        if prefix == "DRV":
            return _parse_drv(fields, payload)
        if prefix == "PRGT":
            return _parse_prg(Prgt, fields, "PRGT", payload)
        if prefix == "PRGC":
            return _parse_prg(Prgc, fields, "PRGC", payload)
        if prefix == "PRGV":
            return _parse_prgv(fields, payload)
        if prefix == "TCOUNT":
            return _parse_tcount(fields, payload)
        if prefix == "CINFO":
            return _parse_cinfo(fields, payload)
        if prefix == "TINFO":
            return _parse_tinfo(fields, payload)
        if prefix == "SINFO":
            return _parse_sinfo(fields, payload)
        return Unknown(prefix, payload)
    except Exception:  # noqa: BLE001 -- totality is the contract
        return Unknown("", line.strip())
