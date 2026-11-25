# Architecture

A **modular monolith**: one FastAPI process with strict internal layering. No microservices,
no Celery, no LangChain. Orchestration is plain Python and is kept separate from HTTP, so it
can later move to LangGraph if workflows get complex.

## Layers

```
HTTP / WebSocket (app/api)          thin: parse, authenticate, call a service, return a schema
        │
Services (app/services)             business rules, transactions, permissions
        │
        ├── AI engine (app/ai)          agent loop, personality, prompts, context, tool selection
        ├── Memory engine (app/memory)  short-term (Redis) + long-term (Postgres/pgvector)
        ├── Voice engine (app/audio)    STT / TTS behind provider interfaces
        └── Tool engine (app/tools)     registry → argument validation → permission/confirmation → execute
        │
Repositories (app/repositories)     all SQL lives here
        │
Core (app/core)                     config, DB engine, Redis, logging, security, errors
```

Only `app/core` exists in Phase 1. The other packages are created when their phase is built,
not as empty placeholders.

## Conventions

- **Configuration:** a single `Settings` class (`app/core/config.py`) populated from
  environment variables. Model names, voices, limits and feature flags are never hardcoded.
  Secrets are `SecretStr`, so they don't appear in reprs or logs.
- **Dependency injection:** infrastructure objects (`Database`, `RedisClient`) are created in
  the app lifespan, stored on `app.state`, and exposed through FastAPI dependencies in
  `app/api/dependencies.py`. Tests swap them out with `app.dependency_overrides`.
- **Errors:** services raise `AppError` subclasses (`app/core/exceptions.py`). Every error
  response has the same shape, `{"error": {"code", "message", "request_id", "details?"}}`.
  Validation errors never echo submitted values, and 5xx responses never include internal
  details.
- **Observability:** the pure-ASGI `RequestContextMiddleware` assigns or propagates an
  `X-Request-ID`, stores it in a context variable, and logs method, path, status and
  duration. Logs are JSON by default (`LOG_JSON`). Bodies and query strings aren't logged,
  and keys such as `password`, `token` and `api_key` are redacted.
- **Database:** async SQLAlchemy 2 with asyncpg. Constraint names follow a deterministic
  naming convention so Alembic autogenerate stays stable. The pgvector extension is enabled
  by migration `0001`.
- **Health:** `/health` is a liveness check and touches nothing. `/ready` checks Postgres
  (including that pgvector is installed) and Redis concurrently, with a timeout on each, and
  returns 503 if any check fails.

## Provider abstraction

The LLM, STT, TTS, embeddings and web search are each a small `Protocol`, with an OpenAI
implementation behind it. Business logic depends on the protocol, never on the SDK. Tests
use fake providers, so no paid calls are made.

- LLM: `app/ai/llm.py` (`LLMProvider`), implemented by `OpenAIResponsesLLM` in `app/ai/client.py`
- STT: `app/audio/stt.py` (`SpeechToTextProvider`), implemented by `OpenAISpeechToText`
- TTS: `app/audio/tts.py` (`TextToSpeechProvider`), implemented by `OpenAITextToSpeech`
- Embeddings: `app/memory/embeddings.py` (`EmbeddingProvider`), implemented by `OpenAIEmbeddings`
- Web search: Phase 6

## AI companion engine (Phase 3)

```
POST /chat ──► ChatService._load ──► CompanionAgent.prepare ──► (commit: release DB conn)
                 │ profile, prefs,        │ detect_language
                 │ recent messages        │ build_context_window (recent N, char budget)
                 │                        │ build_system_prompt(personality, user ctx, capabilities)
                 ▼                        ▼
             ChatService._save ◄── LLMProvider.generate / .stream
                 user + assistant messages, title, updated_at ── commit
```

- `app/ai/personality.py`: personality presets as data
- `app/ai/prompts.py`: system prompt sections. User-supplied values are sanitised and wrapped
  in `<user_context>` / `<conversation_summary>` blocks marked as data.
