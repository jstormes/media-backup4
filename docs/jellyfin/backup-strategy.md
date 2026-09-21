# How the server copy is protected

[Reclaiming the archive](publishing.md#reclaiming-the-archive) deletes the last
local copy of a film once the server's copy is proven by checksum. From that
point the server is the only copy, and the disc is the only fallback. This is
what stands behind the server copy, and -- just as important -- what it does
not do.

Measured on nas2 on 2026-09-13.

## The drives

| Label | Device path | Filesystem | Role |
|---|---|---|---|
| Media1 | `/srv/dev-disk-by-uuid-78AA-077A` | exFAT, 7.3T | the Jellyfin library; publish target |
| Media2 | `/srv/dev-disk-by-uuid-0A63-B16B` | exFAT, 7.3T | second library drive |
| Backup1 | mounted at `/mnt/backup_temp` | exFAT, 7.3T | cold mirror of Media1 |

Media1 and Media2 are in `/etc/fstab` and mount at boot. **Backup1 is
deliberately not**, because it is not normally attached -- see below.

## Backup1 lives in storage, not in the machine

This is the part that looks like a fault and is not.

Backup1 is a cold mirror. It is kept physically in storage and connected only
when there is enough new content to be worth mirroring -- a judgement call made
by the operator, not a schedule. Retrieving it is a deliberate act.

A cron entry runs the backup every night at 04:00 regardless:

```
/etc/cron.d/media-backup     0 4 * * *   /root/scripts/media-backup.sh
/etc/cron.d/jellyfin-backup  30 3 * * *  /root/scripts/jellyfin-backup.sh
```

On the great majority of nights the drive is not attached, the script finds no
backup drive, logs one line and exits 0:

```
2026-09-12 04:00:01 - === Media Backup Started ===
2026-09-12 04:00:01 - No Backup drives found. Exiting.
```

**A run of these is the normal state, not a backlog of failures.** Between
2026-01-25 and 2026-09-13 the log records 232 such nights and 23 completed
runs. That ratio is the design working: the cron exists to catch the drive
whenever it happens to be connected, so that connecting it is the only manual
step. Do not "fix" this by making the skip path exit non-zero or send mail --
it would alarm every night about a disk that is sitting in a cupboard on
purpose.

The signal that matters is not "did it run last night" but "how much new
content has accumulated since the last completed run".

## What a run does

The script is at `/root/scripts/media-backup.sh` (root-only; the log is
world-readable and is the practical way to see what it did). It:

1. looks for an attached drive labelled as a backup for Media1
2. checks the filesystem type matches, and that the backup is large enough
   for what Media1 is using
3. mounts it on `/mnt/backup_temp`
4. rsyncs, mirroring deletions:

```
rsync -rtLv --delete \
  --exclude='$RECYCLE.BIN' --exclude='System Volume Information' \
  /srv/dev-disk-by-uuid-78AA-077A/ /mnt/backup_temp/
```

5. runs `sync`, then unmounts, and logs `Successfully unmounted`

Both the mount and the unmount are the script's own doing, which is why
Backup1 is absent from fstab. If you find `/mnt/backup_temp` mounted with no
backup running, the script died before step 5.

A full run is long. The 2026-09-13 run mirrored 6.5TB in **6h 22m**
(04:00:01 to 10:22:17), most of it spent scanning unchanged files rather than
copying. Do not interpret a run still going at midday as a hang.

## What this protects against, and what it does not

It protects against losing Media1: drive failure, accidental deletion of a
film, a bad publish. The mirror is a full copy on separate hardware, stored
offline, which also puts it out of reach of anything that can reach the
running server.

It is **not** a version history. `--delete` makes Backup1 converge on whatever
Media1 holds at the moment it runs. A file deleted from Media1 is deleted from
Backup1 on the next connection. The window in which a mistake can be undone is
exactly the interval until the next run -- which, given the drive lives in
storage, is usually long, and is the main thing that makes this arrangement
forgiving. Connecting the drive immediately after a bad deletion is the one
way to turn that strength into a loss.

It does not protect Media2, which has no mirror.

Because the copy is made by rsync onto exFAT, it carries no checksum of its
own. `--delete` plus size and mtime is all rsync has to work with there. The
byte-for-byte guarantee described in
[publishing](publishing.md#reclaiming-the-archive) applies to the publish step
onto Media1, not to this mirror.

## Logs

```
/var/log/media-backup.log
/var/log/jellyfin-backup.log
```

Both are appended forever -- there is no logrotate rule for either, and the
jellyfin one had reached 180MB by 2026-09-13. They live on the 952G root
filesystem, so this is slow-moving rather than urgent, but it does not
self-correct.

The logs are the run history: the script timestamps its own phase messages, so
`grep -E '^20[0-9]{2}-' /var/log/media-backup.log` reconstructs every run,
every skip, and how long each took. rsync's own `-v` file list is interleaved
and is the bulk of the size. Note that each line appears twice, because the
script pipes through `tee` into the same file cron is already redirecting to.

## Jellyfin's own configuration

The 03:30 job copies Jellyfin's config -- database, metadata, plugins -- to
`Backups/jellyfin_config/` **on Media1**. It is therefore included in the
Media1 mirror, and carried to Backup1 by the 04:00 run. It is not backed up
anywhere the library is not.
