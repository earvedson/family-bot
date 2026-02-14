"""
Optional: LLM support (e.g. OpenAI).

If OPENAI_API_KEY is set:
- With USE_LLM_EXTRACTION=1: one LLM call (create_weekly_overview_from_raw) receives raw school
  page text per person + calendar and returns the full digest. Preferred path.
- create_weekly_overview(): alternative path when school_infos are already extracted (e.g. rule-based).

Without OPENAI_API_KEY, run_weekly uses build_digest() (no LLM).
"""

from __future__ import annotations

import os
import re
import sys


def _openai_client():
    """Return OpenAI client if key and lib available, else None."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import openai
        return openai.OpenAI(api_key=api_key)
    except ImportError:
        print(
            "OpenAI package not installed; run: pip install openai. Using template digest.",
            file=sys.stderr,
        )
        return None


def extract_school_highlights(
    raw_page_text: str,
    person_name: str,
    class_label: str | None,
    target_week: int,
) -> list[str]:
    """
    Use the LLM to extract week-relevant school items from raw page text.
    Returns a list of lines in digest format: "**Subject:** description".
    If LLM is not configured or fails, returns [].
    """
    client = _openai_client()
    if not client:
        return []

    model = (os.environ.get("OPENAI_DIGEST_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    print(f"Using model: {model}", file=sys.stderr)
    class_info = f" ({class_label})" if class_label else ""
    system = f"""Du är en assistent som plockar ut relevant information från en svensk skolklass-sida.
Sidan gäller {person_name}{class_info}. Vi vill ha en sammanfattning för VECKA {target_week} (nästa vecka).

Uppgift: Läs den råa sidtexten och skriv ut ENDAST de poster som är relevanta för vecka {target_week} samt om det är prov, även efterföljande vecka.
- Observera: vecka anges på inkonsekventa sätt. t ex "v. 6" eller "v.3" eller "v7-11". Det är därför viktigt att kontrollera att vecka {target_week} är angivet eller ingår i intervallet.
- Om det finns prov även för efterföljande vecka, inkludera det.
- Om datum redan är passerat, ta bort dem.
- Ibland står beskrivning över flera rader - ta hänsyn till det och inkludera allt relevant.
- Skriv på svenska

Format: exakt en rad per post, i formen **Ämne:** beskrivning. T.ex. **Svenska:** v7-11 Litteraturhistoria.
Skriv inga rubriker eller förklaringar, bara raderna. """

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": raw_page_text[:30000]},  # cap tokens
            ],
            max_completion_tokens=2048,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text or text.upper().strip() == "INGEN":
            return []
        # Parse lines: expect **Subject:** rest
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Normalize to our format if the model wrote "Subject:" without **
            if re.match(r"^\*\*[^*]+\*\*:", line):
                lines.append(line)
            elif ":" in line and not line.startswith("#"):
                # e.g. "Svenska: v7-11 ..." -> **Svenska:** v7-11 ...
                sub, _, rest = line.partition(":")
                sub = sub.strip().strip("*")
                if sub and rest.strip():
                    lines.append(f"**{sub}:** {rest.strip()}")
        return lines
    except Exception as e:
        print(f"OpenAI API error (school extraction): {e}", file=sys.stderr)
        return []


def create_weekly_overview(
    school_infos: list,
    events_by_person: list,
    target_week: int,
    calendar_error: str | None = None,
    reference_date=None,
) -> str:
    """
    Send school + calendar data to the LLM; it produces the full weekly digest (title, intro, Skola, Kalender).
    If OPENAI_API_KEY is not set or the LLM fails, falls back to build_digest() (no LLM).
    reference_date: used to resolve ISO year for target_week (default: today).
    """
    from digest import build_digest, serialize_school_and_calendar_for_llm

    client = _openai_client()
    if not client:
        return build_digest(
            school_infos,
            events_by_person,
            calendar_error=calendar_error,
            target_week=target_week,
            reference_date=reference_date,
        )

    payload = serialize_school_and_calendar_for_llm(
        school_infos,
        events_by_person,
        target_week,
        calendar_error=calendar_error,
        reference_date=reference_date,
    )
    model = (os.environ.get("OPENAI_DIGEST_MODEL") or "gpt-4o-mini").strip()
    if not model:
        model = "gpt-4o-mini"
    print(f"Using model: {model}", file=sys.stderr)
    system = f"""Du skriver veckosammanfattningen för en familj. Du får rådata för VECKA {target_week}: skolinfo per barn och kalender dag för dag (person/händelser).

Följ instruktioner:
1. Om en händelse flera gånger samma dag - men på olika tider - ange bara en gång och på den tid som verkar rimligast, dvs inte mitt i natten.
2. Om en kalenderhändelse handlar om skolan, t ex om ett prov eller en läxa, skriv ut den i skolsektionen och inte i kalendern. Kolla så att det inte blir en dubbelpost.
3. Om en persons namn ingår i texten för en händelse, ta bort namnet i texten för händelsen.
4. Kontrollera noga att alla händelser från skolan inkluderas och att de är ordnade efter datum. Se också till att alla personer är med och har korrekt skolklass angiven.
5. Kontrollera att alla händelser blivit listade för rätt person.
6. Kontrollera noga att det inte blir en dubbelpost.

