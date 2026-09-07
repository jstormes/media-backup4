"""Tests for resolving a device path to a volatile makemkv disc index."""

import unittest

from media_backup.makemkv import enumeration as enum

from . import makemkv_fixtures as fx


class TestParseDrives(unittest.TestCase):
    def test_filters_the_16_slots_down_to_real_drives(self):
        drives = enum.parse_drives(fx.ENUMERATION_LINES)
        self.assertEqual([d.device for d in drives], [fx.SR0, fx.SR1])

    def test_indices_come_from_the_row_not_the_position(self):
        drives = enum.parse_drives(fx.ENUMERATION_LINES)
        self.assertEqual([d.index for d in drives], [0, 1])

    def test_no_drives_at_all(self):
        self.assertEqual(enum.parse_drives(fx.NO_DISCS.splitlines()), [])

    def test_ignores_non_drv_records(self):
        drives = enum.parse_drives(['MSG:1005,0,0,"x","x"', "TCOUNT:0", "garbage"])
        self.assertEqual(drives, [])


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.drives = enum.parse_drives(fx.ENUMERATION_LINES)

    def test_resolves_device_to_index(self):
        r = enum.resolve(self.drives, fx.SR1, fx.SR1_LABEL)
        self.assertTrue(r)
        self.assertEqual(r.index, 1)

    def test_resolves_the_other_drive_to_its_own_index(self):
        self.assertEqual(enum.resolve(self.drives, fx.SR0, fx.SR0_LABEL).index, 0)

    def test_unknown_device_is_refused(self):
        r = enum.resolve(self.drives, "/dev/sr9")
        self.assertFalse(r)
        self.assertEqual(r.error_kind, enum.NOT_FOUND)

    def test_label_mismatch_is_refused(self):
        """The operator picked one disc; a different one is in the drive now."""
        r = enum.resolve(self.drives, fx.SR1, "SOME_OTHER_MOVIE")
        self.assertFalse(r)
        self.assertEqual(r.error_kind, enum.LABEL_MISMATCH)
        self.assertIn("DVD_VIDEO", r.detail)

    def test_missing_label_on_either_side_does_not_block(self):
        """Unlabelled discs are real; only a positive disagreement refuses."""
        self.assertTrue(enum.resolve(self.drives, fx.SR1, ""))
        drives = enum.parse_drives([
            'DRV:0,2,999,1,"drive","","/dev/sr0"'])
        self.assertTrue(enum.resolve(drives, "/dev/sr0", "ANY_LABEL"))

    def test_empty_drive_is_refused(self):
        drives = enum.parse_drives(['DRV:0,0,999,0,"drive","","/dev/sr0"'])
        r = enum.resolve(drives, "/dev/sr0")
        self.assertFalse(r)
        self.assertEqual(r.error_kind, enum.NO_DISC)

    def test_loading_drive_is_reported_separately_so_it_can_be_retried(self):
        drives = enum.parse_drives(fx.LOADING.splitlines())
        r = enum.resolve(drives, "/dev/sr0")
        self.assertFalse(r)
        self.assertEqual(r.error_kind, enum.LOADING)

    def test_resolution_is_falsey_when_it_fails(self):
        self.assertFalse(bool(enum.resolve([], "/dev/sr0")))
        self.assertTrue(bool(enum.resolve(self.drives, fx.SR0)))


if __name__ == "__main__":
    unittest.main()
