"""Tests for udisks2 parsing and DriveState presentation logic."""

import unittest
from unittest import mock

from media_backup import drives
from media_backup.drives import DriveScanner, DriveState, _decode_ay, _media_label

from . import fixtures as fx


def scan_with(objects):
    """Run a scan against a canned udisks2 object tree."""
    with mock.patch.object(DriveScanner, "_managed_objects", return_value=objects):
        return DriveScanner.scan()


class TestDecodeAy(unittest.TestCase):
    """udisks2 hands back NUL-terminated byte arrays for paths."""

    def test_decodes_list_of_ints(self):
        self.assertEqual(_decode_ay(fx.ay("/dev/sr0")), "/dev/sr0")

    def test_decodes_bytes(self):
        self.assertEqual(_decode_ay(b"/dev/sr1\x00"), "/dev/sr1")

    def test_strips_only_trailing_nul(self):
        self.assertEqual(_decode_ay(fx.ay("/run/media/a b/DISC")), "/run/media/a b/DISC")

    def test_empty_and_none(self):
        self.assertEqual(_decode_ay([]), "")
        self.assertEqual(_decode_ay(None), "")

    def test_invalid_utf8_does_not_raise(self):
        self.assertIsInstance(_decode_ay([0xFF, 0xFE, 0]), str)

    def test_non_bytelike_falls_back_to_str(self):
        self.assertEqual(_decode_ay("/dev/sr2"), "/dev/sr2")


class TestMediaLabel(unittest.TestCase):
    def test_known_names(self):
        self.assertEqual(_media_label("optical_dvd"), "DVD")
        self.assertEqual(_media_label("optical_cd"), "CD")
        self.assertEqual(_media_label("optical_bd"), "Blu-ray")
        self.assertEqual(_media_label("optical_bd_re"), "BD-RE")
        self.assertEqual(_media_label("optical_dvd_plus_r_dl"), "DVD+R DL")

    def test_unknown_optical_is_derived(self):
        self.assertEqual(_media_label("optical_mo"), "MO")

    def test_non_optical_passes_through(self):
        self.assertEqual(_media_label("flash_sd"), "flash_sd")


class TestDriveType(unittest.TestCase):
    """Drive class comes from MediaCompatibility, best capability first."""

    def _state(self, compat=(), model="Some Drive"):
        return DriveState(device="/dev/sr0", model=model, media_compatibility=list(compat))

    def test_prefers_bd_over_dvd_and_cd(self):
        state = self._state(["optical_cd", "optical_dvd", "optical_bd_re"])
        self.assertEqual(state.drive_type, "BD")

    def test_dvd_when_no_bd(self):
        self.assertEqual(self._state(["optical_cd", "optical_dvd_r"]).drive_type, "DVD")

    def test_cd_only(self):
        self.assertEqual(self._state(["optical_cd_rw"]).drive_type, "CD")

    def test_falls_back_to_model_when_compat_missing(self):
        self.assertEqual(self._state(model="HL-DT-ST DVD-RAM GH24").drive_type, "DVD")
        self.assertEqual(self._state(model="Pioneer BD-RW BDR-212").drive_type, "BD")
        self.assertEqual(self._state(model="Generic CD-ROM").drive_type, "CD")

    def test_unknown(self):
        self.assertEqual(self._state(model="Mystery Box").drive_type, "Unknown")


class TestDiscDescription(unittest.TestCase):
    def _state(self, **kw):
        base = dict(device="/dev/sr0", model="BD-RE BU40N", media_compatibility=["optical_bd"])
        base.update(kw)
        return DriveState(**base)

    def test_empty_drive_has_no_description(self):
        self.assertEqual(self._state(has_media=False, media="optical_dvd").disc_description, "")

    def test_data_disc_names_the_media(self):
        self.assertEqual(self._state(has_media=True, media="optical_dvd").disc_description, "DVD")

    def test_blank_disc(self):
        state = self._state(has_media=True, media="optical_bd_re", is_blank=True)
        self.assertEqual(state.disc_description, "Blank BD-RE")

    def test_audio_cd_pluralises(self):
        self.assertEqual(
            self._state(has_media=True, media="optical_cd", audio_tracks=12).disc_description,
            "Audio CD, 12 tracks",
        )
        self.assertEqual(
            self._state(has_media=True, media="optical_cd", audio_tracks=1).disc_description,
            "Audio CD, 1 track",
        )

    def test_blank_takes_priority_over_audio_count(self):
        state = self._state(has_media=True, media="optical_cd", is_blank=True, audio_tracks=0)
        self.assertEqual(state.disc_description, "Blank CD")


