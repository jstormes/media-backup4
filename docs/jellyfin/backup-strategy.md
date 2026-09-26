# How the server copy is protected

[Reclaiming the archive](publishing.md#reclaiming-the-archive) deletes the last
local copy of a film once the server's copy is proven by checksum. From that
point the server is the only copy, and the disc is the only fallback. This is
what stands behind the server copy, and -- just as important -- what it does
not do.

Measured on nas2 on 2026-09-13; drives, script and plan updated 2026-09-26.

## The drives

| Label | Device path | Filesystem | Role |
|---|---|---|---|
| Media1 | `/srv/dev-disk-by-uuid-78AA-077A` | exFAT, 7.3T | library published before 2026-09-18; 95% full, no longer written |
| Media2 | `/srv/dev-disk-by-uuid-0A63-B16B` | exFAT, 7.3T | publish target since 2026-09-18; also written by the UHD test setup |
| Backup1 | mounted at `/mnt/backup_temp/Backup1` during a run | exFAT, 7.3T | cold mirror of Media1 (copy A) |
| Backup2A | mounted at `/mnt/backup_temp/Backup2A` during a run | exFAT, 7.3T | cold mirror of Media2 (copy A); ST8000DM004, serial ZR16HXZR, UUID `FEBB-79A8` |

Media1 and Media2 are in `/etc/fstab` and mount at boot. **The backup drives
are deliberately not**, because they are not normally attached -- see below.

## Two copies of every Media drive

The plan, since 2026-09-26, is **two backup drives per Media drive**, four in
total. The number in a backup drive's label says which Media drive it mirrors;
a letter tells the two copies apart:

| Media drive | Copy A | Copy B |
|---|---|---|
| Media1 | Backup1 (existing, no letter) | not yet made -- label it `Backup1B` |
| Media2 | Backup2A (first run started 2026-09-26) | not yet made -- label it `Backup2B` |

The script pairs `BackupN`, `BackupNA` and `BackupNB` with `MediaN`. Distinct
labels are what make the log show *which* copy was refreshed and when; with two
copies that each spend most of their life in storage, knowing how stale each one
is matters. Two plugged-in devices carrying the same label is refused rather
than guessed at.

### Preparing a new backup drive

A new drive is invisible to the script until it carries a matching label. Lay
it out like Media2 -- GPT, a Microsoft reserved partition, then exFAT -- so it
also reads on Windows. `exfatprogs` was installed on nas2 for this. **Check the
serial first**: dock drives come and go, and `/dev/sdX` names move.

```
lsblk -dn -o NAME,SERIAL,TRAN,SIZE /dev/sdg          # confirm it is the new drive
sudo smartctl -d sat -t short /dev/sdg               # then -l selftest to read the result
printf 'label: gpt\nfirst-lba: 34\nstart=34, size=32734, type=E3C9E316-0B5C-4DB8-817D-F92DF00215AE, name="Microsoft reserved partition"\nstart=32768, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7, name="Basic data partition"\n' | sudo sfdisk /dev/sdg
sudo mkfs.exfat -L Backup2B /dev/sdg2
sudo /root/scripts/media-backup.sh --dry-run         # confirms the pairing, changes nothing
```

`first-lba: 34` is needed to match Media2 exactly; without it `sfdisk` refuses
a partition starting at sector 34.

## Backup drives live in storage, not in the machine

This is the part that looks like a fault and is not.

Each backup drive is a cold mirror. It is kept physically in storage and
connected only when there is enough new content to be worth mirroring -- a
judgement call made by the operator, not a schedule. Retrieving it is a
deliberate act.

A cron entry runs the backup every night at 04:00 regardless:

```
/etc/cron.d/media-backup     0 4 * * *   /root/scripts/media-backup.sh
/etc/cron.d/jellyfin-backup  30 3 * * *  /root/scripts/jellyfin-backup.sh
```

On the great majority of nights no backup drive is attached, the script logs
one line and exits 0:

```
2026-09-12 04:00:01 - === Media Backup Started ===
2026-09-12 04:00:01 - No Backup drives found. Exiting.
```

**A run of these is the normal state, not a backlog of failures.** Between
2026-01-25 and 2026-09-13 the log records 232 such nights and 23 completed
runs. That ratio is the design working: the cron exists to catch a drive
whenever it happens to be connected, so that connecting it is the only manual
step. Do not "fix" this by making the skip path exit non-zero or send mail --
it would alarm every night about disks that are sitting in a cupboard on
purpose.

The signal that matters is not "did it run last night" but "how much new
content has accumulated since each copy's last completed run".

## What a run does

The script is at `/root/scripts/media-backup.sh` (root-only; the log is
world-readable and is the practical way to see what it did). The version
before the 2026-09-26 rewrite is kept beside it as
`media-backup.sh.old-2026-09-26`. For every backup drive that is plugged in,
it:

1. finds the drive by exact label (`^Backup[0-9]+[A-Z]?$`) and its Media drive
   by number, refusing a label found on more than one device
2. skips it if it is already mounted, or if its Media drive is missing or not
   mounted
3. checks the filesystem types match, and that the backup is large enough for
   what the Media drive is using
4. mounts it on `/mnt/backup_temp/<label>` -- one mount point per drive, so a
   stuck mount never gets another drive stacked on it
5. rsyncs, mirroring deletions:

```
rsync -rtLv --stats --delete --modify-window=1 --max-delete=500 \
  --backup --backup-dir=.backup-deleted/<run date and time> \
  --exclude='$RECYCLE.BIN' --exclude='System Volume Information' \
  --exclude=/.backup-deleted \
  /srv/dev-disk-by-uuid-0A63-B16B/ /mnt/backup_temp/Backup2A/
```

6. optionally verifies contents (`--verify`, below)
7. prunes `.backup-deleted/` folders older than 90 days
8. runs `sync`, unmounts, and logs `Successfully unmounted`

Each drive ends with `Backup of MediaN to <label> completed.` or `... FAILED.`,
and the run ends with `=== Media Backup Finished ===` or
`=== Media Backup Finished WITH ERRORS (failed: ...) ===`. The script exits 1
if any plugged-in drive failed or was skipped, 0 otherwise -- including the
no-drive nights.

**Rsync's exit code is checked.** Before the rewrite, a pipe through `tee` and
a trailing `|| true` meant every run logged "completed" whatever rsync did.
Now a non-zero code is a failure, with two exceptions: code 24 (files vanished
mid-copy) is a warning, because the 03:30 Jellyfin job rewrites
`Backups/jellyfin_cache` while a long run is still going; the next run catches
up. Code 25 means the deletion limit was hit -- see below.

The mount and the unmount are both the script's own doing, which is why the
backup drives are absent from fstab. The script unmounts on exit even if it is
killed mid-copy. If you find a `/mnt/backup_temp/<label>` mounted with no
backup running, unmount it before unplugging.

A `flock` on `/run/lock/media-backup.lock` means two runs never overlap. A
first full copy can outlast the 24-hour cron interval; the next night's run
logs `Another media backup is still running. Exiting.` and leaves it alone.

### How long a run takes

An incremental run is mostly scanning: the 2026-09-13 run mirrored 6.5TB in
**6h 22m**, and the 2026-09-14 one took 1h 40m. Do not interpret a run still
going at midday as a hang.

A **first** copy onto an empty drive is much longer. The backup drives are
Seagate BarraCuda SMR disks, which slow sharply under long sustained writes,
and a Media drive starts with tens of thousands of small Jellyfin cache images
that exFAT writes slowly (about 15MB/s at the start of the Backup2A copy). Allow
a day or more for 4-7TB. To start one by hand, detached from the terminal:

```
sudo systemd-run --unit=media-backup-first /root/scripts/media-backup.sh
systemctl status media-backup-first
```

### Options

```
media-backup.sh --dry-run    # show what would be copied and deleted; change nothing
media-backup.sh --verify     # after the copy, compare every file's contents
```

`--verify` re-reads both drives in full (`rsync --checksum --dry-run`) and fails
the run if any file differs. It takes hours per drive on these disks, which is
why it is not the default -- size and modification time are the everyday check.
It is worth running once after a drive's first copy, and after anything that
casts doubt on a drive.

## What this protects against, and what it does not

It protects against losing a Media drive: drive failure, accidental deletion of
a film, a bad publish. Each mirror is a full copy on separate hardware, stored
offline, which also puts it out of reach of anything that can reach the
running server. With two copies per Media drive, one backup failing or being
out of date is no longer the end of the line.

It is **still a mirror, not a version history**, but deletions are no longer
immediate. A file deleted or replaced on a Media drive is moved, on the next
run, into `.backup-deleted/<run date and time>/` on the backup drive, and
erased from there after 90 days. Recovering it means copying it back out of
that folder. Two limits:

- the 90 days count from the run that moved it, not from the mistake;
- the space it takes counts against the backup drive. Backup1 is tight --
  Media1 uses 6.9T of its 7.3T -- so replacing large files there can fill it,
  and the run will then fail with rsync's error.

A run that would delete more than **500** files stops deleting and fails
(rsync code 25), on the theory that a mass deletion on a Media drive is more
likely an accident than intent. The deletions made before it stopped are in
`.backup-deleted`. If the deletion was intended, raise `MAX_DELETE` at the top
of the script for one run.

Media2 also receives output from the UHD test setup, so its mirrors carry that
too. Its growth between publishes is expected.

The copy is made by rsync onto exFAT, and day to day it is judged by size and
mtime only (`--modify-window=1` absorbs exFAT's coarser timestamps). The
byte-for-byte guarantee described in
[publishing](publishing.md#reclaiming-the-archive) applies to the publish step
onto the Media drive; for the mirror it holds only on runs made with
`--verify`.

## Logs

```
/var/log/media-backup.log
/var/log/jellyfin-backup.log
```

Both are rotated weekly by `/etc/logrotate.d/media-backup`, keeping eight
compressed weeks. (Before 2026-09-26 neither was rotated; the jellyfin one had
reached 200MB.)

The logs are the run history: the script timestamps its own phase messages, so
`grep -E '^20[0-9]{2}-' /var/log/media-backup.log` reconstructs every run,
every skip, which drive was refreshed, and how long each took. rsync's own
`-v` file list is interleaved and is the bulk of the size. The script writes
its log itself -- the cron line has no redirect -- and also echoes to the
terminal when run by hand. Entries before 2026-09-26 appear twice, from the
old script piping through `tee` into the file cron was already redirecting to.

## Jellyfin's own configuration

The 03:30 job stops Jellyfin, copies its config and cache -- database,
metadata, plugins, images -- to `Backups/jellyfin_config/` and
`Backups/jellyfin_cache/` **on every Media drive**, writes a portable
`docker-compose.yml` and `README.md` to each drive's root, and restarts it. The
config is therefore in every Media drive's mirror. It is not backed up anywhere
the library is not.
