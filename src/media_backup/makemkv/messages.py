"""MakeMKV ``MSG`` codes this project reacts to.

Codes are stable across versions and languages; the rendered text is not.
Always switch on the code. The full 584-code table and the recipe for
decoding any code are in ``docs/makemkv/message-codes.md``.
"""

from __future__ import annotations

# -- terminal outcome of a backup run ---------------------------------------

BACKUP_DONE = 5081           # "Backup done."
BACKUP_FAILED = 5080         # "Backup failed."
BACKUP_HASH_FAILED = 5082    # "Backup done but %1 files failed hash check."
CANCELLED = 2201             # "Operation was cancelled"

#: The PRGT/PRGC progress-title forms of the three backup outcomes. Same text
#: without the trailing full stop. Never treat these as MSG outcomes.
PROGRESS_TITLE_BACKUP = frozenset({5069, 5070, 5079})

# -- failure causes ---------------------------------------------------------

READ_ERROR = 2003            # "Error '%1' occurred while reading '%2' at offset '%3'"
SCSI_ERROR = 2004
OPEN_FAILED = 5010           # "Failed to open disc" -- context-dependent, see below
NO_DRIVES = 5042
DEST_NOT_EMPTY = 5068        # "Folder %1 already contains a backup..."
NO_DRIVE_ACCESS = 2016       # cdrom group / CAP_SYS_RAWIO -- an environment problem
HASH_FAILED_FILE = 5076
HASH_FAILED_TOO_MANY = 5077
ERR_UNSPECIFIED = 2200
ERR_POSIX = 2301
ERR_SCSI = 2302
ERR_SCSI_SUB = 2303
ERR_INTERNAL = 2304
FATAL_EXIT = 6050
OUT_OF_MEMORY = 6051

# -- benign -----------------------------------------------------------------

ENGINE_STARTED = 1005
DIRECT_DISC_ACCESS = 3007    # LibreDrive
TITLE_TOO_SHORT = 3025
SCANNING_DEVICES = 5018
HASH_TABLE_MISSING = 5083
HASH_TABLE_LOADED = 5085
UPDATE_CHECK_NOTICE = 5074

# -- sets used by outcome judging -------------------------------------------

#: Terminal success for a backup.
SUCCESS = frozenset({BACKUP_DONE})

#: Copy completed, but some files are corrupt. Operator decides.
PARTIAL = frozenset({BACKUP_HASH_FAILED})

#: Terminal failure for a backup.
FAILURE = frozenset({BACKUP_FAILED})

#: Fatal during a backup. NOTE: OPEN_FAILED is deliberately absent -- it is
#: the expected tail of the drive-enumeration idiom. Judge it per invocation.
FATAL = frozenset({
    NO_DRIVES, DEST_NOT_EMPTY, NO_DRIVE_ACCESS,
    ERR_UNSPECIFIED, ERR_POSIX, ERR_SCSI, ERR_SCSI_SUB, ERR_INTERNAL,
    FATAL_EXIT, OUT_OF_MEMORY,
})

#: A damaged or dirty disc emits one of these per failed read, so a bad disc
#: produces a storm of them. Count rather than treating each as terminal.
READ_ERRORS = frozenset({READ_ERROR, SCSI_ERROR})

HASH_ERRORS = frozenset({HASH_FAILED_FILE, HASH_FAILED_TOO_MANY})

#: Informational; never a failure on its own.
BENIGN = frozenset({
    ENGINE_STARTED, DIRECT_DISC_ACCESS, TITLE_TOO_SHORT, SCANNING_DEVICES,
    HASH_TABLE_MISSING, HASH_TABLE_LOADED, UPDATE_CHECK_NOTICE,
    3307, 3309,  # titles discovered / duplicate playlists skipped
})

#: Codes that deserve a specific operator-facing explanation, because the
#: generic "the disc failed" reading would send them after the wrong problem.
DIAGNOSTIC_HINTS: dict[int, str] = {
    NO_DRIVE_ACCESS: (
        "MakeMKV cannot get full access to the drive. This is a permissions "
        "problem, not a bad disc: the user must be in the 'cdrom' group, or "
        "have write access to the device, or have CAP_SYS_RAWIO."
    ),
    DEST_NOT_EMPTY: (
        "The destination directory already contains a backup. MakeMKV refuses "
        "to write into it. This is a bug in this application, not an operator "
        "error -- the directory must be cleared before the run."
    ),
    NO_DRIVES: "MakeMKV found no usable optical drives.",
    OUT_OF_MEMORY: "MakeMKV ran out of memory.",
    READ_ERROR: (
        "The drive could not read part of the disc. Clean the disc and retry."
    ),
}


def describe(code: int) -> str:
    """Return an operator-facing hint for a code, or an empty string."""
    return DIAGNOSTIC_HINTS.get(code, "")