Uppgift: Skriv den färdiga veckosammanfattningen på svenska i följande format:
1. Rubrik: # Vecka {target_week} – Veckosammanfattning
2. En  kort inledning (2–4 meningar) som sammanfattar veckan. De aktiviteter som är återkommande varje vecka kan sammanfattas kort i inledningen. Fokusera på det som är speciellt viktigt denna vecka. Var saklig och beskrivande, inte överdrivet positiv. Observera att det är kommande vecka, så det har inte hänt ännu.
3. Sektion ## Skola med underrubriker per person (t.ex. **Olle (8B):**) och deras punkter. Varje persons punkter ska stå under just den personens underrubrik – flytta aldrig skolposter mellan personer. Om det är prov, markera det med **PROV**. Var noga med att alla händelser från skolan inkluderas och att de är ordnade efter datum. Se också till att alla personer är med och har korrekt klass.
4. Sektion ## Kalender (vecka {target_week}) med underrubriker ### Måndag DD månad osv., och under varje dag **Person:** tid – händelse. ...


"""



    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": payload[:30000]},
            ],
            max_completion_tokens=4096,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"OpenAI API error (fallback to template digest): {e}", file=sys.stderr)
    return build_digest(
        school_infos,
        events_by_person,
        calendar_error=calendar_error,
        target_week=target_week,
        reference_date=reference_date,
    )


def create_weekly_overview_from_raw(
    raw_blocks: list[tuple[str, str | None, str | None, str | None]],
    events_by_person: list,
    target_week: int,
    calendar_error: str | None = None,
    reference_date=None,
) -> str:
    """
    Single LLM call: raw school page text (per person) + calendar in, full digest out.
    raw_blocks: list of (person_name, class_label, raw_text, error) per school page.
    On API failure, falls back to build_digest with synthetic school_infos (highlights=[], error set).
    """
    from digest import build_digest, serialize_raw_school_and_calendar_for_llm
    from school import SchoolInfo

    client = _openai_client()
    if not client:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            print("OPENAI_API_KEY not set; using template digest.", file=sys.stderr)
        school_infos = _raw_blocks_to_school_infos(raw_blocks, target_week)
        return build_digest(
            school_infos,
            events_by_person,
            calendar_error=calendar_error,
            target_week=target_week,
            reference_date=reference_date,
        )

    payload = serialize_raw_school_and_calendar_for_llm(
        raw_blocks,
        events_by_person,
        target_week,
        calendar_error=calendar_error,
        reference_date=reference_date,
    )
    model = (os.environ.get("OPENAI_DIGEST_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    print(f"Using model: {model}", file=sys.stderr)
    system = f"""Du skriver veckosammanfattningen för en familj. Du får rådata för VECKA {target_week}:
1) SKOLA: för varje person en avgränsad block med rå text från deras skolklass-sida.
2) KALENDER: dag för dag med person och händelser.

Gör följande:
A) Skolsektion: För varje persons block, plocka ut ENDAST de poster som gäller vecka {target_week} (och vid prov även efterföljande vecka). Vecka anges ofta som "v. 6", "v7-11" m.m. – kontrollera att vecka {target_week} ingår. Skriv under varje persons underrubrik (t.ex. **Olle (8B):**) BARA det du extraherade från just den personens block – flytta aldrig skolposter mellan personer. Format per post: **Ämne:** beskrivning. Markera prov med **PROV**.
B) Kalendern: Använd dag-för-dag-data. Om samma händelse flera gånger samma dag (olika tider), ange en gång med rimlig tid. Om en kalenderhändelse handlar om skolan (prov/läxa), skriv den i skolsektionen och inte i kalendern – undvik dubbelpost. Ta bort personens namn ur händelsetexten om det ingår.

Utdataformat:
1. Rubrik: # Vecka {target_week} – Veckosammanfattning
2. Kort inledning (2–4 meningar), saklig, fokus på kommande vecka.
3. Sektion ## Skola med underrubriker per person och deras punkter.
4. Sektion ## Kalender (vecka {target_week}) med ### Måndag DD månad osv. och **Person:** tid – händelse.
Skriv på svenska."""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": payload[:30000]},
            ],
            max_completion_tokens=4096,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"OpenAI API error (fallback to template digest): {e}", file=sys.stderr)
    school_infos = _raw_blocks_to_school_infos(raw_blocks, target_week)
    return build_digest(
        school_infos,
        events_by_person,
        calendar_error=calendar_error,
        target_week=target_week,
        reference_date=reference_date,
    )


def _raw_blocks_to_school_infos(
    raw_blocks: list[tuple[str, str | None, str | None, str | None]],
    target_week: int,
):
    """Build list of SchoolInfo for fallback digest (highlights=[], error when block had error)."""
    from school import SchoolInfo

    infos = []
    for person_name, class_label, _raw_text, err in raw_blocks:
        infos.append(SchoolInfo(
            person_name=person_name,
            class_label=class_label,
            url="",
            week=target_week,
            highlights=[],
            error=err,
        ))
    return infos
