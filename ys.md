Track: Primary – Safety, Reporting & Protection;  Secondary – Transparency & Accountability

Problem: In many African communities, people face two recurring dangers. The first is sudden physical crises such as flooding, collapsed roads or bridges, fire, blocked routes, or rising tension. The second is everyday scams and hazards such as fake public notices, fraudulent tax collectors, unlawful checkpoints, or forged receipts. In both cases information is slow, unreliable, or spreads as rumour. Many people, especially low-literacy users, cannot easily understand notices or explain what they are seeing. There is no simple, trusted way for ordinary people to report a problem , stay protected, and help alert others quickly. This information gap increases risk and makes it harder for communities to respond calmly and effectively.

Solution: ComGuard is a WhatsApp-based AI assistant that lets anyone report a crisis or scam by sending a photo, a voice note explaining the issue, and optional location. Users can speak or receive replies in English, Arabic, Yoruba, Pidgin and other regional African languages. The system analyses the photo and voice note, protects the reporter’s identity, gives the individual clear next steps in simple language they understand, forwards an anonymised summary to the relevant trusted authority or government agency, and can trigger real-time safety alert broadcasts to nearby residents when the situation is serious or confirmed.

Why WhatsApp: WhatsApp is already the single app where hundreds of millions of Africans communicate every day. In Nigeria it is used by roughly 95–98 percent of internet users, and across many African markets adoption among connected people exceeds 90 percent. Building on WhatsApp means people do not need to download a new application and can report problems with the tools they already know how to use.

Information sources: The primary inputs are user-submitted photos and voice notes. Vision analysis identifies the type of hazard or reads text on notices. Speech understanding captures context in local languages. Optional location helps route alerts. Indexed official municipal tax codes, state gazettes, penal law PDFs, and public emergency agency (SEMA) bulletins stored in a local vector database.

Approach to trust and accuracy: Reporter identity is protected by stripping personal phone numbers and image metadata before any sharing occurs. The system first analyses the submitted photo and voice note, including checks for AI-generated or synthetic images, to reduce pure rumour. Responses use clear, calm language so users understand what is based on the evidence they provided. Multi-source corroboration is required before community-wide alerts are sent: alerts are triggered only when two or more independent reports are received from the same geofenced area, or upon direct verification by an authority dispatcher. When a report is serious, an anonymised summary can also be forwarded to the relevant trusted authority. The system prioritises actionable guidance over speculation and avoids presenting unverified claims as fact.

Use of AI tools: Gemma Vision performs image OCR, scene classification (for example, flood severity), and real-versus-synthetic photo verification. A ChromaDB vector pipeline indexes municipal laws and emergency procedures so responses can be grounded through retrieval-augmented generation. Whisper and local text-to-speech convert incoming speech to text and synthesise short audio replies in English, Arabic, and regional dialects including Yoruba and other African languages. A FastAPI backend manages asynchronous webhooks, metadata stripping, vector search, and the routing of anonymised JSON payloads to authorities and alert channels. AI coding tools like Claude and AntiGravity were used to build and iterate on the working proof of concept.



This part dosnot ned to e sayig send me a phot of hat yu are siigni g:   ✅ You're set up.

Send me:
📷 a photo of what you're seeing
🎤 a voice note explaining it
📍 your location (tap 📎 → Location) so I can route it correctly

You can send them one at a time — I'll put them together.

If someone is in danger right now, call 112 (national emergency) or 767 (Lagos LASEMA) first.

it shodl act like a governma t officail,ice thi s fro govenrt , by sayign , it will hel them se the report to the rght autheoty,  not just tll tme to report  tell tem to report themselves , the esse fo trh apps to make theat iesir forth users 

Alos, it isnt ackwddging lcoati , the claktn , is to help the use sne the deatilad and to toehr proel andto olaos govennt offcay , not syaing the cloation to sned future issues if somethig happende d, Tht is totally worng .. It is  to alert other users so they will vaodi the place forthemto stay safe ,,, notthe current oen you wote , alos 


