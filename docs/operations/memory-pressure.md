# Why concurrent rips get killed, and how to stop it

On 2026-09-21 at 09:15:58 CDT, four rips in progress died at once -- Bella,
Thor Ragnarok, Spider-Man Homecoming (76.7 GB written) and I Robot. Nothing
crashed and no disc was at fault. `systemd-oomd` killed them, and it killed
all four with one decision.

This document is what was measured that morning, why the obvious diagnostic
says the opposite of the truth, and what to change.

Measured on `dvd-nas`: 8 CPUs, 15.0 GiB RAM, 4 GiB swap, systemd 259
(259.5-0ubuntu3.4).

## The trap: the kernel OOM killer never fired

The first thing anyone checks is the wrong thing:

```bash
grep oom_kill /proc/vmstat     # oom_kill 0
```

That counter is accurate and it is irrelevant. It counts only the **kernel**
OOM killer. `systemd-oomd` is a userspace daemon that kills cgroups based on
PSI and never touches it. On a box where oomd is doing the killing, the kernel
counter reads zero forever.

`dmesg` is worse than useless here, because on this machine it is not readable
without root:

```
dmesg: read kernel buffer failed: Operation not permitted
```

Piped into `grep ... | tail`, that produces empty output and exit status 0 --
indistinguishable from "checked, found nothing". **An unreadable log is not a
clean bill of health.** Same lesson as the empty hash in the publish skill: a
check that cannot run must say so rather than answer.

The diagnostic that actually works:

```bash
journalctl -u systemd-oomd --no-pager | grep Killed
```

which returned, for that morning:

```
Sep 21 09:15:58 dvd-nas systemd-oomd[581]: Killed
  /user.slice/user-1000.slice/user@1000.service/app.slice/ptyxis-spawn-0b869d19-….scope
  due to memory pressure for /user.slice/user-1000.slice/user@1000.service
  being 86.80% > 50.00% for > 20s with reclaim activity
```

## It was not running out of memory

The name "OOM" is misleading. oomd kills on **pressure**, not on exhaustion,
and the two came apart completely here:

| Signal | Value | What it means |
|---|---|---|
| `oom_kill` | 0 | kernel never had to kill anything |
| Swap used | 158 MB of 4 GiB | almost nothing was paged out |
| `Pgscan` | 132,586,751 | enormous, continuous reclaim |
| PSI `full avg10` | 98.61% | everything stalled waiting on reclaim |
| Killed cgroup usage | 2.6 GB | not remotely a memory hog |

**If processes had been ballooning, swap would have been full.** It was 4%
used. Nothing had allocated too much. What was happening is that five bulk
I/O streams were running at once on a 15 GiB box:

| Stream | Direction | Volume |
|---|---|---|
| Spider-Man Homecoming rip | write | 76.7 GB |
| Thor Ragnarok rip | write | 34.7 GB |
| Bella rip | write | 6.4 GB |
| I Robot rip | write | 0.3 GB |
| publish `rsync` | read + ssh | 55 GB |

All of that moves through the page cache. With `vm.dirty_ratio=20` on 15 GiB,
roughly 3 GB of dirty pages accumulate before writeback throttles hard, and
four writers keep refilling it. Every allocation then waits on reclaim, PSI
climbs, and after 20 seconds above the threshold oomd fires.

**The pressure was the machine doing its job.** Sustained bulk I/O is the
entire purpose of this box, and PSI cannot distinguish "thrashing to death"
from "streaming 170 GB through a 15 GiB cache". oomd acts on the number
regardless.

The confirming evidence came half an hour later: with the four rips dead and
the same 55 GB rsync still running, `full avg10` read **0.00**. One large
reader is nothing. Four writers plus a reader is a kill.

## Why all four died together

They were all started from **one Ptyxis terminal tab**. A tab is one cgroup:

```
/user.slice/user-1000.slice/user@1000.service/app.slice/ptyxis-spawn-….scope
```

