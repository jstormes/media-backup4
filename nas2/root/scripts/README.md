# Backup scripts

The scripts here are deployed from the **media-backup4** repo, directory
`nas2/`. Change them there and deploy with `install`; don't edit them here.
If something does get edited here, copy it back to the repo.

| Time | Script | What it does |
|---|---|---|
| 03:30 | `jellyfin-backup.sh` | stops Jellyfin, copies its config and cache to `Backups/` on every Media drive, restarts it |
| 04:00 | `media-backup.sh` | mirrors each MediaN drive to whichever of its backup drives (`BackupN`, `BackupNA`, `BackupNB`) is plugged in |

Cron: `/etc/cron.d/jellyfin-backup`, `/etc/cron.d/media-backup`.
Logs: `/var/log/jellyfin-backup.log`, `/var/log/media-backup.log`, rotated
weekly by `/etc/logrotate.d/media-backup`.

```
/root/scripts/media-backup.sh --dry-run   # what would change, changes nothing
/root/scripts/media-backup.sh --verify    # also compare file contents (hours per drive)
grep -E '^20[0-9]{2}-' /var/log/media-backup.log | tail   # recent runs
```

"No Backup drives found" most nights is normal: the backup drives live in
storage and are docked on demand.

The full description -- drives and labels, two copies per Media drive,
preparing a new backup drive, what a run protects against and what it does
not -- is in the repo at `docs/jellyfin/backup-strategy.md`.

`*.old-2026-09-26` files are the versions from before the 2026-09-26 rewrite.
