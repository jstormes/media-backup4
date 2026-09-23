#!/usr/bin/python3
"""Analyse the vibration sampler's CSV.

The headline result is the TRANSITION table: what happened to a drive that
kept reading the same disc while a *neighbour* started or stopped. Same drive,
same disc, near-enough the same radius -- one variable changed.

The concurrency table above it is descriptive only. It pools different radii
and different discs, so a difference there is not evidence of anything; it is
printed for orientation and because its absence would be suspicious.
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict
from datetime import datetime


def median(xs):
    return st.median(xs) if xs else float("nan")


def load(path):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                r["t"] = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S").timestamp()
                r["bytes_per_s"] = float(r["bytes_per_s"])
                r["r_await_ms"] = float(r["r_await_ms"])
                r["util_pct"] = float(r["util_pct"])
                r["other_active"] = int(r["other_active"])
                r["active_drives"] = int(r["active_drives"])
                r["elapsed"] = float(r.get("read_secs") or r.get("job_elapsed_s") or 0) or None
                r["disc_bytes"] = float(r.get("disc_bytes") or 0)
            except (ValueError, KeyError):
                continue
            rows.append(r)
    return rows


def busy(rows):
    return [r for r in rows if r["bytes_per_s"] > 64 * 1024]


def concurrency_table(rows):
    print("=== descriptive: per drive and disc, binned by neighbours active ===")
    print("    (confounded by radius and disc -- orientation only, not a result)")
    print(f"{'dev':5} {'disc':22} {'nbrs':>4} {'n':>5} {'MB/s':>7} {'await ms':>9} {'util':>6}")
    g = defaultdict(list)
    for r in busy(rows):
        g[(r["dev"], r["label"], r["other_active"])].append(r)
    for (dev, label, nb), rs in sorted(g.items()):
        print(f"{dev:5} {label[:22]:22} {nb:>4} {len(rs):>5} "
              f"{median([x['bytes_per_s'] for x in rs])/1e6:7.2f} "
              f"{median([x['r_await_ms'] for x in rs]):9.2f} "
              f"{median([x['util_pct'] for x in rs]):6.1f}")


def aggregate_table(rows):
    print("\n=== bus check: does the TOTAL cap, or does each drive degrade? ===")
    print("    contention caps the sum; vibration lowers the sum and raises latency")
    print(f"{'drives active':>13} {'n':>6} {'total MB/s':>11} {'per-drive MB/s':>15} {'await ms':>9}")
    byt = defaultdict(list)
    for r in busy(rows):
        byt[(r["ts"], r["active_drives"])].append(r)
    g = defaultdict(list)
    for (ts, act), rs in byt.items():
        g[act].append((sum(x["bytes_per_s"] for x in rs),
                       median([x["r_await_ms"] for x in rs])))
    for act in sorted(g):
        tot = [x[0] for x in g[act]]
        aw = [x[1] for x in g[act]]
        print(f"{act:>13} {len(tot):>6} {median(tot)/1e6:11.2f} "
              f"{median(tot)/1e6/max(act,1):15.2f} {median(aw):9.2f}")


def transitions(rows, window=60.0, guard=10.0):
    """A drive reading continuously while the neighbour count changed."""
    per = defaultdict(list)
    for r in rows:
        per[r["dev"]].append(r)
    for v in per.values():
        v.sort(key=lambda r: r["t"])

    found = []
    for dev, rs in per.items():
        for i in range(1, len(rs)):
            a, b = rs[i - 1], rs[i]
            if a["other_active"] == b["other_active"]:
                continue
            if not (a["bytes_per_s"] > 64 * 1024 and b["bytes_per_s"] > 64 * 1024):
                continue                      # this drive must not itself start/stop
            if a["label"] != b["label"] or not a["label"]:
                continue                      # same disc only
            if a["elapsed"] is None or b["elapsed"] is None:
                continue
            before = [r for r in rs
                      if b["t"] - window <= r["t"] <= b["t"] - guard
                      and r["other_active"] == a["other_active"]
                      and r["label"] == a["label"] and r["bytes_per_s"] > 64 * 1024]
            after = [r for r in rs
                     if b["t"] + guard <= r["t"] <= b["t"] + window
                     and r["other_active"] == b["other_active"]
                     and r["label"] == b["label"] and r["bytes_per_s"] > 64 * 1024]
            if len(before) < 3 or len(after) < 3:
                continue
            found.append({
                "dev": dev, "label": a["label"], "ts": b["ts"],
                "from": a["other_active"], "to": b["other_active"],
                "mb_before": median([r["bytes_per_s"] for r in before]) / 1e6,
                "mb_after": median([r["bytes_per_s"] for r in after]) / 1e6,
                "aw_before": median([r["r_await_ms"] for r in before]),
                "aw_after": median([r["r_await_ms"] for r in after]),
                "elapsed_min": b["elapsed"] / 60.0,
            })
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--window", type=float, default=60.0)
    args = ap.parse_args()

    rows = load(args.csv)
    if not rows:
        raise SystemExit("no usable rows")
    span = (rows[-1]["t"] - rows[0]["t"]) / 3600.0
    print(f"{len(rows)} samples over {span:.1f} h, "
          f"{len(set(r['dev'] for r in rows))} drives\n")

    concurrency_table(rows)
    aggregate_table(rows)

    tr = transitions(rows, window=args.window)
    print(f"\n=== RESULT: within-rip transitions (same drive, same disc) ===")
    if not tr:
        print("  none yet. A transition needs a neighbour to start or stop while this")
        print("  drive keeps reading the same disc, with >=3 clean samples either side.")
        print("  Stagger job starts, or run the controlled experiment in SKILL.md.")
        return
    print(f"{'dev':5} {'disc':18} {'when':9} {'nbrs':>7} {'MB/s':>14} {'d%':>7} "
          f"{'await ms':>16} {'d%':>7} {'radius':>7}")
    ups = downs = 0
    for x in sorted(tr, key=lambda y: y["ts"]):
        dmb = (x["mb_after"] - x["mb_before"]) / x["mb_before"] * 100 if x["mb_before"] else 0
        daw = (x["aw_after"] - x["aw_before"]) / x["aw_before"] * 100 if x["aw_before"] else 0
        worse = x["to"] > x["from"]
        if worse:
            (downs := downs + 1) if dmb < 0 else (ups := ups + 1)
        print(f"{x['dev']:5} {x['label'][:18]:18} {x['ts'][11:]:9} "
              f"{x['from']}->{x['to']:<4} "
              f"{x['mb_before']:6.2f}->{x['mb_after']:6.2f} {dmb:+6.1f}% "
              f"{x['aw_before']:7.2f}->{x['aw_after']:7.2f} {daw:+6.1f}% "
              f"{x['elapsed_min']:6.1f}m")
    # --- direction symmetry: the check that separates a concurrency effect
    # from a time trend. A real neighbour effect must REVERSE sign when the
    # neighbour leaves. A drive that degrades whether neighbours arrive or
    # depart is simply getting slower with time, and pooling only the
    # "started" transitions would report that as a vibration effect.
    print("\n=== direction symmetry (per drive) ===")
    print("    concurrency effect -> opposite signs. time trend -> same sign both ways.")
    print(f"{'dev':5} {'started n':>9} {'med d%':>8} {'stopped n':>10} {'med d%':>8}  verdict")
    bydev = defaultdict(lambda: {"add": [], "rem": []})
    for x in tr:
        d = (x["mb_after"] - x["mb_before"]) / x["mb_before"] * 100 if x["mb_before"] else 0
        bydev[x["dev"]]["add" if x["to"] > x["from"] else "rem"].append(d)
    for dev, g in sorted(bydev.items()):
        a, r_ = g["add"], g["rem"]
        ma, mr = (median(a) if a else float("nan")), (median(r_) if r_ else float("nan"))
        if a and r_:
            verdict = ("TIME TREND - same sign both ways" if (ma < 0) == (mr < 0)
                       else "consistent with a concurrency effect")
        else:
            verdict = "need transitions in both directions"
        print(f"{dev:5} {len(a):>9} {ma:>8.1f} {len(r_):>10} {mr:>8.1f}  {verdict}")

    adds = [x for x in tr if x["to"] > x["from"]]
    if adds:
        dmb = median([(x["mb_after"] - x["mb_before"]) / x["mb_before"] * 100
                      for x in adds if x["mb_before"]])
        daw = median([(x["aw_after"] - x["aw_before"]) / x["aw_before"] * 100
                      for x in adds if x["aw_before"]])
        print(f"\n  {len(adds)} transitions where a neighbour STARTED:")
        print(f"    median throughput change {dmb:+.1f}%   median latency change {daw:+.1f}%")
        print("    vibration predicts throughput DOWN and latency UP together.")
        if len(adds) < 10:
            print(f"    NOTE: {len(adds)} samples is a hypothesis, not a finding.")


if __name__ == "__main__":
    main()
