"""Tests for the saved-titles success/failure policy.

makemkvcon exits 0 on operational failure, so every one of these cases has
exit_code 0 unless stated otherwise. That is the point.

The yardstick is the selection, not the disc. An mkv run leaves out menus,
duplicate angles and unwanted tracks by design, so "90% of the disc size" says
nothing about it; what it must produce is the titles that were chosen, at
about the size the scan said they were.
"""

import unittest

from media_backup.makemkv import inspect as layouts
from media_backup.makemkv import messages as m
from media_backup.makemkv.outcome import (
    BackupObservation, OutcomePolicy, judge,
    CANCELLED, FAILURE, PARTIAL, SUCCESS, SUCCESS_UNVERIFIED,
)

#: What the scan said the chosen titles weigh.
EXPECTED = 4_245_336_064


def obs(**kw):
    base = dict(
        exit_code=0, message_codes={m.MKV_COMPLETE: 1},
        max_total_progress=65536, progress_max=65536, saw_any_progress=True,
        layout=layouts.MKV, titles_expected=1, titles_saved=1, files_written=1,
        expected_bytes=EXPECTED, bytes_written=int(EXPECTED * 0.99),
    )
    base.update(kw)
    return BackupObservation(**base)


class TestSuccess(unittest.TestCase):
    def test_complete_run(self):
        self.assertEqual(judge(obs()).outcome, SUCCESS)

    def test_success_is_good(self):
        self.assertTrue(judge(obs()).is_good)

    def test_several_titles_all_saved(self):
        """A box-set disc: five episodes asked for, five files written."""
        v = judge(obs(titles_expected=5, titles_saved=5, files_written=5,
                      message_codes={m.MKV_COMPLETE: 1}))
        self.assertEqual(v.outcome, SUCCESS)


class TestFailure(unittest.TestCase):
    def test_nothing_was_saved(self):
        v = judge(obs(files_written=0, titles_saved=0, bytes_written=0,
                      layout=layouts.MISSING, message_codes={}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("no titles", v.reason)
        self.assertFalse(v.is_good)

    def test_a_run_that_said_why_says_why(self):
        v = judge(obs(files_written=0, titles_saved=0, bytes_written=0,
                      message_codes={m.MKV_TITLE_FAILED: 1}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("could not save", v.reason)

    def test_dirty_disc(self):
        v = judge(obs(files_written=0, bytes_written=0,
                      message_codes={m.MKV_TITLE_FAILED: 1, m.READ_ERROR: 812}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertEqual(v.detail["read_errors"], 812)

    def test_truncated_output_despite_a_success_message(self):
        """The size check is independent of anything MakeMKV printed."""
        v = judge(obs(bytes_written=int(EXPECTED * 0.22)))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("22%", v.reason)

    def test_progress_never_reached_the_end(self):
        v = judge(obs(max_total_progress=30000))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("46%", v.reason)

    def test_usage_error_is_our_bug(self):
        v = judge(obs(exit_code=1))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("command line", v.reason)

    def test_permissions_error_gets_a_specific_explanation(self):
        """MSG:2016 is an environment fault and must not read as a bad disc."""
        v = judge(obs(message_codes={m.NO_DRIVE_ACCESS: 1}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("permissions", v.reason)
        self.assertNotIn("clean", v.reason.lower())

    def test_an_unwritable_destination_is_not_blamed_on_the_disc(self):
        v = judge(obs(message_codes={m.MKV_BAD_DIRECTORY: 1}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("permissions", v.reason)


class TestPartial(unittest.TestCase):
    def test_some_titles_failed(self):
        v = judge(obs(titles_expected=4, titles_saved=3, titles_failed=1,
                      files_written=3,
                      message_codes={m.MKV_COMPLETE_PARTIAL: 1}))
        self.assertEqual(v.outcome, PARTIAL)
        self.assertTrue(v.is_good, "three of four is still worth keeping")
        self.assertIn("3 of 4", v.reason)

    def test_a_missing_file_is_noticed_without_being_announced(self):
        """MakeMKV said all four; only three are there. Believe the disk."""
        v = judge(obs(titles_expected=4, titles_saved=4, files_written=3,
                      message_codes={m.MKV_COMPLETE: 1}))
        self.assertEqual(v.outcome, PARTIAL)

    def test_the_announcement_alone_is_enough(self):
        v = judge(obs(message_codes={m.MKV_SAVED_PARTIAL: 1}))
        self.assertEqual(v.outcome, PARTIAL)


class TestCancelled(unittest.TestCase):
    def test_terminated_by_us(self):
        v = judge(obs(terminated_by_us=True, stall_reason="operator cancelled",
                      files_written=0, bytes_written=0, max_total_progress=100))
        self.assertEqual(v.outcome, CANCELLED)

    def test_cancel_wins_over_the_incomplete_copy_it_caused(self):
        """Do not send the operator to clean a disc they themselves ejected."""
        v = judge(obs(terminated_by_us=True, files_written=0, bytes_written=0,
                      message_codes={m.MKV_TITLE_FAILED: 1}))
        self.assertEqual(v.outcome, CANCELLED)

    def test_engine_reported_cancellation(self):
        self.assertEqual(judge(obs(message_codes={m.CANCELLED: 1})).outcome, CANCELLED)


class TestUnverified(unittest.TestCase):
    def test_everything_is_there_but_makemkv_never_said_so(self):
        v = judge(obs(message_codes={}))
        self.assertEqual(v.outcome, SUCCESS_UNVERIFIED)
        self.assertTrue(v.is_good)

    def test_a_run_with_no_progress_records_is_not_failed_on_that_basis(self):
        v = judge(obs(saw_any_progress=False, max_total_progress=0, progress_max=0))
        self.assertEqual(v.outcome, SUCCESS)


class TestObservation(unittest.TestCase):
    def test_size_ratio_is_against_the_chosen_titles(self):
        o = obs(expected_bytes=1000, bytes_written=900)
        self.assertAlmostEqual(o.size_ratio, 0.9)

    def test_an_unknown_expected_size_does_not_fail_a_good_run(self):
        o = obs(expected_bytes=0)
        self.assertEqual(o.size_ratio, 0.0)
        self.assertEqual(judge(o).outcome, SUCCESS)

    def test_error_counts(self):
        o = obs(message_codes={m.READ_ERROR: 5, m.SCSI_ERROR: 2, m.HASH_FAILED_FILE: 1})
        self.assertEqual(o.read_error_count, 7)
        self.assertEqual(o.hash_error_count, 1)

    def test_the_floor_is_tunable(self):
        o = obs(bytes_written=int(EXPECTED * 0.5))
        self.assertEqual(judge(o).outcome, FAILURE)
        self.assertEqual(judge(o, OutcomePolicy(size_ratio_floor=0.4)).outcome,
                         SUCCESS)


if __name__ == "__main__":
    unittest.main()
