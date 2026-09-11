"""Tests for which titles get saved, and which discs are handed back.

The numbers in the "real disc" cases are verbatim from the Blu-ray and the
DVD that went through the app on 2026-09-06/07, recovered from their
collection.json. The decoy cases are constructed, because producing one needs
a disc that carries the protection.
"""

import unittest

from media_backup import model
from media_backup.makemkv.selection import (
    CUT_VARIANTS, OBFUSCATION_ORDERINGS, SEPARATE_WORKS, SINGLE, Selection,
    choose, expected_bytes, match_files, obfuscation, permutation_classes,
    relationship, segments, shared_ratio,
)


def title(index, duration, size=1_000_000_000, segments="", source="",
          chapters=0):
    return model.Title(index=index, duration=duration, size_bytes=size,
                       segments=segments, source=source, chapters=chapters)


def decoy(index, duration, source="", chapters=0):
    """One of a pile of playlists cut to the feature's length.

    They overlap: clips 900 and 901 are in every one of them, which is what
    makes them the same footage dressed up repeatedly rather than a box set of
    separate works. Disjoint clip lists are a box set and are saved.
    """
    return title(index, duration, segments=f"{index},900,901", source=source,
                 chapters=chapters)


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
    """Everything the scan reported, longest first. No title is left behind.

    MakeMKV has already applied its own minimum length to this list, and that
    is the only filter in the pipeline. Nothing here re-judges it: a threshold
    that could drop a trailer could drop the second film of a double bill, and
    on 2026-09-08 it did, twice.
    """

    def test_a_film_disc_yields_its_feature_and_every_extra(self):
        chosen = choose(REAL_BLU_RAY)
        self.assertTrue(chosen)
        self.assertEqual([t.duration for t in chosen.titles],
                         ["2:20:05", "0:14:49", "0:13:09", "0:12:31",
                          "0:08:56", "0:05:39", "0:02:01"])

    def test_a_dvd_yields_its_feature_and_its_three_clips(self):
        self.assertEqual([t.duration for t in choose(REAL_DVD).titles],
                         ["1:42:39", "0:02:32", "0:02:32", "0:02:32"])

    def test_a_double_feature_keeps_both_films(self):
        """Verbatim from the "Firehead and Last Lives" DVD.

        The shorter film is 87% of the longer, so the old 90% ratio dropped
        it without a word, and both films spell their cell range the same way,
        so the old duplicate filter dropped one of those too.
        """
        disc = [title(0, "1:23:43", segments="1-15"),
                title(1, "1:35:43", segments="1-12")]
        self.assertEqual([t.duration for t in choose(disc).titles],
                         ["1:35:43", "1:23:43"])

    def test_a_season_disc_keeps_every_episode(self):
        self.assertEqual(len(choose([title(i, "0:45:00") for i in range(8)])
                             .titles), 8)

    def test_a_kids_disc_of_short_shorts_keeps_every_one(self):
        self.assertEqual(len(choose([title(i, f"0:11:{i:02d}") for i in range(12)])
                             .titles), 12)

    def test_a_disc_with_no_timed_titles_is_refused(self):
        chosen = choose([title(0, "")])
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_NO_FEATURE)
        self.assertFalse(chosen.needs_operator, "there is nothing to help with")

    def test_expected_bytes_is_what_the_scan_said_they_weigh(self):
        self.assertEqual(expected_bytes(choose(REAL_DVD)),
                         4_245_336_064 + 99_866_624 + 101_634_048 + 80_885_760)



class TestSegmentsSpelling(unittest.TestCase):
    """Blu-rays list clips; DVDs give a cell range. Both are real."""

    def test_a_blu_ray_clip_list(self):
        self.assertEqual(segments(title(0, "1:00:00", segments="123,141,125")),
                         ["123", "141", "125"])

    def test_a_dvd_cell_range(self):
        self.assertEqual(len(segments(title(0, "1:00:00", segments="1-28"))), 28)

    def test_nothing_reported_is_handled(self):
        self.assertEqual(segments(title(0, "1:00:00")), [])



