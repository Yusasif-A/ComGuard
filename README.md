# ComGuard

A WhatsApp AI assistant that lets anyone report a crisis or a scam with a photo,
a voice note and their location — and warns nearby residents when it is
confirmed.

**Track:** Safety, Reporting & Protection

---

## The problem

African communities face two recurring dangers: sudden physical crises
(flooding, collapsed roads and bridges, fire, blocked routes, rising tension)
and everyday scams (fake public notices, fraudulent tax collectors, unlawful
checkpoints, forged receipts). Information is slow, unreliable, or spreads as
rumour, and low-literacy users often cannot read a notice or explain what they
are seeing. There is no simple, trusted way to report a problem, stay protected,
and alert others quickly.

## The solution

Send ComGuard a photo, a voice note, and optionally your location. It:

1. **Analyses** the photo — reads the text on a notice, identifies the hazard,
   and checks whether the image is AI-generated or altered.
2. **Protects you** — strips phone numbers and image metadata before anything is
   stored or shared.
3. **Tells you what to do** in clear, calm language, grounded in indexed
   municipal tax codes, state gazettes, penal law and SEMA bulletins.
4. **Forwards an anonymised summary** to the relevant authority.
5. **Alerts nearby residents** — but only after two or more independent reports
   from the same area, or direct verification by a dispatcher. One report never
   triggers a public alarm.

Speak or receive replies in **English**, **Yoruba** and **Arabic**, by text or
voice. English and Yoruba use self-hosted speech models; Arabic is heard through
Deepgram and spoken through ElevenLabs.

**Why WhatsApp:** roughly 95–98% of Nigerian internet users are already on it.
Nobody has to install anything new.

---

## Built with

- **Gemma Vision** — OCR, hazard classification, real-vs-synthetic image checks
- **ChromaDB + RAG** — grounding in official municipal and emergency records
- **Whisper / Deepgram / ElevenLabs** — speech in and out, per language
- **FastAPI** — async webhooks, metadata stripping, anonymised routing
- **MongoDB** — reports, corroboration, conversation memory

---

## Run it

```bash
cd whatsapp_chatbot
cp .env.example .env          # fill in your keys
pip install -r requirements.txt

python ingest_corpus.py ./corpus     # index official documents
uvicorn app:app --host 0.0.0.0 --port 5001
```

Or with Docker:

```bash
cd whatsapp_chatbot && docker compose up --build
```

Point the Meta webhook at `https://your-host/whatsapp`.

Generate a `REPORT_SALT` before going live — reporter anonymity depends on it:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Dispatcher view

Read-only endpoints behind HTTP basic auth:

| Endpoint | Purpose |
|---|---|
| `GET /reports/data` | Reports, stats, recent alerts |
| `GET /reports/export.csv` | Full log as CSV |
| `POST /reports/{id}/verify` | Confirm a report |
| `POST /reports/{id}/dismiss` | Reject a report |
