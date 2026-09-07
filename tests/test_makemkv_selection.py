"""Tests for which titles get saved, and which discs are handed back.

The numbers in the "real disc" cases are verbatim from the Blu-ray and the
DVD that went through the app on 2026-09-06/07, recovered from their
collection.json. The decoy cases are constructed, because producing one needs
a disc that carries the protection.
"""

import unittest

from media_backup import model
from media_backup.makemkv.selection import (
    CUT_VARIANTS, SEPARATE_WORKS, SINGLE, Selection, SelectionPolicy, choose,
    describe, distinct, expected_bytes, is_degenerate, match_files,
    relationship, segments, shared_ratio,
)


def title(index, duration, size=1_000_000_000, segments="", source="",
          chapters=0):
    return model.Title(index=index, duration=duration, size_bytes=size,
                       segments=segments, source=source, chapters=chapters)


# Verbatim from the Hancock Blu-ray, read off the disc on 2026-09-07. Two
# films -- a theatrical cut and an extended cut -- each authored twice with an
# identical clip list, which is what MakeMKV reports as four titles.
HANCOCK_THEATRICAL = ("123,124,125,126,127,128,129,130,131,132,133,134,135,"
                      "136,137,138,139,140,150")
HANCOCK_EXTENDED = ("123,141,125,142,127,143,129,144,131,145,133,146,135,"
                    "147,137,148,139,149,150")
HANCOCK = [
    title(0, "1:32:13", 20_000_000_000, HANCOCK_THEATRICAL, "00001.mpls", 16),
    title(1, "1:42:14", 22_000_000_000, HANCOCK_EXTENDED, "00002.mpls", 16),
    title(2, "1:32:13", 20_000_000_000, HANCOCK_THEATRICAL, "00003.mpls", 16),
    title(3, "1:42:14", 22_000_000_000, HANCOCK_EXTENDED, "00004.mpls", 16),
]

#: The two obfuscation playlists on that disc, as parsed from its MPLS: a
#: hundred play items pointing at one clip. MakeMKV filters these before we
#: see them; this is the backstop for a disc where it does not.
HANCOCK_DECOYS = [
    title(4, "1:36:20", 10**10, ",".join(["151"] * 100), "00529.mpls"),
    title(5, "1:37:18", 10**10, "117," + ",".join(["151"] * 100), "00530.mpls"),
]


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

    def test_expected_bytes_is_what_the_scan_said_they_weigh(self):
        self.assertEqual(expected_bytes(choose(REAL_DVD)), 4_245_336_064)


class TestARealProtectedDisc(unittest.TestCase):
    """Hancock: four feature-length titles that are two films."""

    def test_the_two_cuts_survive_and_the_copies_do_not(self):
        chosen = choose(HANCOCK)
        self.assertTrue(chosen)
        self.assertEqual([t.source for t in chosen.titles],
                         ["00002.mpls", "00001.mpls"])

    def test_saving_all_four_would_write_every_frame_twice(self):
        self.assertEqual(len(HANCOCK), 4)
        self.assertEqual(len(choose(HANCOCK).titles), 2)

    def test_the_obfuscation_playlists_are_rejected_on_their_structure(self):
        """A hundred play items pointing at one clip is not a film."""
        chosen = choose(HANCOCK + HANCOCK_DECOYS)
        self.assertEqual([t.source for t in chosen.titles],
                         ["00002.mpls", "00001.mpls"])

    def test_the_decoys_alone_leave_nothing_to_save(self):
        self.assertFalse(choose(HANCOCK_DECOYS))

    def test_a_single_clip_title_is_not_mistaken_for_a_decoy(self):
        """Plenty of real titles are one clip; DVDs report no segments at all."""
        self.assertFalse(is_degenerate(title(0, "1:30:00", segments="123")))
        self.assertFalse(is_degenerate(title(0, "1:30:00", segments="")))

    def test_duplicates_are_dropped_before_the_decoy_count(self):
        """Three cuts authored in pairs is six titles and two too many.

        Counting before deduplicating would refuse a disc that is only
        repeating itself.
        """
        disc = []
        for i in range(3):
            segs = f"{i}00,{i}01,{i}02,{i}03"
            disc.append(title(i * 2, "1:30:00", segments=segs))
            disc.append(title(i * 2 + 1, "1:30:00", segments=segs))
        self.assertEqual(len(disc), 6)
        self.assertTrue(choose(disc), "six titles, but only three films")
        self.assertEqual(len(choose(disc).titles), 3)


