#!/usr/bin/python3
"""Compare the same disc read by two different drives, aligned by radius.

This is the swap test: put one disc in drive A, then in drive B, and ask
whether the difference follows the disc or the drive. It is the only cheap way
to tell "this drive is weak" from "this disc is marginal", and both look
identical in a single rip.

Alignment is by `disc_bytes` -- bytes read since the disc went in -- not by
wall time. Optical is CAV, so read rate is a function of radius, and radius is
what cumulative bytes tracks. Comparing two drives at the same *timestamp*
compares different parts of the disc and answers nothing.

    compare.py vibration.csv --label TRANSFORMERS2_D1_VANILLA

Reads several CSVs if given them, so a rip captured before a sampler restart
still participates.
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict


def load(paths, label):
    rows = []
    for p in paths:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("label") != label:
                    continue
                try:
                    b = float(r["bytes_per_s"])
                    if b <= 64 * 1024:
                        continue
                    rows.append({
                        "dev": r["dev"], "ts": r["ts"], "mb": b / 1e6,
                        "await": float(r["r_await_ms"]),
                        "nbrs": int(r["other_active"]),
                        "disc_mb": float(r.get("disc_bytes") or 0) / 1e6,
                    })
                except (ValueError, KeyError):
                    continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--label", required=True)
    ap.add_argument("--bin-mb", type=float, default=500.0,
                    help="radius bin width in MB of disc read")
    args = ap.parse_args()

    rows = load(args.csv, args.label)
    if not rows:
        raise SystemExit(f"no busy samples for label {args.label!r}")

    devs = sorted({r["dev"] for r in rows})
    print(f"disc {args.label!r}: {len(rows)} samples across {len(devs)} drive(s): "
          f"{', '.join(devs)}")
    if len(devs) < 2:
        print("\nOnly one drive has read this disc. The swap test needs two --")
        print("re-rip the same disc in another drive with the sampler running.")

    has_radius = any(r["disc_mb"] > 0 for r in rows)
    if not has_radius:
        print("\nWARNING: no disc_bytes in this data (pre-fix sampler). Falling back")
        print("to pooled medians, which are confounded by radius -- treat as weak.")

    print(f"\n=== per drive, binned by disc read (bin = {args.bin_mb:.0f} MB) ===")
    print(f"{'radius MB':>12}  " + "  ".join(f"{d:>22}" for d in devs))
    print(f"{'':>12}  " + "  ".join(f"{'MB/s   await   nbrs':>22}" for _ in devs))
    g = defaultdict(lambda: defaultdict(list))
    for r in rows:
        b = int(r["disc_mb"] // args.bin_mb) if has_radius else 0
        g[b][r["dev"]].append(r)
    for b in sorted(g):
        lo = b * args.bin_mb
        cells = []
        for d in devs:
            rs = g[b].get(d)
            if not rs:
                cells.append(f"{'--':>22}")
                continue
            cells.append(f"{st.median([x['mb'] for x in rs]):6.2f} "
                         f"{st.median([x['await'] for x in rs]):7.2f} "
                         f"{st.median([x['nbrs'] for x in rs]):6.1f}")
        print(f"{lo:>7.0f}-{lo+args.bin_mb:<4.0f}  " + "  ".join(cells))

    print("\n=== overall, per drive ===")
    print(f"{'dev':5} {'n':>6} {'MB/s':>7} {'await ms':>9} {'read MB':>9} {'solo %':>7}")
    per = defaultdict(list)
    for r in rows:
        per[r["dev"]].append(r)
    for d in devs:
        rs = per[d]
        solo = 100.0 * sum(1 for x in rs if x["nbrs"] == 0) / len(rs)
        print(f"{d:5} {len(rs):>6} {st.median([x['mb'] for x in rs]):7.2f} "
              f"{st.median([x['await'] for x in rs]):9.2f} "
              f"{max(x['disc_mb'] for x in rs):9.0f} {solo:7.0f}")

    if len(devs) >= 2:
        a, b = devs[0], devs[1]
        # Compare only radii BOTH drives actually reached. A drive that read
        # further has more of the slow outer tail in its median, and pooling
        # the lot reports that extra tail as if it were a drive difference --
        # measured 2026-09-23, it made sr3 look 9% slower than sr1 on a disc
        # where the two are within 5% at every shared radius.
        shared = [k for k in g if a in g[k] and b in g[k]]
        if not shared:
            print("\nNo radius bin was reached by both drives - not comparable.")
            return
        cov = {d: max(x["disc_mb"] for x in per[d]) for d in (a, b)}
        skew = abs(cov[a] - cov[b]) / max(cov.values())
        def med(dev, key):
            return st.median([x[key] for k in shared for x in g[k][dev]])
        ma, mb_ = med(a, "mb"), med(b, "mb")
        aa, ab = med(a, "await"), med(b, "await")
        lo = min(shared) * args.bin_mb
        hi = (max(shared) + 1) * args.bin_mb
        print(f"\n=== like-for-like: {lo:.0f}-{hi:.0f} MB, reached by both ===")
        print(f"{b} vs {a}: throughput x{mb_/ma:.2f}, latency x{ab/aa:.2f}")
        print(f"  coverage: {a} {cov[a]:.0f} MB, {b} {cov[b]:.0f} MB "
              f"({skew*100:.0f}% apart)")
        worst = max(((k, g[k][b] and g[k][a] and
                      st.median([x["mb"] for x in g[k][b]]) /
                      st.median([x["mb"] for x in g[k][a]])) for k in shared),
                    key=lambda kv: abs(kv[1] - 1.0))
        print(f"  largest single-bin gap: x{worst[1]:.2f} at "
              f"{worst[0]*args.bin_mb:.0f}-{(worst[0]+1)*args.bin_mb:.0f} MB")
        print("  A large difference on the SAME disc follows the DRIVE, not the disc.")
        print("  A small difference means the disc is the limit, and the drive is fine.")
        print("  Check the 'solo %' column before believing either -- a drive that read")
        print("  alone is not comparable to one that read alongside three others.")


if __name__ == "__main__":
    main()
