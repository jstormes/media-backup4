# Attribute IDs, drive states and stream flags

Numeric constants used by the `CINFO` / `TINFO` / `SINFO` / `DRV` records in
[robot mode](robot-mode.md), and by the [engine protocol](engine-protocol.md).

Transcribed from `makemkvgui/inc/lgpl/apdefs.h` in the MakeMKV 1.18.3 `-oss`
source. **That header is explicitly placed into the public domain** by its
author, so reproducing these values here carries no licensing obligation.

Values are stable across the versions inspected, but they are an enum in a
vendor header — treat unknown ids as ignorable rather than as errors.

---

## Item attributes (`AP_ItemAttributeId`)

The `id` field in `CINFO:id,...`, `TINFO:title,id,...`, `SINFO:title,stream,id,...`.

All 51 values (0–50):

| id | name | notes |
|---:|------|-------|
| 0 | `ap_iaUnknown` |  |
| 1 | `ap_iaType` | `Video` / `Audio` / `Subtitles` for streams; disc type for `CINFO` |
| 2 | `ap_iaName` | Disc or title name |
| 3 | `ap_iaLangCode` | ISO 639 language code, e.g. `eng` |
| 4 | `ap_iaLangName` |  |
| 5 | `ap_iaCodecId` | Matroska codec id, e.g. `V_MPEG4/ISO/AVC` |
| 6 | `ap_iaCodecShort` |  |
| 7 | `ap_iaCodecLong` |  |
| 8 | `ap_iaChapterCount` | Chapter count |
| 9 | `ap_iaDuration` | `H:MM:SS` |
| 10 | `ap_iaDiskSize` | **Display string** — e.g. `29.3 GB`. Do not parse. |
| 11 | `ap_iaDiskSizeBytes` | **Exact bytes** — use this for arithmetic |
| 12 | `ap_iaStreamTypeExtension` |  |
| 13 | `ap_iaBitrate` | Bitrate |
| 14 | `ap_iaAudioChannelsCount` | Audio channel count |
| 15 | `ap_iaAngleInfo` |  |
| 16 | `ap_iaSourceFileName` | Source file, e.g. `00001.mpls` |
| 17 | `ap_iaAudioSampleRate` |  |
| 18 | `ap_iaAudioSampleSize` |  |
| 19 | `ap_iaVideoSize` | e.g. `1920x1080` |
| 20 | `ap_iaVideoAspectRatio` | e.g. `16:9` |
| 21 | `ap_iaVideoFrameRate` | e.g. `23.976 (120000/5005)` |
| 22 | `ap_iaStreamFlags` | Bitmask — see stream flags below |
| 23 | `ap_iaDateTime` |  |
| 24 | `ap_iaOriginalTitleId` |  |
| 25 | `ap_iaSegmentsCount` |  |
| 26 | `ap_iaSegmentsMap` |  |
| 27 | `ap_iaOutputFileName` | Suggested output filename |
| 28 | `ap_iaMetadataLanguageCode` |  |
| 29 | `ap_iaMetadataLanguageName` |  |
| 30 | `ap_iaTreeInfo` | Sort weight used by the GUI |
| 31 | `ap_iaPanelTitle` |  |
| 32 | `ap_iaVolumeName` |  |
| 33 | `ap_iaOrderWeight` |  |
| 34 | `ap_iaOutputFormat` |  |
| 35 | `ap_iaOutputFormatDescription` |  |
| 36 | `ap_iaSeamlessInfo` |  |
| 37 | `ap_iaPanelText` |  |
| 38 | `ap_iaMkvFlags` |  |
| 39 | `ap_iaMkvFlagsText` |  |
| 40 | `ap_iaAudioChannelLayoutName` |  |
| 41 | `ap_iaOutputCodecShort` |  |
| 42 | `ap_iaOutputConversionType` |  |
| 43 | `ap_iaOutputAudioSampleRate` |  |
| 44 | `ap_iaOutputAudioSampleSize` |  |
| 45 | `ap_iaOutputAudioChannelsCount` |  |
| 46 | `ap_iaOutputAudioChannelLayoutName` |  |
| 47 | `ap_iaOutputAudioChannelLayout` |  |
| 48 | `ap_iaOutputAudioMixDescription` |  |
| 49 | `ap_iaComment` |  |
| 50 | `ap_iaOffsetSequenceId` |  |

### The size pair, 10 and 11

`ap_iaDiskSize` (10) and `ap_iaDiskSizeBytes` (11) describe the same quantity.
`10` is a rounded display string (`29.3 GB`); `11` is exact
(`31506235392`). **Always compute from 11**, and show 10 only if you want
MakeMKV's own formatting.

### Attributes this project relies on

For populating `disk-N.json` (see `PLAN.md`):

| Field in `disk-N.json` | Attribute |
|---|---|
| `title` | `TINFO` id `2` (`ap_iaName`), or `CINFO` id `2` for the disc |
| `bytes_copied` target | `TINFO` id `11` (`ap_iaDiskSizeBytes`) |
| `output_dir` filename hint | `TINFO` id `27` (`ap_iaOutputFileName`) |

Duration (`9`) and chapter count (`8`) are useful for picking the main feature:
on a film disc it is reliably the longest title by a wide margin.

---

## Drive states (`DRV` field 2)

```c
AP_DriveStateEmptyClosed = 0
AP_DriveStateEmptyOpen   = 1
AP_DriveStateInserted    = 2
AP_DriveStateLoading     = 3
AP_DriveStateNoDrive     = 256
AP_DriveStateUnmounting  = 257
```

`info` always emits **16 `DRV` records (slots 0–15)** whether or not drives
exist. Empty slots report `256`. Filter on state; never assume slot 0 is real.

A drive ready to read reports **`2`**. State `3` (loading) means the disc is
still spinning up — a plausible transient right after tray close, which matters
for the udev copy trigger in `PLAN.md`.

## Stream flags (`SINFO` id 22, bitmask)

```c
AP_AVStreamFlag_DirectorsComments           = 1
AP_AVStreamFlag_AlternateDirectorsComments  = 2
AP_AVStreamFlag_ForVisuallyImpaired         = 4
AP_AVStreamFlag_CoreAudio                   = 256
AP_AVStreamFlag_SecondaryAudio              = 512
AP_AVStreamFlag_HasCoreAudio                = 1024
AP_AVStreamFlag_DerivedStream               = 2048
AP_AVStreamFlag_ForcedSubtitles             = 4096
AP_AVStreamFlag_ProfileSecondaryStream      = 16384
AP_AVStreamFlag_OffsetSequenceIdPresent     = 32768
```

Useful for automatic track selection: `4096` marks forced subtitles (the ones
you usually want burned in for foreign dialogue), and `1`/`2` mark commentary
tracks (usually excluded from an automated backup).

## Operation flags

```c
AP_BackupFlagDecryptVideo    = 1     // the --decrypt switch
AP_OpenFlagManualMode        = 1
AP_UpdateDrivesFlagNoScan    = 1
AP_UpdateDrivesFlagNoSingleDrive = 2
AP_Progress_MaxValue         = 65536 // see robot-mode.md — NOT a percentage
```
