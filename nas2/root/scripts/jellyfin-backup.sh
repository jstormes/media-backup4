#!/bin/bash

COMPOSE_FILE="/DockerComposeFiles/Jellyfin/Jellyfin.yml"
LOCK_FILE="${LOCK_FILE:-/run/lock/media-backup.lock}"

# Share media-backup.sh's lock. A first copy onto a new backup drive can run
# past 03:30, and rewriting Backups/ under it would hand it a cache mid-rewrite.
# If it holds the lock, skip tonight: Jellyfin keeps running, and the Media
# drives keep the config from the last run. If the lock is free, hold it
# until the copies are done, so a media backup cannot start mid-rewrite either.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Media backup is running (lock held). Skipping Jellyfin backup."
    exit 0
fi

# 1. Stop Jellyfin container
echo "Stopping Jellyfin..."
docker compose -f "$COMPOSE_FILE" down

# Wait for filesystem buffers to flush
echo "Waiting for files to flush..."
sleep 5

# 2. Find all Media drives by label (Media1, Media2, etc.)
# Uses lsblk to find drives with labels matching Media followed by a digit
MEDIA_DRIVES=$(lsblk -o LABEL,MOUNTPOINT -n | grep -E "^Media[0-9]" | awk '{print $2}')

# 3. Backup to each drive
for MOUNT in $MEDIA_DRIVES; do
    BACKUP_DIR="$MOUNT/Backups"
    mkdir -p "$BACKUP_DIR"

    echo "Backing up to $BACKUP_DIR..."
    # Use -rtLv: recursive, times, dereference symlinks, verbose
    # Skips permissions/owner/group (exFAT doesn't support them)
    # || true allows script to continue despite case-insensitivity conflicts on exFAT
    rsync -rtLv --delete /Jellyfin_Config/ "$BACKUP_DIR/jellyfin_config/" || true
    rsync -rtLv --delete /Jellyfin_Cache/ "$BACKUP_DIR/jellyfin_cache/" || true

    # Write portable docker-compose.yml to drive root
    echo "Writing portable docker-compose.yml to $MOUNT..."
    cat > "$MOUNT/docker-compose.yml" << 'EOF'
# Portable Jellyfin - run from Media drive root
# Usage: docker compose up -d
name: jellyfin

services:
  jellyfin:
    image: jellyfin/jellyfin
    container_name: jellyfin
    environment:
      - PUID=1001
      - PGID=1001
      - TZ=CST/UTC
    volumes:
      - ./Backups/jellyfin_config:/config
      - ./Backups/jellyfin_cache:/cache
      - ./:/media
    ports:
      - 8096:8096
      - 8920:8920
      - 7359:7359/udp
      - 1900:1900/udp
    restart: unless-stopped
EOF

    # Write README.md to drive root
    echo "Writing README.md to $MOUNT..."
    cat > "$MOUNT/README.md" << 'EOF'
# Portable Jellyfin Media Server

This drive contains a complete Jellyfin media server backup that can run on any system with Docker installed.

## Quick Start

1. Mount this drive
2. Open a terminal and navigate to the drive root
3. Run:

```bash
docker compose up -d
```

4. Access Jellyfin at: http://localhost:8096

## Stopping Jellyfin

```bash
docker compose down
```

## Important: Disable Transcoding

If running on unknown or low-powered hardware, **disable hardware transcoding** to avoid playback issues:

1. Open Jellyfin (http://localhost:8096)
2. Go to Dashboard → Playback → Transcoding
3. Set "Hardware acceleration" to **None**
4. Save changes

The original server uses GPU acceleration (`/dev/dri`) which may not be available on other systems.

## Directory Structure

```
/
├── docker-compose.yml    # Docker configuration
├── README.md             # This file
├── Backups/
│   ├── jellyfin_config/  # Jellyfin settings, metadata, users
│   └── jellyfin_cache/   # Image cache, transcodes
├── Movies/               # Movie library
├── Music/                # Music library
├── Shows/                # TV shows library
└── After Hours/          # Additional content
```

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8096 | TCP | Web interface |
| 8920 | TCP | HTTPS (optional) |
| 7359 | UDP | Discovery |
| 1900 | UDP | DLNA |

## Notes

- All user accounts and settings are preserved
- Media metadata and images are included
- First startup may take a moment to initialize
- Transcoding cache will rebuild as needed
EOF
done

# 4. Restart Jellyfin
# Release the lock before starting Jellyfin, so nothing started from here can
# inherit it and keep holding it after this script exits.
exec 9>&-

echo "Starting Jellyfin..."
docker compose -f "$COMPOSE_FILE" up -d

echo "Backup complete!"