oomd selects a *cgroup* and kills everything in it. One tab, one scope, one
decision, four dead rips. The four stopped writing within nine seconds of each
other (09:14:03 to 09:14:12) and oomd logged the kill at 09:15:57, matching its
recorded `Total: 1min 53s` of accumulated pressure -- they stalled first, then
were killed.

Splitting rips across tabs would have limited the blast radius to one rip. It
would not have prevented the kill.

## The load was cumulative, and the last thing to join tipped it

```
08:12:42  Spider-Man starts        1 rip              ran 37 min, fine
08:49:37  Thor starts              2 rips             ran 12 min, fine
09:01:19  Bella starts             3 rips
09:04:43  publish rsync starts     3 rips + publish   survived 11 min
09:12:24  I Robot starts           4 rips + publish
09:14:03  writes stall                                99 s later
09:15:57  oomd kills the scope
```

**Read this carefully before concluding "the publish did it".** The publish
ran for eleven minutes alongside three rips without a kill. What changed 99
seconds before the stall was the *fourth rip*. Four rips never ran without the
publish, so the evidence does not show that four alone would be safe, and it
does not show the publish alone was the trigger either.

What it does show is that the load is cumulative and no single component is
"the cause". That is why the fix is a limit on how much runs at once, rather
than a rule about which pair of activities must not overlap.

## Where the 50% threshold comes from

It is a distro default, not something this project set:

```
/usr/lib/systemd/system/user@.service.d/10-oomd-user-service-defaults.conf
  ManagedOOMMemoryPressure=kill
  ManagedOOMMemoryPressureLimit=50%

/etc/systemd/oomd.conf
  DefaultMemoryPressureDurationSec=20s
```

So: anything under `user@1000.service` -- which is every interactive process
this operator runs -- is killable once user-slice pressure holds above 50% for
20 seconds. Note that **system services are not subject to this**; only the
user session is. That asymmetry is the basis of the fix.

## How rips are actually launched

This matters, because it decides where a fix can go.

Rips are not commands an operator types. `gui_app.py` is a tkinter application
started from a terminal:

```
/usr/bin/python3 gui_app.py
```

It discovers the optical drives and starts a job per drive by itself. Every
`makemkvcon` is therefore a **subprocess of that one GUI process**, which is a
child of one terminal tab, which is one cgroup. That is the entire explanation
for four rips dying on a single oomd decision: there was only ever one cgroup
to kill.

The defaults in force, with **no `config.json` present** -- the application is
running entirely on the dataclass defaults in `config.py`:

| Setting | Default | Consequence |
|---|---|---|
| `max_concurrent_jobs` | `0` | no cap; one job per drive |
| optical drives | 4 (`sr0`-`sr3`) | so up to four concurrent rips |
| `cache_mb` | `1024` | MakeMKV read cache per invocation |

**`cache_mb` looks like the culprit and is not.** Four invocations at 1 GB of
read cache sounds like 4 GB of anonymous memory. It is not: the cache is
allocated lazily. Measured 2026-09-21, an `info` probe peaks at 25 MB RSS with
`--cache=128` and 32 MB with `--cache=1024` -- a 7 MB difference, not 900. The
oomd log settles it from the other direction: the killed cgroup held the GUI
*and* all four rips in **2.6 GB total**. Lowering `cache_mb` would reclaim
something during the save phase, but it is not what filled the machine.

What filled the machine was four concurrent write streams plus a publish read.
The lever that matters is therefore the **number of concurrent jobs**, not the
memory each one holds.

## The plan

Six changes, in the order they are worth doing. Change 3 is written; the
rest are settings or decisions still to make.

### 1. Cap concurrent jobs

The most direct fix, already in the config schema, needing no systemd and no
code change:

```json
// config.json at the repo root (git-ignored; see config.example.json)
{ "max_concurrent_jobs": 2 }
```

`0` means "one job per drive, no cap" -- with four drives that is four
simultaneous multi-GB write streams, which is what the machine could not
absorb alongside a publish transfer. `2` halves the concurrent write load and
still keeps two drives working.

This costs throughput: four drives ripping at once clear a stack of discs
faster than two do. That is the trade, and it is worth making, because a rip
killed at 76 GB has cost far more time than the parallelism ever saved.

