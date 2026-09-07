"""Tests for the pure collection/disc data model."""

import unittest

from media_backup import model
from media_backup.model import Attempt, Collection, Disc, Title


def disc(state=model.PENDING, ordinal=1, label="DISC"):
    return Disc(ordinal=ordinal, label=label, state=state)


class TestRoundTrip(unittest.TestCase):
    def test_empty_collection(self):
        c = Collection(identifier="123")
        self.assertEqual(Collection.from_dict(c.to_dict()).to_dict(), c.to_dict())

    def test_fully_populated(self):
        c = Collection(identifier="883929665938", title="Spider-Verse",
                       expected_disc_count=2, notes="case is cracked")
        d = Disc(label="DISC1", media="optical_bd", disc_size_bytes=31506235392)
        d.titles = [Title(index=0, name="Feature", duration="2:20:05",
                          size_bytes=31506235392, source="00001.mpls")]
        d.attempts = [Attempt(attempt=1, device="/dev/sr0", exit_code=0,
                              message_codes={"5081": 1}, outcome="success")]
        c.discs = [d]
        self.assertEqual(Collection.from_dict(c.to_dict()).to_dict(), c.to_dict())

    def test_unknown_fields_from_a_future_version_are_ignored(self):
        data = Collection(identifier="x").to_dict()
        data["invented_later"] = {"a": 1}
        data["discs"] = [{**Disc().to_dict(), "also_new": True}]
        self.assertEqual(Collection.from_dict(data).identifier, "x")

    def test_missing_fields_fall_back_to_defaults(self):
        c = Collection.from_dict({"identifier": "only-this"})
        self.assertEqual(c.state, model.OPEN)
        self.assertEqual(c.schema_version, model.SCHEMA_VERSION)
        self.assertEqual(c.discs, [])


class TestDisc(unittest.TestCase):
    def test_state_predicates(self):
        self.assertTrue(disc(model.COPYING).is_active)
        self.assertFalse(disc(model.COPYING).is_terminal)
        self.assertTrue(disc(model.DONE).is_terminal)
        self.assertTrue(disc(model.DONE).is_good)
        self.assertTrue(disc(model.FAILED).is_terminal)
        self.assertFalse(disc(model.FAILED).is_good)

    def test_pending_is_neither_active_nor_terminal(self):
        """A disc waiting for the operator to press Start is neither."""
        d = disc(model.PENDING)
        self.assertFalse(d.is_active)
        self.assertFalse(d.is_terminal)

    def test_attempt_bookkeeping(self):
        d = disc()
        self.assertEqual(d.attempt_count, 0)
        self.assertIsNone(d.last_attempt)
        d.attempts = [Attempt(attempt=1), Attempt(attempt=2)]
        self.assertEqual(d.attempt_count, 2)
        self.assertEqual(d.last_attempt.attempt, 2)

    def test_display_name_falls_back(self):
        self.assertEqual(Disc(label="MOVIE").display_name, "MOVIE")
        self.assertEqual(Disc(makemkv_disc_name="MK").display_name, "MK")
        self.assertEqual(Disc(ordinal=3).display_name, "Disc 3")


class TestCollection(unittest.TestCase):
    def test_ordinals_increment(self):
        c = Collection()
        self.assertEqual(c.next_ordinal(), 1)
        c.discs = [disc(ordinal=1), disc(ordinal=2)]
        self.assertEqual(c.next_ordinal(), 3)

    def test_ordinals_do_not_reuse_a_removed_number(self):
        c = Collection()
        c.discs = [disc(ordinal=1), disc(ordinal=5)]
        self.assertEqual(c.next_ordinal(), 6)

    def test_has_active_jobs(self):
        c = Collection(discs=[disc(model.DONE), disc(model.COPYING)])
        self.assertTrue(c.has_active_jobs)
        c.discs[1].state = model.FAILED
        self.assertFalse(c.has_active_jobs)

    def test_lookup_by_id(self):
        d = disc()
        c = Collection(discs=[d])
        self.assertIs(c.disc(d.disc_id), d)
        self.assertIsNone(c.disc("nope"))


class TestCompleteness(unittest.TestCase):
    def test_all_good(self):
        c = Collection(discs=[disc(model.DONE), disc(model.DONE, ordinal=2)])
        self.assertTrue(c.is_complete)
        self.assertEqual(c.finish_warnings(), [])

    def test_empty_collection_is_not_complete(self):
        c = Collection()
        self.assertFalse(c.is_complete)
        self.assertIn("no discs", c.finish_warnings()[0])

    def test_a_failed_disc_blocks_completeness_but_not_finishing(self):
        c = Collection(discs=[disc(model.DONE), disc(model.FAILED, ordinal=2, label="D2")])
        self.assertFalse(c.is_complete)
        self.assertTrue(any("Not backed up: D2" in w for w in c.finish_warnings()))

    def test_active_job_is_warned_about(self):
        c = Collection(discs=[disc(model.COPYING, label="D1")])
        self.assertTrue(any("Still copying: D1" in w for w in c.finish_warnings()))

    def test_never_started_disc_is_warned_about(self):
        c = Collection(discs=[disc(model.DONE), disc(model.PENDING, ordinal=2, label="D2")])
        self.assertTrue(any("Never started: D2" in w for w in c.finish_warnings()))

    def test_declared_count_mismatch_is_the_box_set_guard(self):
        c = Collection(expected_disc_count=4, discs=[disc(model.DONE)])
        self.assertFalse(c.is_complete)
        self.assertTrue(any("declared as 4" in w for w in c.finish_warnings()))

    def test_declared_count_met(self):
        c = Collection(expected_disc_count=2,
                       discs=[disc(model.DONE), disc(model.DONE, ordinal=2)])
        self.assertTrue(c.is_complete)
        self.assertEqual(c.finish_warnings(), [])


if __name__ == "__main__":
    unittest.main()
