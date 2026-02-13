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
- **SCHOOL_CLASSES** – En eller flera klasser, format `etikett|url` med komma mellan: t.ex. `ClassA|https://.../a,ClassB|https://.../b`. Etiketten är det som visas i digesten; byt vid behov när klasser/år ändras.
- **ICS_URLS** – Valfritt. En eller flera kalender-URL:er, kommaseparerade. Från iCloud: Calendar → dela kalender → Public Calendar / prenumerationslänk.

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
- `calendar.py` – Hämtar ICS-URL:er och listar händelser för kommande 7 dagar
- `digest.py` – Bygger meddelandet (skola + kalender)
- `discord_notify.py` – Skickar till Discord via webhook
- `run_weekly.py` – Entry point för cron
