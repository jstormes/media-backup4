#!/usr/bin/python3
"""Sample per-optical-drive read behaviour against how many drives are active.

Reads /proc/diskstats and the open collections' collection.json. Issues no SCSI
command and opens no drive: the sampler must not perturb what it measures.

One CSV row per drive per interval:

    ts, dev, label, collection, bytes_per_s, r_await_ms, util_pct,
    reads_per_s, active_drives, other_active, job_elapsed_s

`active_drives` counts optical drives that read anything in the interval;
`other_active` is that minus this drive, which is the independent variable.
`job_elapsed_s` is a proxy for radius -- optical is CAV, so read speed climbs
with elapsed time, and any comparison that ignores it is comparing radii.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

DISKSTATS = Path("/proc/diskstats")
COLLECTIONS = Path("/srv/media-backup/collections")
SECTOR = 512
ACTIVE_STATES = {"queued", "resolving", "scanning", "copying", "verifying", "ejecting"}


def diskstats() -> dict[str, tuple[int, int, int]]:
    """{sr name: (reads_completed, sectors_read, ms_reading, io_ticks)}."""
    out = {}
    for line in DISKSTATS.read_text().splitlines():
        f = line.split()
        if len(f) < 14 or not f[2].startswith("sr"):
            continue
        out[f[2]] = (int(f[3]), int(f[5]), int(f[6]), int(f[12]))
    return out


def discs_in_drives() -> dict[str, dict]:
    """{device: {label, collection, started_at}} for discs being worked on.

    Read from the store rather than from udev, because during a rip the disc is
    not mounted and udev's label is whatever it saw at insert.
    """
    out = {}
    if not COLLECTIONS.is_dir():
        return out
    for p in COLLECTIONS.glob("*/collection.json"):
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for d in c.get("discs", []):
            if d.get("state") not in ACTIVE_STATES:
                continue
            for a in reversed(d.get("attempts", [])):
                dev = a.get("device")
                if dev:
                    out[dev] = {"label": d.get("label", ""),
                                "collection": c.get("title", ""),
                                "started_at": a.get("started_at", "")}
                    break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--idle-bytes", type=int, default=64 * 1024,
                    help="bytes/interval below which a drive counts as idle")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists() or out.stat().st_size == 0
    fh = open(out, "a", newline="")
    w = csv.writer(fh)
    if new:
        w.writerow(["ts", "dev", "label", "collection", "bytes_per_s",
                    "r_await_ms", "util_pct", "reads_per_s", "active_drives",
                    "other_active", "job_elapsed_s"])
        fh.flush()

    prev, prev_t = diskstats(), time.monotonic()
    starts: dict[str, float] = {}

    while True:
        time.sleep(args.interval)
        now, t = diskstats(), time.monotonic()
        dt = t - prev_t
        if dt <= 0:
            prev, prev_t = now, t
            continue

        discs = discs_in_drives()
        per = {}
        for dev, (reads, sectors, ms_read, ticks) in now.items():
            if dev not in prev:
                continue
            p = prev[dev]
            d_reads = reads - p[0]
            d_bytes = (sectors - p[1]) * SECTOR
            d_ms = ms_read - p[2]
            d_ticks = ticks - p[3]
            per[dev] = {
                "bytes_per_s": d_bytes / dt,
                "reads_per_s": d_reads / dt,
                "r_await_ms": (d_ms / d_reads) if d_reads else 0.0,
                "util_pct": min(100.0, 100.0 * d_ticks / (dt * 1000.0)),
                "busy": d_bytes >= args.idle_bytes,
            }

        active = sum(1 for v in per.values() if v["busy"])
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        for dev, v in sorted(per.items()):
            node = f"/dev/{dev}"
            info = discs.get(node, {})
            if v["busy"]:
                starts.setdefault(node, t)
            else:
                starts.pop(node, None)
            elapsed = round(t - starts[node], 1) if node in starts else ""
            w.writerow([stamp, dev, info.get("label", ""), info.get("collection", ""),
                        round(v["bytes_per_s"], 1), round(v["r_await_ms"], 2),
                        round(v["util_pct"], 1), round(v["reads_per_s"], 1),
                        active, active - (1 if v["busy"] else 0), elapsed])
        fh.flush()
        prev, prev_t = now, t


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
