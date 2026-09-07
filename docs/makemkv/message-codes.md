# MakeMKV Message Codes

Robot mode emits `MSG:code,flags,count,"text","format",params...`. The text is
localised; **the code is not**. Switch on the code.

`robot-mode.md` lists only the handful of codes that happened to appear during
its capture session, and states that no completion message or read-error code
is known. That is not correct — the full table ships with the product.

## Where the table comes from

MakeMKV ships its message catalogue as gettext `.mo` files inside
`/usr/local/share/MakeMKV/appdata.tar`, one per language. **The msgid is the
message code**, zero-padded to six digits and colon-prefixed (`:005081`); the
msgstr is the translation. There is no English catalogue, because English is
the source language compiled into `makemkvcon` itself.

To decode any code:

```bash
mkdir -p /tmp/mk && cd /tmp/mk
tar xf /usr/local/share/MakeMKV/appdata.tar && gunzip -f makemkv_deu.mo.gz
python3 -c "
import gettext
with open('makemkv_deu.mo','rb') as f: cat = gettext.GNUTranslations(f)._catalog
print(cat[':005081'])"
# -> Backup beendet.
```

English text for a code is recovered by finding its counterpart in the binary:

```bash
strings -n 8 /usr/local/bin/makemkvcon | grep -i 'backup done'
```

Every English string in the table below was confirmed this way, and every code
was confirmed present in the German catalogue. Verified against MakeMKV 1.18.3.

## Codes this project acts on

### Terminal outcome of a `backup` run

| Code | English | Meaning |
|---|---|---|
| `5081` | `Backup done.` | **Success.** The terminal success signal. |
| `5080` | `Backup failed.` | **Failure.** |
| `5082` | `Backup done but %1 files failed hash check.` | **Partial** — copy finished, N files are corrupt. |
| `2201` | `Operation was cancelled` | We terminated it, or the engine aborted. |

`5069` / `5070` / `5079` are the same three messages without the trailing full
stop. Those are the `PRGT`/`PRGC` progress-title forms; the `MSG` forms are
5080/5081/5082. Match on the code, and do not be surprised by the near-duplicate.

### Failure causes

| Code | English | Meaning |
|---|---|---|
| `2003` | `Error '%1' occurred while reading '%2' at offset '%3'` | **A read error — the dirty/damaged disc signal.** Emitted once per failure, so a bad disc produces a storm. Count them. |
| `2004` | Error transferring SCSI command to drive | Drive-level I/O failure. |
| `5010` | `Failed to open disc` | Context-dependent — see below. |
| `5042` | `The program can't find any usable optical drives.` | No drives. |
| `5068` | `Folder %1 already contains a backup, please choose another folder` | **The destination was not empty.** See below. |
| `2016` | `Failed to get full access to drive "%1 %2". Make sure that you either have write access to device "%3", are member of "cdrom" group or have CAP_SYS_RAWIO enabled.` | Environment/permissions, not the disc. |
| `5076` | `Hash check failed for file %1 at offset %2, file is corrupt.` | One corrupt file. |
| `5077` | `Too many hash check errors in file %1.` | Gave up on a file. |
| `2200` / `2301` / `2302` / `2303` / `2304` | Unspecified / Posix / SCSI / SCSI:sub / Internal error | Generic engine errors. |
| `6050` / `6051` | Fatal error, program will exit / out of memory | Fatal. |

### Benign — never treat as failure

| Code | English |
|---|---|
| `1005` | `%1 started` |
| `3007` | `Using direct disc access mode` (LibreDrive) |
| `3025` | `Title #%1 has length of %2 seconds which is less than minimum title length of %3 seconds and was therefore skipped` |
| `5018` | `Scanning CD-ROM devices` |
| `5083` | `Content hash table missing, unable to verify integrity of M2TS files.` |
| `5085` | `Loaded content hash table, will verify integrity of M2TS files.` |
| `5074` | Update-check-enabled notice |

## Two codes with sharp edges

**`5010` is not always an error.** The drive-enumeration idiom
(`info disc:9999`) always ends with `MSG:5010` and exit 0, because disc 9999
genuinely cannot be opened. Judge 5010 per invocation: expected from
enumeration, fatal from a backup.

**`5068` makes the `rejected/` move mandatory.** `makemkvcon` refuses to back
up into a directory that already contains a backup. Moving a failed partial
aside is therefore a *precondition* for retry, not merely a convenience for
inspection. The destination must be empty or absent immediately before spawn.

## Related settings

`~/.MakeMKV/settings.conf` recognises `io_ErrorRetryCount` and
`io_IgnoreReadErrors`. The retry count governs how long a damaged disc grinds
before the engine gives up, which is the main input to sizing any stall
timeout. Neither is set by default; do not change them silently.

## Full code inventory

Every code present in at least one shipped catalogue (584 total, union across
14 languages; the German catalogue alone has 412). A code appearing here is
real even if this project has no opinion about it — decode it with the recipe
above rather than assuming it is an error.

