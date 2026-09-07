"""Tests for judging a backup's output tree on disk."""

import tempfile
import unittest
from pathlib import Path

from media_backup.makemkv import inspect as insp


class LayoutTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = Path(self._tmp.name)

    def make_bdmv(self, root=None, streams=True):
        root = root or self.dest
        (root / "BDMV" / "STREAM").mkdir(parents=True, exist_ok=True)
        (root / "BDMV" / "index.bdmv").write_bytes(b"x" * 64)
        if streams:
            (root / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"y" * 4096)

    def make_video_ts(self, root=None, vobs=True):
        root = root or self.dest
        (root / "VIDEO_TS").mkdir(parents=True, exist_ok=True)
        (root / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(b"x" * 32)
        if vobs:
            (root / "VIDEO_TS" / "VTS_01_1.VOB").write_bytes(b"y" * 2048)


class TestClassifyLayout(LayoutTestCase):
    def test_blu_ray(self):
        self.make_bdmv()
        self.assertEqual(insp.classify_layout(self.dest), insp.BDMV)

    def test_dvd(self):
        self.make_video_ts()
        self.assertEqual(insp.classify_layout(self.dest), insp.VIDEO_TS)

    def test_lowercase_dvd_filenames(self):
        (self.dest / "VIDEO_TS").mkdir()
        (self.dest / "VIDEO_TS" / "video_ts.ifo").write_bytes(b"x")
        (self.dest / "VIDEO_TS" / "vts_01_1.vob").write_bytes(b"y")
        self.assertEqual(insp.classify_layout(self.dest), insp.VIDEO_TS)

    def test_empty_bdmv_skeleton_is_not_a_finished_copy(self):
        """A killed job can leave the directories but no streams."""
        self.make_bdmv(streams=False)
        self.assertEqual(insp.classify_layout(self.dest), insp.UNKNOWN)

    def test_video_ts_without_vobs_is_not_a_finished_copy(self):
        self.make_video_ts(vobs=False)
        self.assertEqual(insp.classify_layout(self.dest), insp.UNKNOWN)

    def test_aacs_is_not_required(self):
        """Unencrypted discs have no AACS/CERTIFICATE; they are still valid."""
        self.make_bdmv()
        self.assertEqual(insp.classify_layout(self.dest), insp.BDMV)

    def test_missing_directory(self):
        self.assertEqual(insp.classify_layout(self.dest / "nope"), insp.MISSING)

    def test_empty_directory(self):
        self.assertEqual(insp.classify_layout(self.dest), insp.UNKNOWN)

    def test_both_structures(self):
        self.make_bdmv()
        self.make_video_ts()
        self.assertEqual(insp.classify_layout(self.dest), insp.MIXED)


class TestTreeSize(LayoutTestCase):
    def test_sums_nested_files(self):
        self.make_bdmv()
        self.assertEqual(insp.tree_size(self.dest), 64 + 4096)

    def test_missing_path_is_zero(self):
        self.assertEqual(insp.tree_size(self.dest / "nope"), 0)

    def test_symlinks_are_not_followed_into_double_counting(self):
        self.make_bdmv()
        before = insp.tree_size(self.dest)
        (self.dest / "link").symlink_to(self.dest / "BDMV" / "index.bdmv")
        self.assertEqual(insp.tree_size(self.dest), before)


class TestIsEmpty(LayoutTestCase):
    def test_absent_counts_as_empty(self):
        self.assertTrue(insp.is_empty(self.dest / "nope"))

    def test_new_directory(self):
        self.assertTrue(insp.is_empty(self.dest))

    def test_directory_with_content(self):
        """makemkvcon refuses a non-empty destination (MSG:5068)."""
        self.make_bdmv()
        self.assertFalse(insp.is_empty(self.dest))

    def test_a_single_hidden_file_still_counts_as_non_empty(self):
        (self.dest / ".hidden").write_text("x")
        self.assertFalse(insp.is_empty(self.dest))


if __name__ == "__main__":
    unittest.main()
