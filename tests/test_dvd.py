"""Tests for reading a DVD's own title tables.

No disc is touched. The IFO structures are built byte by byte to the
DVD-Video layout, which is the only way to exercise the parsing without a
drive -- and building them is also the check that the offsets in `dvd.py` are
the ones the specification names.
"""

import struct
import unittest
from unittest import mock

from media_backup import dvd
from media_backup.makemkv import selection

SECTOR = dvd.SECTOR


def bcd(n):
    return ((n // 10) << 4) | (n % 10)


def pgc(seconds, cells, programs=1, sectors=None):
    """One program chain: a header, a cell position table, a playback table.

    ``cells`` is (vob, cell) pairs for the position table at +0xEA -- what each
    cell is called. ``sectors`` is (first, last) pairs for the playback table at
    +0xE8 -- where each cell's bytes are. Omitting ``sectors`` leaves the
    playback pointer at zero, which is the case a reader must survive: the
    position table alone still identifies the cells.
    """
    body = bytearray(0x100)
    body[2] = programs
    body[3] = len(cells)
    body[4] = bcd(seconds // 3600)
    body[5] = bcd(seconds // 60 % 60)
    body[6] = bcd(seconds % 60)
    position = 0x100
    struct.pack_into(">H", body, dvd.PGC_CELL_POSITION, position)
    table = bytearray()
    for vob, cell in cells:
        table += struct.pack(">H", vob) + bytes([0, cell])
    if sectors is None:
        # A parser that reads the position data at 0xE8 by mistake gets zeros
        # here rather than something plausible, so the mistake shows up.
        struct.pack_into(">H", body, dvd.PGC_CELL_PLAYBACK, 0)
        return bytes(body) + bytes(table)

    playback = position + len(table)
    struct.pack_into(">H", body, dvd.PGC_CELL_PLAYBACK, playback)
    entries = bytearray()
    for first, last in sectors:
        entry = bytearray(dvd.CELL_PLAYBACK_ENTRY)
        struct.pack_into(">I", entry, dvd.CELL_FIRST_SECTOR, first)
        struct.pack_into(">I", entry, dvd.CELL_LAST_SECTOR, last)
        entries += entry
    return bytes(body) + bytes(table) + bytes(entries)


def pgcit(chains):
    """A program chain information table holding `chains`."""
    header = struct.pack(">HHI", len(chains), 0, 0)
    entries = bytearray()
    blobs = bytearray()
    offset = len(header) + 8 * len(chains)
    for body in chains:
        entries += struct.pack(">II", 0, offset + len(blobs))
        blobs += body
    return bytes(header) + bytes(entries) + bytes(blobs)


def vts_ifo(chains):
    table = pgcit(chains)
    sectors = 2
    data = bytearray(sectors * SECTOR + len(table) + SECTOR)
    data[0:12] = b"DVDVIDEO-VTS"
    struct.pack_into(">I", data, dvd.VTS_PGCIT, sectors)
    data[sectors * SECTOR:sectors * SECTOR + len(table)] = table
    return bytes(data)


def iso_record(name, extent, size, is_dir):
    name_b = name.encode()
    length = 33 + len(name_b)
    length += length % 2
    rec = bytearray(length)
    rec[0] = length
    struct.pack_into("<I", rec, 2, extent)
    struct.pack_into("<I", rec, 10, size)
    rec[25] = 2 if is_dir else 0
    rec[32] = len(name_b)
    rec[33:33 + len(name_b)] = name_b
    return bytes(rec)


class FakeDisc:
    """A disc laid out well enough for the reader to walk it."""

    def __init__(self, chains, vts_name="VTS_01_0.IFO"):
        ifo = vts_ifo(chains)
        self.sectors = {}
        ifo_extent = 40
        for i in range(0, len(ifo), SECTOR):
            self.sectors[ifo_extent + i // SECTOR] = ifo[i:i + SECTOR].ljust(SECTOR, b"\0")
        videots = iso_record(vts_name, ifo_extent, len(ifo), False)
        self.sectors[30] = videots.ljust(SECTOR, b"\0")
        root = iso_record("VIDEO_TS", 30, SECTOR, True)
        self.sectors[20] = root.ljust(SECTOR, b"\0")
        pvd = bytearray(SECTOR)
        pvd[1:6] = b"CD001"
        pvd[156:190] = iso_record("\x00", 20, SECTOR, True)[:34].ljust(34, b"\0")
        struct.pack_into("<I", pvd, 156 + 2, 20)
        struct.pack_into("<I", pvd, 156 + 10, SECTOR)
        pvd[156 + 25] = 2
        self.sectors[dvd.PVD_SECTOR] = bytes(pvd)

    def read(self, first, count):
        return b"".join(self.sectors.get(first + i, b"\0" * SECTOR)
                        for i in range(count))


def read_fake(disc):
    with mock.patch.object(dvd._Reader, "sectors",
                           lambda self, first, count=1: disc.read(first, count)):
        return dvd.read_titles("/dev/fake")


#: Challenge of the Superfriends disc 1, read off the disc 2026-09-12. A
#: play-all, seven episodes on their own VOBs, an alternate-audio copy of the
#: first, and four menus. MakeMKV offered four titles for this.
SUPERFRIENDS = [
    pgc(9137, [(2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (7, 1), (8, 1)], programs=7),
    pgc(12, [(1, 1)]),
    pgc(66, [(9, 1)]),
    pgc(29, [(10, 1)]),
    pgc(1311, [(2, 1)]),
    pgc(1305, [(3, 1)]),
    pgc(1304, [(4, 1)]),
    pgc(1305, [(5, 1)]),
    pgc(1305, [(6, 1)]),
    pgc(1303, [(7, 1)]),
    pgc(1303, [(8, 1)]),
    pgc(32, [(11, 1)]),
    pgc(1311, [(2, 1)]),
]


class TestReadingTheDisc(unittest.TestCase):
    def setUp(self):
        self.chains = read_fake(FakeDisc(SUPERFRIENDS))

    def test_every_program_chain_is_found(self):
        """Thirteen, where MakeMKV enumerated four."""
        self.assertEqual(len(self.chains), 13)

    def test_durations_come_back_in_seconds(self):
        self.assertEqual(self.chains[0].seconds, 9137)
        self.assertEqual(self.chains[4].seconds, 1311)

    def test_cells_are_addressed_globally(self):
        """The point of the exercise: 2/1 means the same content anywhere."""
        self.assertEqual(dvd.clip_list(self.chains[4]), "1002/1")
        self.assertEqual(dvd.clip_list(self.chains[12]), "1002/1")

    def test_the_play_all_carries_every_episode_cell(self):
        self.assertEqual(len(self.chains[0].cells), 7)
        self.assertEqual(self.chains[0].programs, 7)

    def test_a_title_set_prefixes_its_cells(self):
        """Two title sets may both hold a "VOB 2"; they are not the same."""
        self.assertTrue(all(c.vob >= 1000 for c in self.chains[0].cells))


class TestFeedingTheClassification(unittest.TestCase):
    """The reason this module exists: DVDs get the Blu-ray reasoning."""

    def setUp(self):
        from media_backup import model
        chains = read_fake(FakeDisc(SUPERFRIENDS))
        self.titles = []
        for c in chains:
            t = model.Title(index=c.number, segments=dvd.clip_list(c),
                            duration=f"0:{c.seconds // 60:02d}:{c.seconds % 60:02d}",
                            size_bytes=c.seconds * 3_000_000)
            t.streams = 1 if c.number == 113 else 2
            self.titles.append(t)
        # Cells out of the IFO are disc-global by construction, and the disc
        # is a season of a show. Both are facts the caller has; neither is
        # something classify should have to guess.
        self.verdict = selection.classify(self.titles, model.KIND_SERIES,
                                          clips_global=True)

    def test_all_seven_episodes_are_content(self):
        """Including the four MakeMKV never offered."""
        episodes = [105, 106, 107, 108, 109, 110, 111]
        self.assertEqual([self.verdict[i] for i in episodes],
                         [selection.CONTENT] * 7)

    def test_the_play_all_is_recognised(self):
        self.assertEqual(self.verdict[101], selection.PLAY_ALL)

    def test_the_alternate_audio_copy_is_a_duplicate(self):
        self.assertEqual(self.verdict[113], selection.DUPLICATE)

    def test_the_duplicate_does_not_hide_the_play_all(self):
        """The ordering bug this disc exposed.

        PGC 113 shares cell 1002/1 with PGC 105. Resolved after the play-all
        test instead of before it, the play-all's children overlap, the
        disjointness check fails, and seven episodes are dropped as fragments
        of a title that then gets published in their place.
        """
        self.assertNotEqual(self.verdict[101], selection.CONTENT)
        self.assertNotIn(selection.FRAGMENT, [self.verdict[i] for i in (105, 111)])


class TestRefusals(unittest.TestCase):
    def test_a_disc_that_is_not_iso9660_says_so(self):
        disc = FakeDisc(SUPERFRIENDS)
        disc.sectors[dvd.PVD_SECTOR] = b"\0" * SECTOR
        with self.assertRaises(dvd.DvdError) as caught:
            read_fake(disc)
        self.assertIn("ISO 9660", str(caught.exception))

    def test_a_disc_with_no_video_ts_says_so(self):
        disc = FakeDisc(SUPERFRIENDS)
        disc.sectors[20] = iso_record("README.TXT", 30, 10, False).ljust(SECTOR, b"\0")
        with self.assertRaises(dvd.DvdError) as caught:
            read_fake(disc)
        self.assertIn("VIDEO_TS", str(caught.exception))


class TestWhereACellsBytesAre(unittest.TestCase):
    """The cell playback table, which is how a cell could be extracted.

    Reading what a cell is *called* identifies it; reading where it *is* is
    what would let one be pulled out of the VOB files. The two tables are
    indexed together and carry none of each other's fields, so a reader has to
    keep them aligned by position and nothing else.
    """

    def chain(self, **kw):
        return read_fake(FakeDisc([pgc(1311, [(2, 1)], **kw)]))[0]

    def test_the_sector_range_is_read_from_the_playback_table(self):
        chain = self.chain(sectors=[(1_000, 1_499)])
        self.assertEqual((chain.cells[0].first_sector,
                          chain.cells[0].last_sector), (1_000, 1_499))

    def test_the_size_counts_both_ends(self):
        """A cell from sector 1000 to 1499 is 500 sectors, not 499."""
        self.assertEqual(self.chain(sectors=[(1_000, 1_499)]).cells[0].sectors, 500)

    def test_a_pgc_with_no_playback_table_still_names_its_cells(self):
        """The identity does not depend on the sectors being there."""
        cell = self.chain().cells[0]
        self.assertEqual((cell.vob, cell.cell), (1002, 1))
        self.assertEqual((cell.first_sector, cell.last_sector), (0, 0))
        self.assertEqual(cell.sectors, 0)

    def test_the_sectors_survive_the_title_set_prefixing(self):
        """read_titles rewrites every cell to prefix the VOB id with the VTS.

        It builds new Cell objects to do it, so this is the test that stops the
        sector range being dropped on the floor there.
        """
        cell = self.chain(sectors=[(2_000, 2_999)]).cells[0]
        self.assertEqual(cell.vob, 1002, "prefixed with the title set")
        self.assertEqual((cell.first_sector, cell.last_sector), (2_000, 2_999))
