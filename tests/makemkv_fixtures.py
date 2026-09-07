"""Captured ``makemkvcon -r`` output used by the tests.

``ENUMERATION`` is a verbatim capture from this machine (MakeMKV 1.18.3, two
USB Blu-ray drives), so the record shapes, the 16 always-emitted DRV slots and
the trailing expected MSG:5010 are all real rather than idealised.

Everything below the ENUMERATION block is **hand-built** to the same shape,
because producing it needs physical media -- a dirty disc, a DVD, a full
multi-hour backup. They are marked individually. This mirrors the honesty of
``tests/fixtures.py``, which distinguishes its captured udisks2 payloads from
its hand-built audio-CD and blank-disc entries for the same reason.

Replace a hand-built block with a real capture as soon as one exists; the
tests should not need changing when you do.
"""

# --- VERBATIM CAPTURE: makemkvcon -r --cache=1 info disc:9999 ---------------
# sr0: Blu-ray, SPIDER_MAN_ACROSS_SPIDER_VERSE.  sr1: DVD, DVD_VIDEO.
# Exit code 0.  The trailing MSG:5010 is the expected tail of this idiom.
ENUMERATION = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
DRV:0,2,999,12,"BD-RE HL-DT-ST BD-RE  WH16NS40 1.05 KLOO6JG4911","SPIDER_MAN_ACROSS_SPIDER_VERSE","/dev/sr0"
DRV:1,2,999,1,"BD-RE HL-DT-ST BD-RE BU40N FR07 902HS017569","DVD_VIDEO","/dev/sr1"
DRV:2,256,999,0,"","",""
DRV:3,256,999,0,"","",""
DRV:4,256,999,0,"","",""
DRV:5,256,999,0,"","",""
DRV:6,256,999,0,"","",""
DRV:7,256,999,0,"","",""
DRV:8,256,999,0,"","",""
DRV:9,256,999,0,"","",""
DRV:10,256,999,0,"","",""
DRV:11,256,999,0,"","",""
DRV:12,256,999,0,"","",""
DRV:13,256,999,0,"","",""
DRV:14,256,999,0,"","",""
DRV:15,256,999,0,"","",""
MSG:5010,0,0,"Failed to open disc","Failed to open disc"
TCOUNT:0
"""

ENUMERATION_LINES = ENUMERATION.splitlines()

SR0 = "/dev/sr0"
SR1 = "/dev/sr1"
SR0_LABEL = "SPIDER_MAN_ACROSS_SPIDER_VERSE"
SR1_LABEL = "DVD_VIDEO"

# --- HAND-BUILT: no drive holds a disc -------------------------------------
NO_DISCS = "\n".join(
    [ENUMERATION_LINES[0]]
    + [f'DRV:{i},256,999,0,"","",""' for i in range(16)]
    + ['MSG:5010,0,0,"Failed to open disc","Failed to open disc"', "TCOUNT:0"]
)

# --- HAND-BUILT: a drive still spinning up after the tray closed -----------
LOADING = "\n".join([
    ENUMERATION_LINES[0],
    'DRV:0,3,999,0,"BD-RE HL-DT-ST BD-RE BU40N FR07 902HS017569","","/dev/sr0"',
    'MSG:5010,0,0,"Failed to open disc","Failed to open disc"',
    "TCOUNT:0",
])

# --- HAND-BUILT: a successful backup, trimmed to its shape -----------------
# The PRGV cadence of a real multi-hour run is not yet characterised; what
# matters to the parser and the judge is the terminal MSG:5081 and that total
# progress reaches PRGV's own max.
BACKUP_SUCCESS = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
MSG:3007,0,0,"Using direct disc access mode","Using direct disc access mode"
MSG:5085,0,0,"Loaded content hash table, will verify integrity of M2TS files.","Loaded content hash table, will verify integrity of M2TS files."
PRGT:5069,0,"Saving all titles to MKV file"
PRGC:5069,0,"Saving all titles to MKV file"
PRGV:0,0,65536
PRGV:16384,8192,65536
PRGV:65536,32768,65536
PRGV:65536,65536,65536
MSG:5081,0,0,"Backup done.","Backup done."
"""

