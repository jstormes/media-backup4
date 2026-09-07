"""Tests for on-disk collection persistence."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_backup import model
from media_backup.config import Config
from media_backup.store import CollectionStore, StoreError


class FakeDrive:
    def __init__(self, label="DISC1", media="optical_dvd", size=4_556_390_400):
        self.label, self.media, self.size = label, media, size


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = Config(media_path=self.root)
        self.store = CollectionStore(self.cfg)

    def a_collection(self, identifier="123"):
        return self.store.create(identifier)


class TestSaveAndLoad(StoreTestCase):
    def test_create_then_reload(self):
        c = self.a_collection("883929665938")
        loaded = self.store.load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].collection_id, c.collection_id)
        self.assertEqual(loaded[0].identifier, "883929665938")

    def test_no_collections_yet(self):
        self.assertEqual(self.store.load_all(), [])

    def test_missing_root_is_not_an_error(self):
        store = CollectionStore(Config(media_path=self.root / "nope"))
        self.assertEqual(store.load_all(), [])

    def test_save_leaves_no_temp_file(self):
        c = self.a_collection()
        self.assertFalse((self.store.collection_dir(c) / "collection.json.tmp").exists())

    def test_second_save_creates_a_backup_copy(self):
        c = self.a_collection()
        c.title = "changed"
        self.store.save(c)
        self.assertTrue((self.store.collection_dir(c) / "collection.json.bak").is_file())

    def test_corrupt_json_falls_back_to_the_backup(self):
        c = self.a_collection("original")
        c.title = "second write"
        self.store.save(c)
        (self.store.collection_dir(c) / "collection.json").write_text("{truncated")
        loaded = self.store.load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].identifier, "original")

    def test_unreadable_collection_is_skipped_not_fatal(self):
        good = self.a_collection("good")
        broken = self.cfg.collections_path / "broken-one"
        broken.mkdir()
        (broken / "collection.json").write_text("{nope")
        with self.assertLogs("media_backup.store", level="ERROR"):
            loaded = self.store.load_all()
        self.assertEqual([c.identifier for c in loaded], ["good"])

    def test_stray_files_in_the_collections_dir_are_ignored(self):
        self.a_collection()
        (self.cfg.collections_path / "README.txt").write_text("hi")
        self.assertEqual(len(self.store.load_all()), 1)

    def test_save_failure_raises_storeerror(self):
        c = self.a_collection()
        with mock.patch("os.replace", side_effect=OSError("read-only")):
            with self.assertRaises(StoreError):
                self.store.save(c)


class TestDiscs(StoreTestCase):
    def test_add_disc_creates_its_directory_and_records_the_disc(self):
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive(label="MOVIE_A"))
        self.assertEqual(d.label, "MOVIE_A")
        self.assertEqual(d.disc_size_bytes, 4_556_390_400)
        self.assertTrue(self.store.disc_dir(c, d).is_dir())
        self.assertEqual(self.store.load_all()[0].discs[0].disc_id, d.disc_id)

    def test_ordinals_increment_across_adds(self):
        c = self.a_collection()
        first = self.store.add_disc(c, FakeDrive())
        second = self.store.add_disc(c, FakeDrive(label="DISC2"))
        self.assertEqual((first.ordinal, second.ordinal), (1, 2))


class TestPrepareAttempt(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.a_collection()
        self.d = self.store.add_disc(self.c, FakeDrive())

    def test_first_attempt_gets_an_empty_data_dir(self):
        data, log, n = self.store.prepare_attempt(self.c, self.d)
        self.assertEqual(n, 1)
        self.assertTrue(data.is_dir())
        self.assertEqual(list(data.iterdir()), [])
        self.assertTrue(log.parent.is_dir())

    def test_retry_moves_the_partial_aside_and_clears_the_way(self):
        """makemkvcon refuses a non-empty destination, so this is required."""
        data, _log, _n = self.store.prepare_attempt(self.c, self.d)
        (data / "BDMV").mkdir()
        (data / "BDMV" / "index.bdmv").write_bytes(b"partial")
        self.d.attempts.append(model.Attempt(attempt=1))

        data2, _log2, n2 = self.store.prepare_attempt(self.c, self.d)
        self.assertEqual(n2, 2)
        self.assertEqual(list(data2.iterdir()), [], "destination must be empty")
        rejected = self.store.reject_dir(self.c, self.d, 1)
        self.assertTrue((rejected / "BDMV" / "index.bdmv").is_file(),
                        "the partial must be kept for inspection")

    def test_the_rejected_path_is_recorded_relative_to_the_collection(self):
        data, _, _ = self.store.prepare_attempt(self.c, self.d)
        (data / "junk").write_text("x")
        self.d.attempts.append(model.Attempt(attempt=1))
        self.store.prepare_attempt(self.c, self.d)
        recorded = self.d.attempts[-1].rejected_path
        self.assertFalse(os.path.isabs(recorded), recorded)
        self.assertIn("rejected", recorded)

    def test_old_rejected_attempts_are_pruned_but_logs_are_not(self):
        """Three failed Blu-ray attempts would be 100+ GB of partial output."""
        self.cfg = Config(media_path=self.root, keep_rejected_attempts=1)
        self.store = CollectionStore(self.cfg)
        for i in range(1, 4):
            data, log, _ = self.store.prepare_attempt(self.c, self.d)
            (data / "blob").write_bytes(b"x" * 10)
            log.write_text(f"attempt {i}")
            self.d.attempts.append(model.Attempt(attempt=i))
        kept = list((self.store.disc_dir(self.c, self.d) / "rejected").iterdir())
        self.assertEqual(len(kept), 1, "only the most recent partial is kept")
        logs = list((self.store.disc_dir(self.c, self.d) / "logs").iterdir())
        self.assertGreaterEqual(len(logs), 3, "logs are always kept")

    def test_rejected_bytes_reports_what_is_being_held(self):
        data, _, _ = self.store.prepare_attempt(self.c, self.d)
        (data / "blob").write_bytes(b"x" * 2048)
        self.d.attempts.append(model.Attempt(attempt=1))
        self.store.prepare_attempt(self.c, self.d)
        self.assertEqual(self.store.rejected_bytes(self.c), 2048)


class TestFinishAndCancel(StoreTestCase):
    def test_finish_renames_into_finished_and_marks_it_complete(self):
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        d.state = model.DONE
        target = self.store.finish(c)
        self.assertEqual(target.parent, self.cfg.finished_dir)
        self.assertTrue((target / ".complete").is_file())
        self.assertFalse(self.store.collection_dir(c).exists())
        self.assertEqual(self.store.load_all(), [])

    def test_relative_paths_still_resolve_after_the_finish_rename(self):
        """The directory moves, so absolute paths in the JSON would rot."""
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        data, log, _ = self.store.prepare_attempt(c, d)
        (data / "BDMV").mkdir()
        rel_data = self.store.relative(c, data)
        rel_log = self.store.relative(c, log)
        log.write_text("x")
        d.state = model.DONE
        target = self.store.finish(c)
        self.assertTrue((target / rel_data / "BDMV").is_dir())
        self.assertTrue((target / rel_log).is_file())

    def test_cancel_moves_rather_than_deletes(self):
        """A mis-click must not destroy hours of ripping."""
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        data, _, _ = self.store.prepare_attempt(c, d)
        (data / "partial.bin").write_bytes(b"x" * 100)
        target = self.store.cancel(c)
        self.assertEqual(target.parent, self.cfg.cancelled_dir)
        self.assertTrue((target / self.store.relative(c, data) / "partial.bin").is_file())

    def test_cancelled_collections_get_no_complete_marker(self):
        c = self.a_collection()
        self.assertFalse((self.store.cancel(c) / ".complete").exists())

    def test_finishing_while_a_disc_is_copying_is_refused(self):
        """Renaming out from under makemkvcon would orphan its output."""
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        d.state = model.COPYING
        with self.assertRaises(StoreError):
            self.store.finish(c)
        with self.assertRaises(StoreError):
            self.store.cancel(c)
        self.assertTrue(self.store.collection_dir(c).is_dir())

    def test_cross_filesystem_move_explains_itself(self):
        c = self.a_collection()
        real_replace = os.replace

        def only_fail_the_move(src, dst, *a, **k):
            if str(self.cfg.finished_dir) in str(dst):
                raise OSError("Invalid cross-device link")
            return real_replace(src, dst, *a, **k)

        with mock.patch("os.replace", side_effect=only_fail_the_move):
            with self.assertRaises(StoreError) as ctx:
                self.store.finish(c)
        self.assertIn("same filesystem", str(ctx.exception))
        self.assertTrue(self.store.collection_dir(c).is_dir(),
                        "a failed move must leave the collection where it was")


class TestRecovery(StoreTestCase):
    def test_interrupted_discs_are_failed_and_explained(self):
        c = self.a_collection()
        copying = self.store.add_disc(c, FakeDrive(label="A"))
        good = self.store.add_disc(c, FakeDrive(label="B"))
        copying.state = model.COPYING
        good.state = model.DONE
        self.store.save(c)

        reloaded = self.store.load_all()[0]
        recovered = self.store.recover_interrupted(reloaded)

        self.assertEqual([d.label for d in recovered], ["A"])
        disc = reloaded.discs[0]
        self.assertEqual(disc.state, model.FAILED)
        self.assertEqual(disc.last_attempt.error_kind, model.ERR_INTERRUPTED)
        self.assertIn("cannot resume", disc.last_attempt.failure_reason)
        self.assertEqual(reloaded.discs[1].state, model.DONE)

    def test_recovery_persists(self):
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        d.state = model.VERIFYING
        self.store.save(c)
        self.store.recover_interrupted(self.store.load_all()[0])
        self.assertEqual(self.store.load_all()[0].discs[0].state, model.FAILED)

    def test_recovery_is_a_no_op_when_nothing_was_running(self):
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        d.state = model.DONE
        self.assertEqual(self.store.recover_interrupted(c), [])

    def test_recovery_appends_rather_than_overwriting_a_finished_attempt(self):
        c = self.a_collection()
        d = self.store.add_disc(c, FakeDrive())
        d.attempts.append(model.Attempt(attempt=1, ended_at=model.now(),
                                        outcome="failure"))
        d.state = model.COPYING
        self.store.recover_interrupted(c)
        self.assertEqual(d.attempt_count, 2)


if __name__ == "__main__":
    unittest.main()
