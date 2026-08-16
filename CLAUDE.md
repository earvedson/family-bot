# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Swedish-language weekly digest bot: scrapes configured school class pages (homework/tests/quizzes)
and ICS calendars, builds a weekly summary, and posts it to a Discord channel via webhook. No
web framework, no database — it's a small set of scripts run via cron. There is no test suite.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in DISCORD_WEBHOOK_URL, PERSON_SCHOOL, etc.

python run_weekly.py                              # send this week's digest to Discord
python run_weekly.py --dry-run                     # write digest_preview.txt instead of sending
python run_weekly.py --dry-run -o out.txt           # custom output file
python run_weekly.py --dry-run --week 8 --year 2025 # preview a specific ISO week
python run_weekly.py --check-updates                # weekday mode: diff vs snapshot, notify only on changes
python run_weekly.py --check-updates --dry-run       # preview the diff notification without sending/updating snapshot
```

There's no lint/test/build tooling configured — `--dry-run` against a real `.env` is the way to
validate changes (inspect `digest_preview.txt`).

## Architecture: the pipeline

`run_weekly.py` is the only entry point and orchestrates a linear pipeline. Two independent axes
of behavior determine which functions actually run:

1. **`config.USE_LLM_EXTRACTION`** — whether school-page parsing is rule-based or LLM-based.
2. **Whether `OPENAI_API_KEY` is set** — whether the final digest text is LLM-written or template-built.

This gives four effective code paths through `run_weekly.py::main`, all converging on either
`digest.build_digest()` (template) or one of the `llm_improve.create_weekly_overview*()` functions
(LLM writes the whole digest and falls back to `build_digest()` on any API failure):

| USE_LLM_EXTRACTION | OPENAI_API_KEY | School parsing | Digest writer |
|---|---|---|---|
| off | unset | `school.fetch_all_school_info` (regex/keyword filter) | `digest.build_digest` |
| off | set | same | `llm_improve.create_weekly_overview` |
| on | unset | `school.fetch_all_raw_school_texts` (raw text, no filtering) | `_raw_blocks_to_school_infos` → `digest.build_digest` |
| on | set | same raw fetch | `llm_improve.create_weekly_overview_from_raw` (single LLM call does extraction *and* writing) |

Calendar fetching (`cal_fetcher.fetch_events_for_week`) is independent of this and always runs the
same way regardless of the axes above.

**Target week resolution** (`run_weekly.py`): with no `--week`, Mon–Fri runs target the *current*
ISO week, Sat–Sun runs target *next* week (so a Sunday-evening cron run produces next week's
digest). `--week`/`--year` overrides this. `reference_date` is a date such that
`reference_date + 7 days` falls in the target week/year — it's threaded through most functions
purely to resolve the correct ISO year when a week number could span two years.

**Person model**: people are configured once in `config.PERSON_SCHOOL` (`Name|ClassLabel|URL`) and
referenced by name elsewhere. `config.PERSON_CALENDARS` (`Names|ICS_URL`, `;`-joined names for a
shared calendar) is independently configured — calendar people and school people don't have to be
the same set. A calendar named `Familjen` is special-cased in `digest.build_digest` as "whole
family together" and surfaced at the top of the digest.

**School parsing** (`school.py`, rule-based path): fetches page text via BeautifulSoup, splits it
into segments by `SUBJECT_HEADERS`, and keeps lines matching `IMPORTANT_KEYWORDS` (prov/läxa/etc.)
or a week reference (`WEEK_REF`). `_line_applies_to_week` keeps lines mentioning the target week
±1 or with no week ref at all (ambiguous → kept), drops lines that are purely past weeks. There's
special-cased handling for "Engelska" sections where a lone week-range line (e.g. "Week 3 - 8")
pulls in following lines as context, since that subject's content is often split oddly. This logic
was tuned empirically — see `DIGEST_REVIEW.md` for the reasoning behind specific filter rules
before changing them.

**Calendar fetching** (`cal_fetcher.py`): computes the Monday–Sunday range for the target week in
`CALENDAR_TIMEZONE`, expands recurring events (RRULE) via `recurring_ical_events` (falls back to
non-expanded + date-filter if that package is missing), and attributes each event to a person only
if a name in `all_names` appears in the event summary — otherwise the event is shown to everyone
sharing that calendar. `digest.py` further dedupes same-day events with identical
(summary, location), preferring a timed instance over a midnight/all-day one.

**Snapshot/diff** (`snapshot.py`, used by `--check-updates`): Sunday's full run saves a JSON
snapshot per `(iso_year, target_week)` under `config.DIGEST_SNAPSHOT_DIR`. Weekday
`--check-updates` runs refetch, build a fresh snapshot, and diff against the stored one:
- School diff: compares `school_hashes` (LLM-extraction path, hash of raw text) or `school_highlights`
  (rule-based path, set difference on highlight lines) — whichever key the stored snapshot has.
- Calendar diff: new events are those whose `(person, start, summary)` key isn't in the stored set.
- If a full digest was sent (not just extracted), `snapshot.parse_school_section_from_digest`
  parses the actual `## Skola` markdown back out into `school_digest_highlights`, so weekday diffs
  can report only the *new* lines actually sent (via `llm_improve.get_new_school_items_only`)
  instead of just "page changed".

**Discord delivery** (`discord_notify.py`): splits on 2000-char limit by paragraph (`\n\n`) first,
falling back to line splits for an oversized single paragraph; prepends `@here` to the first chunk.

## Config

All configuration is env vars loaded from `.env` by `config.py` (simple hand-rolled parser, no
external dependency) at import time. See `.env.example` for the full list and format of each
variable (`PERSON_SCHOOL`, `SPECIAL_INFO_<NAME>`, `PERSON_CALENDARS`, `ICS_URLS`,
`CALENDAR_TIMEZONE`, `OPENAI_API_KEY`/`OPENAI_DIGEST_MODEL`, `USE_LLM_EXTRACTION`,
`DIGEST_SNAPSHOT_DIR`). Never commit `.env` — it's gitignored; only `.env.example` (no real values)
should be committed.