# --- HAND-BUILT: a dirty disc ----------------------------------------------
# A damaged disc emits MSG:2003 once per failed read, so a real transcript
# carries a storm of them. Three stand in for the storm.
BACKUP_DIRTY_DISC = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
PRGV:0,0,65536
PRGV:8192,4096,65536
MSG:2003,0,3,"Error 'Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR' occurred while reading '/BDMV/STREAM/00800.m2ts' at offset '1049624576'","Error '%1' occurred while reading '%2' at offset '%3'","Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR","/BDMV/STREAM/00800.m2ts","1049624576"
MSG:2003,0,3,"Error 'Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR' occurred while reading '/BDMV/STREAM/00800.m2ts' at offset '1049690112'","Error '%1' occurred while reading '%2' at offset '%3'","Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR","/BDMV/STREAM/00800.m2ts","1049690112"
MSG:2003,0,3,"Error 'Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR' occurred while reading '/BDMV/STREAM/00800.m2ts' at offset '1049755648'","Error '%1' occurred while reading '%2' at offset '%3'","Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR","/BDMV/STREAM/00800.m2ts","1049755648"
MSG:5080,0,0,"Backup failed.","Backup failed."
"""

# --- HAND-BUILT: destination was not empty ---------------------------------
BACKUP_DEST_NOT_EMPTY = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
MSG:5068,0,1,"Folder /srv/x already contains a backup, please choose another folder","Folder %1 already contains a backup, please choose another folder","/srv/x"
MSG:5080,0,0,"Backup failed.","Backup failed."
"""

# --- HAND-BUILT: permissions problem, not a disc problem -------------------
BACKUP_NO_ACCESS = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
MSG:2016,0,3,"Failed to get full access to drive \"HL-DT-ST BD-RE BU40N\". Make sure that you either have write access to device \"/dev/sr0\", are member of \"cdrom\" group or have CAP_SYS_RAWIO enabled.","Failed to get full access to drive \"%1 %2\". Make sure that you either have write access to device \"%3\", are member of \"cdrom\" group or have CAP_SYS_RAWIO enabled.","HL-DT-ST","BD-RE BU40N","/dev/sr0"
MSG:5080,0,0,"Backup failed.","Backup failed."
"""

# --- HAND-BUILT: backup finished but files are corrupt ---------------------
BACKUP_HASH_FAILURES = """\
PRGV:65536,65536,65536
MSG:5076,0,2,"Hash check failed for file 00800.m2ts at offset 12345, file is corrupt.","Hash check failed for file %1 at offset %2, file is corrupt.","00800.m2ts","12345"
MSG:5082,0,1,"Backup done but 1 files failed hash check.","Backup done but %1 files failed hash check.","1"
"""

# --- HAND-BUILT: a disc scan, for the title inventory ----------------------
DISC_SCAN = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
CINFO:1,6209,"Blu-ray disc"
CINFO:2,0,"Spider-Man: Across The Spider-Verse"
CINFO:28,0,"eng"
TCOUNT:2
TINFO:0,2,0,"Spider-Man: Across The Spider-Verse"
TINFO:0,9,0,"2:20:05"
TINFO:0,10,0,"29.3 GB"
TINFO:0,11,0,"31506235392"
TINFO:0,16,0,"00001.mpls"
SINFO:0,0,1,6201,"Video"
SINFO:0,0,5,0,"V_MPEG4/ISO/AVC"
SINFO:0,0,19,0,"1920x1080"
TINFO:1,2,0,"Extras, Part 2"
TINFO:1,11,0,"323100672"
"""


