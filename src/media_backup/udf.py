"""Reading files off an optical disc without mounting it.

The forensics capture needs a disc's navigation data -- playlists, BD-J
objects, the Java archives -- while the machine is busy and the operator is
not root. Mounting needs root; ``udisks`` automount is deliberately disabled
on ``sr*`` by ``99-media-backup-no-automount.rules`` so a disc is never
mounted under a running ``makemkvcon``. So this reads the UDF filesystem
straight off the block device, which the ``cdrom`` group already permits.

``libudfread`` does the work. It ships with ``libbluray`` and is therefore
already installed wherever MakeMKV's dependencies are.

**None of this needs decryption.** AACS encrypts the stream payload in
``BDMV/STREAM``; ``index.bdmv``, ``MovieObject.bdmv``, every ``.mpls`` and
every ``.jar`` are in the clear. That is what makes a capture cheap -- 76 MB
of navigation data beside 49 GB of streams, measured on Knives Out 2026-09-11.

Reading the disc competes with anything else using that drive. Do not point
this at a drive with a running job.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Iterator

__all__ = ["UdfError", "UdfImage", "Entry"]

#: libudfread's own directory-entry types. Deliberately *not* the values in
#: ``dirent.h`` -- UDF_DT_DIR is 1 here where DT_DIR is 4 there, and reading
#: the header rather than assuming that cost an afternoon on 2026-09-11: a
#: walk keyed on 4 silently found no directories and reported a Blu-ray as ten
#: files.
UDF_DT_UNKNOWN = 0
UDF_DT_DIR = 1
UDF_DT_REG = 2

_LIB = "libudfread.so.3"

#: One read syscall. Larger than the 2048-byte UDF block so a playlist comes
#: back in one call, small enough that a stray huge file cannot balloon.
_CHUNK = 1 << 16


class UdfError(Exception):
    pass


class _Dirent(ctypes.Structure):
    _fields_ = [("d_type", ctypes.c_uint), ("d_name", ctypes.c_char_p)]


@dataclass(frozen=True)
class Entry:
    """One name in a directory."""

    name: str
    path: str
    is_dir: bool
    size: int = 0


def _bind():
    try:
        lib = ctypes.CDLL(_LIB)
    except OSError as exc:
        raise UdfError(
            f"{_LIB} is not available: {exc}. It ships with libbluray; "
            "install libudfread (Debian/Ubuntu: libudfread0).") from exc

    lib.udfread_init.restype = ctypes.c_void_p
    lib.udfread_open.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.udfread_open.restype = ctypes.c_int
    lib.udfread_close.argtypes = [ctypes.c_void_p]
    lib.udfread_opendir.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.udfread_opendir.restype = ctypes.c_void_p
    lib.udfread_readdir.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Dirent)]
    lib.udfread_readdir.restype = ctypes.POINTER(_Dirent)
    lib.udfread_closedir.argtypes = [ctypes.c_void_p]
    lib.udfread_file_open.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.udfread_file_open.restype = ctypes.c_void_p
    lib.udfread_file_size.argtypes = [ctypes.c_void_p]
    lib.udfread_file_size.restype = ctypes.c_int64
    lib.udfread_file_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t]
    lib.udfread_file_read.restype = ctypes.c_ssize_t
    lib.udfread_file_close.argtypes = [ctypes.c_void_p]
    lib.udfread_get_volume_id.argtypes = [ctypes.c_void_p]
    lib.udfread_get_volume_id.restype = ctypes.c_char_p
    return lib


class UdfImage:
    """A mounted-in-name-only view of one disc. Use as a context manager."""

    def __init__(self, device: str) -> None:
        self._lib = _bind()
        self._handle = self._lib.udfread_init()
        if not self._handle:
            raise UdfError("udfread_init failed")
        if self._lib.udfread_open(self._handle, device.encode()) < 0:
            self._lib.udfread_close(self._handle)
            self._handle = None
            raise UdfError(
                f"{device} does not hold a readable UDF filesystem. An empty "
                "drive, a CD, or a disc the drive has not finished loading "
                "all look like this.")
        self.device = device

    def __enter__(self) -> "UdfImage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._handle:
            self._lib.udfread_close(self._handle)
            self._handle = None

    @property
    def volume_id(self) -> str:
        raw = self._lib.udfread_get_volume_id(self._handle)
        return raw.decode(errors="replace") if raw else ""

    def listdir(self, path: str) -> list[Entry]:
        directory = self._lib.udfread_opendir(self._handle, path.encode())
        if not directory:
            return []
        out: list[Entry] = []
        entry = _Dirent()
        try:
            while self._lib.udfread_readdir(directory, ctypes.byref(entry)):
                name = entry.d_name.decode(errors="replace")
                if name in (".", ".."):
                    continue
                full = f"{path.rstrip('/')}/{name}"
                is_dir = entry.d_type == UDF_DT_DIR
                out.append(Entry(name, full, is_dir,
                                 0 if is_dir else self.size(full)))
        finally:
            self._lib.udfread_closedir(directory)
        return sorted(out, key=lambda e: e.name)

    def walk(self, path: str = "/") -> Iterator[Entry]:
        """Every file under ``path``, depth first. Directories are not yielded."""
        for entry in self.listdir(path):
            if entry.is_dir:
                yield from self.walk(entry.path)
            else:
                yield entry

    def size(self, path: str) -> int:
        handle = self._lib.udfread_file_open(self._handle, path.encode())
        if not handle:
            return 0
        try:
            return int(self._lib.udfread_file_size(handle))
        finally:
            self._lib.udfread_file_close(handle)

    def read(self, path: str, limit: int | None = None) -> bytes:
        """The whole file, or its first ``limit`` bytes."""
        handle = self._lib.udfread_file_open(self._handle, path.encode())
        if not handle:
            raise UdfError(f"{path} is not on {self.device}")
        buf = ctypes.create_string_buffer(_CHUNK)
        chunks: list[bytes] = []
        got = 0
        try:
            while limit is None or got < limit:
                want = _CHUNK if limit is None else min(_CHUNK, limit - got)
                n = self._lib.udfread_file_read(handle, buf, want)
                if n <= 0:
                    break
                chunks.append(buf.raw[:n])
                got += n
        finally:
            self._lib.udfread_file_close(handle)
        return b"".join(chunks)
