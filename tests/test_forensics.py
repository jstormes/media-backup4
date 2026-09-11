"""Tests for the obfuscated-disc forensics capture.

The disc reader itself (``media_backup.udf``) is not tested here: it needs a
real Blu-ray in a real drive, and a fake UDF image is a test of the fake. What
is tested is everything that decides *what* gets captured and what is made of
it afterwards, all of which is pure or filesystem-only.
"""

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from media_backup import forensics
from media_backup.makemkv import selection


class TestWhatGetsCaptured(unittest.TestCase):
    """The payload is 49 GB and the answer is in the other 80 MB."""

    def copy(self, path, size=1000, assets=False):
        return forensics._should_copy(
            path, size, forensics.DEFAULT_FILE_CAP, assets)[0]

    def test_the_encrypted_payload_is_never_captured(self):
        self.assertFalse(self.copy("/BDMV/STREAM/00500.m2ts", 30 * 10**9))

    def test_navigation_data_is_captured(self):
        for path in ("/BDMV/index.bdmv", "/BDMV/MovieObject.bdmv",
                     "/BDMV/PLAYLIST/00988.mpls", "/BDMV/BDJO/00000.bdjo",
                     "/BDMV/CLIPINF/00500.clpi"):
            self.assertTrue(self.copy(path), path)

    def test_a_java_archive_is_captured_whole_however_big(self):
        """It is the thing we are collecting. A cap must not reach it."""
        self.assertTrue(self.copy("/BDMV/JAR/03001.jar", 10**9))

    def test_an_extensionless_file_is_captured(self):
        """Knives Out's second copy of its application has no extension.

        ``/BDMV/JAR/03000/809ad4ac00000`` is a zip of 707 classes. A policy
        keyed on names alone walks past the bytecode it exists to collect.
        """
        self.assertTrue(self.copy("/BDMV/JAR/03000/809ad4ac00000", 4 * 10**6))

    def test_artwork_is_left_behind_unless_asked_for(self):
        self.assertFalse(self.copy("/BDMV/META/DL/menu.png", 10**6))
        self.assertTrue(self.copy("/BDMV/META/DL/menu.png", 10**6, assets=True))

    def test_an_unknown_giant_is_recorded_rather_than_copied(self):
        self.assertFalse(self.copy("/BDMV/AUXDATA/big.unknown", 10**9))


def _jar(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, b"\xca\xfe\xba\xbe")
    return buf.getvalue()


class TestIndexingTheArchives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / "disc" / "BDMV" / "JAR").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def write(self, relative, data):
        path = self.out / "disc" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def index(self):
        forensics._index_jars(self.out)
        return json.loads((self.out / "jars.json").read_text())

    def test_an_archive_is_found_by_magic_not_by_name(self):
        self.write("BDMV/JAR/03000/809ad4ac00000",
                   _jar(["a.class", "ab.class"]))
        self.assertEqual(len(self.index()), 1)

    def test_obfuscated_class_names_are_flagged(self):
        """Knives Out ships 707 classes called a, aa, ab. Worth knowing.

        The names carry nothing, so anyone planning to read the application
        should know before they start that structure is all there is.
        """
        self.write("BDMV/JAR/03001.jar",
                   _jar([f"{c}.class" for c in "a ab ac ad ae af".split()]))
        entry = next(iter(self.index().values()))
        self.assertTrue(entry["name_obfuscated"])

    def test_ordinary_class_names_are_not_flagged(self):
        self.write("BDMV/JAR/00006.jar",
                   _jar(["com/studio/Menu.class", "com/studio/Player.class",
                         "com/studio/PlaylistChooser.class"]))
        entry = next(iter(self.index().values()))
        self.assertFalse(entry["name_obfuscated"])

    def test_a_file_that_is_not_an_archive_is_ignored(self):
        self.write("BDMV/index.bdmv", b"INDX0200" + b"\0" * 100)
        self.assertEqual(self.index(), {})