# --- RECONSTRUCTED FROM A REAL RUN: a DVD scan -----------------------------
# The title names, durations and sizes are verbatim from what MakeMKV returned
# for /dev/sr0 on 2026-09-06, recovered from that disc's collection.json. The
# disc's volume label is the generic "DVD_VIDEO"; MakeMKV knows it as "Fresh
# Horses", which is the whole reason CINFO is worth reading.
#
# The CINFO block is a real capture too, taken 2026-09-07 by re-reading the
# finished ISO with `info iso:...`. It settles what was open when this fixture
# was first written: a DVD does emit CINFO:2, so the fallback to the feature's
# title name in _scan_titles is belt-and-braces rather than the only path.
# CINFO:32 is the raw volume name, which is the whole point -- "DVD_VIDEO"
# stamped on the disc against "Fresh Horses" that MakeMKV knows it as.
DVD_SCAN = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
CINFO:1,6206,"DVD disc"
CINFO:2,0,"Fresh Horses"
CINFO:28,0,"eng"
CINFO:29,0,"English"
CINFO:30,0,"Fresh Horses"
CINFO:32,0,"DVD_VIDEO"
CINFO:33,0,"0"
TCOUNT:4
TINFO:0,2,0,"Fresh Horses"
TINFO:0,9,0,"1:42:39"
TINFO:0,11,0,"4245336064"
TINFO:1,2,0,"Fresh Horses"
TINFO:1,9,0,"0:02:32"
TINFO:1,11,0,"99866624"
TINFO:2,2,0,"Fresh Horses"
TINFO:2,9,0,"0:02:32"
TINFO:2,11,0,"101634048"
TINFO:3,2,0,"Fresh Horses"
TINFO:3,9,0,"0:02:32"
TINFO:3,11,0,"80885760"
"""

#: The same scan with the disc-level records stripped, for the fallback path.
DVD_SCAN_NO_CINFO = "\n".join(
    line for line in DVD_SCAN.splitlines() if not line.startswith("CINFO:"))


# --- VERBATIM CAPTURE: makemkvcon mkv, 2026-09-07 --------------------------
# A real run against the finished "Fresh Horses" ISO -- four titles saved in
# one pass with --minlength. The 275 PRGV records are thinned to a
# representative few, the empty DRV slots dropped and the destination path
# made stable; nothing else is edited.
#
# This is the first captured completion sequence for an mkv run: 5011, then
# 5014 announcing the count, then 5005 and 5036 tallying it. 5036 is what
# outcome.judge treats as success.
MKV_SUCCESS = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
PRGT:5018,0,"Scanning CD-ROM devices"
PRGC:5018,0,"Scanning CD-ROM devices"
PRGV:0,0,65536
DRV:0,2,999,12,"BD-RE HL-DT-ST BD-RE  WH16NS40 1.05 KLOO6JG4911","HANCOCK","/dev/sr1"
DRV:1,0,999,0,"BD-RE HL-DT-ST BD-RE BU40N FR07 902HS017569","","/dev/sr0"
PRGT:3100,0,"Opening DVD disc"
MSG:3007,0,0,"Using direct disc access mode","Using direct disc access mode"
PRGC:3102,0,"Processing title sets"
PRGC:3120,1,"Scanning contents"
PRGC:3103,0,"Processing titles"
MSG:3028,0,3,"Title #1 was added (28 cell(s), 1:42:39)","Title #%1 was added (%2 cell(s), %3)","1","28","1:42:39"
MSG:3028,16777216,3,"Title #2 was added (2 cell(s), 0:02:32)","Title #%1 was added (%2 cell(s), %3)","2","2","0:02:32"
MSG:3028,16777216,3,"Title #3 was added (2 cell(s), 0:02:32)","Title #%1 was added (%2 cell(s), %3)","3","2","0:02:32"
MSG:3028,0,3,"Title #4 was added (2 cell(s), 0:02:32)","Title #%1 was added (%2 cell(s), %3)","4","2","0:02:32"
PRGC:3104,0,"Decrypting data"
MSG:5011,0,0,"Operation successfully completed","Operation successfully completed"
PRGT:5024,0,"Saving all titles to MKV files"
MSG:5014,131072,2,"Saving 4 titles into directory file:///srv/out","Saving %1 titles into directory %2","4","file:///srv/out"
PRGC:5057,0,"Analyzing seamless segments"
PRGC:5017,0,"Saving to MKV file"
PRGV:10749,10078,65536
PRGV:32287,30271,65536
PRGV:53109,49793,65536
PRGC:5057,1,"Analyzing seamless segments"
PRGC:5017,1,"Saving to MKV file"
PRGC:5057,2,"Analyzing seamless segments"
PRGC:5017,2,"Saving to MKV file"
PRGC:5057,3,"Analyzing seamless segments"
PRGC:5017,3,"Saving to MKV file"
PRGV:65536,65536,65536
MSG:5005,128,1,"4 titles saved","%1 titles saved","4"
MSG:5036,260,1,"Copy complete. 4 titles saved.","Copy complete. %1 titles saved.","4"
"""

