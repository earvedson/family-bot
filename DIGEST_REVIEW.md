# Digest preview review (target week 8)

## What looks good

- **Title:** "Vecka 8" matches target week.
- **Svenska (both):** Present with week ranges (v7-11, v9-14, v48-9). Range filtering works.
- **Engelska (8B):** "Week 3 - 8" appears; English week refs work.
- **NO (Elsa):** V.8 and V.9 with dates – correct and useful.
- **Idrott:** Fyspass + "ta med" lines – correct.
- **Musik:** v.7, v.8, v.9 lines – in window, correct.
- **Spanska (6B):** Läxa till torsdag – useful, no week ref so kept.

## Issues

### 1. Low-value / generic lines (no week, likely from past blocks)

- **Matematik (both):** Standalone "Prov" and "Ingen läxa" – on the site these sit under v.5/v.6 blocks. They have no week ref so they pass the filter and add noise.
- **Matematik (8B):** "- Här finns planering för kapitlet, filmer med genomgångar och info om läxor + prov!" – generic Classroom ad, not week-specific.
- **NO (8B):** Only "Prov" – same as above, not useful without date/week.

### 2. Engelska (8B) very terse

- Only "Week 3 - 8" is shown. The Shakespeare / Romeo and Juliet description has no week ref and no prov/läxa keyword, so it is never captured. Either add a rule to keep the line immediately after a week-range line for that subject, or leave to the LLM to expand.

### 3. Possible typo on site

- **Svenska (6B):** "v48-9" may be a site typo (v48-49 or v4–8–9). Parser keeps it because "9" is in the target window. No code change needed.

## Recommended filter changes

1. **Drop very short, generic lines when they have no week ref**  
   If a line has no week reference and is exactly one of a few generic phrases ("Prov", "Ingen läxa", "Ingen läxa."), do not add it. That removes standalone prov/läxa from past-week blocks while keeping e.g. "Prov tisdag." or "Läxa till torsdag - räkna...".

2. **Drop generic Classroom promo line**  
   If a line has no week ref and matches a pattern like "Här finns planering för kapitlet" or "- Här finns planering", do not add it.

### Implemented in `school.py`

- **Generic no-week lines:** Lines with no week ref that are exactly "Prov", "Ingen läxa", or "Ingen läxa." are now skipped (they usually come from past-week blocks).
- **Classroom promo:** Lines that start with "Här finns planering för kapitlet/..." (no week ref) are skipped.

### Result after changes

- **Olle (8B):** Matematik and NO no longer show standalone "Prov" / "Ingen läxa" or the Classroom line. Svenska, Engelska, Idrott, Musik unchanged and correct.
- **Elsa (6B):** Standalone "Ingen läxa" and "Prov" under Matematik/NO removed. "Prov tisdag." kept (more specific). NO still shows V.8 and V.9 with dates.

### Iteration 2 – Implemented

- **Engelska follow-line:** When a line is only a week range (e.g. "Week 3 - 8") in the Engelska section, we now pull in the next line(s) as context (up to 5 lines or 220 chars), stopping at the next subject header or "Classroom:". This adds the Shakespeare / Romeo and Juliet description when the page has it on the next line. (If the live page splits the paragraph differently, the LLM is prompted to add a short note about Engelska content when it only sees "Week 3 - 8" with little text.)
- **Generic skip:** Added "Prov." (with period) to the list of generic phrases to skip.
- **LLM prompt:** Instruction to shorten long descriptions to one sentence if needed, and to optionally add one line about Engelska (e.g. Shakespeare) when the digest only shows a short week-range line.

### Iteration 3 – Engelska segment vs live page

- **Rest-of-segment logic:** For Engelska week-range-only lines we now take all text in the segment after that line (up to the next subject/Classroom), join and truncate to 220 chars, so the full paragraph is used when it appears in the same block.
- **Live page finding:** The actual Engelska segment on the 8B page currently only contains: "Engelska: Classroom: ... Week 2 - 5 We are starting the new year with " — so the segment ends there (next subject starts). The fuller Shakespeare/Romeo and Juliet text is not in this segment (likely in a linked Classroom page or elsewhere in the DOM). The digest correctly reflects what’s in the segment; no code bug.

### Optional next steps

- **Svenska (6B) "v48-9":** If the site fixes the typo, no change needed; otherwise leave as-is.
- If the school site later puts the full Engelska paragraph in the same block, the rest-of-segment logic will pick it up automatically.

---

## LLM-based extraction (recommended if output is disappointing)

**Problem:** Rule-based filtering is brittle. Key content can sit in different DOM blocks, line boundaries vary, and week rules are crude, so important items are missing or noise gets through.

**Approach:** Use the LLM to extract relevant content from *raw* page text instead of pre-filtering with rules. The model sees the full page and decides what applies to the target week.

**How to enable:** In `.env` set:
- `OPENAI_API_KEY=...` (required)
- `USE_LLM_EXTRACTION=1`

Then run as usual (or `--dry-run` to preview). The pipeline will:
1. Fetch raw text for each school page (same as now, strikethrough removed).
2. Send each page’s text to the LLM with the target week; the LLM returns only lines in the format `**Subject:** description` that are relevant for that week.
3. Merge with calendar and send to the LLM; it produces the full weekly overview (create_weekly_overview).

**Trade-offs:** Uses more tokens (one call per school page) and needs a working API key. In return you get better handling of varied structure, missing content (e.g. Engelska context), and week relevance. You can keep rule-based as default and switch to `USE_LLM_EXTRACTION=1` when the output is disappointing.
