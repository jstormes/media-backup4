---
name: drive-vibration-watch
description: Sample per-drive optical read throughput, latency and errors against how many drives are spinning, to test whether concurrent drives degrade each other through chassis vibration. Use when asked to monitor drive performance, investigate slow or erroring rips, test the vibration theory, or decide how many drives to run at once.
---

# Does running drives together make them worse?

The theory under test: **four optical drives bolted to one chassis vibrate each
other, and a drive fighting vibration reads slower, retries more, and
eventually throws read errors.** Optical is a plausible place for this to bite.
A Blu-ray track pitch is 320 nm and the objective lens rides on a voice-coil
servo; an HDD alongside it has orders of magnitude more margin. Rotational
vibration is a measured, documented effect in disk arrays, and nobody has
characterised it for a stack of BD-RW drives in a desktop case.

This skill collects the evidence. It does not assume the theory is true, and
§"Rival explanations" exists because two other mechanisms produce the same
first-order symptom.

## What would count as evidence

The symptom to look for is **not** "throughput is low". It is:

| Signal | Where | Vibration predicts |
|---|---|---|
| `r_await` — mean ms per read | `/proc/diskstats` field 4 ÷ field 1 | **rises** with neighbours spinning |
| read throughput | field 3 (sectors) × 512 | **falls** |
| `%util` | field 10 | stays pinned near 100 either way |
| `MSG:2003` read errors | the attempt log / `message_codes` | **rises**, in the worst cases |

A drive that is retrying is still "busy", so `%util` is useless on its own --
it reads ~100% whether the drive is streaming cleanly or grinding. The pair
that matters is **latency up while throughput is down**.

## The one comparison that is not confounded

**Do not compare one rip against another.** Four things differ between any two
rips and every one of them moves throughput more than vibration plausibly does:

1. **Radius.** Optical drives run CAV, so linear read speed climbs steadily
   from the hub to the rim. A rip is ~2× faster at its end than its start.
   Comparing "a drive that started alone" with "a drive that started in a
   group" compares two different radii.
2. **Media.** BD-50 dual-layer, BD-25 and DVD stream at completely different
   rates, and a layer change stalls outright.
3. **Drive model.** This machine has a Pioneer BDR-212D ×2, a PLDS DH-16AES
   and an Optiarc AD-7190S. They are not comparable to each other.
4. **Disc condition.** A dirty disc is slow with nothing else running.

What controls all four at once is a **transition within a single rip**: the
moment a *neighbour* starts or stops while this drive keeps reading the same
disc at almost the same radius. Compare the 60 s before against the 60 s
after. Same drive, same disc, same radius, one variable changed.

`analyse.py` is built around those transitions. The concurrency-bin table it
also prints is descriptive only -- read it for orientation, never as the
result.

## Running it

```bash
# Sample every 5s into a CSV. Safe to leave running for days; ~6 MB/day.
python3 .claude/skills/drive-vibration-watch/sample.py \
        --out /srv/media-backup/forensics/vibration.csv --interval 5

# Analyse whenever. Reads the CSV; does not need the sampler stopped.
python3 .claude/skills/drive-vibration-watch/analyse.py \
        /srv/media-backup/forensics/vibration.csv
```

The sampler reads `/proc/diskstats` and the open collections' `collection.json`
to learn which disc is in which drive. It touches no drive, issues no SCSI
command, and costs nothing measurable -- **it must not perturb what it
measures**, so never add a probe that opens a device.

## Rival explanations, which must be ruled out before claiming vibration

**1. Bus or controller contention.** All four drives hang off one PCIe SATA
controller (`pci-0000:01:00.0`, ports ata-1/2/4/5). If the controller or its
link were the limit, per-drive throughput would fall when neighbours start --
exactly what vibration predicts.

*How to tell them apart:* contention **caps the sum**; vibration **degrades
each drive**. If total throughput across all active drives sits at a ceiling
while each drive's share shrinks, that is the bus. If the total also falls and
per-drive `r_await` climbs, that is not the bus -- nothing about a queue makes
an individual read take longer when the aggregate is well under capacity.
`analyse.py` prints total throughput per concurrency level for this reason.
Four drives at ~25 MB/s is ~100 MB/s against a SATA link an order of magnitude
faster, so contention is unlikely here, but "unlikely" is not "excluded".

**2. The disc, not the neighbours.** A marginal disc produces high `r_await`
and low throughput from the moment it spins up, with no relationship to what
else is running. Observed 2026-09-23: sr3 read at 5.7 MB/s with 44 ms
`r_await` while sr1, same model, read at 23 MB/s with 10.7 ms. That is a
four-fold latency difference between identical drives in the same chassis at
the same instant, which vibration cannot explain -- if the case were shaking,
it would shake both.

A per-disc baseline is therefore mandatory. `analyse.py` reports each drive's
figures **per disc label**, never pooled.

## The controlled experiment, when passive data is not enough

Transitions occur only where the operator happens to start and stop jobs, and
they are never randomised. If the passive result is suggestive but weak, run
this deliberately -- it is the version that can actually settle it:

1. Pick one disc and one drive. Rip it **alone**, sampler running. Note the
   throughput/latency curve against elapsed time.
2. Re-rip the **same disc in the same drive** with the other three drives
   loaded and ripping. Same curve.
3. Compare at equal elapsed time, which means equal radius.

A real effect shows as a curve that is depressed along its whole length, not
at one point. Repeat with the neighbours *spinning but idle* (discs loaded, no
rip running) to separate **vibration from spin** from **vibration from seek** --
they are different mechanisms and the fix for each is different.

**If the effect is real, the cheap confirmation is physical:** rip the same
disc again with the case on its side, or with the drive cage decoupled by
foam. A result that moves when the mounting changes and not when the software
changes is vibration, and nothing else is.

## Reporting

Say which comparison the number came from. "sr1 is slower with neighbours
running" is worthless without stating whether it came from a within-rip
transition or a pooled average, because the pooled average is confounded by
radius and the transition is not. State the sample count -- a conclusion drawn
from three transitions is a hypothesis, not a finding.

Record anything conclusive in the project memory, and in
`docs/operations/` alongside `memory-pressure.md`, which is the precedent for
writing up a measured machine-level constraint.