**1000s** (8 codes)

```
 1000  1004  1005  1007  1008  1009  1010  1017
```

**2000s** (43 codes)

```
 2001  2003  2004  2006  2007  2008  2009  2010  2011  2012  2013  2014
 2015  2016  2017  2018  2019  2020  2021  2022  2023  2024  2200  2201
 2202  2203  2204  2205  2206  2207  2208  2209  2210  2211  2300  2301
 2302  2303  2304  2400  2401  2402  2404
```

**3000s** (118 codes)

```
 3000  3001  3002  3003  3004  3005  3006  3007  3008  3009  3010  3011
 3012  3013  3014  3015  3016  3017  3018  3019  3020  3021  3022  3023
 3024  3025  3026  3027  3028  3029  3030  3031  3032  3033  3034  3035
 3036  3037  3038  3039  3040  3041  3042  3043  3100  3101  3102  3103
 3104  3105  3106  3107  3108  3109  3110  3111  3120  3200  3201  3202
 3210  3220  3221  3300  3301  3302  3303  3304  3305  3306  3307  3308
 3309  3310  3311  3312  3313  3314  3315  3316  3317  3318  3319  3320
 3321  3322  3323  3324  3325  3326  3327  3328  3329  3330  3331  3332
 3333  3334  3335  3336  3337  3338  3339  3340  3341  3342  3343  3344
 3345  3346  3347  3400  3401  3402  3404  3405  3406  3407
```

**4000s** (34 codes)

```
 4001  4002  4003  4004  4007  4008  4009  4020  4021  4022  4023  4024
 4025  4026  4027  4028  4040  4041  4042  4043  4044  4045  4046  4047
 4048  4049  4050  4051  4052  4053  4055  4060  4061  4062
```

**5000s** (135 codes)

```
 5000  5001  5002  5003  5004  5005  5006  5007  5008  5009  5010  5011
 5012  5013  5014  5015  5016  5017  5018  5019  5020  5021  5022  5024
 5025  5026  5027  5028  5029  5030  5031  5033  5036  5037  5038  5039
 5040  5041  5042  5043  5044  5045  5046  5047  5048  5049  5050  5051
 5052  5053  5054  5055  5056  5057  5058  5060  5061  5062  5063  5064
 5065  5066  5067  5068  5069  5070  5071  5072  5073  5074  5075  5076
 5077  5078  5079  5080  5081  5082  5083  5084  5085  5086  5087  5088
 5089  5090  5091  5092  5093  5094  5095  5100  5101  5201  5202  5203
 5204  5207  5208  5209  5210  5214  5215  5216  5217  5218  5219  5220
 5221  5222  5223  5224  5225  5226  5227  5228  5233  5234  5235  5238
 5239  5240  5248  5249  5250  5301  5302  5303  5304  5305  5306  5307
 5308  5380  5381
```

**6000s** (215 codes)

```
 6001  6002  6003  6005  6006  6007  6008  6010  6011  6012  6013  6014
 6015  6016  6017  6018  6019  6020  6021  6022  6023  6024  6026  6030
 6031  6032  6034  6035  6036  6037  6038  6039  6040  6041  6042  6043
 6044  6045  6046  6047  6048  6050  6051  6052  6053  6054  6055  6056
 6057  6058  6059  6060  6061  6062  6063  6064  6065  6066  6067  6068
 6069  6070  6071  6072  6073  6074  6075  6076  6077  6078  6079  6080
 6081  6082  6083  6084  6085  6086  6087  6088  6089  6090  6091  6092
 6093  6094  6095  6096  6097  6098  6099  6100  6101  6102  6103  6104
 6105  6106  6107  6108  6109  6110  6111  6112  6119  6120  6121  6122
 6123  6124  6125  6126  6127  6128  6129  6130  6131  6132  6133  6134
 6135  6136  6137  6138  6139  6142  6143  6144  6145  6146  6147  6148
 6150  6151  6152  6153  6154  6156  6157  6158  6159  6164  6165  6166
 6167  6168  6169  6170  6171  6172  6173  6174  6175  6177  6178  6179
 6180  6181  6182  6183  6184  6187  6188  6189  6190  6191  6192  6200
 6201  6202  6203  6204  6205  6206  6207  6209  6210  6211  6212  6213
 6214  6215  6216  6220  6221  6222  6223  6224  6225  6226  6227  6228
 6229  6300  6301  6302  6303  6304  6305  6306  6307  6308  6309  6320
 6321  6322  6323  6324  6325  6326  6327  6329  6330  6331  6332
```

**8000s** (31 codes)

```
 8000  8001  8002  8003  8004  8005  8006  8007  8008  8009  8010  8011
 8012  8013  8014  8015  8096  8097  8098  8099  8100  8101  8102  8103
 8104  8105  8106  8107  8108  8109  8110
```

