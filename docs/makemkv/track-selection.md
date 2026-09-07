# Which tracks end up in the file

Measured 2026-09-07 against MakeMKV 1.18.3, a Blu-ray (Spider-Man: Across The
Spider-Verse) and a DVD (Fresh Horses). Every claim here was run, not read —
see "How this was got wrong twice" at the bottom, which is why.

## The short version

`makemkvcon` keeps **every track the disc carries**: all audio, all subtitles
in every language, and a DVD's closed captions where it has them. Nothing in
this project changes that, and there is no per-run way to change it.

## There is no command-line lever

`makemkvcon --profile=<name or file>` is **accepted and ignored.**

| tried on the same DVD title | audio | subtitles |
|---|---|---|
| no `--profile` | ac3 | VobSub + CC |
| `--profile=<file selecting video only>` | ac3 | VobSub + CC |
| `--profile=<a path that does not exist>` | ac3 | VobSub + CC |
| `--profile=nosuchprofile` | ac3 | VobSub + CC |
| `--profile=` MakeMKV's own **flac** profile | **ac3** | VobSub + CC |

The last row is the one that settles it: MakeMKV's own shipped FLAC profile
left the audio as AC3. Profiles are a GUI concept. An unknown profile name is
not an error either — it succeeds silently, so a typo would never be noticed.

## The lever that does work

`app_DefaultSelectionString` in **`~/.MakeMKV/settings.conf`**:

```
app_DefaultSelectionString = "-sel:all,+sel:video"
```

Verified: with that line the same title came out with a video track and
nothing else, against `video, audio, subtitle, subtitle` without it.

Two consequences worth being clear about:

* It is **global to the user**, not per-run. Setting it changes what the
  MakeMKV GUI does too.
* It is therefore an ambient setting outside this repository that affects what
  an archive contains. On this machine `settings.conf` holds nothing but the
  registration key, so the engine's own defaults apply — which is what is
  wanted, but it is not *pinned*.

A run can be given its own by pointing `HOME` at a directory holding a private
`.MakeMKV/settings.conf` (proved to work, copying `app_Key` across so Blu-ray
decryption still functions). That is not implemented: the defaults already
keep everything, and a shadow MakeMKV home is a lot of machinery for
determinism alone.

## The selection string

MakeMKV's shipped default, from `default.mmcp.xml` inside
`/usr/local/share/MakeMKV/appdata.tar`:

```
-sel:all,+sel:(favlang|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo,=100:all,-10:favlang
```

Read left to right, each clause adjusting the set:

| clause | effect |
|---|---|
| `-sel:all` | start with nothing selected |
| `+sel:(favlang\|nolang\|single)` | add tracks in the preferred language, tracks with no language, tracks that are the only one of their kind |
| `-sel:(havemulti\|havecore)` | drop a track when a better version exists (the stereo downmix, the DTS core under a DTS-HD MA) |
| `-sel:mvcvideo` | drop the 3D MVC video stream |
| `=100:all`, `-10:favlang` | ratings — ordering and the default-track flag, not what is included |

To inspect the shipped profiles:

```bash
mkdir -p /tmp/mk && cd /tmp/mk && tar xf /usr/local/share/MakeMKV/appdata.tar
grep -o 'app_DefaultSelectionString="[^"]*"' default.mmcp.xml
```

## What is captured

**Subtitles are tracks inside the .mkv, never separate files.** A Blu-ray's
PGS bitmaps are muxed in as `S_HDMV/PGS` (`hdmv_pgs_subtitle` to ffprobe); a
DVD's VobSub as `S_VOBSUB`. No `.sup` or `.idx`/`.sub` is written alongside.

**All languages are kept.** The same Blu-ray title extracted three ways —
MakeMKV's default, an explicit `+sel:all`, and the default with
`app_PreferredLanguage="eng"` — gave the identical track list every time,
English *and* Spanish PGS included.

**DVD closed captions are captured.** MakeMKV finds the EIA-608 captions
embedded in the MPEG-2 video, converts them to text, and offers them as
`S_CC608/DVD` — "CC→Text English ( Lossy conversion )". In the output that is
a `subrip` track. "Lossy" is MakeMKV's own word and is accurate: the words
survive, the 608 positioning, colour and roll-up styling do not.

**Blu-rays generally have no closed captions to capture.** They carry PGS
subtitle bitmaps rather than line-21 captions; the SDH PGS track is the
equivalent. The Spider-Man feature reports 22 streams — one video, seven
audio, fourteen PGS — and no caption stream at all.

**Derived "forced only" tracks are not captured, and that loses nothing.**
MakeMKV synthesises a forced-subtitles-only variant of each subtitle track
(attribute 22 flags `6144` = `ForcedSubtitles` + `DerivedStream`). It is a
filtered copy of a track already being kept, it is not on the disc, and it can
be regenerated at any time.

## How this was got wrong twice

Both mistakes are recorded because the shape of them is worth remembering.

**First:** the default selection string was *read* and concluded to drop
non-preferred languages. It does not. Reading a rule is not running it.

**Second, worse:** `--profile` was declared to work on the strength of
comparing a short extra (which has no subtitles) against the main feature
(which does). Two different titles. The profile had changed nothing; the
titles differed. A config knob, a profile writer and a page of documentation
were built on that before the comparison was repeated properly on one title.

The check that catches both: **change one variable, and only one.**