class TestPreferringTheRicherCopy(unittest.TestCase):
    """A disc can offer the same footage twice with different track sets.

    Hancock's feature comes as 23 streams and as 15 -- same runtime to the
    frame, same seven audio tracks, fifteen subtitle tracks against seven.
    Keeping whichever came first filed the poorer one about half the time.
    """

    def setUp(self):
        segs = "1,2,3,4,5"
        self.poor = title(0, "1:32:13", segments=segs, source="00003.mpls")
        self.poor.streams = 15
        self.rich = title(1, "1:32:13", segments=segs, source="00001.mpls")
        self.rich.streams = 23

    def test_the_richer_copy_wins_whichever_comes_first(self):
        for order in ([self.poor, self.rich], [self.rich, self.poor]):
            with self.subTest(first=order[0].source):
                kept = distinct(order)
                self.assertEqual([t.source for t in kept], ["00001.mpls"])

    def test_only_one_survives(self):
        self.assertEqual(len(distinct([self.poor, self.rich])), 1)

    def test_titles_with_different_clip_lists_both_survive(self):
        other = title(2, "1:42:14", segments="9,8,7")
        kept = distinct([self.rich, other])
        self.assertEqual(len(kept), 2)

    def test_the_scan_order_is_kept(self):
        """Order carries meaning downstream; the tie-break must not shuffle."""
        a = title(0, "1:42:14", segments="9,8,7")
        kept = distinct([a, self.poor, self.rich])
        self.assertEqual([t.index for t in kept], [0, 1])

    def test_titles_reporting_no_streams_still_deduplicate(self):
        a = title(0, "1:30:00", segments="1,2")
        b = title(1, "1:30:00", segments="1,2")
        self.assertEqual(len(distinct([a, b])), 1)


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


class TestCanWeDecide(unittest.TestCase):
    """The question the operator actually needs answered."""

    def test_one_feature_is_never_in_doubt(self):
        chosen = choose([title(0, "1:40:00", segments="1,2,3", source="a")])
        self.assertTrue(chosen)
        self.assertFalse(chosen.needs_operator)

    def test_two_genuine_cuts_are_decided(self):
        """Hancock: the cuts differ in which clips they use, not the order."""
        self.assertTrue(choose(HANCOCK))
        self.assertFalse(choose(HANCOCK).needs_operator)

    def test_the_same_clips_in_a_different_order_is_not_two_cuts(self):
        disc = [
            title(0, "1:50:00", segments="1,2,3,4,5", source="00800.mpls", chapters=12),
            title(1, "1:50:00", segments="5,4,3,2,1", source="00801.mpls", chapters=12),
        ]
        chosen = choose(disc)
        self.assertFalse(chosen)
        self.assertTrue(chosen.needs_operator)
        self.assertEqual(chosen.error_kind, model.ERR_AMBIGUOUS_TITLES)
        self.assertIn("different order", chosen.reason)

    def test_a_feature_length_title_with_one_chapter_is_doubted(self):
        disc = [
            title(0, "1:50:00", segments="1,2,3", source="a", chapters=14),
            title(1, "1:49:00", segments="4,5,6", source="b", chapters=1),
        ]
        chosen = choose(disc)
        self.assertFalse(chosen)
        self.assertTrue(chosen.needs_operator)
        self.assertIn("one chapter", chosen.reason)

    def test_too_many_candidates_needs_a_person(self):
        disc = [title(i, "1:50:00", segments=f"{i}a,{i}b,{i}c") for i in range(9)]
        chosen = choose(disc)
        self.assertTrue(chosen.needs_operator)
        self.assertEqual(chosen.error_kind, model.ERR_DECOY_TITLES)

    def test_a_disc_with_nothing_on_it_does_not_need_a_person(self):
        """There is nothing for them to help with."""
        chosen = choose([title(0, "0:03:00")])
        self.assertFalse(chosen)
        self.assertFalse(chosen.needs_operator)

    def test_the_candidates_are_handed_over_to_choose_between(self):
        disc = [title(i, "1:50:00", segments=f"{i}a,{i}b", source=f"0080{i}.mpls",
                      chapters=12) for i in range(9)]
        chosen = choose(disc)
        self.assertEqual(len(chosen.candidates), 9)
        self.assertIn("00800.mpls", chosen.reason)
        self.assertIn("1:50:00", chosen.reason)
        self.assertIn("12 chapters", chosen.reason)
        self.assertIn("MakeMKV", chosen.reason, "and what to do about it")

    def test_a_long_candidate_list_is_trimmed(self):
        disc = [title(i, "1:50:00", segments=f"{i}a,{i}b") for i in range(30)]
        listed = choose(disc).reason
        self.assertIn("...and 22 more", listed)

    def test_candidates_are_carried_even_when_the_disc_is_decided(self):
        self.assertEqual(len(choose(HANCOCK).candidates), 2)


class TestSegmentsSpelling(unittest.TestCase):
    """Blu-rays list clips; DVDs give a cell range. Both are real."""

    def test_a_blu_ray_clip_list(self):
        self.assertEqual(segments(title(0, "1:00:00", segments="123,141,125")),
                         ["123", "141", "125"])

    def test_a_dvd_cell_range(self):
        self.assertEqual(len(segments(title(0, "1:00:00", segments="1-28"))), 28)

    def test_a_range_is_not_mistaken_for_a_decoy(self):
        """28 distinct cells, spelled as a range, is an ordinary DVD title."""
        self.assertFalse(is_degenerate(title(0, "1:42:39", segments="1-28")))

    def test_nothing_reported_is_handled(self):
        self.assertEqual(segments(title(0, "1:00:00")), [])


