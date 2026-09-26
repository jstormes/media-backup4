# nas2 backup scripts

Copies of the backup jobs that run on **nas2**, the Jellyfin server. Paths
under this directory mirror where each file lives on nas2:

| Repo path | On nas2 | What it is |
|---|---|---|
| `root/scripts/media-backup.sh` | `/root/scripts/media-backup.sh` | 04:00 mirror of each MediaN drive to its plugged-in BackupN/BackupNA/BackupNB drives |
| `root/scripts/jellyfin-backup.sh` | `/root/scripts/jellyfin-backup.sh` | 03:30 copy of Jellyfin's config and cache onto every Media drive |
| `root/scripts/README.md` | `/root/scripts/README.md` | pointer back to this repo for anyone looking on nas2 |
| `etc/cron.d/media-backup` | `/etc/cron.d/media-backup` | schedule for the mirror; no redirect, because the script writes its own log |
| `etc/cron.d/jellyfin-backup` | `/etc/cron.d/jellyfin-backup` | schedule for the Jellyfin copy |
| `etc/logrotate.d/media-backup` | `/etc/logrotate.d/media-backup` | weekly rotation of both logs, eight kept |

What the jobs do, why the backup drives are absent most nights, and how to
prepare a new backup drive: [How the server copy is protected](../docs/jellyfin/backup-strategy.md).

**The copy on nas2 is what runs.** These were taken from nas2 on 2026-09-26,
checked by sha256 against the installed files. Change them here, then deploy.
If someone edits them on nas2 directly, copy the change back here.

## Deploying

```
scp nas2/root/scripts/media-backup.sh nas2:~/
ssh nas2 'bash -n ~/media-backup.sh && sudo install -m 755 -o root -g root ~/media-backup.sh /root/scripts/media-backup.sh'
```

Use `install`, not an in-place edit. `install` writes a new file, so a backup
already running keeps reading the version it started with; bash reads a script
as it goes, so editing the running file under it can break the run midway.

Before deploying a change to `media-backup.sh`, try it with
`sudo /root/scripts/media-backup.sh --dry-run` while a backup drive is
plugged in. `LOG_FILE` and `MOUNT_BASE` can be overridden from the
environment to test against loopback drives without touching the real log.
