# The `guiserver` engine protocol

An alternative to [robot mode](robot-mode.md), documented so the decision is
reversible with evidence rather than guesswork.

**This project does not use it.** Read this only if robot mode hits a concrete
limitation.

---

## What it is

MakeMKV's own GUI does not shell out to `makemkvcon -r`. It spawns the engine as
a co-process and speaks a shared-memory protocol. `libmmbd` — the LGPL
decryption library in the same tarball — does the same thing. Both are clients
of one protocol.

```
makemkv (Qt GUI)  ─┐
libmmbd (LGPL C)  ─┼─→  makemkvcon guiserver  (the engine)
your code?        ─┘
```

## The spawn contract

From `makemkvgui/src/api_posix.cpp`, function `ApSpawnApp`:

```c
argv = { "<path>/makemkvcon", "guiserver", "A0001+<transport-data>" };
```

- stdin and stdout are pipes created by the caller.
- The engine replies on the pipe with `A0001:<transport-data>$` (terminator is
  `$`).
- The ABI string is `AP_ABI_VER = "A0001"` and **must match exactly.** The
  shipped binary contains `#BADVER#A0001`, confirming it rejects mismatches.
- The `MAKEMKVCON` environment variable overrides the engine path. Otherwise the
  engine is searched for in `/bin`, `/usr/bin`, `/usr/local/bin` — which is why
  this project installs to `/usr/local`.

Three transports exist in the source: shared memory (`clt_shm.cpp`), pipes
(`clt_pipe.cpp`) and stdio (`clt_std.cpp`). The GUI defaults to shared memory;
passing `-std` selects stdio. `libmmbd` uses stdio.

## The wire structure

```c
typedef struct _AP_SHMEM {
    uint32_t cmd;
    uint32_t flags;
    uint32_t pad1, pad2;
    uint32_t args[32];
    uint8_t  strbuf[65008];
} AP_SHMEM;                    // ~64 KB
```

Commands are an enum (`AP_CMD`). The ones relevant to disc backup:

| Command | Purpose |
|---|---|
| `apCallBackupDisc` | Back up a disc — the equivalent of `backup`. |
| `apCallSaveAllSelectedTitlesToMkv` | Remux the selected titles. |
| `apCallOpenCdDisk` / `apCallCloseDisk` / `apCallEjectDisk` | Disc lifecycle. |
| `apCallUpdateAvailableDrives` | Drive enumeration. |
| `apCallSetOutputFolder` | Destination. |
| `apCallGetUiItemState` / `apCallSetUiItemState` | **Per-title selection.** |
| `apCallCancelAllJobs` | **Mid-job cancellation.** |
| `apCallGetSettingInt` / `...String` / `apCallSaveSettings` | Settings. |

Engine→client callbacks mirror robot mode's records:

| Callback | Robot-mode equivalent |
|---|---|
| `apBackUpdateCurrentBar` / `apBackUpdateTotalBar` | `PRGV` |
| `apBackSetTotalName` / `apBackUpdateLayout` | `PRGT` / `PRGC` |
| `apBackSetTitleInfo` / `apBackSetTrackInfo` | `TINFO` / `SINFO` |
| `apBackUpdateDrive` | `DRV` |
| `apBackReportUiMessage` | `MSG` |
| `apBackReportUiDialog` | *(no equivalent — robot mode cannot prompt)* |

Progress uses the same `AP_Progress_MaxValue = 65536` scale.

## What it buys over robot mode

1. **Per-title selection** — choose exactly which titles to rip in one job.
   Robot mode's `mkv` takes one title id per invocation.
2. **Mid-job cancellation** (`apCallCancelAllJobs`). Robot mode offers only
   process termination.
3. **Structured data** — no text parsing, no quote-escaping ambiguity.
4. **Interactive dialogs** — the engine can ask questions. Robot mode cannot
   surface them at all.

## What it costs

- Hand-marshalling a 64 KB struct across a semaphore protocol.
- No vendor documentation whatsoever; the source is the specification.
- `unsafe` Rust and a hard dependency on struct layout.
- The ABI string gates compatibility — a future engine bumping past `A0001`
  breaks the client, with no graceful degradation.

## Licensing

`aproxy.h` and `apdefs.h` carry an explicit grant:

> *This file is hereby placed into public domain, no copyright is claimed.*

So the protocol constants may be reproduced and bound against freely.
`libmmbd` itself is LGPL 2.1+.

## Reference implementation

If this path is ever taken, do not start from scratch — `libmmbd` is a working
client in a few hundred lines of C:

| File | Role |
|---|---|
| `libmmbd/src/mmconn.cpp` | Connection setup — `m_apc.Init(&m_std, ":makemkvcon", &err)`. |
| `libmmbd/src/marmmbd.cpp` | Command dispatch — `ExecCmd(apCallOpenMMBD, ...)`. |
| `makemkvgui/src/client.cpp` | `CApClient::Init` — the handshake. |
| `makemkvgui/src/clt_std.cpp` | The stdio transport (simplest of the three). |
| `makemkvgui/src/api_posix.cpp` | `ApSpawnApp` — the exact argv. |

Source is in the archived `makemkv-oss-1.18.3.tar.gz`.

## `libmmbd` is not a shortcut

`libmmbd` exposes a clean C API (`mmbd_open`, `mmbd_decrypt_unit`) and a
libaacs/libbdplus-compatible ABI, which makes it look like a linkable MakeMKV
library. It is not one for our purposes:

- It only does **decryption** — no title analysis, no MKV muxing, no backup.
- It is itself a client of `guiserver`, so it still spawns `makemkvcon`.

It is the right tool for building a *player* on libbluray. It is the wrong tool
for a disc-backup application.