**This knob was built for exactly this.** `jobs.py` describes it as a global
cap "for a machine whose disk cannot keep up with four drives at once", and
the gate in `JobManager._pump()` enforces it. The project anticipated the
failure; the setting was simply never turned on, because there is no
`config.json` to turn it on in.

**Not yet applied.** Creating that file changes how the application starts,
and that belongs to the operator rather than to this investigation.

### 2. Put the GUI in a scope oomd will not kill

Because every rip is a child of `gui_app.py`, exempting that one process
exempts all of them, with no per-rip plumbing:

```bash
systemd-run --user --scope -p ManagedOOMPreference=omit \
    --unit=media-backup -- /usr/bin/python3 gui_app.py
```

Verified on this machine (systemd 259): the scope's cgroup carries the
`user.oomd_omit=1` extended attribute, which is what oomd reads.

* `omit` -- never a candidate. Correct here: a rip is long, expensive to
  restart, and not the thing misbehaving.
* `avoid` -- deprioritised but still killable.

**This protects the rips by redirecting oomd, not by removing the pressure.**
Under real pressure it will pick some other cgroup in the user session --
plausibly the browser, the editor, or the desktop shell. That is the right
trade on this box, but it is a trade. Pair it with change 1 so the pressure is
less likely to arise at all.

Per-rip scopes would be finer-grained, and would stop one decision taking every
rip, but they need `command._prefix()` in `makemkv/command.py` to wrap each
invocation. Worth doing only if change 1 proves insufficient.

### 3. Refuse to start new work while pressure is high -- **implemented**

Changes 1 and 2 are settings. This one is code, and it is in the tree:

* `media_backup/pressure.py` reads `/proc/pressure/memory` and answers, in a
  sentence, whether new work should wait.
* `config.max_memory_pressure` is the limit as a percentage of `full avg60`.
  **It defaults to `0`, which is off** -- with no `config.json` on this
  machine, nothing is gated until one exists. 30 is the suggested value and
  is a judgement, not a measurement: no baseline of this box's pressure
  during ordinary ripping has been taken. `config.validate` warns if it is
  set at or above 50, because oomd has already decided by then.
* `JobManager._pump()` consults it before starting a job, and
  `_hold_queue()` puts the reason in the disc's state detail so a held job
  says why rather than sitting there looking stuck.

Two properties worth keeping if this is ever rewritten:

**It gates starting, never running.** A job writing a 76 GB title is the
expensive thing to lose; the cheap thing to postpone is the one that has not
begun. Pressure never cancels work in flight.

**Unreadable PSI means go ahead.** This is the opposite of the rule for the
checksum in the publish skill, where a check that cannot run must block. The
asymmetry is deliberate: there, proceeding costs the only copy of a film;
here, blocking would stop every rip forever on any kernel without PSI. A
safety feature that can silently halt all work is worse than the hazard.

A held queue re-checks itself after one `avg60` window, because nothing else
calls `_pump()` when the pressure comes from something that is not a job --
a publish transfer, for instance.

### 4. Do not start a publish transfer into a loaded box

The publish rsync on 2026-09-21 started at 09:04:43. oomd fired at 09:15:58,
eleven minutes later. It was not the sole cause -- four concurrent rips were
the bulk of it -- but it was the addition that pushed a loaded box over, and it
was the one process that was optional at that moment.

Before starting a transfer, check what is running:

```bash
pgrep -x makemkvcon | wc -l             # rips in flight
cat /proc/pressure/memory               # 'full avg60' well under 50 is safe
```

**Use `-x`, not `-f`.** `pgrep -f makemkvcon` searches whole command lines,
and the shell running the check has the pattern in its own command line, so it
matches itself and reports a rip that is not there. Measured 2026-09-21: `-f`
returned 1 on a box with no rip running at all. `-x` matches the process name
exactly and cannot self-match. This is the same trap the publish skill records
for `pkill -f`, which took out two shells with exit 144 -- and it caught this
document's first draft too.

