"""Tests for reading a saved file's own account of itself.

The runner judges completeness on duration because size lies: MakeMKV reports
a title's size on the disc and the remux comes in under it, by 16% on a
Blu-ray. Duration is measured against a figure the disc gave, and neither end
of that comparison moves with container overhead.

The parser is checked against files built here byte by byte, and was checked
against ffprobe on the real 20 GB Hancock output: 5533.568 and 6134.368
seconds, agreeing exactly.
"""

import struct
import tempfile
import unittest
from pathlib import Path

from media_backup import mkv

from .mkv_fixtures import (DURATION, INFO, SCALE_NS, SEGMENT, TIMESTAMP_SCALE,
                           _element, matroska_header, write_mkv)


class MkvTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class TestReadingDuration(MkvTestCase):
    def test_a_real_length(self):
        """Hancock's theatrical cut, as measured off the disc."""
        path = write_mkv(self.root / "a.mkv", 5533.568, 4096)
        self.assertAlmostEqual(mkv.duration_seconds(path), 5533.568, places=3)

    def test_the_length_does_not_depend_on_the_file_size(self):
        small = write_mkv(self.root / "s.mkv", 6134.368, 512)
        big = write_mkv(self.root / "b.mkv", 6134.368, 50_000_000)
        self.assertEqual(mkv.duration_seconds(small), mkv.duration_seconds(big))

    def test_a_non_default_timestamp_scale_is_honoured(self):
        """Duration is in Segment Ticks, and the file says how long a tick is."""
        info = (_element(TIMESTAMP_SCALE, (1_000_000_000).to_bytes(4, "big"))
                + _element(DURATION, struct.pack(">d", 90.0)))
        path = self.root / "scaled.mkv"
        path.write_bytes(_element(SEGMENT, _element(INFO, info)))
        self.assertAlmostEqual(mkv.duration_seconds(path), 90.0)

    def test_a_four_byte_float_duration(self):
        info = (_element(TIMESTAMP_SCALE, SCALE_NS.to_bytes(4, "big"))
                + _element(DURATION, struct.pack(">f", 1000.0)))
        path = self.root / "f32.mkv"
        path.write_bytes(_element(SEGMENT, _element(INFO, info)))
        self.assertAlmostEqual(mkv.duration_seconds(path), 1.0)


class TestSayingSoWhenItCannotTell(MkvTestCase):
    """None is an answer, and the caller has to treat it as one."""

    def test_a_file_that_is_not_matroska(self):
        path = self.root / "not.mkv"
        path.write_bytes(b"this is not a container" * 10)
        self.assertIsNone(mkv.duration_seconds(path))

    def test_a_file_that_is_not_there(self):
        self.assertIsNone(mkv.duration_seconds(self.root / "absent.mkv"))

    def test_an_empty_file(self):
        path = self.root / "empty.mkv"
        path.write_bytes(b"")
        self.assertIsNone(mkv.duration_seconds(path))

    def test_a_container_with_no_duration_in_it(self):
        """An unfinalised file: the muxer never wrote the length."""
        info = _element(TIMESTAMP_SCALE, SCALE_NS.to_bytes(4, "big"))
        path = self.root / "nodur.mkv"
        path.write_bytes(_element(SEGMENT, _element(INFO, info)))
        self.assertIsNone(mkv.duration_seconds(path))

    def test_a_header_truncated_mid_element(self):
        path = self.root / "cut.mkv"
        path.write_bytes(matroska_header(1234.5)[:12])
        self.assertIsNone(mkv.duration_seconds(path))

    def test_it_gives_up_rather_than_reading_a_whole_disc(self):
        """Info sits near the front; a 20 GB file must not be read to say so."""
        path = self.root / "huge.mkv"
        with path.open("wb") as handle:
            handle.write(_element(SEGMENT, b""))
            handle.truncate(mkv.SCAN_LIMIT + 1_000_000)
        self.assertIsNone(mkv.duration_seconds(path))


if __name__ == "__main__":
    unittest.main()
