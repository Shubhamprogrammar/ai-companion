# Implementation status

| Phase | Scope | Status |
|------|-------|--------|
| 1 | Foundation: config, DB, Redis, Alembic, health, Docker, tests | ✅ Implemented and tested |
| 2 | Auth and user management | ✅ Implemented and tested |
| 3 | AI conversation engine (OpenAI, personality, context) | ✅ Implemented and tested |
| 4 | Voice engine (STT, TTS, complete audio turn) | ✅ Implemented and tested (Stage 1: request/response) |
| 5 | Memory engine (pgvector, retrieval, summaries) | ✅ Implemented and tested |
| 6 | Agent tools (registry, reminders, tasks, confirmations) | ✅ Implemented and tested |
| 7 | WebSocket voice | ✅ Implemented and tested (client-side turn detection; see notes) |
| 8 | Production readiness | ✅ Implemented and tested (see [deployment](deployment.md)) |

## Phase 1: what exists

- `GET /api/v1/health`: liveness
- `GET /api/v1/ready`: readiness (PostgreSQL + pgvector, Redis)
- Environment-based settings with validation
- Async SQLAlchemy engine, session dependency, naming conventions
- Async Alembic; migration `0001` enables pgvector
- Structured JSON logging, request IDs, access-log latency, redaction of sensitive fields
- Uniform, safe error envelope
- Docker Compose: `pgvector/pgvector:pg17`, `redis:7-alpine`, API (non-root, healthcheck,
  migrates on start)
- 24 tests: unit, API, and integration against real Postgres and Redis

## Phase 2: what exists

- Register, login, refresh, logout, and `/auth/me`
- `/users/me` (GET/PATCH), `/users/me/preferences` (GET/PATCH)
- Argon2id password hashing (in a worker thread, with transparent rehash on login).
  Logins against unknown emails take the same time as wrong passwords.
- HS256 JWTs with `sub`, `jti`, `type`, `iss`, `iat` and `exp` all required. Access and refresh
  tokens can't be used in place of each other.
- Refresh-token **rotation** with **reuse detection**: replaying a rotated-out token revokes
  all of that user's sessions. Rows are locked during a refresh, so concurrent refreshes
  of the same token are serialised.
- Logout revokes the access token (Redis deny-list with TTL) and the refresh token (DB).
- Per-IP Redis fixed-window rate limit on register, login and refresh (`AUTH_RATE_LIMIT_PER_MINUTE`)
- Audit events on the `app.audit` logger: `user.registered`, `auth.login_succeeded`,
  `auth.login_failed`, `auth.logout`, `auth.refresh_token_reuse_detected`,
  `user.privacy_settings_changed`
- Migration `0002`: `users`, `user_profiles`, `user_preferences`, `refresh_tokens`
- 76 tests in total (45 unit/API without infrastructure, 31 against real Postgres/Redis)

## Design decisions and deviations from the original spec

| Change | Reason |
|---|---|
| Added a `refresh_tokens` table | Needed for revocation on logout, rotation and reuse detection. Only the `jti` is stored, never the token |
| Added `users.is_active` | Lets an account be disabled without deleting its data |
| Added `user_preferences.conversation_retention_days` | Spec requires users to control conversation retention. `null` = keep. Enforcement arrives with conversations (Phase 3) |
| `register` returns the user, not tokens | Keeps credential issuance in one place (`/login`) |
| `logout` takes an optional `{refresh_token}` body | Access tokens are stateless; the client must say which refresh token to revoke |
| Enums are stored as VARCHAR, not native PG enums | Adding personalities or languages needs no migration; the API schemas enforce validity |
| `preferred_language` adds `auto` (default) | Follow the language the user speaks, rather than forcing one |
| `voice` is validated by pattern only | Real voice names are checked against the TTS provider in Phase 4. `null` = server default |
| Rate limiter **fails open** if Redis is down | An outage shouldn't block all logins. Access-token revocation checks **fail closed** (503) |

## Known limitations

