"""Tests for makemkvcon argv construction."""

import unittest
from pathlib import Path

from media_backup.config import Config
from media_backup.makemkv import command

CFG = Config(makemkvcon=Path("/usr/local/bin/makemkvcon"), cache_mb=1024,
             decrypt=True, use_stdbuf=True)


class TestMkvArgv(unittest.TestCase):
    def test_uses_a_disc_index_source(self):
        """The binary rejects dev:, so the caller resolves an index first."""
        argv = command.mkv_argv(CFG, 3, Path("/srv/out"), 0)
        self.assertIn("disc:3", argv)
        self.assertFalse(any(a.startswith("dev:") for a in argv))

    def test_switches_precede_the_subcommand(self):
        argv = command.mkv_argv(CFG, 0, Path("/srv/out"), 0)
        self.assertLess(argv.index("--decrypt"), argv.index("mkv"))
        self.assertLess(argv.index("-r"), argv.index("mkv"))

    def test_saves_one_named_title(self):
        """One run per title, by its index from the scan.

        A length filter cannot say "these two of the four", and cannot
        separate a title from another of the same runtime at all.
        """
        argv = command.mkv_argv(CFG, 0, Path("/srv/out"), 3)
        self.assertEqual(argv[-3:], ["disc:0", "3", "/srv/out"])

    def test_no_length_filter_so_the_ids_mean_what_the_scan_meant(self):
        """A title id is a position in the list MakeMKV is showing."""
        argv = command.mkv_argv(CFG, 0, Path("/srv/out"), 3)
        self.assertFalse(any(a.startswith("--minlength") for a in argv))

    def test_destination_is_the_final_positional(self):
        self.assertEqual(command.mkv_argv(CFG, 0, Path("/srv/out"), 0)[-1], "/srv/out")

    def test_decrypt_can_be_turned_off(self):
        argv = command.mkv_argv(Config(decrypt=False), 0, Path("/o"), 0)
        self.assertNotIn("--decrypt", argv)

    def test_cache_is_always_passed(self):
        self.assertIn("--cache=1024", command.mkv_argv(CFG, 0, Path("/o"), 0))

    def test_stdbuf_wraps_the_command_when_enabled(self):
        self.assertEqual(command.mkv_argv(CFG, 0, Path("/o"), 0)[:3],
                         ["stdbuf", "-oL", "-eL"])

    def test_stdbuf_can_be_disabled(self):
        argv = command.mkv_argv(Config(use_stdbuf=False), 0, Path("/o"), 0)
        self.assertNotIn("stdbuf", argv)
        self.assertTrue(argv[0].endswith("makemkvcon"))


class TestEnumerateArgv(unittest.TestCase):
    def test_uses_the_sentinel_index(self):
        argv = command.enumerate_argv(CFG)
        self.assertIn(f"disc:{command.ENUMERATION_INDEX}", argv)
        self.assertIn("info", argv)

    def test_does_not_request_progress(self):
        """Enumeration is a one-shot; progress records would be noise."""
        self.assertFalse(any(a.startswith("--progress") for a in command.enumerate_argv(CFG)))


class TestInfoArgv(unittest.TestCase):
    def test_scans_a_specific_disc(self):
        argv = command.info_argv(CFG, 1)
        self.assertIn("disc:1", argv)
        self.assertIn("info", argv)
        self.assertNotIn("backup", argv)


if __name__ == "__main__":
    unittest.main()