class TestDescribe(unittest.TestCase):
    def test_it_names_what_the_operator_will_see_in_makemkv(self):
        line = describe([title(0, "1:42:14", segments="1,2,3",
                               source="00002.mpls", chapters=16)])
        self.assertIn("00002.mpls", line)
        self.assertIn("1:42:14", line)
        self.assertIn("16 chapters", line)
        self.assertIn("3 of 3 clips distinct", line)


class TestTellingTheCutsApart(unittest.TestCase):
    """Hancock carries two cuts. A later process has to be able to say which.

    The clip lists say it outright. Seamless branching stores the common
    footage once and the differing segments separately, so the two cuts share
    a backbone and each carries its own -- 10 shared clips, 9 exclusive to
    each, measured off the disc on 2026-09-07.
    """

    def setUp(self):
        self.chosen = choose(HANCOCK).titles
        self.extended, self.theatrical = self.chosen

    def test_the_two_cuts_share_a_backbone(self):
        shared = set(segments(self.extended)) & set(segments(self.theatrical))
        self.assertEqual(len(shared), 10)

    def test_and_each_carries_its_own_segments(self):
        a, b = set(segments(self.theatrical)), set(segments(self.extended))
        self.assertEqual(len(a - b), 9)
        self.assertEqual(len(b - a), 9)

    def test_they_read_as_cuts_of_one_film_not_two_films(self):
        self.assertEqual(relationship(self.chosen), CUT_VARIANTS)
        self.assertAlmostEqual(shared_ratio(self.extended, self.theatrical),
                               10 / 19, places=2)

    def test_the_longer_one_is_first(self):
        """Which is the extended cut is the one thing duration settles."""
        self.assertEqual(self.extended.duration, "1:42:14")
        self.assertEqual(self.theatrical.duration, "1:32:13")
        self.assertGreater(self.extended.seconds, self.theatrical.seconds)

    def test_episodes_are_not_cuts(self):
        """Different content shares nothing, however similar the runtimes."""
        disc = [title(i, "0:45:00", segments=f"{i}0,{i}1,{i}2,{i}3")
                for i in range(4)]
        self.assertEqual(relationship(disc), SEPARATE_WORKS)

    def test_one_title_relates_to_nothing(self):
        self.assertEqual(relationship([title(0, "1:40:00", segments="1,2")]),
                         SINGLE)

    def test_titles_with_no_segments_are_not_called_cuts(self):
        """A DVD that reports nothing must not be guessed at."""
        disc = [title(0, "1:40:00"), title(1, "1:38:00")]
        self.assertEqual(relationship(disc), SEPARATE_WORKS)


class TestMatchingFilesToTitles(unittest.TestCase):
    """Which .mkv on disk is which title -- the link the archive needs."""

    def test_the_suggested_name_is_used_when_it_is_right(self):
        titles = [title(0, "1:40:00", 4_000_000_000)]
        titles[0].suggested_file = "Hancock-A1_t00.mkv"
        got = match_files(titles, [("Hancock-A1_t00.mkv", 3_900_000_000)])
        self.assertEqual(got, {0: "Hancock-A1_t00.mkv"})

    def test_the_designator_carries_when_the_index_has_moved(self):
        """The _tNN counts within whatever list MakeMKV was showing.

        The save pass runs a different --minlength from the scan, so the
        suggested name can be stale while the designator is not.
        """
        one = title(0, "1:40:00", 4_000_000_000)
        one.suggested_file, one.designator = "Hancock-A1_t03.mkv", "A1"
        got = match_files([one], [("Hancock-A1_t00.mkv", 3_900_000_000)])
        self.assertEqual(got, {0: "Hancock-A1_t00.mkv"})

    def test_size_settles_it_when_nothing_else_does(self):
        big = title(0, "1:42:14", 22_000_000_000)
        small = title(1, "1:32:13", 20_000_000_000)
        got = match_files([big, small],
                          [("b.mkv", 19_500_000_000), ("a.mkv", 21_500_000_000)])
        self.assertEqual(got, {0: "a.mkv", 1: "b.mkv"})

    def test_a_file_is_never_claimed_twice(self):
        a = title(0, "1:40:00", 4_000_000_000)
        b = title(1, "1:39:00", 4_000_000_001)
        got = match_files([a, b], [("only.mkv", 4_000_000_000)])
        self.assertEqual(list(got.values()), ["only.mkv"])

    def test_nothing_written_matches_nothing(self):
        self.assertEqual(match_files([title(0, "1:40:00")], []), {})


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
