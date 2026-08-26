# School scraping rewrite — plan

Rydsbergskollen has moved from "content directly on the Google Sites page" to "Sites page links
out to two Google Docs." Confirmed by fetching the real page
(`https://sites.google.com/edu.lerum.se/rydsbergskollen/b-laget/7b`) and both linked docs. This
plan covers the new scraper design and a full-system review aimed at the stated goal: keep the
family clearly informed of the upcoming week, given that every source is teacher-edited and error
prone.

Scope note: all `PERSON_SCHOOL` entries are on this same site/pattern (confirmed with the user),
so this plan targets **one solid structured parser**, not a multi-shape/LLM-fallback framework.

## 1. What the new page actually looks like

The class page (`.../b-laget/7b`) is now just a directory: teacher list, study-support hours, and
two outbound links, confirmed present in the raw server HTML (`curl`, no JS rendering needed):

- **Provschema** (`docs.google.com/document/d/.../edit`) — a *term-long test schedule*, shared
  across the whole "lag" (6B/7B/8B/9B all read the same doc).
- **Veckoplanering 7B** (`drive.google.com/open?id=...`, redirects to a Docs URL) — the
  *class-specific weekly plan*, one doc reused/edited every week.

Both are public Google Docs. `GET https://docs.google.com/document/d/{id}/export?format=html`
works anonymously (verified with plain `curl`, no cookies) and returns real `<table>` markup —
far more reliable to parse than the text/CSV export, which flattens multi-paragraph cells and
loses row/column boundaries.

### Veckoplanering doc structure (verified)

Body children, in order, repeat this block once per week:

```
<p>Veckoplanering 7B</p>
<p>Vecka: 35</p>
<table> 1 row, 1 cell   -- "Aktuell information: <free text>"
<table> 16 rows, 3 cols -- header: Ämne+Classroom-kod | Veckans planering: | Övrigt/Läxor:
                           15 subject rows below it
```

The doc currently holds one real week (35) plus a blank **`Vecka: MALL`** template block that
teachers presumably copy downward for future weeks — so the number of blocks will grow over the
term, not stay at one.

Column 1 cell has the subject name on its first line and a Classroom join-code (e.g. `s7mxb5uu`)
on a following line — noise for parents, must be split off and dropped. Cells are multi-paragraph
(e.g. Matematik: chapter name + "Planering för kapitlet här" as a link). Some cell text is only a
link caption (`Planering för kapitlet här`, `Terminsplanering 26/27`) — the `<a href>` must be
captured or the text is meaningless on its own.

### Provschema doc structure (verified)

One `<table>` per ISO week, 6 rows × 5 cols, spanning the whole term (v34–v51 currently):

```
Vecka 40 | 6B | 7B | 8B | 9B
Måndag   |    |    |    |
Tisdag   |    |    |    |
Onsdag   |    |    |    |
Torsdag  |    | Prov spanska, franska och tyska | |
Fredag   |    |    |    |
```

This is the good source: dated (weekday-level), spans many weeks ahead, shared across siblings —
so tests are knowable even in weeks the Veckoplanering doc hasn't caught up to yet.

## 2. Parser design (`school.py` rewrite)

### Discovery (per class landing page, once per run)

1. `httpx.get(landing_url)` → BeautifulSoup, find all `<a href>` matching
   `docs.google.com/document/`, `drive.google.com/open`, or `docs.google.com/spreadsheets/`.
2. **Classify by label text, not just `a.get_text()`.** Verified against the real page: the
   Provschema link's own `<a>` text is `"Provschema"`, but the Veckoplanering link's `<a>` text is
   **empty** — its label ("Veckoplanering 7B") lives in a parent `<div>` two levels up (Google
   Sites renders these as custom button components, not plain link text). So: try `a.get_text()`
   first; if empty/whitespace, walk up to ~4 ancestor levels and take the first one with non-empty,
   reasonably short (<150 char) text. Classify that resolved label (case-insensitive substring):
   contains "prov" → Provschema; contains "veckoplanering"/"planering" → Veckoplanering.
3. If a matched-URL link's label doesn't classify as either, don't just log to stderr — surface it
   per-person in the digest (a bare "couldn't identify one of the links on this page" note), since
   a silently-skipped link produces an empty Skola section that reads as "nothing this week."
4. Normalize any matched URL to `(kind, doc_id)` via regex on `/document/d/([\w-]+)` or
   `open?id=([\w-]+)` (Docs export tried first; no Sheets link exists on the verified page, so
   Sheets support is out of scope — add it if one actually shows up rather than building it
   speculatively).