class TestDisplayName(unittest.TestCase):
    def test_joins_vendor_and_model(self):
        state = DriveState(device="/dev/sr0", model="BD-RE BU40N", vendor="HL-DT-ST")
        self.assertEqual(state.display_name, "HL-DT-ST BD-RE BU40N")

    def test_model_only_when_vendor_missing(self):
        state = DriveState(device="/dev/sr0", model="BD-RE BU40N")
        self.assertEqual(state.display_name, "BD-RE BU40N")


class TestScan(unittest.TestCase):
    def test_finds_both_drives_sorted(self):
        found = scan_with(fx.EMPTY_AND_DVD)
        self.assertEqual([d.device for d in found], ["/dev/sr0", "/dev/sr1"])

    def test_model_is_not_truncated_at_a_space(self):
        """Regression: lsblk -P parsing split on whitespace, giving 'BD-RE'."""
        sr0 = scan_with(fx.EMPTY_AND_DVD)[0]
        self.assertEqual(sr0.model, "BD-RE  WH16NS40")
        self.assertEqual(sr0.display_name, "HL-DT-ST BD-RE  WH16NS40")

    def test_empty_drive_reports_no_media(self):
        sr0 = scan_with(fx.EMPTY_AND_DVD)[0]
        self.assertFalse(sr0.has_media)
        self.assertEqual(sr0.label, "")
        self.assertEqual(sr0.fs_type, "")
        self.assertEqual(sr0.size, 0)

    def test_empty_drive_is_still_listed(self):
        """Regression: Drive.Optical is False with no disc, so it can't gate discovery."""
        objects = fx.EMPTY_AND_DVD
        self.assertFalse(objects[fx.DRIVE_WH16NS40][fx.IFACE_DRIVE]["Optical"])
        self.assertIn("/dev/sr0", [d.device for d in scan_with(objects)])

    def test_data_disc_metadata(self):
        sr1 = scan_with(fx.EMPTY_AND_DVD)[1]
        self.assertTrue(sr1.has_media)
        self.assertEqual(sr1.label, "DVD_VIDEO")
        self.assertEqual(sr1.fs_type, "udf")
        self.assertEqual(sr1.size, 4556390400)
        self.assertEqual(sr1.serial, "393032485330313735363920")
        self.assertEqual(sr1.bus, "usb")
        self.assertTrue(sr1.is_readonly)

    def test_audio_cd_is_detected_without_a_filesystem(self):
        """Regression: IdType is empty for an audio CD, so it read as 'No disc'."""
        cd = scan_with(fx.AUDIO_CD)[0]
        self.assertEqual(cd.fs_type, "")
        self.assertTrue(cd.has_media)
        self.assertEqual(cd.audio_tracks, 12)
        self.assertEqual(cd.disc_description, "Audio CD, 12 tracks")

    def test_blank_disc_is_detected_without_a_filesystem(self):
        blank = scan_with(fx.BLANK_DISC)[0]
        self.assertTrue(blank.has_media)
        self.assertTrue(blank.is_blank)
        self.assertEqual(blank.disc_description, "Blank BD-RE")

    def test_mount_points_are_decoded(self):
        mounted = scan_with(fx.MOUNTED_DVD)[0]
        self.assertEqual(mounted.mount_points, ["/run/media/user/DVD_VIDEO"])

    def test_non_optical_devices_are_ignored(self):
        self.assertEqual(scan_with(fx.NON_OPTICAL), [])

    def test_partitions_are_not_listed_as_drives(self):
        objects = dict(fx.EMPTY_AND_DVD)
        objects[fx.BLOCK_PREFIX + "sr1p1"] = {
            fx.IFACE_BLOCK: fx.block("/dev/sr1p1", fx.DRIVE_BU40N, "DVD_VIDEO", "udf"),
            fx.IFACE_PARTITION: {"Number": 1},
        }
        self.assertEqual([d.device for d in scan_with(objects)], ["/dev/sr0", "/dev/sr1"])

    def test_block_without_a_known_drive_is_skipped(self):
        objects = {fx.BLOCK_PREFIX + "sr9": {fx.IFACE_BLOCK: fx.block("/dev/sr9", "/dangling")}}
        self.assertEqual(scan_with(objects), [])

    def test_missing_model_becomes_unknown(self):
        objects = {
            fx.BLOCK_PREFIX + "sr0": {fx.IFACE_BLOCK: fx.block("/dev/sr0", fx.DRIVE_BU40N)},
            fx.DRIVE_BU40N: {fx.IFACE_DRIVE: fx.drive("", "")},
        }
        found = scan_with(objects)[0]
        self.assertEqual(found.model, "Unknown")
        self.assertIsNone(found.serial)

    def test_dbus_failure_returns_empty_list(self):
        error = drives.GLib.GError("udisks2 is not running")
        with mock.patch.object(DriveScanner, "_managed_objects", side_effect=error):
            with self.assertLogs("media_backup.drives", level="ERROR"):
                self.assertEqual(DriveScanner.scan(), [])


if __name__ == "__main__":
    unittest.main()