- Expired `refresh_tokens` rows are deleted by the maintenance job (Phase 8).
- No email verification, password reset or password change yet. These were not in scope.
- The rate limit is keyed by client IP. Behind a reverse proxy, set `FORWARDED_ALLOW_IPS`
  (see [deployment](deployment.md#client-ips-and-forwarded_allow_ips)). Phase 8 also added a
  per-account limit on failed logins.

## Phase 3: what exists

- **Conversations:** full CRUD, archive status, offset pagination, and ownership-scoped
  lookups (another user's conversation returns 404, not 403).
- **Messages:** role, content, type and JSONB metadata (model, tokens, latency, detected
  language). Ordered by `clock_timestamp()`, so a turn written in one transaction stays ordered.
- **`POST /chat`** and **`POST /chat/stream`** (SSE via FastAPI's native `EventSourceResponse`):
  - A new conversation is created on the first message and auto-titled from it.
  - The turn is saved only after the model replies, so a failure leaves nothing behind.
  - The read transaction is closed before the model call, so no DB connection is held while waiting.
- **OpenAI Responses API** (`openai` 3.58), behind an `LLMProvider` protocol:
  - **Privacy:** `store=false`. An HMAC `safety_identifier` is sent instead of the user id.
  - **Settings:** `reasoning` is only sent when configured, `max_output_tokens` is configurable.
  - **Retries:** the SDK's own exponential-backoff retries are used. This is safe because
    generation has no side effects.
  - **Errors:** all provider errors, timeouts and empty or failed outputs become a safe `503 ai_unavailable`.
  - **Logging:** latency and token counts are logged, never message content.
- **Personality engine:** the `warm_friend` preset is data in `app/ai/personality.py`. Adding a
  style means adding an enum value and a `Personality` entry.
- **System prompt** built from tested sections:
  - **Behaviour:** identity, AI transparency (no claims to be human or to have feelings),
    conversation style rules from the spec, language mirroring, honest capabilities and
    limitations, and care/safety.
  - **Data:** user context (name, timezone, local time) and summary, delimited as data and sanitised.
- **Language detection** (`app/ai/language.py`): a cheap English/Hindi/Hinglish heuristic. It's a
  hint to the model, not a hard rule.
- **Context window:** the most recent N messages that fit a character budget. The newest
  message is always kept.
- **Retention:** `scripts/purge_conversations.py` enforces each user's
  `conversation_retention_days`.
- **Per-user chat rate limit** (`CHAT_RATE_LIMIT_PER_MINUTE`).
- Migration `0003`: `conversations`, `messages`.
- 142 tests, all passing on Python 3.14 and 3.13. A live smoke test (`scripts/live_chat_smoke.py`)
  was run against `gpt-6.2-sol`.

### Phase 3 design notes

| Decision | Reason |
|---|---|
| Responses API, not Chat Completions | OpenAI's current primary API. Supports `instructions`, `store=false`, `safety_identifier`, and the tool calling planned for Phase 6 |
| Intent detection is delegated to the model | A separate classifier call would add latency to every turn. Tool selection (Phase 6) and memory decisions (Phase 5) use model-driven decisions with backend validation |
| No `ai/reasoning.py` / `ai/response.py` modules | No logic exists for them yet. Language detection is in `ai/language.py`, and response parsing is in the provider |
| Conversation titles come from the first message | Free and instant. An LLM-generated title could be added later |
| `has_more` + offset pagination | Simple and sufficient at personal-companion scale |
| Message `metadata` isn't exposed in API responses | It holds internal model and token details |

### Live test observations (`gpt-6.2-sol`)

- **Language:** Hinglish, Hindi (Devanagari) and English were each mirrored correctly turn by turn.
- **Honesty:** it declined to set a reminder instead of pretending, and answered "are you a real
  person?" truthfully.
- **Latency:** about 1.5–3.5 s per reply without streaming. The first request after startup was
  around 7 s, mostly connection setup. Streaming gives faster first output.
- **Fixes made after the first run:** replies included markdown bold, and Hindi replies
  transliterated tech terms. Both are now ruled out in the prompt and verified.

## Phase 4: what exists (voice, Stage 1)

- **`POST /voice/transcribe`:** STT only, nothing persisted.
- **`POST /voice/synthesize`:** TTS streamed to the client as the provider produces it. The first
  chunk is fetched before responding, so provider failures still return a proper 503. Voice and
  speed come from an explicit override, then the user's preference, then `OPENAI_TTS_VOICE`.
- **`POST /voice/turn`:** the complete pipeline: audio → validate → STT → companion (voice mode)
  → TTS → base64 audio + transcript + reply + per-stage timings.
- **Audio validation:**
  - **Format:** detected from magic bytes; filename and content type are never trusted.
  - **Size and content:** the size limit is checked while reading (never more than limit + 1
    bytes is read), and empty or tiny files are rejected.
  - **Oversized requests:** a body-size middleware rejects them by `Content-Length` before
    the body is parsed.
- **Providers:** `SpeechToTextProvider` / `TextToSpeechProvider` protocols, with OpenAI
  implementations (`audio.transcriptions`, `audio.speech`).
  - **STT:** sends `languages` hints (`["en","hi"]`).
  - **TTS:** sends the personality's delivery `instructions` (left out for `tts-1*` models).
    Text over 4096 characters is split at sentence boundaries, including the Hindi danda (।),
    and the chunks are concatenated for mp3, opus, aac and pcm.
- **Speech text cleanup:** markdown, links and emoji are stripped before TTS.
- **Voice mode in the prompt:** short replies (one or two sentences, at most one question),
  nothing unspeakable, read the transcript charitably, and match the spoken register.
- **Preference validation:** `voice` is checked against `OPENAI_TTS_VOICES`. The default voice is
  validated at startup.
- **Audio retention (opt-in):** `LocalAudioStore` writes files with UUID-only paths (0700
  directories, 0600 files) and deletes them when the conversation is deleted or purged.
- **Error codes:** `audio_too_large` 413, `unsupported_audio_format` 415, `invalid_audio` /
  `no_speech_detected` 422, `speech_unavailable` 503.
- **Observability:** STT, TTS and whole-turn latency are logged (`stt_ms`, `llm_ms`, `tts_ms`,
  `total_ms`). Transcripts and audio are never logged.
- **Tests:** 202, passing on Python 3.14 and 3.13. The STT/TTS providers are tested through the
  real SDK against a mocked transport.

### Phase 4 design notes

| Decision | Reason |
|---|---|
| If TTS fails in `/voice/turn`, return 200 with `audio: null` and `audio_error` | The turn is already saved. Returning an error would make clients retry and duplicate it |
| Base64 audio in the JSON for `/voice/turn` | One response carries the transcript, reply text, audio and timings; it works in Swagger and curl. Raw streamed audio is what `/voice/synthesize` and the WebSocket (Phase 7) are for |
| `gpt-transcribe` + `gpt-4o-mini-tts` + `coral` voice | Current models according to the SDK; the other transcription models have an announced shutdown on 2027-02-26. Voice is configurable per deployment and per user |
| No audio-duration limit beyond file size | That would need audio decoding (ffmpeg). The size limit bounds cost |

### Live test observations (`gpt-transcribe`, `gpt-4o-mini-tts`/coral, `gpt-6.2-sol`)

- **English and Hindi:** both transcribed almost word for word in a TTS → STT round trip.
- **Hinglish is always transcribed in Devanagari.** "Yaar, aaj office mein..." comes back as
  "यार, आज ऑफिस में...". Neither `prompt` nor `languages` changes this. It's handled in the prompt:
  Mira is told the script says nothing about register and should match casual Hinglish. Transcripts
  shown in a UI will appear in Devanagari. Transliterating to Roman script would need an extra step.
- **Latency per complete turn**, after limiting voice replies to one or two sentences: about
  6.3–7.3 s, made up of STT about 0.8–1.4 s, LLM about 2.7–3.7 s and TTS about 2.2–2.9 s. Before the
  length rule, turns took 8–10 s with up to about 25 s of audio. `OPENAI_REASONING_EFFORT=low`
  made no measurable difference.
- **Next latency step:** Stage 2 (Phase 7) streams the LLM output into TTS sentence by sentence,
  so audio can start after the first sentence instead of after the whole reply.

## Phase 5: what exists (memory engine)

**Short-term memory:** the current conversation's recent messages (`ai/context.py`) plus a
rolling **conversation summary** of the turns that have scrolled out of the context window. The
summary is updated in the background once `SUMMARY_TRIGGER_MESSAGES` messages are outside
the window (`conversations.summarized_message_count` tracks how far it covers).

**Long-term memory:** the `memories` table, holding content, category, importance 1–5, source,
the source conversation and a `vector(1536)` embedding.

- **Recall** (before each reply, in the request path):
  - **Matches:** the message is embedded and the user's own memories are searched by cosine
    distance (cutoff `MEMORY_MAX_DISTANCE=0.78`). The results are merged with up to
    `MEMORY_CORE_LIMIT` "core" facts (importance ≥ 4), de-duplicated, and `last_used_at` is updated.
  - **Prompt:** the memories go into the prompt inside a delimited `<user_memories>` data block,
    with instructions to use them naturally and never invent any.
  - **Failure:** if embeddings fail, recall degrades to core facts only and the reply still happens.
- **Extraction** (after the reply, in a background task, never adding latency):
  - **Pre-filter:** trivial messages ("ok", "haha") are skipped unless they contain a "remember"
    hint, including *yaad rakhna* and *याद रखना*.
  - **Proposal:** the model returns strict JSON (Responses API `json_schema`) with `add`/`update`
    operations, given the user's existing related memories under aliases `m1…m8`. Real ids never
    reach the model; only aliases from that candidate set are accepted, and updates re-check
    ownership.
  - **Validation:** the backend checks action, category (the summary category is not allowed),
    content length (3–500), importance (an int 1–5), de-duplication and a cap of 5 per turn.
  - **De-duplication:** a new memory within `MEMORY_DEDUP_DISTANCE` of an existing one updates
    that memory instead (importance is only ever raised).
  - **Policy, written into the extraction prompt:** keep stable facts, preferences, goals,
    projects, decisions, routines and explicit "remember this" requests. Never store small
    talk, passing moods, the assistant's words, credentials (PIN, OTP, passwords, card and ID
    numbers), or detailed health information unless explicitly asked.
  - **Limit:** `MEMORY_MAX_PER_USER` (default 500).
- **Summaries as memory:** when memory is on, each conversation summary is also stored as a
  searchable `conversation_summary` memory. That enables "what did we talk about last time?"
  across conversations.
- **User control:**
  - **API:** `/memories` list, filter, create, edit, delete, delete all (`?confirm=true`) and search.
  - **Switches:** the per-user `memory_enabled` preference and the global `MEMORY_ENABLED` switch.
  - **Memory off:** no recall, no embedding calls, no extraction, and the prompt tells Mira that
    memory is off, so she says so if asked to remember something.
- **Deletion:**
  - **Conversations:** deleting a conversation, or its retention purge, deletes its summary
    memory. Facts extracted from it are kept, because they're about the user, and the
    reference is set to null.
  - **Users:** deleting a user cascades to all their memories.
- **Background jobs:** `core/background.py` has an in-process runner with bounded concurrency
  and drains in-flight jobs at shutdown. Jobs use their own DB session. Summarisation locks the
  conversation row only when it is actually about to summarise.
- **Model:** an optional `OPENAI_MEMORY_MODEL` uses a cheaper model for extraction and summaries.
- **Tests:** 271, passing on Python 3.14 and 3.13. Similarity search, user scoping,
  de-duplication and summaries run against real pgvector with deterministic bag-of-words embeddings.

### Phase 5 design notes

| Decision | Reason |
|---|---|
| Exact vector search (no HNSW index) | Each query covers one user's few hundred rows via the `user_id` index: exact and fast. HNSW with a per-user filter is approximate and can return too few rows. Revisit if a single user grows past roughly 10k memories |
| Extraction runs in-process in the background (no queue) | No new infrastructure (the spec says no Celery). Best-effort is acceptable: a lost job only means one missed memory |
| Memories are stored as English third-person facts | Consistent display and dedup whatever the conversation language. Recall still works for Hinglish and Hindi queries (measured below) |
| The extraction model can't delete memories | Deletion is consequential, so it's left to the user (API) and, in Phase 6, to a confirmation-gated tool |
| No separate `short_term.py` | Short-term memory is the context window plus the conversation summary, both already implemented. Redis-backed session state arrives with WebSocket sessions (Phase 7) |
| Summaries are always maintained, even with memory off | They're part of the *current* conversation's context. Only the cross-conversation summary *memory* respects the memory switch |

### Live test observations (`text-embedding-3-small`, `gpt-6.2-sol`)

- **Extraction:** one Hinglish message ("Main Rohan hoon, Pune se. Backend developer hoon...
  chai bahut pasand hai") became 5 clean memories: name (importance 5), job (4), goal (4),
  city (3) and a preference for chai (3).
- **Correction:** "I moved from Pune to Bengaluru" **updated** the existing memory ("Rohan is
  from Pune and now lives in Bengaluru"). The count stayed at 5, with no stale copy.
- **Secrets:** "My bank PIN is 4321 and my password is hunter2" stored nothing, and Mira advised
  changing them.
- **Recall cutoff, calibrated with real embeddings:** relevant query/memory pairs scored
  0.56–0.74 (e.g. "meri behen ka birthday kab hai?" vs "User's sister Priya's birthday is 5 June"
  scored 0.56), and unrelated pairs scored ≥ 0.80. The initial cutoff of 0.7 missed "drinks → chai"
  (0.73). With 0.78, "suggest an evening snack" brought up chai naturally.
- **Memory off:** in a new conversation Mira didn't know the user's name and said so.
- **Latency:** the recall embedding adds about 0.3–1.2 s before each reply. Extraction and
  summaries add nothing to reply time.

## Phase 6: what exists (agent tools)

- **Tool calling:** OpenAI Responses API function tools in **strict** mode, run in an agent loop
  (`CompanionAgent.run` / `run_stream`).
  - **Limits:** up to `TOOL_MAX_ROUNDS` model ↔ tool round trips. After the last round,
    `tool_choice="none"` forces a text answer, and calls per turn are capped by
    `TOOL_MAX_CALLS_PER_TURN`.
  - **State:** with `store=false`, the model's output items are replayed each round, with
    `include=["reasoning.encrypted_content"]`.
- **Registry:** a closed set of 13 tools, plus `search_web` when configured:
  - **Reminders:** `create_reminder`, `list_reminders`, `update_reminder`, `delete_reminder`
  - **Tasks:** `create_task`, `list_tasks`, `update_task`, `delete_task`
  - **Settings:** `get_preferences`, `update_preferences`
  - **Memory:** `find_memories`, `delete_memory`
  - **Confirmation:** `confirm_pending_action`

  `TOOLS_ENABLED` / `TOOLS_DISABLED` switch tools off. There are no tools for arbitrary code,
  SQL or shell commands.
- **One Pydantic model per tool** generates the strict JSON schema the model sees (startup fails
  if a schema isn't strict-compatible) **and** validates every call. Extra fields, bad UUIDs,
  bad dates and bad enums are all rejected.
- **The executor** (`tools/executor.py`):
  - **Pipeline:** unknown tool / bad JSON / invalid arguments → `invalid`; then confirmation
    check; then run; then a `tool_executions` row for **every** call.
  - **What the model gets back:** a structured `succeeded` / `error` / `confirmation_required`
    result, never a raised exception.
  - **Failures:** a failed tool rolls back and reloads the session's user and profile, so the
    turn continues.
- **Ownership:** tools only reach data through user-scoped services. Another user's id gives
  `not_found`, with no information leaked and no confirmation offered.
- **Time handling:**
  - **Prompt:** the model receives the user's local time and timezone, and is told to resolve
    relative phrases and ask when a time is vague.
  - **Tool arguments:** use local `YYYY-MM-DDTHH:MM`, which the backend localises with the
    user's IANA timezone and validates (not in the past, at most 5 years ahead).
  - **Results:** return the resolved time as `Wed 25 Nov 2026, 09:00 (Asia/Kolkata)`.
- **Confirmations:**
  - **Which tools:** `delete_*` and `update_preferences(memory_enabled=false)` create an
    `awaiting_confirmation` execution (with a summary and an expiry) instead of running.
  - **Approving:** via `POST /tools/confirmations/{id}/approve`, or `confirm_pending_action` in
    chat, which only works if the **user's own message** is an explicit affirmative (EN, Hindi
    and Hinglish, with a negation guard) and the confirmation belongs to this conversation.
  - **Context and records:** pending confirmations are shown to the model in the next turn's
    prompt, and expired ones are marked `expired`.
- **Web search:** the `WebSearchProvider` interface. `OpenAIWebSearch` uses the Responses API's
  built-in `web_search` tool (billed per search by OpenAI; no extra key needed).
  - **Off by default:** with `WEB_SEARCH_PROVIDER=none` the tool isn't offered, and the prompt
    says live lookups aren't possible.
  - **Sources:** returned from the answer's citations, falling back to the pages the search
    consulted.
- **REST:** full CRUD for `/reminders` and `/tasks`, plus `/tools` (list, executions,
  confirmations, approve, reject).
- **Response changes:**
  - `POST /chat` and `POST /voice/turn` responses include `actions`.
  - SSE adds `tool_started` and `tool` events.
  - Assistant messages record their tool activity in metadata.
- **Tests:** 332, passing on Python 3.14 and 3.13. Tool payloads, multi-round replay, web
  search parsing, every executor rejection path, cross-user attempts, both confirmation paths,
  expiry, the round limit and the streaming tool events are all covered.

### Phase 6 design notes

| Decision | Reason |
|---|---|
| Added `/tools/*` endpoints, `GET /tasks/{id}` and `from`/`to` reminder filters | The tool engine and confirmations need a client-facing API, and it keeps parity with reminders |
| A conversational "yes" is checked against the user's actual text | Otherwise the model, or an injected instruction, could approve destructive actions itself |
| Executed actions commit immediately; messages are saved after the final reply | If the model fails after a tool ran, the action still stands, and it's visible in `/tools/executions` |
| Reminders are stored without delivery | No client or push channel exists yet. Real-time delivery could ride on the Phase 7 WebSocket |
| The confirmation-tool wrapper and the confirmed action are both audited | Gives a complete trail of who approved what, and how |

### Live test observations (`gpt-6.2-sol`, Asia/Kolkata)

- **Clear reminder:** "Remind me tomorrow at 9 AM to study Python" was saved as `03:30Z`, which
  is 09:00 IST. Mira's reply quoted the resolved date.
- **Hinglish reminder:** "Kal shaam 6 baje gym jaana hai, yaad dila dena" was saved for 18:00 the
  next day, and confirmed in Hinglish.
- **Ambiguity:** "Remind me later to call mom" → she asked for a day and time, and nothing was
  created. "Due next Friday" → she asked whether that meant 27 Nov or 4 Dec.
- **Honesty:** asked to "mark the FastAPI task done" when it had never been created, she checked
  the list and said so instead of pretending.
- **Confirmation:** "Gym wala reminder delete kar do" → she listed reminders, then proposed the
  deletion ("permanently delete kar doon?") without doing it. "Haan, kar do" → it was deleted.
  The final state confirms it.
- **Limits:** "Can you call my mom?" → she can't, and offered a reminder instead. A weather
  question with search off → she can't check live data.
- **Web search** (provider test): returned the current Pune weather with timestamps and a
  source URL.

## Phase 7: what exists (real-time WebSocket voice)

- **Endpoint:** `/api/v1/ws/voice/{conversation_id|new}`. The full protocol is in
  [websocket-protocol.md](websocket-protocol.md).
- **Authentication:** via `session.start{token}` (browsers) or an `Authorization` header (native
  clients), never in the URL. A per-user concurrent-session cap is held in Redis.
- **Limits:** auth, idle and max-duration timeouts, frame and utterance size limits, and the
  shared per-user chat rate limit.
- **Turn pipeline:** a streamed utterance (any accepted container, or raw `pcm16` wrapped into
  WAV) → STT with **streamed transcript output** → reply streamed token by token, with tool
  events → a sentence chunker → per-sentence **streaming TTS** sent as binary frames while the
  rest is generated → `response.done` with timings.
- **Barge-in:** `speech.start`, `text.message` or `response.cancel` cancels the LLM and TTS
  immediately. The partial reply is saved with `interrupted: true` (and skipped as empty in
  later context), and `response.cancelled` reports what was saved.
- **Typed input:** `text.message` works in a voice session, and the reply stays speakable.
  `text_only` sessions skip TTS.
- **Tools and confirmations:** `tool.started` / `tool.completed` events, plus
  `confirmation.respond`.
- **Robustness:**
  - **Per-turn DB sessions:** each turn gets its own session, so a cancellation can't corrupt
    shared state.
  - **Serialised sends:** the turn task and the receive loop write to the socket through one lock.
  - **Failures:** a TTS failure keeps the text reply flowing, and invalid messages are reported
    without closing the session.
- **Observability:** session opened/closed (reason, turns, interruptions, duration), per-turn
  `stt_ms` / `first_text_ms` / `first_audio_ms` / `total_ms`, and interruptions. No transcript
  or reply content is logged.
- **Tests:** 357, passing on Python 3.14 and 3.13. These include 25 WebSocket and streaming
  tests on the same event loop via `httpx-ws`, covering auth, handshake errors, ownership,
  session caps, a full streamed turn, multi-turn, barge-in with partial save, cancellation,
  pcm16, text-only, typed input, tool confirmations, TTS failure and the protocol errors. The
  async scan found no warnings.

### Phase 7 design notes

| Decision | Reason |
|---|---|
| Client-driven turn-taking (`speech.start` / `speech.end`) | Reliable and simple. Browsers and mobile SDKs have good VAD. Server-side VAD is planned |
| STT runs on the complete utterance (transcript output streamed) | The STT API transcribes files. Measured: the first delta is about as late as the full text (1.55 s vs 1.69 s), so the streamed output is for live captions, not latency. Incremental STT needs the Realtime API (planned) |
| The first TTS segment may break at a comma (≥ 24 chars); later ones at sentence ends | Measured: the gap from first text to first audio went from ~2.6 s to ~2.0 s, and natural phrasing is kept for the rest |
| Per-turn DB session | Cancelling a turn mid-query must not poison a session that's shared across turns |
| `session.start` must come first, even when a header is used | One handshake path, and it carries the audio formats |
| `GET`-style dependency getters now take `HTTPConnection` | So the same overridable dependencies serve both HTTP and WebSocket routes |

### Live test observations (real WebSocket against Docker)

| Turn | Transcript | First text | **First audio** | Done |
|---|---|---|---|---|
| Hinglish (warm) | 1.1 s | 2.9 s | **4.9 s** | 7.8 s |
| English, barged in at first audio | — | — | 3.8 s | cancelled in **55 ms** |
| Hindi, after barge-in | 1.2 s | 2.5 s | **4.7 s** | 8.6 s |
| Typed + tool call (reminder 08:00 IST → `02:30Z`) | — | 3.1 s | **4.9 s** | 5.4 s |

- **Compared with Phase 4:** `POST /voice/turn` gave no audio until 6.3–7.3 s.
- **The first request after a restart is slow:** first text at 6.8 s, which is the provider's
  cold start.
- **TTS first byte is provider-bound:** 1.0–1.7 s for mp3, pcm and opus, with or without
  instructions, so changing the format doesn't help.
- **Going below about 3.5 s** would need speech-to-speech (OpenAI Realtime API), noted as a
  future option.

## Phase 8: what exists (production readiness)

**Security review.** An independent reviewer read the whole codebase, and I fixed what it found:

| Finding | Severity | Fix |
|---|---|---|
| The model could request a confirmation and approve it in the **same turn**. Only a "yes"-like word anywhere in the user's message was checked, so a prompt injection (e.g. from a web result) could delete something when the user said "ok, search …" | Medium–high | A confirmation can only be approved in a **later** turn than the one that asked for it, and the answer must be short (≤ 8 words), affirmative and not negative. The conversation check now covers rejection too |
| Bodies without `Content-Length` (chunked) were unbounded: memory and disk exhaustion before authentication | Medium | `BodySizeLimitMiddleware` counts bytes as they arrive and returns a 413 `request_too_large` |
| Behind Docker, all clients shared the bridge gateway's IP, so one attacker could lock everyone out of login | Medium | `FORWARDED_ALLOW_IPS` is **required** in the production Compose file and documented, and failed logins are also limited **per account** (`LOGIN_MAX_FAILURES` within `LOGIN_LOCKOUT_MINUTES`; the key is a hash of the email, and unknown emails are treated the same way) |
| The WebSocket had no per-IP connection limit, and frames up to 16 MiB were accepted before the app's size check | Medium | `WS_CONNECT_RATE_LIMIT_PER_MINUTE` is checked before authentication; Uvicorn gets `--ws-max-size`; text frames are size-checked; the first frame is capped at 16 KB before it is parsed |
| WebSocket sessions outlived logout and token expiry | Low | Sessions close with 4401 (`token_expired` / `token_revoked`), the deny-list is re-checked every 15 s, and the new `auth.refresh` message lets clients extend a session |
| Session slots were a bare counter that could stay stuck for about 31 minutes after a crash | Low | A Redis sorted set of per-session leases (60 s, renewed while the session is alive) |
| SQLAlchemy errors would log bound parameters (message text) | Low | `hide_parameters=True` |
| Injected text could become a memory | Low | The extraction prompt now records only what the user said or confirmed, and treats both messages as data |
| `approve` looked up the execution without a user filter (nothing leaked) | Hardening | The lookup is scoped to the user |

The review found no IDOR. Every query for user data is scoped to the authenticated user.

**Production configuration**

- `APP_ENV=production` refuses to start with any of these: wildcard CORS, `DB_ECHO`,
  `LOG_LEVEL=DEBUG`, rate limiting off, `OPENAI_STORE_RESPONSES`. All problems are listed at
  once.
- Missing OpenAI settings, and a missing `TRUSTED_HOSTS` in production, are logged as
  `configuration warning` lines.
- Security headers on every response: `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, CORP, and `Cache-Control: no-store` unless a route sets
  its own.
- Optional `TRUSTED_HOSTS` check; loopback is always allowed for the healthcheck.
- `scripts/start.sh` options:
  - `WEB_CONCURRENCY`
  - `FORWARDED_ALLOW_IPS`
  - `RUN_MIGRATIONS`
  - graceful-shutdown timeout
  - `--no-server-header`
  - `--ws-max-size`
- `docker-compose.prod.yml`:
  - Postgres and Redis are not published
  - a DB password is required
  - no test database
  - read-only root filesystem, all capabilities dropped, `no-new-privileges`
- `API_PORT` is configurable.

**Maintenance**

- `app/services/maintenance.py` and `scripts/maintenance.py`, one-shot or `--loop`.
- Run by a new `maintenance` Compose service, with a Redis lock so runs never overlap.
- Each run purges conversations past their retention period, deletes expired refresh tokens
  (revoked ones stay until they expire, for reuse detection), expires stale confirmations,
  and deletes `tool_executions` older than `TOOL_EXECUTION_RETENTION_DAYS` (default 90).

**Verified**

- **Tests:** 386 tests pass on Python 3.14 and 3.13.
- **Static checks:** ruff is clean, `pip-audit` reports no known vulnerabilities, and a
  secret scan of the repo is clean (`.env` is untracked).
- **Docker:** the image builds from scratch (`--no-cache --pull`, 355 MB). The production
  overlay, run as a separate Compose project:
  - ran every migration on an empty database
  - returned 404 for `/docs`
  - ignored a spoofed `X-Forwarded-For` by default and honoured it from a trusted proxy
  - had a read-only root filesystem and ran as uid 10001
  - shut down cleanly in about 1 s
- **Load check** (`scripts/load_check.py`, non-AI endpoints, everything on one 4-core
  laptop):
  - one worker: about 150–160 requests/s, median 63 ms at 10 concurrent clients
  - 4 workers: about 240 requests/s with 0 errors, limited by the load generators
  - login: 115–145 ms (Argon2id)

The full deployment and operations guide is [docs/deployment.md](deployment.md).

## After Phase 8: voice test page

At the user's request, `GET /voice-test` serves one static HTML file with plain JavaScript
(`app/static/voice-test.html`). It is a **developer test tool for the WebSocket voice API**,
not a product frontend: the spec's "no frontend" rule still applies to the app itself.

- **Features:**
  - login and registration
  - picking a conversation and a voice
  - push-to-talk (button or Space) or hands-free mode, with simple energy-based speech
    detection and a short pre-roll
  - streaming microphone input as pcm16
  - playing raw 24 kHz PCM as it arrives
  - barge-in, typed messages, and confirmation buttons
  - showing each turn's latency
  - refreshing the token automatically (`auth.refresh`)
- **When it's served:** outside production only, and only when `TEST_UI_ENABLED` (default on).
- **Verified:** in headless Chrome with a WAV file standing in for the microphone.
  - A Hinglish request was detected hands-free, transcribed, and created a reminder.
  - Mira's reply audio streamed back and played.
  - A typed follow-up worked.
  - No JavaScript errors.
  - Route tests cover the page being served in development and absent in production or when
    disabled.

## Not yet implemented

- Server-side VAD and incremental (during-speech) transcription. Turn-taking is client-driven
  and STT runs per utterance.
- Speech-to-speech via the OpenAI Realtime API (the next step for latency).
- Reminder delivery (notifications): reminders are stored and listed only.
- Account deletion, email verification, password reset and password change. These weren't in
  the original scope. There is no self-service deletion yet: an operator has to delete the
  `users` row, which cascades to all of that user's data.
- If an **SSE** client (`/chat/stream`) disconnects mid-reply, nothing is saved for that turn.
  WebSocket voice does save interrupted replies.
- Metrics endpoints (Prometheus) and tracing. Logs carry latencies and request IDs instead.