5. Fetch `export?format=html` for each; **memoize by doc_id within the run** (Provschema is shared
   across siblings — fetch once, not once per configured person).

### `parse_veckoplanering(html) -> list[WeekBlock]`

Walk `soup.body` children in order (a small state machine, not `find_all` position guessing):

- `<p>` matching `Vecka:\s*(\d+)` → start a new block with that week number. `Vecka:\s*MALL` (or
  anything non-numeric) → explicitly skip, don't emit a block.
- Next `<table>` with 1 row → block's `info` (free text).
- Next `<table>` whose header row contains "Ämne" and "Veckans planering" and "Övrigt" (fuzzy
  substring match, not exact) → parse subject rows. **If a subject table appears whose header
  doesn't match these keywords, treat the block as unparseable rather than guessing** — this is
  the one place a renamed column should surface as a warning, not silently misparse.
- **`Vecka: MALL` (or any non-numeric week) with non-empty content cells → detect it, but don't
  substitute it for the target week's content.** Implemented and then reverted based on what the
  real document actually contains: verified against the live page, the filled-in MALL block holds
  *stale leftovers from a previous week* (reused as scaffolding for the next edit), not pre-filled
  upcoming content — e.g. its Musik cell says "PRIK - Gitarr" where the real week-35 block says
  "trumset", and its Bild cell duplicates week 35 verbatim. Showing it under the target week's
  heading would misinform rather than help, so the digest reports `status="week_not_published"`
  as normal and the block is only logged (to stderr, not the digest) for the maintainer, since it's
  the document's steady state rather than a one-off anomaly.
- **Duplicate week numbers → don't silent-overwrite.** If two blocks claim the same week number
  (e.g. a copied block never renumbered), keying a dict by week number makes the second overwrite
  the first with no signal. Keep the block with more non-empty cells, and record a warning noting
  the collision.
- Per subject row: `cell.get_text("\n", strip=True)` → first line = subject name, remaining
  line(s) checked against a short alnum-code pattern and dropped if they look like a Classroom
  code. Capture `<a href>` in "plan"/"homework" cells as `[text](url)`. Collapse blank runs, join
  multi-paragraph text with a single space. Skip subjects where both plan and homework cells are
  empty (no need to render 8 lines of "nothing this week").

Return **all** week blocks found (the doc will accumulate more over the term), keyed by week
number, not just the first — the caller picks by target week.

### `parse_provschema(html) -> dict[week, dict[weekday, dict[class_label, text]]]`

Iterate `<table>` elements; header row's first cell → `Vecka\s*(\d+)`, remaining header cells →
class labels (normalize case/whitespace). Remaining 5 rows → weekday → class → cell text (usually
empty). Also expose a flattened `upcoming_tests(class_label, from_week, n_weeks)` helper for the
lookahead feature in §4.

### Combining into `SchoolInfo` per person

- Look up `target_week` in the parsed Veckoplanering blocks for that person's class.
  - **Found** → build `highlights` as `"**{subject}:** {plan}"` / `"**{subject} (läxa):**
    {homework}"` lines (same `**X:** text` shape the digest/snapshot already expect), plus
    `info_note` from the "Aktuell information" table.
  - **Not found** → do **not** fall back to showing a different week's content under the target
    week's heading (the one failure mode that actively misinforms). Set
    `status="week_not_published"` and record the latest week number actually present, so the
    digest can say e.g. *"Veckoplaneringen är inte uppdaterad för v36 än (senast: v35)."*
- Look up `target_week` (and +1, for heads-up) in Provschema for the person's `class_label`;
  append as `tests: list[(weekday, description)]`. `class_label` is now load-bearing — if it
  doesn't match any Provschema column, that's a loud config error
  (`"Klass '8b' hittades inte i provschema; kolumner: 6B, 7B, 8B, 9B"`), not a silent empty list.
- Each doc fetch/parse failure is caught independently — a broken Provschema link shouldn't blank
  out a working Veckoplanering, and vice versa; errors accumulate into `SchoolInfo.error` /
  a new `warnings: list[str]` rather than aborting the person.

### Removed

The old `SUBJECT_HEADERS` allowlist, `IMPORTANT_KEYWORDS`/`WEEK_REF` line-filtering,
`_line_applies_to_week`, `GENERIC_NO_WEEK_PHRASES`, `CLASSROOM_PROMO_PATTERN`, and the Engelska
week-range special case all existed to compensate for unstructured free-text pages. The new pages
are structured (subject and week are given by table position, homework is given by column), so
this whole heuristic layer goes away — it's the main simplification the new format buys.