class TestTellingTheCutsApart(unittest.TestCase):
    """Hancock carries two cuts. A later process has to be able to say which.

    The clip lists say it outright. Seamless branching stores the common
    footage once and the differing segments separately, so the two cuts share
    a backbone and each carries its own -- 10 shared clips, 9 exclusive to
    each, measured off the disc on 2026-09-07.
    """

    def setUp(self):
        # Every title is copied now, so the two cuts arrive as four titles:
        # each authored twice. The pair to compare is one of each cut.
        self.chosen = choose(HANCOCK).titles
        self.extended = next(t for t in self.chosen if t.duration == "1:42:14")
        self.theatrical = next(t for t in self.chosen if t.duration == "1:32:13")

    def test_the_two_cuts_share_a_backbone(self):
        shared = set(segments(self.extended)) & set(segments(self.theatrical))
        self.assertEqual(len(shared), 10)

    def test_and_each_carries_its_own_segments(self):
        a, b = set(segments(self.theatrical)), set(segments(self.extended))
        self.assertEqual(len(a - b), 9)
        self.assertEqual(len(b - a), 9)

    def test_they_read_as_cuts_of_one_film_not_two_films(self):
        self.assertEqual(relationship([self.extended, self.theatrical]),
                         CUT_VARIANTS)
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


#: Verbatim from "Knives Out" (Lionsgate), read off the disc 2026-09-11. The
#: disc scans to 283 titles, 201 of which are these same fifteen clips in 201
#: different orders. These are the first ten of that pool.
KNIVES_OUT_POOL = [
    (1, "510,505,513,512,502,506,507,508,504,503,520,509,519,518,515", "00008.mpls"),
    (3, "510,505,513,512,502,506,503,508,519,518,509,507,504,520,515", "00027.mpls"),
    (6, "510,505,513,512,502,508,520,504,509,519,506,503,518,507,515", "00033.mpls"),
    (7, "510,505,513,512,502,504,519,520,503,507,508,509,518,506,515", "00034.mpls"),
    (11, "510,505,513,512,502,520,518,509,508,507,506,503,504,519,515", "00047.mpls"),
    (12, "510,505,513,512,502,508,518,509,519,506,507,503,504,520,515", "00049.mpls"),
    (13, "510,505,513,512,502,507,506,518,509,504,520,508,519,503,515", "00050.mpls"),
    (14, "510,505,513,512,502,503,519,504,518,507,508,506,509,520,515", "00052.mpls"),
    (15, "510,505,513,512,502,506,520,503,518,519,507,509,508,504,515", "00056.mpls"),
    (16, "510,505,513,512,502,520,506,507,504,519,508,509,503,518,515", "00060.mpls"),
]

#: Verbatim from "Saban's Power Rangers" (Lionsgate), 2026-09-11: 308 titles,
#: 287 of them thirteen clips in 287 different orders. First ten of the pool.
POWER_RANGERS_POOL = [
    (0, "505,507,502,501,506,509,504,513,511,508,514,512,510", "00009.mpls"),
    (1, "505,501,507,502,506,511,508,513,509,504,512,514,510", "00014.mpls"),
    (2, "505,501,502,507,506,509,511,504,508,513,514,512,510", "00017.mpls"),
    (3, "505,507,502,501,506,509,504,511,508,513,514,512,510", "00021.mpls"),
    (4, "505,501,507,502,506,512,513,509,508,504,514,511,510", "00028.mpls"),
    (5, "505,501,507,502,506,508,512,511,504,513,509,514,510", "00029.mpls"),
    (6, "505,501,507,502,506,508,512,504,509,514,513,511,510", "00034.mpls"),
    (7, "505,501,507,502,506,508,509,513,512,514,504,511,510", "00037.mpls"),
    (8, "505,501,507,502,506,514,511,513,512,504,509,508,510", "00038.mpls"),
    (9, "505,502,507,501,506,509,511,513,504,508,514,512,510", "00040.mpls"),
]

#: The one that is actually the film, matched against the segment map the
#: MakeMKV forum publishes for the US retail pressing. Ripped by hand and
#: verified 2026-09-11; it is one of the 287 and looks like all the others.
POWER_RANGERS_REAL = (
    289, "505,501,507,502,506,509,511,513,508,504,514,512,510", "00988.mpls")


def pool(rows, duration):
    return [title(i, duration, 24_000_000_000, segs, src, chapters=16)
            for i, segs, src in rows]


