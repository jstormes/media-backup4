"""Tests for the backup success/failure policy.

makemkvcon exits 0 on operational failure, so every one of these cases has
exit_code 0 unless stated otherwise. That is the point.
"""

import unittest

from media_backup.makemkv import inspect as layouts
from media_backup.makemkv import messages as m
from media_backup.makemkv.outcome import (
    BackupObservation, OutcomePolicy, judge,
    CANCELLED, FAILURE, PARTIAL, SUCCESS, SUCCESS_UNVERIFIED,
)

DISC = 4_556_390_400


def obs(**kw):
    base = dict(
        exit_code=0, message_codes={m.BACKUP_DONE: 1},
        max_total_progress=65536, progress_max=65536, saw_any_progress=True,
        layout=layouts.BDMV, bytes_written=int(DISC * 0.99), disc_size_bytes=DISC,
    )
    base.update(kw)
    return BackupObservation(**base)


class TestSuccess(unittest.TestCase):
    def test_complete_run(self):
        self.assertEqual(judge(obs()).outcome, SUCCESS)

    def test_video_ts_layout_is_equally_good(self):
        self.assertEqual(judge(obs(layout=layouts.VIDEO_TS)).outcome, SUCCESS)

    def test_success_is_good(self):
        self.assertTrue(judge(obs()).is_good)

    def test_unencrypted_disc_without_aacs_still_succeeds(self):
        """AACS/CERTIFICATE are recorded but never required."""
        self.assertEqual(judge(obs()).outcome, SUCCESS)


class TestFailure(unittest.TestCase):
    def test_explicit_backup_failed_message(self):
        v = judge(obs(message_codes={m.BACKUP_FAILED: 1}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertFalse(v.is_good)

    def test_dirty_disc(self):
        v = judge(obs(message_codes={m.BACKUP_FAILED: 1, m.READ_ERROR: 812},
                      bytes_written=int(DISC * 0.3)))
        self.assertEqual(v.outcome, FAILURE)
        self.assertEqual(v.detail["read_errors"], 812)

    def test_truncated_copy_despite_a_success_message(self):
        """The size check is independent of anything MakeMKV printed."""
        v = judge(obs(bytes_written=int(DISC * 0.22)))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("22%", v.reason)

    def test_progress_never_reached_the_end(self):
        v = judge(obs(max_total_progress=30000))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("46%", v.reason)

    def test_no_output_directory(self):
        v = judge(obs(layout=layouts.MISSING, bytes_written=0))
        self.assertEqual(v.outcome, FAILURE)

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

    def test_destination_not_empty_is_flagged_as_our_bug(self):
        v = judge(obs(message_codes={m.DEST_NOT_EMPTY: 1}))
        self.assertEqual(v.outcome, FAILURE)
        self.assertIn("bug in this application", v.reason)


class TestPartial(unittest.TestCase):
    def test_hash_check_failures(self):
        v = judge(obs(message_codes={m.BACKUP_HASH_FAILED: 1, m.HASH_FAILED_FILE: 3}))
        self.assertEqual(v.outcome, PARTIAL)
        self.assertTrue(v.is_good, "a partial copy is still worth keeping")
        self.assertIn("3", v.reason)


class TestCancelled(unittest.TestCase):
    def test_terminated_by_us(self):
        v = judge(obs(terminated_by_us=True, stall_reason="operator cancelled",
                      bytes_written=0, max_total_progress=100))
        self.assertEqual(v.outcome, CANCELLED)

    def test_cancel_wins_over_the_incomplete_copy_it_caused(self):
        """Do not send the operator to clean a disc they themselves ejected."""
        v = judge(obs(terminated_by_us=True, bytes_written=0,
                      message_codes={m.BACKUP_FAILED: 1}))
        self.assertEqual(v.outcome, CANCELLED)

    def test_engine_reported_cancellation(self):
        self.assertEqual(judge(obs(message_codes={m.CANCELLED: 1})).outcome, CANCELLED)


class TestUnverified(unittest.TestCase):
    def test_unrecognised_layout_is_kept_not_failed(self):
        """DVD layouts are uncharacterised; failing them would block day one."""
        v = judge(obs(layout=layouts.UNKNOWN))
        self.assertEqual(v.outcome, SUCCESS_UNVERIFIED)
        self.assertTrue(v.is_good)

    def test_unrecognised_layout_can_be_made_strict(self):
        v = judge(obs(layout=layouts.UNKNOWN),
                  OutcomePolicy(allow_unknown_layout=False))
        self.assertEqual(v.outcome, FAILURE)

    def test_good_copy_with_no_completion_message(self):
        v = judge(obs(message_codes={}))
        self.assertEqual(v.outcome, SUCCESS_UNVERIFIED)

    def test_a_run_with_no_progress_records_is_not_failed_on_that_basis(self):
        v = judge(obs(saw_any_progress=False, max_total_progress=0, progress_max=0))
        self.assertEqual(v.outcome, SUCCESS)


class TestObservation(unittest.TestCase):
    def test_ratios_handle_unknown_disc_size(self):
        o = obs(disc_size_bytes=0)
        self.assertEqual(o.size_ratio, 0.0)
        self.assertEqual(judge(o).outcome, SUCCESS,
                         "an unknown disc size must not fail a good copy")

    def test_error_counts(self):
        o = obs(message_codes={m.READ_ERROR: 5, m.SCSI_ERROR: 2, m.HASH_FAILED_FILE: 1})
        self.assertEqual(o.read_error_count, 7)
        self.assertEqual(o.hash_error_count, 1)


if __name__ == "__main__":
    unittest.main()