Two or more active rips, or `full avg60` already above ~20, means wait. The
archive is not going anywhere.

When a transfer must run alongside rips, cap it so it cannot dominate
writeback:

```bash
rsync --bwlimit=20M …      # rsync 3.4.1, supported
```

The 2026-09-21 transfer sustained **22.5 MB/s**, measured over its first 22
minutes (30.4 GB). A 20 MB/s cap therefore costs almost nothing on a quiet box.
On a busy one it is the difference between throttling this transfer deliberately
and having oomd throttle it for you. Where the ceiling actually comes from --
the link, the exFAT volume, or ssh -- was not established and is worth knowing
before tuning the number.

### 5. Make writeback start earlier

Defaults on this box allow a large dirty backlog to build before throttling:

```
vm.dirty_ratio = 20            # ~3.0 GB on 15 GiB
vm.dirty_background_ratio = 10 # ~1.5 GB
```

For a machine whose steady state is several concurrent multi-GB writes, lower
values keep the backlog small and the reclaim stalls short:

```
# /etc/sysctl.d/99-media-backup.conf
vm.dirty_ratio = 10
vm.dirty_background_ratio = 5
```

This reduces the height of the pressure spikes. It does not by itself prevent
a kill, which is why it is fifth rather than first. **Not yet applied** --
it changes global behaviour and should be a deliberate decision.

`nocache` (which would drop the page cache behind a bulk copy via
`posix_fadvise(DONTNEED)`) is **not installed**; `apt install nocache` if the
sysctl change proves insufficient.

### 6. Consider turning oomd off on this box

Defensible, and worth stating plainly rather than leaving implicit. This is a
ripper and a NAS. Its designed workload is sustained bulk I/O, which is
precisely the pattern oomd misreads as memory exhaustion. It has now cost four
rips and roughly 118 GB of re-reading.

```bash
sudo systemctl disable --now systemd-oomd
```

The cost: nothing then arrests a genuine runaway before the kernel OOM killer
does, and the kernel killer picks its victim by badness score rather than by
cgroup -- it could choose a rip anyway, or the desktop session.

**Prefer changes 1 and 2 together.** Change 1 reduces the pressure; change 2
stops oomd choosing the rips when pressure happens anyway. Between them they
address the cause and the symptom, and leave the safety net in place for
everything else. Reach for this option only if both prove impractical.

## After a kill: the archive lies

This is the part that bites later. A killed rip leaves its collection reading
`state=copying` with no process behind it. The four from 2026-09-21:

| Collection | State | Files | Last write |
|---|---|---|---|
| Bella | `copying` | 8 | 09:14:12 |
| Thor Ragnarok | `copying` | 2 | 09:14:03 |
| Spider-Man Homecoming | `copying` | 18 | 09:14:09 |
| I Robot | `copying` | 1 | 09:14:04 |

`copying` means "a rip is in progress". Nothing was in progress. The state is
not wrong at the moment it was written -- it is stale, because the process that
would have advanced it was killed between writes.

**Detect it by checking state against reality, not by reading state**:

```bash
pgrep -f makemkvcon >/dev/null || echo "no rip running; any 'copying' is stale"
```

A collection in `copying` whose newest file has not changed in minutes, with no
`makemkvcon` alive, is stranded. Its partial files are real bytes and may be
worth keeping, but the collection cannot finish and will never reach
`finished/`, so the publish step will never see it.

Do not delete these on sight, and do not publish them: a partial rip has the
same shape as a complete one. Resolve them deliberately -- that is a separate
decision from the one this document covers.

## Diagnosing the next one

In order, and the first two are the ones that get skipped:

```bash
journalctl -u systemd-oomd --no-pager | grep Killed   # did oomd kill something
grep oom_kill /proc/vmstat                            # did the KERNEL kill something
cat /proc/pressure/memory                             # is it still under pressure
free -m                                               # swap used? if not, not exhaustion
pgrep -a -f makemkvcon                                # what is actually running
```

If oomd killed something and swap is near-empty, it is this document's failure,
not a memory leak. Look for concurrent bulk I/O, not for a process that grew.