class TestPlaylistObfuscation(unittest.TestCase):
    """Discs that hide the feature among permutations of its own clip list.

    Copying everything is the policy and it is right, up to the disc that
    turns it into terabytes. Power Rangers projected 7.5 TB from a 46.6 GiB
    disc onto a volume with 1.5 TB free and three other jobs on it, and wrote
    106 GiB of decoys before anyone noticed. Knives Out is the same shape.

    So these discs are refused -- not resolved. Nothing here picks the film.
    """

    def test_knives_out_is_refused(self):
        chosen = choose(pool(KNIVES_OUT_POOL, "2:10:13"))
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_DECOY_TITLES)

    def test_power_rangers_is_refused(self):
        chosen = choose(pool(POWER_RANGERS_POOL, "2:03:58"))
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_DECOY_TITLES)

    def test_the_refusal_is_a_person_not_a_retry(self):
        """Retrying finds the same 283 titles. A human has to break the tie."""
        chosen = choose(pool(KNIVES_OUT_POOL, "2:10:13"))
        self.assertTrue(chosen.needs_operator)

    def test_the_whole_list_comes_back_with_the_refusal(self):
        """Whoever resolves this matches a published clip map against these.

        A count cannot be matched against anything, so the titles travel with
        the refusal even though nothing will be copied from them yet.
        """
        titles = pool(KNIVES_OUT_POOL, "2:10:13")
        chosen = choose(titles)
        self.assertEqual(len(chosen.titles), len(titles))

    def test_the_reason_says_what_was_seen(self):
        chosen = choose(pool(POWER_RANGERS_POOL, "2:03:58"))
        self.assertIn("10 titles", chosen.reason)
        self.assertIn("13 clips", chosen.reason)
        self.assertIn("2:03:58", chosen.reason)

    def test_the_real_playlist_is_not_singled_out(self):
        """It is in the pool and it looks like every other member of it.

        This is the whole difficulty: title 289 is the film, and nothing in
        the scan says so. If this test ever starts passing for the wrong
        reason -- because something in here learned to pick -- that is the
        mistake section 9 of the SPEC exists to prevent.
        """
        rows = POWER_RANGERS_POOL + [POWER_RANGERS_REAL]
        chosen = choose(pool(rows, "2:03:58"))
        self.assertFalse(chosen)
        self.assertEqual(chosen.error_kind, model.ERR_DECOY_TITLES)

    def test_content_authored_twice_is_not_obfuscation(self):
        """Hancock offers each cut twice, with an identical clip list.

        Same clips *and* same order is a disc that authored its feature
        twice. It is copied twice, as it has been since 2026-09-08.
        """
        self.assertTrue(choose(HANCOCK))
        self.assertEqual(len(choose(HANCOCK).titles), 4)

    def test_branched_cuts_are_not_obfuscation(self):
        """Each cut carries clips the other lacks. That is what branching is."""
        self.assertIsNone(obfuscation(HANCOCK))

    def test_a_double_feature_is_not_obfuscation(self):
        disc = [title(0, "1:23:43", segments="1-15"),
                title(1, "1:35:43", segments="1-12")]
        self.assertIsNone(obfuscation(disc))

    def test_a_season_of_episodes_is_not_obfuscation(self):
        """Same runtime, different clips. Length alone means nothing."""
        disc = [title(i, "0:45:00", segments=f"{i}") for i in range(12)]
        self.assertIsNone(obfuscation(disc))

    def test_one_reordering_short_of_the_threshold_is_left_alone(self):
        """The gap measured on real discs is 201-and-287 against 1.

        Nothing in a 106-disc archive scored between them, so the threshold
        is not balanced on a knife edge -- but it is a threshold, and a disc
        under it is copied rather than refused.
        """
        rows = POWER_RANGERS_POOL[:OBFUSCATION_ORDERINGS - 1]
        self.assertTrue(choose(pool(rows, "2:03:58")))

    def test_permutation_classes_group_by_clips_not_order(self):
        classes = permutation_classes(pool(KNIVES_OUT_POOL, "2:10:13"))
        self.assertEqual(len(classes), 1, "one clip list, ten orders")
        self.assertEqual(len(next(iter(classes.values()))), 10)

    def test_a_dvd_cell_range_is_expanded_before_grouping(self):
        """DVDs spell a clip list "1-28"; the grouping must see through it."""
        classes = permutation_classes([title(0, "1:00:00", segments="1-3"),
                                       title(1, "1:00:00", segments="3,2,1")])
        self.assertEqual(len(classes), 1)
