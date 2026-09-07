"""udisks2 ``GetManagedObjects`` payloads used by the tests.

The optical entries were captured verbatim from a live udisks2 on two USB
Blu-ray drives, so the *types* match what D-Bus really returns -- notably
``Block.Device`` as a NUL-terminated ``list[int]`` (``ay``) and
``Filesystem.MountPoints`` as a list of those (``aay``).  The audio-CD and
blank-disc entries are hand-built from the same shape, since producing them
needs physical media.
"""

IFACE_BLOCK = "org.freedesktop.UDisks2.Block"
IFACE_DRIVE = "org.freedesktop.UDisks2.Drive"
IFACE_FILESYSTEM = "org.freedesktop.UDisks2.Filesystem"
IFACE_PARTITION = "org.freedesktop.UDisks2.Partition"

BLOCK_PREFIX = "/org/freedesktop/UDisks2/block_devices/"
DRIVE_PREFIX = "/org/freedesktop/UDisks2/drives/"

DRIVE_BU40N = DRIVE_PREFIX + "HL_DT_ST_BD_RE_BU40N_393032485330313735363920"
DRIVE_WH16NS40 = DRIVE_PREFIX + "HL_DT_ST_BD_RE__WH16NS40_PROLIFICMP00000003A"
DRIVE_USB_STICK = DRIVE_PREFIX + "Generic_Flash_Disk_12345"

#: Every optical drive reports the same compatibility list whether or not a
#: disc is loaded.  This is what identifies a drive as optical.
OPTICAL_COMPAT = [
    "optical_bd", "optical_bd_r", "optical_bd_re",
    "optical_cd", "optical_cd_r", "optical_cd_rw",
    "optical_dvd", "optical_dvd_plus_r", "optical_dvd_plus_r_dl",
    "optical_dvd_plus_rw", "optical_dvd_r", "optical_dvd_ram", "optical_dvd_rw",
]


def ay(text):
    """Encode a string the way udisks2 does: NUL-terminated ``ay``."""
    return list(text.encode()) + [0]


def block(device, drive, label="", id_type="", size=0, read_only=True):
    return {
        "Device": ay(device),
        "Drive": drive,
        "IdLabel": label,
        "IdType": id_type,
        "IdUsage": "filesystem" if id_type else "",
        "Size": size,
        "ReadOnly": read_only,
    }


def drive(model, serial, media="", available=False, tracks=0, audio=0,
          blank=False, compat=None, size=0, vendor="HL-DT-ST", bus="usb"):
    return {
        "Model": model,
        "Vendor": vendor,
        "Serial": serial,
        "ConnectionBus": bus,
        # udisks2 reports Optical=False on a drive with no disc in it.
        "Optical": bool(available),
        "OpticalBlank": blank,
        "Media": media,
        "MediaAvailable": available,
        "MediaCompatibility": OPTICAL_COMPAT if compat is None else compat,
        "OpticalNumTracks": tracks,
        "OpticalNumAudioTracks": audio,
        "Size": size,
        "Removable": True,
    }


# -- whole-tree scenarios ---------------------------------------------------

#: sr0 empty, sr1 holding a UDF video DVD.  Captured live.
EMPTY_AND_DVD = {
    BLOCK_PREFIX + "sr0": {
        IFACE_BLOCK: block("/dev/sr0", DRIVE_WH16NS40),
    },
    BLOCK_PREFIX + "sr1": {
        IFACE_BLOCK: block("/dev/sr1", DRIVE_BU40N, "DVD_VIDEO", "udf", 4556390400),
        IFACE_FILESYSTEM: {"MountPoints": []},
    },
    DRIVE_WH16NS40: {
        IFACE_DRIVE: drive("BD-RE  WH16NS40", "PROLIFICMP00000003A"),
    },
    DRIVE_BU40N: {
        IFACE_DRIVE: drive("BD-RE BU40N", "393032485330313735363920",
                           media="optical_dvd", available=True, tracks=1,
                           size=4556390400),
    },
}

#: A mounted disc, to exercise ``aay`` mount-point decoding.
MOUNTED_DVD = {
    BLOCK_PREFIX + "sr1": {
        IFACE_BLOCK: block("/dev/sr1", DRIVE_BU40N, "DVD_VIDEO", "udf", 4556390400),
        IFACE_FILESYSTEM: {"MountPoints": [ay("/run/media/user/DVD_VIDEO")]},
    },
    DRIVE_BU40N: {
        IFACE_DRIVE: drive("BD-RE BU40N", "393032485330313735363920",
                           media="optical_dvd", available=True, tracks=1),
    },
}

#: An audio CD: present, but no filesystem at all -- IdType is empty.
AUDIO_CD = {
    BLOCK_PREFIX + "sr1": {
        IFACE_BLOCK: block("/dev/sr1", DRIVE_BU40N),
    },
    DRIVE_BU40N: {
        IFACE_DRIVE: drive("BD-RE BU40N", "393032485330313735363920",
                           media="optical_cd", available=True, tracks=12, audio=12),
    },
}

#: A blank BD-RE: present, no filesystem, no tracks.
BLANK_DISC = {
    BLOCK_PREFIX + "sr1": {
        IFACE_BLOCK: block("/dev/sr1", DRIVE_BU40N),
    },
    DRIVE_BU40N: {
        IFACE_DRIVE: drive("BD-RE BU40N", "393032485330313735363920",
                           media="optical_bd_re", available=True, blank=True),
    },
}

#: A USB stick with a partition -- neither object may be reported as a drive.
NON_OPTICAL = {
    BLOCK_PREFIX + "sdc": {
        IFACE_BLOCK: block("/dev/sdc", DRIVE_USB_STICK, size=16000000000),
    },
    BLOCK_PREFIX + "sdc1": {
        IFACE_BLOCK: block("/dev/sdc1", DRIVE_USB_STICK, "DATA", "vfat", 16000000000),
        IFACE_PARTITION: {"Number": 1},
        IFACE_FILESYSTEM: {"MountPoints": [ay("/run/media/user/DATA")]},
    },
    DRIVE_USB_STICK: {
        IFACE_DRIVE: drive("Flash Disk", "12345", compat=[], vendor="Generic"),
    },
}
