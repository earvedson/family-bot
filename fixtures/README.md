# Fixtures

Real Google Docs HTML exports captured 2026-08-25 from Rydsbergskollen 7B (`veckoplanering_v35.html`,
week 35 + the blank `MALL` template; `provschema.html`, weeks 34–51 for 6B–9B). No test framework is
set up in this repo (see `CLAUDE.md`), but these are handy for a quick manual sanity check that
`school.py`'s parser still handles the real table shapes after a change:

```bash
python3 -c "
import school
vp = open('fixtures/veckoplanering_v35.html', encoding='utf-8').read()
ps = open('fixtures/provschema.html', encoding='utf-8').read()
blocks, warnings, fallback = school.parse_veckoplanering(vp)
print('weeks:', sorted(blocks.keys()), 'warnings:', warnings)
weeks, class_labels = school.parse_provschema(ps)
print('7B week 40 tests:', school._collect_tests_for_week(weeks, 40, '7B'))
"
```

Expected: `weeks: [35]`, one warning about the filled-in `MALL` block, and week 40 shows a Thursday
Spanish/French/German test for 7B. If the live site changes shape again, re-capture with
`curl -sL "https://docs.google.com/document/d/<id>/export?format=html"` for the two doc links found
on a class landing page, and update these files.