## 3. The Saturday/Sunday problem (must handle explicitly)

Per `CLAUDE.md`, an unqualified Sunday-evening run targets **next** ISO week. Today the
Veckoplanering doc only has week 35 (current) + a MALL template — a Sunday run asking for week 36
would find nothing yet, because teachers fill next week's block during the week, not in advance.
This is not a bug to route around; the digest must **report** it (see §2 "not published" status)
rather than paper over it. Provschema, being pre-filled weeks ahead, is the reliable lookahead
source for that same run — tests still show up even when the weekly plan doesn't.

## 4. Full-system review

### High-value, falls out of the rewrite almost free

- **Dated tests.** Provschema gives weekday-level test dates, which the old free-text page never
  reliably did. Two uses: (a) merge test entries into the same day-by-day Mon–Fri agenda as
  calendar events, instead of a separate prose "Skola" block — this is the biggest single
  improvement for "clearest possible" weekly view, since a parent currently has to cross-reference
  two sections to know "what happens Thursday." (b) a real multi-week "Kommande prov" lookahead
  section, independent of whether the weekly plan has caught up.
- **Sharper `--check-updates` diffs.** Structured cells mean a weekday diff can say "Matematik: ny
  läxa" instead of "sidan har ändrats" — this already works via the existing
  `school_highlights` set-diff in `snapshot.py`, no snapshot code change needed as long as
  highlights stay in the flat `**X:** text` shape.

### Correctness risks the rewrite must not introduce

- **`snapshot.parse_school_section_from_digest`** regex-parses the rendered `## Skola` markdown
  back into per-person lines for the weekday diff path. It's shape-compatible with `**X:** text`
  lines, so keep that convention in whatever renders `highlights`. Read/test this path once the
  new format is in, since it's the one place a format change breaks something *silently* (no
  exception, just wrong diffs).
- **Snapshot format versioning.** Old snapshots on disk under `DIGEST_SNAPSHOT_DIR` predate this
  parser. Add a `format_version` key to `build_snapshot`'s output; have `--check-updates` treat a
  missing/mismatched version as "no prior snapshot" (same code path as a missing file) instead of
  either crashing on a new key shape or reporting the entire week as "new." One-line change in
  `snapshot.py`, but skipping it means the first weekday run after deploy misbehaves.
- **`class_label` becomes load-bearing**, not cosmetic. Validate it against the Provschema header
  columns at fetch time and surface a clear config error if it doesn't match (see §2).

### Decided

