"""Tests for which audio track plays when nobody chooses one.

The track lists are verbatim from files in the archive, read with mkvmerge -J
on 2026-09-12. Nothing here runs mkvmerge or mkvpropedit: read_tracks and
apply_to take the subprocess runner as an argument, so the parsing and the
argv construction are tested against real output without a file or a disc.
"""

import json
import unittest

from media_backup import tracks
from media_backup.tracks import Change, Track


def mkvmerge_json(rows):
    """What mkvmerge -J prints, in the shape this module reads."""
    return json.dumps({"tracks": [
        {"id": i, "type": kind, "properties": {
            "number": number, "language": language, "default_track": default,
            "forced_track": forced, "track_name": name,
            **({"audio_channels": channels} if kind == "audio" else {})}}
        for i, (number, kind, language, default, forced, name, channels)
        in enumerate(rows)]})


class FakeRun:
    """Stands in for subprocess.run, recording what it was asked to do."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return self


#: Challenge of the Super Friends, disc 1 title 0 (DVD). MakeMKV flagged the
#: English mono track -- the disc's own first track -- as default, and also
#: flagged an English VobSub, which is why subtitles come up over English
#: dialogue on this file. Only the audio is this module's business.
SUPERFRIENDS = [
    Track(1, "video", "eng", False, False),
    Track(2, "audio", "eng", True, False, "Mono", 1),
    Track(3, "audio", "spa", False, False, "Mono", 1),
    Track(4, "audio", "eng", False, False, "Stereo", 2),
    Track(5, "subtitles", "eng", True, False),
    Track(6, "subtitles", "fre", False, False),
    Track(7, "subtitles", "spa", False, False),
    Track(8, "subtitles", "eng", False, False),
]

#: Speed Racer / Mach GoGoGo disc 1, title 0 (BD): Japanese audio only, being
#: a DTS-HD MA track and the DTS core inside it, with English PGS subtitles.
#: The disc that proved a preferred-language *filter* writes silence.
SPEED_RACER = [
    Track(1, "video", "eng", True, False),
    Track(2, "audio", "jpn", True, False, "Surround 5.1", 6),
    Track(3, "audio", "jpn", False, False, "Surround 5.1", 6),
    Track(4, "subtitles", "eng", True, False),
    Track(5, "subtitles", "eng", False, False),
]


class TestReadingTheTracks(unittest.TestCase):
    def test_the_matroska_track_number_is_what_is_read(self):
        """Not ffprobe's stream index and not mkvmerge's id.

        mkvpropedit selects with track:@N on the TrackNumber. On this file the
        ids are 0-based and the numbers 1-based, so using the id would edit
        the track before the intended one.
        """
        run = FakeRun(mkvmerge_json([
            (1, "video", "eng", False, False, "", 0),
            (2, "audio", "eng", True, False, "Mono", 1)]))
        got = tracks.read_tracks("x.mkv", run=run)
        self.assertEqual([t.number for t in got], [1, 2])
        self.assertEqual(run.calls, [["mkvmerge", "-J", "x.mkv"]])

    def test_a_track_with_no_language_reads_as_english(self):
        """Matroska says an absent language element means English.

        mkvmerge reports "eng" for it, so this is the specification agreeing
        with the tool rather than a guess made here.
        """
        run = FakeRun(json.dumps({"tracks": [
            {"id": 0, "type": "audio",
             "properties": {"number": 1, "default_track": False}}]}))
        self.assertTrue(tracks.read_tracks("x.mkv", run=run)[0].is_english)

    def test_warnings_from_mkvmerge_are_not_a_failure(self):
        """It exits 1 for warnings and still prints usable JSON."""
        run = FakeRun(mkvmerge_json([(1, "audio", "eng", True, False, "", 2)]),
                      returncode=1)
        self.assertEqual(len(tracks.read_tracks("x.mkv", run=run)), 1)

    def test_a_real_failure_says_so(self):
        run = FakeRun("", returncode=2, stderr="No such file")
        with self.assertRaises(tracks.TrackError):
            tracks.read_tracks("nope.mkv", run=run)

    def test_output_that_is_not_json_says_so(self):
        with self.assertRaises(tracks.TrackError):
            tracks.read_tracks("x.mkv", run=FakeRun("Segmentation fault"))


class TestChoosingTheDefault(unittest.TestCase):
    def test_english_already_default_changes_nothing(self):
        """The disc said which mix is the main one and it is English.

        Rewriting the header to say what it already says would touch every
        file in the archive for no effect.
        """
        self.assertEqual(tracks.plan(SUPERFRIENDS), [])

    def test_the_first_english_track_is_chosen_not_the_loudest(self):
        """Superfriends with the disc's default moved to the Spanish track.

        First in track order, not most channels: the order is the authoring
        order, and picking by channel count would promote this disc's later
        stereo remix over its original mono mix.
        """
        rows = [t for t in SUPERFRIENDS]
        rows[1] = Track(2, "audio", "eng", False, False, "Mono", 1)
        rows[2] = Track(3, "audio", "spa", True, False, "Mono", 1)
        got = tracks.plan(rows)
        self.assertEqual([(c.number, c.value) for c in got],
                         [(2, True), (3, False)])

    def test_a_japanese_only_title_is_left_alone(self):
        """Speed Racer. There is no English audio to prefer.

        Inventing a preference here is what the selection-string attempt did,
        and it wrote 3.85 GB with no audio at all.
        """
        self.assertEqual(tracks.plan(SPEED_RACER), [])

    def test_subtitles_are_never_touched(self):
        """Including the English VobSub that MakeMKV flagged default.

        Whether captions should come up automatically is a different question
        with a different answer per disc; answering it here would put them
        over English dialogue on every DVD that carries them.
        """
        rows = [t for t in SUPERFRIENDS]
        rows[1] = Track(2, "audio", "spa", True, False, "Mono", 1)
        rows[2] = Track(3, "audio", "eng", False, False, "Mono", 1)
        self.assertTrue(all(c.number in (2, 3) for c in tracks.plan(rows)))

    def test_a_forced_track_is_never_touched(self):
        forced = Track(2, "audio", "eng", False, True, "Forced", 2)
        plain = Track(3, "audio", "fre", True, False, "", 6)
        self.assertEqual(tracks.plan([forced, plain]), [])

    def test_a_file_with_one_undefaulted_english_track_gets_the_flag(self):
        got = tracks.plan([Track(1, "video", "eng", True, False),
                           Track(2, "audio", "eng", False, False, "", 2)])
        self.assertEqual([(c.number, c.value) for c in got], [(2, True)])


class TestWritingTheFlags(unittest.TestCase):
    def test_the_command_selects_by_track_number(self):
        argv = tracks.argv_for("/x/Film.mkv", [Change(4, "flag-default", True, ""),
                                               Change(2, "flag-default", False, "")])
        self.assertEqual(argv, ["mkvpropedit", "/x/Film.mkv",
                                "--edit", "track:@4", "--set", "flag-default=1",
                                "--edit", "track:@2", "--set", "flag-default=0"])

    def test_nothing_to_do_runs_nothing(self):
        """Not even mkvpropedit -- an untouched file keeps its mtime."""
        run = FakeRun(mkvmerge_json([
            (1, "audio", "eng", True, False, "Mono", 1)]))
        self.assertEqual(tracks.apply_to("x.mkv", run=run), [])
        self.assertEqual(len(run.calls), 1, "read only, no edit")

    def test_a_failed_edit_is_raised(self):
        class Failing(FakeRun):
            def __call__(self, argv, **kwargs):
                self.calls.append(argv)
                if argv[0] == "mkvpropedit":
                    return FakeRun("", returncode=2, stderr="read-only file")
                return self
        run = Failing(mkvmerge_json([
            (1, "audio", "spa", True, False, "", 6),
            (2, "audio", "eng", False, False, "", 6)]))
        with self.assertRaises(tracks.TrackError):
            tracks.apply_to("x.mkv", run=run)


if __name__ == "__main__":
    unittest.main()
