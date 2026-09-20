# ComGuard

A WhatsApp AI assistant that lets anyone report a crisis or a scam with a photo,
a voice note and their location — and warns nearby residents when it is
confirmed.

**Track:** Primary — Safety, Reporting & Protection. Secondary — Transparency &
Accountability.

---

## The problem

African communities face two recurring dangers: sudden physical crises
(flooding, collapsed roads and bridges, fire, blocked routes, rising tension)
and everyday scams (fake public notices, fraudulent tax collectors, unlawful
checkpoints, forged receipts). Information is slow, unreliable, or spreads as
rumour, and low-literacy users often cannot read a notice or explain what they
are seeing. There is no simple, trusted way to report a problem, stay protected,
and alert others quickly.

## What it does

Send ComGuard a photo, a voice note, and optionally your location. It:

1. **Analyses** the photo — reads the text on a notice, identifies the hazard,
   and checks whether the image is AI-generated or altered.
2. **Protects the reporter** — strips phone numbers and image metadata before
   anything is stored or shared.
3. **Tells them what to do** in clear, calm language, grounded in official
   records rather than guesswork.
4. **Flags the report to the right agency** on the reporter's behalf, as an
   anonymised summary. The point of the service is that people do not have to
   work out who to contact themselves.
5. **Alerts nearby residents so they can avoid the area** — but only after two
   or more independent reports from the same place, or direct verification by a
   dispatcher. One report never triggers a public alarm.

Replies come in **English**, **Yoruba** and **Arabic**, by text or voice. A
photo sent on its own is answered with a voice note, because someone who sends
only a picture often cannot type or read the reply.

**Why WhatsApp:** roughly 95–98% of Nigerian internet users are already on it.
Nobody has to install anything new.

---

## Information sources

The primary inputs are the user's own photo and voice note. Vision analysis
identifies the hazard or reads the text off a notice; speech understanding
captures the context in the user's language; optional location routes the report
and defines who gets warned.

Answers about what is official — a levy amount, a checkpoint's lawfulness, what
a genuine notice must carry — are grounded by retrieval over indexed municipal
tax codes, state gazettes, penal law and public emergency agency (SEMA)
bulletins held in a local vector database. When the records hold no answer, the
assistant says so rather than inventing one.

---

## Trust and accuracy

Reporter identity is protected before anything is stored or shared: phone
numbers become non-reversible pseudonyms and image metadata, including GPS, is
destroyed. Submitted photos are checked for signs of being AI-generated or
edited, so a recycled image does not become a confirmed incident.

Community-wide alerts require multi-source corroboration — two or more
independent reporters describing the same thing in the same area within a short
window, or direct verification by an authority dispatcher. Responses state what
is evidenced and what is not, and unverified claims are never presented as fact.

---

## Built with

- **Gemma Vision** — image OCR, hazard and severity classification, and
  real-versus-synthetic photo verification
- **ChromaDB retrieval pipeline** — indexes municipal law and emergency
  procedure so answers are grounded through RAG
- **Whisper, Deepgram and ElevenLabs** — speech to text and text to speech, per
  language
- **FastAPI** — asynchronous webhooks, metadata stripping, vector search, and
  routing of anonymised payloads to agencies and alert channels
- **MongoDB** — reports, corroboration and conversation memory

Claude and AntiGravity were used to build and iterate on the working proof of
concept.
