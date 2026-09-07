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
