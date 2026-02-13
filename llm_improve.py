"""
Optional: improve the digest with an LLM (e.g. OpenAI).

If OPENAI_API_KEY is set, the raw digest is sent to the API for a pass that can:
- Add a short summary or highlight the most important items
- Tighten or clarify wording while keeping the same structure and language
- Leave the text unchanged if nothing useful to add

Set OPENAI_API_KEY in .env to enable. Without it, improve_digest() returns the text unchanged.
"""

from __future__ import annotations

import os


def improve_digest(raw_digest: str) -> str:
    """
    Optionally run the digest through an LLM to improve clarity or add a brief summary.

    Returns improved text, or raw_digest if LLM is not configured or fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return raw_digest

    try:
        import openai
    except ImportError:
        # Optional: pip install openai to enable LLM improvement
        return raw_digest

    client = openai.OpenAI(api_key=api_key)
    model = os.environ.get("OPENAI_DIGEST_MODEL", "gpt-4o-mini")

    system = """Du är en hjälpsam assistent. Du får en veckosammanfattning (skola + kalender) på svenska.
Din uppgift: förbättra texten så den blir tydlig och användbar. Du får:
- Lägga till en mycket kort sammanfattning (1–2 meningar) högst upp om det underlättar
- Förenkla eller skärpa formuleringar
- Behålla exakt samma struktur (rubriker Skola, Kalender, personer) och innehåll
- Skriva på svenska
Om inget väsentligt förbättras, returnera texten i stort sett oförändrad. Svara med den slutliga texten, ingen kommentar."""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": raw_digest},
            ],
            max_tokens=4096,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text if text else raw_digest
    except Exception:
        return raw_digest
