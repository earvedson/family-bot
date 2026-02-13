# Weekly digest bot

En bot som varje vecka sammanställer skolinfo (klassidor du konfigurerar) och kalenderhändelser (t.ex. iCloud via ICS) och skickar en sammanfattning till Discord.

## Krävs

- Python 3.10+
- Discord-webhook-URL för en kanal
- En eller flera ICS-prenumerationslänkar till kalendrar (valfritt)

## Installation

```bash
cd /path/to/family-bot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Konfiguration

Kopiera `.env.example` till `.env` och fyll i värdena. **Committa aldrig `.env`** – filen innehåller webhook-URL, kalenderlänkar och eventuellt skol-URL:er och är redan listad i `.gitignore`.

```bash
cp .env.example .env
```

- **DISCORD_WEBHOOK_URL** – Skapa en Incoming Webhook i Discord: Kanalinställningar → Integrations → Webhooks → New Webhook, kopiera URL.
- **PERSON_SCHOOL** – Person + klass: format `Name|ClassLabel|URL`, kommaseparerat. T.ex. `Alice|6B|https://...,Bob|8B|https://...`. Namn och klassetikett (6B, 8B) är konfigurerbara; byt vid behov när klasser/år ändras.
- **PERSON_CALENDARS** – Valfritt. Kalender kopplad till person(er): format `Names|ICS_URL`. `Names` är ett namn eller flera med `;` (t.ex. `Alice;Bob` = kalender för båda). Samma person kan ha flera kalendrar genom flera rader. Digesten grupperar händelser per person.
- **ICS_URLS** – Valfritt (fallback). Global kalender om PERSON_CALENDARS inte är satt. Kommaseparerade ICS-URL:er.
- **OPENAI_API_KEY** – Valfritt. Om satt körs digesten genom en LLM som kan lägga till en kort sammanfattning och förtydliga formuleringar. Kräver `pip install openai`. Modell: **OPENAI_DIGEST_MODEL** (standard: gpt-4o-mini).

Om du publicerar repot: alla känsliga och hemspecifika värden ska ligga i `.env`. Committa bara `.env.example` (utan riktiga värden). Kontrollera att `.env` finns i `.gitignore`.

## Köra manuellt

```bash
python run_weekly.py
```

Om `DISCORD_WEBHOOK_URL` inte är satt skrivs digesten ut i stderr och skriptet avslutar med felkod 1.

## Schemaläggning med cron

Kör en gång per vecka, t.ex. söndag 18:00 eller måndag 07:00:

```bash
crontab -e
```

Lägg till (ändra sökväg till din installation):

```cron
# Söndag 18:00 – veckosammanfattning
0 18 * * 0  cd /path/to/family-bot && .venv/bin/python run_weekly.py
```

Alternativt måndag 07:00:

```cron
0 7 * * 1  cd /path/to/family-bot && .venv/bin/python run_weekly.py
```

Se till att cron har tillgång till samma miljö om du använder `.env` (kör från projektdirectory så att `config.py` hittar `.env`).

## Projektstruktur

- `config.py` – Läser URL:er och webhook från miljö/`.env`
- `school.py` – Hämtar och parsar konfigurerade klassidor (prov, läxor, förhör)
- `calendar.py` – Hämtar ICS-URL:er och listar händelser per person (enstaka eller delade kalendrar)
- `digest.py` – Bygger meddelandet (skola + kalender)
- `llm_improve.py` – Valfritt: skickar digesten till en LLM för förtydligande/sammanfattning (kräver OPENAI_API_KEY)
- `discord_notify.py` – Skickar till Discord via webhook
- `run_weekly.py` – Entry point för cron
