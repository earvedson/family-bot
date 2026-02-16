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
- **SPECIAL_INFO_&lt;Name&gt;** – Valfritt. Per-person notiser (t.ex. ämnesbyten: "Bild (inte Musik); Slöjd (inte Hemkunskap)"). Namnet ska matcha PERSON_SCHOOL; nyckeln är SPECIAL_INFO_ + namnet i versaler med mellanslag ersatta med understreck (t.ex. `SPECIAL_INFO_OLLE=...`). Visas i digesten och skickas till LLM som kontext.
- **PERSON_CALENDARS** – Valfritt. Kalender kopplad till person(er): format `Names|ICS_URL`. `Names` är ett namn eller flera med `;` (t.ex. `Alice;Bob` = kalender för båda). Samma person kan ha flera kalendrar genom flera rader. Digesten grupperar händelser per person.
- **ICS_URLS** – Valfritt (fallback). Global kalender om PERSON_CALENDARS inte är satt. Kommaseparerade ICS-URL:er.
- **OPENAI_API_KEY** – Valfritt. Om satt skickas skol- och kalenderdata till en LLM som skriver hela veckosammanfattningen (rubrik, inledning, Skola, Kalender). Kräver `pip install openai`. Modell: **OPENAI_DIGEST_MODEL** (standard: gpt-4o-mini).
- **USE_LLM_EXTRACTION** – Valfritt. Sätt till `1` (eller `true`/`yes`) för att skicka rå sidtext per barn + kalender i ett enda LLM-anrop som returnerar hela veckosammanfattningen. Rekommenderas om innehållet saknas eller regelbaserat filter blir fel; kräver **OPENAI_API_KEY**.
- **CALENDAR_TIMEZONE** – Valfritt. Tidszon för kalenderveckan och händelsetider (t.ex. Europe/Stockholm). Standard: Europe/Stockholm.

**Kalender:** Händelser hämtas för nästa veckas måndag–söndag (samma vecka som skolinfo). I digesten visas kalendern **dag för dag**: under varje veckodag (t.ex. "Måndag 17 februari") listas vad varje person har den dagen. Om du har en kalender med namnet **Familjen** (t.ex. `Familjen|webcal://...`) tolkas den som att hela familjen gör något tillsammans; det nämns i veckosammanfattningen högst upp.

**Veckofilter (skola):** Skolsidorna är ofta ostrukturerade och listar planering för många veckor. Boten fokuserar på *nästa vecka* (räknat från kördatum). Två lägen: (1) **Regelbaserat** (standard): `school.py` filtrerar rader som nämner nästa vecka; om OPENAI_API_KEY är satt skriver LLM hela digesten utifrån den data. (2) **USE_LLM_EXTRACTION=1**: Ett enda LLM-anrop får rå sidtext per barn och kalender, extraherar skolinfo för nästa vecka och skriver hela veckosammanfattningen – ofta bättre när sidor varierar i upplägg. Kör gärna söndag så att "nästa vecka" blir veckan som börjar måndag.

Om du publicerar repot: alla känsliga och hemspecifika värden ska ligga i `.env`. Committa bara `.env.example` (utan riktiga värden). Kontrollera att `.env` finns i `.gitignore`.

## Köra manuellt

```bash
python run_weekly.py
```

Om `DISCORD_WEBHOOK_URL` inte är satt skrivs digesten ut i stderr och skriptet avslutar med felkod 1.

**För att granska och justera filtreringen** (skickas inte till Discord):

```bash
python run_weekly.py --dry-run
```

Digesten sparas i `digest_preview.txt`. Öppna filen, granska innehållet, ändra t.ex. `school.py` (veckofilter, ämnesrubriker) eller `llm_improve.py` (LLM-prompt) och kör `--dry-run` igen tills resultatet är bra. Annat filnamn: `python run_weekly.py --dry-run -o min_preview.txt`.

**Skriv ut en viss vecka:** `python run_weekly.py --dry-run --week 8` ger digest för ISO vecka 8 (nuvarande år). Använd `--year 2025` för ett visst år, t.ex. `python run_weekly.py --dry-run -w 10 -y 2025`.

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

Se till att cron har tillgång till samma miljö om du använder `.env` (kör från projektdirectory så att `config.py` hittar `.env`). För steg-för-steg-installation på en Raspberry Pi, se [RASPBERRY_PI.md](RASPBERRY_PI.md).

## Projektstruktur

- `config.py` – Läser URL:er och webhook från miljö/`.env`
- `school.py` – Hämtar och parsar konfigurerade klassidor (prov, läxor, förhör); filtrerar till nästa vecka
- `cal_fetcher.py` – Hämtar ICS för måndag–söndag i målveckan, händelser per person; återkommande händelser (RRULE) expanderas till varje förekomst (kräver `recurring-ical-events`)
- `digest.py` – Bygger meddelandet (skola + kalender dag för dag)
- `llm_improve.py` – Valfritt: skickar digesten till en LLM för förtydligande, veckofilter på Skola-delen och sammanfattning (kräver OPENAI_API_KEY)
- `discord_notify.py` – Skickar till Discord via webhook
- `run_weekly.py` – Entry point för cron