#: The same run with some titles lost. Hand-built from the 5037/5004 pair,
#: whose text is decoded from MakeMKV's own catalogue -- see
#: docs/makemkv/message-codes.md for the recipe.
MKV_PARTIAL = "\n".join(
    [line for line in MKV_SUCCESS.splitlines()
     if not line.startswith(("MSG:5005", "MSG:5036"))]
    + ['MSG:5003,0,2,"Failed to save title 2 to file B1_t01.mkv",'
       '"Failed to save title %1 to file %2","2","B1_t01.mkv"',
       'MSG:5004,128,2,"3 titles saved, 1 failed","%1 titles saved, %2 failed","3","1"',
       'MSG:5037,260,2,"Copy complete. 3 titles saved, 1 failed.",'
       '"Copy complete. %1 titles saved, %2 failed.","3","1"'])

#: HAND-BUILT: a disc hiding its feature among decoy playlists. Forty titles
#: within a few seconds of each other, which is the shape the protection
#: takes and the shape selection.choose refuses.
DECOY_SCAN = "\n".join(
    [ENUMERATION_LINES[0], "TCOUNT:40"]
    + [f'TINFO:{i},2,0,"Unknown"\nTINFO:{i},9,0,"2:18:{i % 60:02d}"\n'
       f'TINFO:{i},11,0,"{30_000_000_000 + i}"' for i in range(40)])

#: The same capture with its tallies at one title -- the common case, a film
#: disc where only the feature is selected. Derived rather than re-captured,
#: since only the counts differ.
MKV_SUCCESS_ONE = (
    MKV_SUCCESS
    .replace('"4 titles saved","%1 titles saved","4"',
             '"1 titles saved","%1 titles saved","1"')
    .replace('"Copy complete. 4 titles saved.","Copy complete. %1 titles saved.","4"',
             '"Copy complete. 1 titles saved.","Copy complete. %1 titles saved.","1"')
    .replace('"Saving 4 titles into directory', '"Saving 1 titles into directory'))

# --- HAND-BUILT: mkv runs that go wrong ------------------------------------
# The read-error lines are lifted verbatim from BACKUP_DIRTY_DISC; the
# terminal codes are the mkv family, decoded from MakeMKV's own catalogue.
MKV_DIRTY_DISC = "\n".join(
    [line for line in BACKUP_DIRTY_DISC.splitlines()
     if not line.startswith("MSG:5080")]
    + ['MSG:5003,0,2,"Failed to save title 1 to file A1_t00.mkv",'
       '"Failed to save title %1 to file %2","1","A1_t00.mkv"',
       'MSG:5004,128,2,"0 titles saved, 1 failed","%1 titles saved, %2 failed","0","1"'])

MKV_NO_ACCESS = "\n".join(
    [line for line in BACKUP_NO_ACCESS.splitlines()
     if not line.startswith("MSG:5080")])
