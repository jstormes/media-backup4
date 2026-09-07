"""Tests for which titles get saved, and which discs are handed back.

The numbers in the "real disc" cases are verbatim from the Blu-ray and the
DVD that went through the app on 2026-09-06/07, recovered from their
collection.json. The decoy cases are constructed, because producing one needs
a disc that carries the protection.
"""

import unittest

from media_backup import model
from media_backup.makemkv.selection import (
    Selection, SelectionPolicy, choose, expected_bytes,
)


def title(index, duration, size=1_000_000_000):
    return model.Title(index=index, duration=duration, size_bytes=size)


#: Verbatim from "Spider-Man: Across The Spider-Verse": one feature and a long
#: tail of extras, the longest of which is 14:49.
REAL_BLU_RAY = [
    title(0, "2:20:05", 31_506_235_392), title(1, "0:14:49", 1_911_000_000),
    title(2, "0:13:09", 1_696_000_000), title(3, "0:12:31", 1_610_000_000),
    title(4, "0:08:56", 1_148_000_000), title(5, "0:05:39", 730_000_000),
    title(6, "0:02:01", 419_000_000),
]

#: Verbatim from "Fresh Horses": a feature and three clips.
REAL_DVD = [
    title(0, "1:42:39", 4_245_336_064), title(1, "0:02:32", 99_866_624),
    title(2, "0:02:32", 101_634_048), title(3, "0:02:32", 80_885_760),
]


class TestRealDiscs(unittest.TestCase):
    def test_a_film_yields_its_feature_alone(self):
        chosen = choose(REAL_BLU_RAY)
        self.assertTrue(chosen)
        self.assertEqual([t.duration for t in chosen.titles], ["2:20:05"])

    def test_the_extras_are_not_mistaken_for_features(self):
        """An absolute "over ten minutes" would have taken four of these."""
        over_ten = [t for t in REAL_BLU_RAY if t.seconds >= 600]
        self.assertEqual(len(over_ten), 4, "which is why the rule is relative")
        self.assertEqual(len(choose(REAL_BLU_RAY).titles), 1)

    def test_a_dvd_yields_its_feature_alone(self):
        self.assertEqual([t.duration for t in choose(REAL_DVD).titles],
                         ["1:42:39"])

    def test_minlength_is_the_shortest_thing_chosen(self):
        """One pass with a length filter saves exactly the selection."""
        self.assertEqual(choose(REAL_DVD).min_length_seconds, 6159)

    def test_expected_bytes_is_what_the_scan_said_they_weigh(self):
        self.assertEqual(expected_bytes(choose(REAL_DVD)), 4_245_336_064)


class TestDecoys(unittest.TestCase):
    """Playlist obfuscation: many titles all cut to the feature's length."""

    def setUp(self):
        self.disc = [title(i, f"2:18:{i % 60:02d}") for i in range(40)]

    def test_a_protected_disc_is_refused(self):
        chosen = choose(self.disc)
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_DECOY_TITLES)

    def test_it_says_what_the_operator_should_do(self):
        self.assertIn("by hand", choose(self.disc).reason)
        self.assertIn("40 titles", choose(self.disc).reason)

    def test_nothing_is_selected_from_a_refused_disc(self):
        self.assertEqual(choose(self.disc).titles, ())

    def test_the_limit_is_where_the_policy_says(self):
        five = [title(i, "1:30:00") for i in range(5)]
        six = [title(i, "1:30:00") for i in range(6)]
        self.assertTrue(choose(five), "five episodes is a box set")
        self.assertFalse(choose(six), "six of the same length is not")

    def test_a_stricter_limit_can_be_set(self):
        two = [title(i, "1:30:00") for i in range(2)]
        self.assertFalse(choose(two, SelectionPolicy(max_feature_titles=1)))


class TestBoxSets(unittest.TestCase):
    def test_episodes_of_similar_length_are_all_features(self):
        disc = [title(i, f"0:45:{i:02d}") for i in range(4)]
        self.assertEqual(len(choose(disc).titles), 4)

    def test_a_feature_with_a_long_extra_still_takes_only_the_feature(self):
        disc = [title(0, "2:00:00"), title(1, "1:00:00")]
        self.assertEqual([t.duration for t in choose(disc).titles], ["2:00:00"])

    def test_an_alternate_cut_is_kept(self):
        disc = [title(0, "2:30:00"), title(1, "2:20:00")]
        self.assertEqual(len(choose(disc).titles), 2)


class TestNothingWorthSaving(unittest.TestCase):
    def test_a_disc_of_short_clips_is_refused(self):
        disc = [title(i, "0:03:00") for i in range(3)]
        chosen = choose(disc)
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_NO_FEATURE)
        self.assertIn("10 minutes", chosen.reason)

    def test_the_floor_can_be_lowered_for_a_disc_of_shorts(self):
        disc = [title(i, "0:03:00") for i in range(3)]
        self.assertTrue(choose(disc, SelectionPolicy(min_feature_seconds=60)))

    def test_an_empty_scan_is_refused_rather_than_guessed_at(self):
        chosen = choose([])
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_NO_FEATURE)

    def test_titles_with_no_duration_are_ignored(self):
        disc = [title(0, ""), title(1, "1:30:00")]
        self.assertEqual([t.duration for t in choose(disc).titles], ["1:30:00"])


class TestSelectionObject(unittest.TestCase):
    def test_it_is_falsey_when_it_refused(self):
        self.assertFalse(Selection(False))
        self.assertTrue(Selection(True))

    def test_titles_come_back_longest_first(self):
        disc = [title(0, "1:00:00"), title(1, "1:05:00")]
        self.assertEqual([t.duration for t in choose(disc).titles],
                         ["1:05:00", "1:00:00"])


if __name__ == "__main__":
    unittest.main()