#: A scan transcript in the shape makemkvcon emits, carrying the one thing
#: the analysis needs: clip lists that are permutations of each other. Nine
#: distinct orders of four clips -- three clips cannot reach the threshold,
#: since there are only six permutations of three.
_ORDERS = [
    [510, 505, 513, 502], [510, 513, 505, 502], [505, 510, 513, 502],
    [505, 513, 510, 502], [513, 505, 510, 502], [513, 510, 505, 502],
    [502, 510, 505, 513], [502, 505, 510, 513], [502, 513, 505, 510],
]
POOL_SCAN = "\n".join(
    [f"TCOUNT:{len(_ORDERS)}"]
    + [f'TINFO:{i},2,0,"Film"\n'
       f'TINFO:{i},9,0,"2:10:13"\n'
       f'TINFO:{i},11,0,"30000000000"\n'
       f'TINFO:{i},16,0,"{i:05d}.mpls"\n'
       f'TINFO:{i},8,0,"20"\n'
       f'TINFO:{i},26,0,"{",".join(str(c) for c in order)}"'
       for i, order in enumerate(_ORDERS)])


class TestReadingASavedScan(unittest.TestCase):
    def test_titles_come_back_with_their_clip_lists(self):
        titles = forensics.titles_from_scan(POOL_SCAN)
        self.assertEqual(len(titles), 9)
        self.assertEqual(titles[0].source, "00000.mpls")
        self.assertEqual(titles[0].duration, "2:10:13")
        self.assertEqual(titles[0].segments, "510,505,513,502")
        self.assertEqual(titles[0].chapters, 20)
        self.assertEqual(titles[0].size_bytes, 30_000_000_000)

    def test_a_transcript_with_nothing_in_it_is_not_an_error(self):
        self.assertEqual(forensics.titles_from_scan(""), [])

    def test_rubbish_lines_are_stepped_over(self):
        titles = forensics.titles_from_scan(
            "not a record\nTINFO:0,9,0,\"1:00:00\"\n\n")
        self.assertEqual(len(titles), 1)

    def test_the_saved_scan_feeds_the_same_detector_the_app_uses(self):
        """The capture's value is that it is the app's own view, kept."""
        found = selection.obfuscation(forensics.titles_from_scan(POOL_SCAN))
        self.assertIsNotNone(found)
        self.assertEqual(found[1], 9, "nine distinct orders of four clips")


class TestTheAnswerAndTheCorpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def disc(self, name, **analysis):
        out = self.root / name
        out.mkdir()
        base = {"playlists_on_disc": 447, "titles_from_scan": 283,
                "largest_pool": 201, "pool_clip_count": 15,
                "fpl_main_feature": False}
        base.update(analysis)
        (out / "analysis.json").write_text(json.dumps(base))
        (out / "capture.json").write_text(json.dumps({"volume_id": name}))
        return out

    def test_an_answer_records_how_it_was_reached(self):
        """Not every answer is equally strong, and a rule trained on the weak
        ones is a rule that loses a film."""
        out = self.disc("KNIVES_OUT-abc")
        forensics.record_truth(out, "00988.mpls", "forum-map",
                               "matched the published US retail clip map",
                               segments_map="510,505,513")
        truth = json.loads((out / "truth.json").read_text())
        self.assertEqual(truth["playlist"], "00988.mpls")
        self.assertEqual(truth["how"], "forum-map")
        self.assertIn("retail", truth["evidence"])
        self.assertTrue(truth["recorded_at"])

    def test_the_summary_shows_which_discs_are_still_unanswered(self):
        self.disc("KNIVES_OUT-abc")
        answered = self.disc("POWER_RANGERS-def")
        forensics.record_truth(answered, "00988.mpls", "forum-map", "verified")

        rows = {r["disc"]: r for r in forensics.summarise(self.root)}
        self.assertEqual(rows["KNIVES_OUT-abc"]["answer"], "")
        self.assertEqual(rows["POWER_RANGERS-def"]["answer"], "00988.mpls")

    def test_an_empty_corpus_is_not_an_error(self):
        self.assertEqual(forensics.summarise(self.root), [])