remove parralle request for eleven las sedf the tst at once :  ssages "HTTP/1.1 200 OK"
2026-09-20 06:57:17 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:57:41 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:57:41 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:57:56 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:58:25 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
2026-09-20 06:58:25 INFO:__main__:⚡ Acknowledged wamid.HBgNMjM0ODAyMDgxMjUyMxUCABIYIEFDNDNCRTQ1ODg2QzNGMjJFNUU1Mzg0MDZEQkU5MTk5AA== from 2348020812523
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:58:25 INFO:__main__:[bg] image from 2348020812523 (arabic)
2026-09-20 06:58:27 INFO:httpx:HTTP Request: POST https://graph.facebook.com/v20.0/1269648222904797/messages "HTTP/1.1 200 OK"
2026-09-20 06:58:27 WARNING:anonymiser:⚠️ REPORT_SALT is not set — reporter pseudonyms are guessable by anyone who can read the database. Set REPORT_SALT to a long random string.
2026-09-20 06:58:27 INFO:reports:📝 Draft report CG-XTNTY9 opened for CG-A7CE225A
2026-09-20 06:58:29 INFO:httpx:HTTP Request: GET https://graph.facebook.com/v20.0/1039133822481194 "HTTP/1.1 200 OK"
2026-09-20 06:58:40 WARNING:__main__:Image download attempt 1 failed: [Errno 11001] getaddrinfo failed
2026-09-20 06:58:43 INFO:httpx:HTTP Request: GET https://graph.facebook.com/v20.0/1039133822481194 "HTTP/1.1 200 OK"
2026-09-20 06:58:44 INFO:httpx:HTTP Request: GET https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1039133822481194&source=getMedia&ext=1789884223&hash=ATyxMr__p2mIYBX39cwAUB8Cq4gllcNN8yHPYgKkiI3A1g "HTTP/1.1 200 OK"
2026-09-20 06:58:45 INFO:anonymiser:🧼 Image metadata stripped (exif=False, gps=False, 328647 → 298363 bytes)
2026-09-20 06:58:47 INFO:httpx:HTTP Request: POST https://llama3-8b.publicaai.com/v1/chat/completions "HTTP/1.1 200 OK"
2026-09-20 06:58:47 INFO:vision:👁️ Vision: category=structural_collapse severity=critical authenticity=likely_real(0.95) document=False danger=True
2026-09-20 06:58:47 INFO:unified_agent:🧭 VOICE | العربية | no image
2026-09-20 06:58:48 INFO:httpx:HTTP Request: POST https://llama3-8b.publicaai.com/v1/chat/completions "HTTP/1.1 200 OK"
2026-09-20 06:58:50 INFO:unified_agent:💾 Saved 2 message(s) for 2348020812523
2026-09-20 06:58:50 INFO:reports:📨 Report CG-XTNTY9 submitted — structural_collapse (critical)
2026-09-20 06:58:50 INFO:alerts:📭 No authority endpoint for 'structural_collapse' — report CG-XTNTY9 stored for dashboard review only
2026-09-20 06:58:50 INFO:reports:🔗 Corroboration for CG-XTNTY9: False (1 independent reporters (need 2))
2026-09-20 06:58:50 INFO:elevenlabs_tts:🔊 ElevenLabs TTS (sync): 21 chars (eleven_multilingual_v2)
2026-09-20 06:58:50 INFO:elevenlabs_tts:🔊 ElevenLabs TTS (sync): 42 chars (eleven_multilingual_v2)
2026-09-20 06:58:50 INFO:elevenlabs_tts:🔊 ElevenLabs TTS (sync): 64 chars (eleven_multilingual_v2)
2026-09-20 06:58:50 INFO:elevenlabs_tts:🔊 ElevenLabs TTS (sync): 38 chars (eleven_multilingual_v2)
2026-09-20 06:58:50 INFO:elevenlabs_tts:🔊 ElevenLabs TTS (sync): 102 chars (eleven_multilingual_v2)
2026-09-20 06:58:51 INFO:elevenlabs_tts:🔊 ElevenLabs TTS (sync): 24 chars (eleven_multilingual_v2)
2026-09-20 06:58:57 INFO:httpx:HTTP Request: POST https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL "HTTP/1.1 429 Too Many Requests"
2026-09-20 06:58:57 WARNING:__main__:⚠️ TTS failed for one sentence: ElevenLabs returned 429: {"detail":{"type":"rate_limit_error","code":"concurrent_limit_exceeded","message":"Too many concurrent requests. Your current subscription is associated with a maximum of 2 concurrent requests (running in parallel). This is done such that a single user does not overwhelm our systems and affect other
2026-09-20 06:58:58 INFO:httpx:HTTP Request: POST https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL "HTTP/1.1 429 Too Many Requests"
2026-09-20 06:58:58 INFO:httpx:HTTP Request: POST https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL "HTTP/1.1 429 Too Many Requests"
2026-09-20 06:58:58 INFO:httpx:HTTP Request: POST https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL "HTTP/1.1 429 Too Many Requests"
2026-09-20 06:58:58 WARNING:__main__:⚠️ TTS failed for one sentence: ElevenLabs returned 429: {"detail":{"type":"rate_limit_error","code":"concurrent_limit_exceeded","message":"Too many concurrent requests. Your current subscription is associated with a maximum of 2 concurrent requests (running in parallel). This is done such that a single user does not overwhelm our systems and affect other
2026-09-20 06:58:58 WARNING:__main__:⚠️ TTS failed for one sentence: ElevenLabs returned 429: {"detail":{"type":"rate_limit_error","code":"concurrent_limit_exceeded","message":"Too many concurrent requests. Your current subscription is associated with a maximum of 2 concurrent requests (running in parallel). This is done such that a single user does not overwhelm our systems and affect other
2026-09-20 06:58:58 WARNING:__main__:⚠️ TTS failed for one sentence: ElevenLabs returned 429: {"detail":{"type":"rate_limit_error","code":"concurrent_limit_exceeded","message":"Too many concurrent requests. Your current subscription is associated with a maximum of 2 concurrent requests (running in parallel). This is done such that a single user does not overwhelm our systems and affect other
2026-09-20 06:58:58 INFO:httpx:HTTP Request: POST https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL "HTTP/1.1 200 OK"
2026-09-20 06:58:58 INFO:elevenlabs_tts:✅ ElevenLabs TTS: 30973 bytes
2026-09-20 06:58:58 INFO:httpx:HTTP Request: POST https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL "HTTP/1.1 200 OK"
2026-09-20 06:58:58 INFO:elevenlabs_tts:✅ ElevenLabs TTS: 149673 bytes
2026-09-20 06:59:01 INFO:httpx:HTTP Request: POST https://graph.facebook.com/v20.0/1269648222904797/media "HTTP/1.1 200 OK"
2026-09-20 06:59:04 INFO:httpx:HTTP Request: POST https://graph.facebook.com/v20.0/1269648222904797/messages "HTTP/1.1 200 OK"
2026-09-20 06:59:06 INFO:httpx:HTTP Request: POST https://graph.facebook.com/v20.0/1269648222904797/messages "HTTP/1.1 200 OK"
2026-09-20 06:59:07 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:07 INFO:httpx:HTTP Request: POST https://graph.facebook.com/v20.0/1269648222904797/messages "HTTP/1.1 200 OK"
2026-09-20 06:59:08 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:08 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:08 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:09 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:09 INFO:httpx:HTTP Request: POST https://graph.facebook.com/v20.0/1269648222904797/messages "HTTP/1.1 200 OK"
2026-09-20 06:59:10 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:10 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:10 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:11 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:11 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:35 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK
2026-09-20 06:59:54 WARNING:whatsapp.hmac_validator:WHATSAPP_APP_SECRET not set — skipping HMAC validation. Set this to your Meta App Secret to secure the webhook endpoint.
INFO:     197.159.74.29:0 - "POST /whatsapp HTTP/1.1" 200 OK

for forfirs dusaster  right aurheoty wll be norifed  wwn rreplyig to users , for bridgge collapres an soe on right atheiry will be notiifed ad so on    
so i dhodlfnt just say you can report to teh auetut , kt shodul say shoulf flag this to th rght auhrruty .. for exlation , ot just say wi lll flag this to thememtm the agecy immeduaryyl 