- `app/ai/language.py`: English/Hindi/Hinglish hint
- `app/ai/context.py`: short-term context window
- `app/ai/agent.py`: per-turn orchestration. Phases 5 and 6 extend `prepare` with memory
  retrieval and tools.
- `app/services/chat.py`: loading, persistence and streaming events. No HTTP concerns.

## Authentication (Phase 2)

- **Passwords:** Argon2id (`argon2-cffi`), run in a worker thread. A dummy verification runs
  for unknown emails, so timing doesn't reveal which accounts exist.
- **Tokens:** short-lived access JWTs and long-lived refresh JWTs, both HMAC-signed with
  `SECRET_KEY`, each with a unique `jti` and a `type` claim.
- **Refresh:** each issued refresh token's `jti` is recorded in `refresh_tokens`. Refreshing
  locks the row, revokes it and issues a new pair. Presenting a revoked refresh token revokes
  every session for that user (theft detection).
- **Logout:** the access-token `jti` goes into a Redis deny-list whose TTL equals the token's
  remaining lifetime. Every authenticated request checks it, and fails closed with 503 if
  Redis is unreachable.
- **Authorization:** routes depend on `CurrentUserDep` / `AuthContextDep`. Every user-owned
  query is scoped by the authenticated `user.id`. There are no endpoints that take another
  user's ID.
- **Request flow:** `route → dependency builds service(session, repos) → service commits`.
  Route handlers contain no business logic.

## Voice engine (Phase 4)

```
POST /voice/turn ─► BodySizeLimitMiddleware (Content-Length) ─► read ≤ limit+1 bytes
   ─► validate_audio (magic bytes, size) ─► STT ─► ChatService.send(message_type=voice)
   ─► [AudioStore.save if retention on] ─► prepare_text_for_speech ─► TTS ─► JSON + base64 audio
```

- `app/audio/audio_utils.py`: format detection, validation, speech-text cleanup, TTS chunking
- `app/audio/stt.py`, `app/audio/tts.py`: provider protocols and OpenAI implementations
- `app/audio/storage.py`: `DisabledAudioStore` (default) and `LocalAudioStore`
- `app/services/voice.py`: transcription, voice settings resolution, the complete turn and timings
- `app/audio/streaming.py` is reserved for WebSocket streaming (Phase 7)

## Memory engine (Phase 5)

```
ChatService._load ── MemoryCoordinator.recall ── embed(message) ─► pgvector search (user-scoped)
                                                               + core facts ─► <user_memories>
ChatService._save ── MemoryCoordinator.schedule_after_turn ─► TaskRunner (background)
                       └► MemoryPipeline.after_turn (own DB session)
                            ├ extract: candidates(m1..m8) → LLM json_schema → validate → dedup → embed → save
                            └ summarize: if ≥ N msgs outside window → lock row → update summary (+ summary memory)
```

- `app/memory/embeddings.py`: provider protocol and OpenAI implementation (fixed 1536 dims)
- `app/memory/retrieval.py`: similar + core recall; degrades gracefully
- `app/memory/extraction.py`: extraction prompt, strict schema, alias mapping and validation
- `app/memory/summarizer.py`: rolling conversation summaries
- `app/repositories/memories.py`: every query is user-scoped
- `app/services/memory_pipeline.py`: the request-time coordinator and the background pipeline
- `app/services/memories.py`: user-facing CRUD and search
- `app/core/background.py`: in-process task runner (an inline runner in tests)

## Tool engine (Phase 6)

```
CompanionAgent.run(prepared, executor)
  loop ≤ TOOL_MAX_ROUNDS:
    LLM (tools=registry.specs(), strict) ──► function_calls?
        no  → final text reply
        yes → for each call: ToolExecutor.run(call)
                 registry lookup → JSON parse → Args.model_validate (Pydantic)
                 → needs_confirmation? → ToolExecution(awaiting_confirmation) + summary
                 → else tool.run(ctx) via user-scoped services → ToolExecution(succeeded|failed)
              replay output items + function_call_output → next round
  last round: tool_choice="none" (text only)
```

