"""Tests for the makemkvcon robot-mode line parser."""

import unittest

from media_backup.makemkv.records import (
    Cinfo, Drv, Msg, Prgc, Prgt, Prgv, Sinfo, Tcount, Tinfo, Unknown,
    parse_line, split_fields,
)

from . import makemkv_fixtures as fx


class TestSplitFields(unittest.TestCase):
    def test_plain_numeric(self):
        self.assertEqual(split_fields("0,0,65536"), ["0", "0", "65536"])

    def test_quoted_values(self):
        self.assertEqual(split_fields('1,2,"a b"'), ["1", "2", "a b"])

    def test_comma_inside_quotes_is_literal(self):
        """A naive split(',') corrupts this."""
        self.assertEqual(split_fields('5,2,0,"Episode 3, Part 2"'),
                         ["5", "2", "0", "Episode 3, Part 2"])

    def test_colon_inside_quotes_is_literal(self):
        self.assertEqual(split_fields('2,0,"Spider-Man: Across The Spider-Verse"'),
                         ["2", "0", "Spider-Man: Across The Spider-Verse"])

    def test_backslash_escaped_quote(self):
        """Escaping is undocumented; accept the C-style convention."""
        self.assertEqual(split_fields(r'6,0,"Best of \"Friends\""'),
                         ["6", "0", 'Best of "Friends"'])

    def test_doubled_quote(self):
        """...and the CSV-style convention, since we do not know which is used."""
        self.assertEqual(split_fields('6,0,"Best of ""Friends"""'),
                         ["6", "0", 'Best of "Friends"'])

    def test_empty_fields(self):
        self.assertEqual(split_fields('1,256,999,0,"","",""'),
                         ["1", "256", "999", "0", "", "", ""])

    def test_unterminated_quote_does_not_raise(self):
        self.assertIsInstance(split_fields('1,2,"never closed'), list)

    def test_unicode(self):
        self.assertEqual(split_fields('1,0,"Amélie"'), ["1", "0", "Amélie"])


class TestParseLine(unittest.TestCase):
    def test_blank_lines_are_none(self):
        for line in ("", "   ", "\n", "\r\n"):
            self.assertIsNone(parse_line(line))

    def test_msg(self):
        r = parse_line(fx.ENUMERATION_LINES[0])
        self.assertIsInstance(r, Msg)
        self.assertEqual(r.code, 1005)
        self.assertEqual(r.count, 1)
        self.assertEqual(r.params, ("MakeMKV v1.18.3 linux(x64-release)",))

    def test_msg_without_params(self):
        r = parse_line('MSG:5010,0,0,"Failed to open disc","Failed to open disc"')
        self.assertEqual((r.code, r.params), (5010, ()))

    def test_drv_real_row(self):
        r = parse_line(fx.ENUMERATION_LINES[1])
        self.assertIsInstance(r, Drv)
        self.assertEqual(r.index, 0)
        self.assertEqual(r.device, "/dev/sr0")
        self.assertEqual(r.disc_name, fx.SR0_LABEL)
        self.assertTrue(r.is_drive)
        self.assertTrue(r.has_disc)

    def test_drv_empty_slot(self):
        r = parse_line('DRV:9,256,999,0,"","",""')
        self.assertFalse(r.is_drive)
        self.assertFalse(r.has_disc)

    def test_drv_loading_is_transient(self):
        r = parse_line('DRV:0,3,999,0,"drive","","/dev/sr0"')
        self.assertTrue(r.is_loading)
        self.assertFalse(r.has_disc)

    def test_prgv_scales_by_its_own_max(self):
        r = parse_line("PRGV:16384,32768,65536")
        self.assertIsInstance(r, Prgv)
        self.assertAlmostEqual(r.step_pct, 25.0)
        self.assertAlmostEqual(r.total_pct, 50.0)

    def test_prgv_zero_max_does_not_divide_by_zero(self):
        r = parse_line("PRGV:0,0,0")
        self.assertEqual((r.step_pct, r.total_pct), (0.0, 0.0))

    def test_prgt_and_prgc(self):
        self.assertIsInstance(parse_line('PRGT:5018,0,"Scanning"'), Prgt)
        self.assertIsInstance(parse_line('PRGC:5018,0,"Scanning"'), Prgc)

    def test_tcount(self):
        self.assertEqual(parse_line("TCOUNT:21"), Tcount(21))

    def test_cinfo_tinfo_sinfo(self):
        self.assertEqual(parse_line('CINFO:1,6209,"Blu-ray disc"'),
                         Cinfo(1, 6209, "Blu-ray disc"))
        self.assertEqual(parse_line('TINFO:0,11,0,"31506235392"'),
                         Tinfo(0, 11, 0, "31506235392"))
        self.assertEqual(parse_line('SINFO:0,0,5,0,"V_MPEG4/ISO/AVC"'),
                         Sinfo(0, 0, 5, 0, "V_MPEG4/ISO/AVC"))


class TestParserIsTotal(unittest.TestCase):
    """parse_line runs on a worker thread; an exception would freeze a job."""

    def test_unknown_prefix(self):
        r = parse_line('WIBBLE:1,2,3')
        self.assertIsInstance(r, Unknown)
        self.assertEqual(r.prefix, "WIBBLE")

    def test_no_colon_at_all(self):
        self.assertIsInstance(parse_line("just some text"), Unknown)

    def test_too_few_fields(self):
        self.assertIsInstance(parse_line("DRV:0,2"), Unknown)
        self.assertIsInstance(parse_line("MSG:1005"), Unknown)

    def test_non_numeric_where_number_expected(self):
        self.assertIsInstance(parse_line('PRGV:a,b,c'), Unknown)
        self.assertIsInstance(parse_line('TCOUNT:many'), Unknown)

    def test_truncated_line_from_a_killed_process(self):
        r = parse_line('DRV:0,2,999,12,"BD-RE HL-DT-ST BD-R')
        self.assertIsInstance(r, (Drv, Unknown))  # must not raise

    def test_never_raises_on_hostile_input(self):
        for line in ('MSG:' + '"' * 50, "PRGV:" + "9" * 400, ":::::",
                     'DRV:,,,,,,', "\x00\x01\x02", 'TINFO:1,2,3,"' + "," * 100):
            with self.subTest(line=line[:24]):
                parse_line(line)  # the assertion is that this returns

    def test_whole_captured_transcript_parses(self):
        for line in fx.ENUMERATION_LINES:
            self.assertIsNotNone(parse_line(line))

    def test_every_fixture_transcript_parses(self):
        for name in ("BACKUP_SUCCESS", "BACKUP_DIRTY_DISC", "BACKUP_NO_ACCESS",
                     "BACKUP_HASH_FAILURES", "DISC_SCAN", "NO_DISCS", "LOADING"):
            for line in getattr(fx, name).splitlines():
                with self.subTest(fixture=name):
                    self.assertNotIsInstance(parse_line(line), Unknown)


if __name__ == "__main__":
    unittest.main()
