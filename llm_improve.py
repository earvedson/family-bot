"""
Optional: LLM support (Claude, via the Anthropic API).

If ANTHROPIC_API_KEY is set: create_weekly_overview() sends already-extracted school_infos +
calendar data to Claude, which writes the full digest (title, intro, ## Skola, ## Kalender).
Without ANTHROPIC_API_KEY, run_weekly uses build_digest() (no LLM) instead.
"""

from __future__ import annotations

import os
import sys


def _anthropic_client():
    """Return an Anthropic client if an API key and the SDK are available, else None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        print(
            "Anthropic package not installed; run: pip install anthropic. Using template digest.",
            file=sys.stderr,
        )
        return None


def create_weekly_overview(
    school_infos: list,
    events_by_person: list,
    target_week: int,
    calendar_error: str | None = None,
    reference_date=None,
) -> str:
    """
    Send school + calendar data to Claude; it produces the full weekly digest (title, intro, Skola, Kalender).
    If ANTHROPIC_API_KEY is not set or the API call fails, falls back to build_digest() (no LLM).
    reference_date: used to resolve ISO year for target_week (default: today).
    """
    from digest import build_digest, serialize_school_and_calendar_for_llm

    client = _anthropic_client()
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
    model = (os.environ.get("ANTHROPIC_DIGEST_MODEL") or "claude-sonnet-5").strip()
    if not model:
        model = "claude-sonnet-5"
    print(f"Using model: {model}", file=sys.stderr)
    system = f"""Du skriver veckosammanfattningen för en familj. Du får rådata för VECKA {target_week}: skolinfo per barn och kalender dag för dag (person/händelser). Kalenderdatan har redan slagit ihop provdatum (från provschemat) med riktiga kalenderhändelser under respektive veckodag, markerade **PROV**. Skolinfon kan innehålla rader märkta *(Obs: ...)* - det är tekniska varningar om att källsidan var svårtolkad; ta hänsyn till dem men skriv inte ut dem ordagrant i sammanfattningen. Skolinfon kan också innehålla en rad **Kommande prov:** med prov för veckor efter VECKA {target_week} - det är ett separat framåtblickande avsnitt, inte denna veckas kalender.

Följ instruktioner:
1. Om en händelse flera gånger samma dag - men på olika tider - ange bara en gång och på den tid som verkar rimligast, dvs inte mitt i natten.
2. Prov (**PROV**) är redan daterade och ska stå kvar i kalendern under sin veckodag - flytta INTE prov till skolsektionen. Läxor och övrig veckoplanering (utan datum) hör hemma i skolsektionen, inte kalendern. Kolla så att det inte blir en dubbelpost mellan en riktig kalenderhändelse och ett prov.
3. Om en persons namn ingår i texten för en händelse, ta bort namnet i texten för händelsen.
4. Kontrollera noga att alla händelser från skolan inkluderas och att de är ordnade efter datum. Se också till att alla personer är med och har korrekt skolklass angiven.
5. Kontrollera att alla händelser blivit listade för rätt person.
6. Kontrollera noga att det inte blir en dubbelpost.

Uppgift: Skriv den färdiga veckosammanfattningen på svenska i följande format:
1. Rubrik: # Vecka {target_week} – Veckosammanfattning
2. En  kort inledning (2–4 meningar) som sammanfattar veckan. De aktiviteter som är återkommande varje vecka kan sammanfattas kort i inledningen. Fokusera på det som är speciellt viktigt denna vecka, inklusive eventuella prov. Var saklig och beskrivande, inte överdrivet positiv. Observera att det är kommande vecka, så det har inte hänt ännu.
3. Sektion ## Skola med underrubriker per person (t.ex. **Olle (8B):**) och deras punkter (veckoplanering, läxor, ev. **Kommande prov:**-lista). Varje persons punkter ska stå under just den personens underrubrik – flytta aldrig skolposter mellan personer. Skriv INTE ut denna veckas prov här - de står redan i kalendern.
4. Sektion ## Kalender (vecka {target_week}) med underrubriker ### Måndag DD månad osv., och under varje dag **Person:** tid – händelse, inklusive **PROV**-poster på sin veckodag.


"""



    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": payload[:30000]}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if text:
            return text
    except Exception as e:
        print(f"Anthropic API error (fallback to template digest): {e}", file=sys.stderr)
    return build_digest(
        school_infos,
        events_by_person,
        calendar_error=calendar_error,
        target_week=target_week,
        reference_date=reference_date,
    )