- `app/tools/base.py`: `Tool`, `ToolArgs`, `ToolContext` (user, profile, session, the user's
  own message), `ToolError`
- `app/tools/schema.py`: Pydantic → OpenAI strict JSON Schema
- `app/tools/registry.py`, `app/tools/defaults.py`: the closed tool set and config filtering
- `app/tools/executor.py`: validation, confirmation gate, execution, audit, results for the model
- `app/tools/confirmations.py`: pending actions, expiry, approve/reject, and the rules for
  conversational approval

**Confirmation rules.** A consequential action runs only after one of two things happens:

- **Explicit approval through the API:** REST `POST /tools/confirmations/{id}/approve` or
  WebSocket `confirmation.respond`, both scoped to the user.
- **Approval in conversation:** the model calls `confirm_pending_action`, and the backend
  checks all of the following:
  1. The confirmation belongs to this user and this conversation, and hasn't expired.
  2. It was requested in an **earlier** turn. `ToolContext.created_confirmations` records the
     ones created during the current turn, and those are refused.
  3. The user's own message for this turn is a short (≤ 8 words), explicit yes with no
     negation.

  The model can't approve on the user's behalf, and injected text can't request and approve
  an action in one go.
- `app/tools/{reminders,tasks,preferences,memories,web_search}.py`: the tools themselves (thin;
  logic lives in services)
- `app/services/productivity.py`: reminder/task business rules shared by the REST API and tools

## Real-time voice (Phase 7)

```
WebSocket /ws/voice/{id|new}  (app/api/websocket/voice.py: accept, auth, session cap, frame pump)
   └► VoiceSession (app/services/voice_session.py), one per connection
        speech.start → UtteranceBuffer (size-capped) → speech.end → turn task:
          services(user) = fresh DB session + ChatService/VoiceService/ToolActionService
          STT.transcribe_stream ─► transcript.delta/done
          ChatService.begin_turn → stream_turn ─► response.text.delta, tool.* ─► SentenceChunker
                                                         └► queue ─► speaker task: TTS.stream per sentence ─► binary frames
          → response.done (persisted) | CancelledError → save_interrupted → response.cancelled
```

- `app/audio/streaming.py`: `UtteranceBuffer`, `pcm16_to_wav`, `SentenceChunker`
- `app/schemas/realtime.py`: client message models (discriminated union) and close codes
- `RealtimeDeps` bundles app-level providers; each turn builds its services on a new session
- `SessionSlots`: per-user concurrent sessions as a Redis sorted set of 60 s leases. The
  socket loop renews its lease, so a crashed session frees its slot within a minute. Fails
  open, but authentication fails closed without Redis anyway.
- `SocketAuth`: the session's access token. The session closes when the token expires or is
  revoked (deny-list re-checked every 15 s), and `auth.refresh` swaps in a new token for the
  same user.
- Connection hardening: a per-IP connection limit before authentication, and Uvicorn's
  `--ws-max-size` on top of the app's own frame checks.

## Production concerns (Phase 8)

- **Settings guards:** `Settings._production_guards` refuses unsafe production
  configuration, and `configuration_warnings()` logs non-fatal gaps at startup.
- **Middleware**, outermost first:
  1. `RequestContextMiddleware`
  2. `TrustedHostMiddleware` (when `TRUSTED_HOSTS` is set)
  3. `SecurityHeadersMiddleware`
  4. CORS, outside the size limit so browsers can read 413 responses
  5. `BodySizeLimitMiddleware`, which checks declared sizes and counts streamed bytes
- **Login protection:** per-IP rate limits, plus `LoginAttemptStore`, a per-account failure
  counter in Redis keyed by a hash of the email.
- **Housekeeping:** `MaintenanceService` (retention, token and audit pruning, stale
  confirmations) runs from `scripts/maintenance.py` in the `maintenance` container, under a
  Redis lock. There's no in-app scheduler and no Celery: one small loop process is enough.
- **Deployment, proxying, backups and monitoring:** see [deployment.md](deployment.md).
