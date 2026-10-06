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

`run_weekly.py` is the only entry point and orchestrates a linear pipeline. School parsing
(`school.fetch_all_school_info`) always runs the same way — it's structured extraction, not a
filter with an on/off mode (see "School parsing" below). The one remaining axis is **whether
`ANTHROPIC_API_KEY` is set**, which decides how the already-extracted `SchoolInfo` list and calendar
data become digest text:

| ANTHROPIC_API_KEY | Digest writer |
|---|---|
| unset | `digest.build_digest` (template) |
| set | `llm_improve.create_weekly_overview` (Claude writes the whole digest via the Anthropic Messages API; falls back to `build_digest` on any API failure) |

Calendar fetching (`cal_fetcher.fetch_events_for_week`) is independent of this and always runs the
same way.

**Target week resolution** (`run_weekly.py`): with no `--week`, Mon–Fri runs target the *current*
ISO week, Sat–Sun runs target *next* week (so a Sunday-evening cron run produces next week's
digest). `--week`/`--year` overrides this. `reference_date` is a date such that
`reference_date + 7 days` falls in the target week/year — it's threaded through most functions
purely to resolve the correct ISO year when a week number could span two years.

**Person model**: people are configured once in `config.PERSON_SCHOOL` (`Name|ClassLabel|URL`) and
referenced by name elsewhere. `config.PERSON_CALENDARS` (`Names|ICS_URL`, `;`-joined names for a
shared calendar) is independently configured — calendar people and school people don't have to be
the same set. A calendar named `Familjen` is special-cased in `digest.build_digest` as "whole
family together" and surfaced at the top of the digest. Two optional per-person env vars, keyed the
same way (`<PREFIX>_<NAME>`, name uppercased/underscored): `SPECIAL_INFO_<NAME>` is a free-text note
shown in the digest (doesn't filter anything); `SUPPRESS_SUBJECTS_<NAME>` is a comma-separated list
of subject names (matched case-insensitively against Veckoplanering's "Ämne" column) actually
filtered out of that person's `highlights` in `school.py` — e.g. a subject they don't take. It
also applies to Provschema tests (`_filter_test_description`): those are free text, so the class's
Veckoplanering subject names are used as the vocabulary — an entry mentioning only suppressed
subjects is dropped, a shared one ("Prov spanska, franska och tyska") is shortened to the rest.


**School parsing** (`school.py`): the class landing page (Google Sites) is just a directory —
`_discover_source_links` fetches it and finds two outbound Google Doc links by label text (with an
ancestor-text fallback, since Google Sites sometimes renders a link's label on a parent `<div>`
rather than the `<a>` itself): a **Provschema** (term-long test schedule, one table per ISO week,
shared across a whole class-year "lag" so it's fetched once per run and memoized by doc ID) and a
**Veckoplanering** (class-specific weekly plan, one 3-column subject table per week, reused/edited
by the teacher every week). Both are fetched via `docs.google.com/document/d/{id}/export?format=html`
(works anonymously for public docs) and parsed as structured tables — there's no regex/keyword
filtering layer since subject and week are given by table position, not inferred from free text.

`parse_veckoplanering` walks the doc as a small state machine keyed off `Vecka: N` paragraphs, and
is deliberately tolerant of teacher error: a week block whose header row doesn't match the expected
columns is treated as unparseable (warning) rather than guessed at; duplicate week numbers keep
whichever block has more content (warning). A `Vecka: MALL` template block that was filled in but
never renumbered is detected but **never** substituted for a missing target week — on the real
document this template turned out to hold stale leftovers from a *previous* week (reused as
scaffolding), not pre-filled upcoming content, so showing it under the target week's heading would
misinform rather than help; it's logged to stderr for the maintainer only, since it's the
document's steady state rather than a per-run anomaly worth repeating in every digest.
`SchoolInfo.status == "week_not_published"` is set instead — this is expected on an unqualified
Sunday-evening run, which targets *next* week (see "Target week resolution" below) before the
teacher has filled that block in. Provschema, being pre-filled weeks ahead, still surfaces tests
for such weeks via `SchoolInfo.tests` (this week, dated by weekday) and `.upcoming_tests` (next
`UPCOMING_TEST_WEEKS_AHEAD` weeks) even when the weekly plan hasn't caught up.
`SchoolInfo.class_label` is load-bearing here — it selects the Provschema column, and a mismatch
against that doc's class-label headers is a surfaced warning, not a silent empty result.

`digest.py` merges `SchoolInfo.tests` into the day-by-day calendar view (see "Calendar fetching"
below) rather than showing them in the Skola section, since they're now dated;
`.upcoming_tests` renders as a "Kommande prov" lookahead under each person's Skola block instead,
since it spans weeks outside the day-by-day loop's range.

**Calendar fetching** (`cal_fetcher.py`): computes the Monday–Sunday range for the target week in
`CALENDAR_TIMEZONE`, expands recurring events (RRULE) via `recurring_ical_events` (falls back to
non-expanded + date-filter if that package is missing), and attributes each event to a person only
if a name in `all_names` appears in the event summary — otherwise the event is shown to everyone
sharing that calendar. `digest.py` further dedupes same-day events with identical
(summary, location), preferring a timed instance over a midnight/all-day one. Provschema test
entries (`SchoolInfo.tests`) are placed into the same day-by-day view by weekday name, deduped
against a real calendar event only on an exact summary match (deliberately not fuzzy — see
`SCHOOL_SCRAPING_PLAN.md` §4 for why).

**Snapshot/diff** (`snapshot.py`, used by `--check-updates`): Sunday's full run saves a JSON
snapshot per `(iso_year, target_week)` under `config.DIGEST_SNAPSHOT_DIR`, tagged with
`SNAPSHOT_FORMAT_VERSION` — `load_snapshot` treats a missing/mismatched version as no prior
snapshot (same as a missing file) rather than diffing against an incompatible shape. Weekday
`--check-updates` runs refetch, build a fresh snapshot, and diff against the stored one:
- School diff: set difference on `school_highlights` (weekly-plan/homework lines) and, separately,
  on `school_tests` (`"weekday|description"` strings) — a newly-scheduled test is diffable even
  though it's rendered in the calendar section, not as a highlight.
- Calendar diff: new events are those whose `(person, start, summary)` key isn't in the stored set.
- If a full digest was sent, `snapshot.parse_school_section_from_digest` additionally parses the
  actual `## Skola` markdown back out into `school_digest_highlights` (informational baseline of
  what was actually sent; not currently used for diffing).

**Discord delivery** (`discord_notify.py`): splits on 2000-char limit by paragraph (`\n\n`) first,
falling back to line splits for an oversized single paragraph; prepends `@here` to the first chunk.

## Deploying to the Raspberry Pi

Production runs via cron on a Raspberry Pi on the home network (setup: `RASPBERRY_PI.md`). Its
host/user are deliberately not in this repo (it's public) — they're in the maintainer's Obsidian
vault note `~/Obsidian/Personal/2. Areas/family-bot.md`, which also lists any **pending deploy
steps** left by a session on another machine. Read it whenever asked to deploy/update the Pi, and
tick off/remove the pending steps once done. A deploy is: push to `main`, `git pull` in the project
dir on the Pi, and apply any `.env` changes there by hand (`.env` isn't in git). The Pi is only
reachable from the home network.

## Config

All configuration is env vars loaded from `.env` by `config.py` (simple hand-rolled parser, no
external dependency) at import time. See `.env.example` for the full list and format of each
variable (`PERSON_SCHOOL`, `SPECIAL_INFO_<NAME>`, `PERSON_CALENDARS`, `ICS_URLS`,
`CALENDAR_TIMEZONE`, `ANTHROPIC_API_KEY`/`ANTHROPIC_DIGEST_MODEL`, `DIGEST_SNAPSHOT_DIR`). Never commit
`.env` — it's gitignored; only `.env.example` (no real values) should be committed.