- **`USE_LLM_EXTRACTION` → dropped for this school.** `fetch_all_raw_school_texts()` fetched only
  the *landing page* text, which post-rewrite has no real content. Structured parsing already
  produces precise, clean data, so the raw-extraction axis adds cost without benefit here. Remove
  the `USE_LLM_EXTRACTION` raw-page path and simplify `CLAUDE.md`'s pipeline-paths table down to
  the 2 paths that still matter: template vs. LLM-written prose, both fed by the structured
  extraction (`school_infos`/`SchoolInfo`, same as `create_weekly_overview`'s input today).
  `run_weekly.py` keeps the `OPENAI_API_KEY`-set branch as-is; the `USE_LLM_EXTRACTION` branch and
  `fetch_all_raw_school_texts`/`create_weekly_overview_from_raw`/`_raw_blocks_to_school_infos` are
  removed as dead code once the switch is gone.
- **Provschema tests merge into the day-by-day calendar — as a rendering choice downstream of
  first-class data, not as data that only exists at render time.** This distinction matters
  because of `--check-updates`: `build_snapshot` (`snapshot.py:80`) stores `school_highlights`
  from `SchoolInfo.highlights` and `calendar` only from what `fetch_events_for_week` returned. If
  tests are synthesized into calendar-shaped entries inside `digest.py` at render time (the
  original plan here), a newly-scheduled test lands in **neither** snapshot key — not in
  `school_highlights` (no longer rendered as a highlight) and not in `calendar` (synthesized after
  the snapshot is built) — so `diff_snapshots` sees no change and the single most valuable weekday
  notification this bot can send (a teacher just scheduled a test) silently never fires. It's also
  invisible to `parse_school_section_from_digest` (`snapshot.py:22`), which only scans between
  `## Skola` and `## Kalender`.
  Fix: `SchoolInfo.tests` is first-class data produced by `school.py`. `build_snapshot` gets a new
  `school_tests` key (person → list of `"weekday|description"` strings) with its own set-difference
  in `diff_snapshots`, exactly parallel to `school_highlights`. Where the digest *renders* tests
  (day-by-day loop vs. Skola block) is then a pure display decision that can't break the diff
  either way.
- **Two distinct mechanisms, not one**, because `build_digest`'s day-by-day loop
  (`digest.py:19`, `_week_dates`) only produces the 7 dates of `target_week` — tests for
  `target_week+1..+n` have no slot in that loop and would be silently dropped if routed through it:
  - Tests **within** `target_week` → merged into the existing per-day loop, in both `build_digest`
    and `serialize_school_and_calendar_for_llm` (so the LLM path gets the same unified view).
  - Tests in **later** weeks (the lookahead) → a separate flat "Kommande prov" list with its own
    `date.fromisocalendar` per test and its own ISO-year resolution — it does not go through
    `_events_by_day_and_person`.
- **Tests get their own formatter, not `_format_event_short`.** That function
  (`digest.py:86`) renders a midnight timestamp as "Heldag", which is the wrong register for a
  test with no known time (e.g. avoid "Heldag – Prov spanska, franska"; render as something like
  "**PROV** – spanska, franska" instead).
- **Dedup against real calendar events: exact-summary match only, or skip it.** Provschema text
  ("Prov spanska, franska och tyska") and a plausible ICS entry ("Spanskaprov") are unlikely to
  match on any fuzzy heuristic without a real risk of *suppressing* a genuine test rather than
  preventing a duplicate — skip fuzzy matching. If dedup is worth doing at all, key it on
  person + day + exact summary string; note `_dedupe_events_same_day` (`digest.py:54`) already
  prefers a timed event over an all-day/midnight one, so an exact match against a real timed
  calendar entry would correctly suppress the (timeless) synthetic version for free. Accepting an
  occasional double-mention is a smaller cost than losing a real test.

### Lower-priority observations

- No test suite is a deliberate repo choice, but teacher-edited pages are exactly the thing that
  breaks silently. Minimal-footprint suggestion: keep the two HTML exports I captured today as
  fixtures (`fixtures/veckoplanering_v35.html`, `fixtures/provschema.html`) so a future "does the
  parser still work" check doesn't require hitting the live site.
- Politeness/rate limits are a non-issue: with per-run memoization by doc ID, one run does 1
  landing-page fetch + up to 2 doc fetches per *distinct* class, not per person.

## 5. File-level task breakdown

1. **`school.py`** — rewrite as described in §2; drop the now-dead regex/keyword machinery;
   extend `SchoolInfo` with `tests: list[tuple[str, str]]`, `info_note: Optional[str]`,
   `status: str` (`"ok" | "week_not_published" | "fetch_error"`), `warnings: list[str]`.
2. **`digest.py`** — render `info_note` and `status="week_not_published"` messaging; merge
   in-target-week `tests` into the day-by-day calendar loop (both `build_digest` and
   `serialize_school_and_calendar_for_llm`), with exact-summary-only dedup against real calendar
   events; render `target_week+1..+n` tests as a separate "Kommande prov" list, not via the
   per-day loop; give tests their own formatter (not `_format_event_short`'s "Heldag" path).
3. **`snapshot.py`** — add `SNAPSHOT_FORMAT_VERSION`; guard `load_snapshot`/diff on mismatch; add
   a `school_tests` key to `build_snapshot` (parallel to `school_highlights`) with its own
   set-difference in `diff_snapshots`, so a newly-scheduled test is visible to `--check-updates`
   regardless of where/how it's rendered.
4. **`run_weekly.py` / `llm_improve.py`** — remove the `USE_LLM_EXTRACTION` branch,
   `fetch_all_raw_school_texts`, `create_weekly_overview_from_raw`, and
   `_raw_blocks_to_school_infos`; both remaining paths (template / LLM prose) consume
   `SchoolInfo` as `create_weekly_overview` already does.
5. **`.env.example` / `CLAUDE.md`** — no new config required; update the pipeline-paths table in
   `CLAUDE.md` if `USE_LLM_EXTRACTION` is simplified away.

## 6. Decided, and one still open

Decided (see §4 "Decided"): tests merge into the day-by-day calendar; `USE_LLM_EXTRACTION` is
dropped for this school.

Still open:

1. Any of the other 15 subject rows (Hkk, Slöjd, Tyska, Sv/eng, Franska, …) that should always be
   suppressed for a given kid regardless of content, the way `SPECIAL_INFO_<NAME>` already handles
   "doesn't take Musik"? Worth confirming the existing special-info mechanism still covers this
   cleanly with the new source.
