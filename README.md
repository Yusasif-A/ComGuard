# ComGuard

A WhatsApp assistant for reporting community emergencies and scams, and for
warning neighbours when something is confirmed.

Someone sends a photo, a voice note and (optionally) their location. ComGuard
reads the picture, listens to the description, strips every trace of who sent
it, tells the person what to do next in their own language, forwards an
anonymised summary to the right agency, and — only once the report is
independently corroborated — warns people nearby.

Built on WhatsApp because that is where people already are: roughly 95–98% of
Nigerian internet users, and over 90% across many African markets. Nobody has to
install anything.

---

## What it does

**Reporting.** Photo, voice note, text, location — in any order, over several
minutes. The pieces attach to one open report rather than becoming several,
because three messages from one person must never look like three witnesses.

**Vision.** Gemma reads text off notices verbatim, classifies the hazard or
scam, judges severity, and flags images that look AI-generated or manipulated.
One model does all of it; there is no separate detector.

**Grounding.** Municipal tax codes, state gazettes, penal law and SEMA bulletins
are indexed in a Chroma vector store. When someone asks "is this levy real?",
the answer comes from the record — and when the record has nothing, the
assistant says so instead of inventing a statute.

**Reporter protection.** Image metadata (including GPS) is destroyed by
re-encoding the pixels. Phone numbers become HMAC pseudonyms. Free text is
scrubbed of numbers, emails and self-identification before it is *stored*, not
just before it is sent. Coordinates are rounded to roughly a 1km cell before
anyone outside sees them.

**Corroboration before alarm.** A community broadcast requires either two
independent reporters describing the same thing within 3km and 3 hours, or a
dispatcher's direct confirmation — plus high severity, plus no similar alert in
the last two hours. One person with a phone can raise a report; one person with
a phone cannot make the system shout at a neighbourhood.

**Languages.** English and Yoruba today, Arabic staged and ready. Gemma reasons
in English and the reply is translated out, because the translation service
writes better Yoruba than the model does.

---

## Adding a language

One entry in `config.py` plus environment variables. Arabic is already written;
to turn it on, fill these in and restart:

```
ARABIC_STT_API_URL=...
ARABIC_TTS_BASE_URL=...
ARABIC_TTS_MODEL=...
ARABIC_NLLB_URL=...
```

It then appears in the language menu on its own. `app.py`, `services.py` and the
agent contain no language names at all — they walk the registry. The only thing
worth adding by hand is a set of onboarding strings in `app.py`'s `UI_STRINGS`;
without them that language falls back to English text.

---

## Running it

```bash
cd whatsapp_chatbot
cp .env.example .env          # then fill it in
pip install -r requirements.txt

# Index the official documents (PDF/TXT/MD) the assistant cites
python ingest_corpus.py ./corpus

uvicorn app:app --host 0.0.0.0 --port 5001
```

Or with Docker:

```bash
cd whatsapp_chatbot
docker compose up --build
```

Point the Meta webhook at `https://your-host/whatsapp` and use the same
`WHATSAPP_VERIFY_TOKEN` on both sides. Set `WHATSAPP_APP_SECRET` too — without
it, webhook signatures are not checked and anyone who learns the URL can post
fake reports.

### Two settings to get right before going live

`REPORT_SALT` — a long random string. Reporter pseudonyms are derived from it.
Without one the service still runs, but the pseudonyms become guessable by
anyone who can read the database.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`CORROBORATION_THRESHOLD` — leave it at 2. Setting it to 1 turns the service
from a safety line into a rumour amplifier.

---

## Dispatcher endpoints

Behind HTTP basic auth (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`). With no
password configured they refuse to serve rather than defaulting to open.

| Endpoint | Purpose |
|---|---|
| `GET /reports/data` | Reports, headline stats, recent alerts |
| `GET /reports/export.csv` | Full report log as CSV |
| `POST /reports/{id}/verify` | Confirm a report — may trigger a broadcast |
| `POST /reports/{id}/dismiss` | Reject a report |
| `GET /health` | Liveness |

There is also a CLI:

```bash
python admin.py reports --status submitted
python admin.py show CG-7K3M9Q
python admin.py stats
python admin.py forget <phone-number>     # erase a person's settings + messages
```

---

## Layout

| File | Responsibility |
|---|---|
| `app.py` | WhatsApp webhook, message routing, report pipeline, dashboard |
| `config.py` | Language registry and all tunable thresholds |
| `vision.py` | Gemma image analysis + text classification |
| `anonymiser.py` | EXIF destruction, pseudonyms, text scrubbing |
| `reports.py` | Report store, geofencing, corroboration |
| `alerts.py` | Authority dispatch, community broadcast gating |
| `store.py` | User settings, alert opt-in, conversation log |
| `unified_agent.py` | The conversational agent and its one tool |
| `comguard_prompt.py` | System prompts (text/voice × English/other) |
| `retriever.py` | Ensemble retrieval over the official corpus |
| `ingest_corpus.py` | Builds that corpus from PDFs |
| `services.py` | STT/TTS/translation, wired from the registry |

---

## Known gaps

- **Translations need a native speaker.** The Yoruba onboarding strings in
  `app.py`, the refusal messages in `unified_agent.py`, and the glossary seed in
  `nllb_translator.py` are best-effort and marked with ⚠️ in the source. A
  confidently wrong safety instruction is worse than an English one. Drop a
  reviewed `glossary.json` next to `nllb_translator.py` to extend the glossary
  without touching code.
- **Reports without a location cannot corroborate or be corroborated.** This is
  deliberate — there is no way to tell whether they describe the same incident
  or one 200km away — but it does mean a location-less report will never trigger
  an alert on its own.
- **Authority webhooks are unconfigured by default.** Reports are stored and
  visible on the dashboard, just not pushed anywhere until
  `AUTHORITY_WEBHOOK_URL` (or a per-category override) is set.
- **The corpus ships empty.** Until `ingest_corpus.py` has been run, the
  assistant cannot cite official sources and will say so rather than guess.
