auto-fixer and validator for SRT subtitle files. enforces Netflix Timed Text Style Guide — splits long blocks, balances lines, fixes timing, and writes compliant SRT back in-place. research-backed syllable-weighted timing, not naive character splitting.

```bash
./subtitle-linter episode.srt
```

[![python](https://img.shields.io/badge/python-3-93450a.svg?style=flat-square)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-grey.svg?style=flat-square)](https://opensource.org/licenses/MIT)

---

## what it does

two independent tools:

**`subtitle-linter`** — the auto-fixer. reads an SRT file, runs a multi-phase transformation pipeline, writes a corrected SRT back. not a linter that just warns — it actually rewrites your subtitles.

**`validator.py`** — read-only auditor. scans SRT files against Netflix rules, produces a markdown (and optionally JSON) report. doesn't touch your files.

### the fixer pipeline

1. **parse** — reads SRT with UTF-8/BOM support, drops single-character artifacts (`.`, `-`, `?`)
2. **split long blocks** — anything over 84 chars or 8 seconds gets split. prefers splitting at punctuation and clause boundaries. time distributed using 70% syllable / 30% character hybrid ratio, adjusted for punctuation pauses and hesitation markers
3. **balance lines** — each subtitle re-wrapped to max 42 chars/line. dynamic split ratio (50/50 for short text, 40/60 top-heavy for longer text) based on eye-tracking research. scoring system penalizes bad splits, rewards punctuation boundaries
4. **extend short durations** — subtitles under 833ms get extended without overlapping neighbors
5. **renumber** — sequential indices, gaps filled

### the validator checks

| rule | severity | what it catches |
|:---|:---|:---|
| line length | error | lines exceeding 42 characters |
| line count | error | blocks with more than 2 lines |
| min/max duration | warning | subtitles shorter than 833ms or longer than 7s |
| reading speed | warning | exceeds 17 CPS (adult) or 15 CPS (children) |
| line balance | warning | shorter line is less than 25% of longer line |
| double spaces | warning | consecutive spaces in text |
| dual speaker format | warning | incorrect dual-speaker formatting |

## install

```bash
git clone https://github.com/yigitkonur/cli-subtitle-linter.git
cd cli-subtitle-linter
pip install -r requirements.txt
```

only dependency is `pyphen` for syllable counting. falls back to regex vowel counting if missing.

## usage

### fix subtitles

```bash
./subtitle-linter episode.srt                 # fix in-place, creates .bak backup
./subtitle-linter episode.srt --no-backup     # fix in-place, no backup
./subtitle-linter episode.srt --dry-run       # print stats, don't write anything
```

### validate subtitles

```bash
python src/validator.py --pairs-dir ./srt_files --output report
python src/validator.py --pairs-dir ./srt_files --output report --children --json
```

| flag | default | description |
|:---|:---|:---|
| `--pairs-dir` | `pairs` | directory to scan for `en_*.srt` files |
| `--output` | `netflix_validation_report` | output filename (no extension) |
| `--children` | off | use 15 CPS limit instead of 17 CPS |
| `--json` | off | also emit JSON report |

validator produces a markdown report with executive summary, per-rule breakdown, top 20 worst files, sample violations, and a two-phase fix recommendation.

## Netflix standards encoded

```
max chars per line        42
max chars per block       84  (2 × 42)
max duration              8s  (fixer) / 7s (validator)
min duration              833ms
min gap between subs      50ms
min words per line        2
min chars per line        15
min line ratio            30%  (shorter ÷ longer)
reading speed (adult)     17 CPS
reading speed (children)  15 CPS
```

## how the timing works

most subtitle tools split time proportional to character count. this one uses a hybrid approach grounded in psycholinguistics research:

- **70% syllable, 30% character** — syllable-to-duration correlation is r=0.92-0.96 vs r=0.78-0.88 for characters alone
- **punctuation pause adjustment** — periods get 0.60s weight, ellipsis 0.80s, commas 0.25s, em-dashes 0.40s. hesitation markers (uh, um, er) add 0.20s each. pause contribution capped at 20% of total
- **bottom-heavy line layout** — eye-tracking studies show it reduces upward saccades 25% and cognitive load 22%

validated on 107 interview videos (32,890 subtitles in, 55,898 out, zero overlaps).

## before / after

**before** (one block, ~9 seconds, lines way over 42 chars):

```
3
00:00:09,759 --> 00:00:18,269
To give viewers a high-level sense of what we'll be covering, we're gonna start with the basics of
what pre-training is, and then dig into how Nick thinks about strategy, data, alignment, and infrastructure at Anthropic.
```

**after** (three blocks, syllable-weighted timing, lines under 42 chars):

```
4
00:00:09,759 --> 00:00:12,761
To give viewers a high-level
sense of what we'll be covering, we're

5
00:00:12,761 --> 00:00:15,410
...with the basics
of what pre-training is, and then dig into

6
00:00:15,410 --> 00:00:18,269
...about strategy, data, alignment,
and infrastructure at Anthropic.
```

## project structure

```
subtitle-linter           — shell wrapper, entry point
src/
  subtitle_linter.py      — the auto-fixer (all four phases)
  validator.py            — read-only auditor + report generator
examples/
  before_*.srt            — raw auto-generated SRT samples
  after_*.srt             — same files after the fixer ran
docs/
  RESEARCH.md             — academic references for design decisions
```

## license

MIT
