"""Reading /proc/pressure/memory, and refusing to start work when it is high.

See docs/operations/memory-pressure.md for the incident these guard.
"""

import tempfile
import unittest
from pathlib import Path

from media_backup import pressure

SAMPLE = ("some avg10=0.10 avg60=26.96 avg300=25.83 total=126229711\n"
          "full avg10=0.09 avg60=26.65 avg300=25.45 total=124850502\n")

#: The reading from the morning of the kill.
UNDER_LOAD = ("some avg10=98.61 avg60=82.63 avg300=31.42 total=1\n"
              "full avg10=98.61 avg60=82.63 avg300=31.42 total=1\n")


class TestRead(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text):
        p = self.root / "memory"
        p.write_text(text)
        return p

    def test_parses_both_rows(self):
        now = pressure.read(self.write(SAMPLE))
        self.assertTrue(now.available)
        self.assertAlmostEqual(now.full_avg60, 26.65)
        self.assertAlmostEqual(now.full_avg10, 0.09)
        self.assertAlmostEqual(now.some_avg60, 26.96)

    def test_a_missing_file_is_unavailable_not_zero(self):
        now = pressure.read(self.root / "absent")
        self.assertFalse(now.available)

    def test_garbage_is_unavailable(self):
        self.assertFalse(pressure.read(self.write("not psi at all\n")).available)

    def test_partial_rows_do_not_raise(self):
        now = pressure.read(self.write("full avg10=oops avg60=12.5 total=1\n"))
        self.assertTrue(now.available)
        self.assertAlmostEqual(now.full_avg60, 12.5)
        self.assertAlmostEqual(now.full_avg10, 0.0)


class TestBlocked(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text):
        p = self.root / "memory"
        p.write_text(text)
        return p

    def test_zero_limit_disables_the_check(self):
        self.assertEqual(pressure.blocked(0, path=self.write(UNDER_LOAD)), "")

    def test_high_pressure_blocks_and_says_why(self):
        why = pressure.blocked(30, path=self.write(UNDER_LOAD))
        self.assertIn("memory pressure", why)
        self.assertIn("83", why)

    def test_quiet_box_does_not_block(self):
        self.assertEqual(pressure.blocked(30, path=self.write(SAMPLE)), "")

    def test_at_the_limit_is_not_over_it(self):
        text = "full avg10=0 avg60=30.0 avg300=0 total=1\n"
        self.assertEqual(pressure.blocked(30, path=self.write(text)), "")

    def test_unavailable_psi_permits_work(self):
        """The opposite of the checksum rule, and deliberately so.

        A check that cannot run must not be able to stop every rip forever.
        """
        self.assertEqual(pressure.blocked(30, path=self.root / "absent"), "")
