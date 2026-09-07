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
        argv = command.mkv_argv(CFG, 3, Path("/srv/out"), 600)
        self.assertIn("disc:3", argv)
        self.assertFalse(any(a.startswith("dev:") for a in argv))

    def test_switches_precede_the_subcommand(self):
        argv = command.mkv_argv(CFG, 0, Path("/srv/out"), 600)
        self.assertLess(argv.index("--decrypt"), argv.index("mkv"))
        self.assertLess(argv.index("-r"), argv.index("mkv"))

    def test_saves_all_titles_above_the_selected_length(self):
        """One pass with a length filter, not one spawn per title id.

        Title ids are assigned per scan, so a list of them would have to be
        re-resolved against the very run that consumes them.
        """
        argv = command.mkv_argv(CFG, 0, Path("/srv/out"), 7564)
        self.assertIn("--minlength=7564", argv)
        self.assertEqual(argv[-3:], ["disc:0", "all", "/srv/out"])

    def test_destination_is_the_final_positional(self):
        self.assertEqual(command.mkv_argv(CFG, 0, Path("/srv/out"), 600)[-1], "/srv/out")

    def test_decrypt_can_be_turned_off(self):
        argv = command.mkv_argv(Config(decrypt=False), 0, Path("/o"), 600)
        self.assertNotIn("--decrypt", argv)

    def test_cache_is_always_passed(self):
        self.assertIn("--cache=1024", command.mkv_argv(CFG, 0, Path("/o"), 600))

    def test_stdbuf_wraps_the_command_when_enabled(self):
        self.assertEqual(command.mkv_argv(CFG, 0, Path("/o"), 600)[:3],
                         ["stdbuf", "-oL", "-eL"])

    def test_stdbuf_can_be_disabled(self):
        argv = command.mkv_argv(Config(use_stdbuf=False), 0, Path("/o"), 600)
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
