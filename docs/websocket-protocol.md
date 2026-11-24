# WebSocket voice protocol

**Endpoint:** `ws(s)://<host>/api/v1/ws/voice/{conversation_id}`. Use the literal `new`
instead of an id to start a new conversation; the id arrives in the first `response.done`.

Two kinds of frame are used:

- **Text frames** carry JSON events, each with a `type` field.
- **Binary frames** carry audio: the user's speech going to the server, and the companion's
  speech coming back.

## What is and isn't real-time

| Stage | Behaviour |
|---|---|
| User audio upload | **Streamed:** binary chunks are sent as the user speaks. |
| Speech-to-text | Runs on the **complete utterance** after `speech.end`. The transcript *output* streams (`transcript.delta`), but recognition doesn't start until the utterance ends. Truly incremental STT (OpenAI Realtime transcription) is **planned**. |
| Turn-taking | **Client-driven:** the client sends `speech.start` / `speech.end`, using its own voice-activity detection or push-to-talk. Server-side VAD is **planned**. |
| Companion reply (text) | **Streamed** token by token (`response.text.delta`). |
| Companion reply (speech) | **Streamed per sentence.** Each sentence goes to TTS as soon as it's complete, and the first one may break early at a comma. Audio bytes stream as binary frames while later sentences are still being generated. |
| Interruptions (barge-in) | **Supported.** `speech.start`, `text.message` or `response.cancel` during a reply cancels generation and speech immediately. The partial reply is saved with `interrupted: true`. |
| Tools and confirmations | **Supported:** tool events, plus `confirmation.respond`. |

Measured with `gpt-transcribe`, `gpt-6.2-sol` and `gpt-4o-mini-tts`: first reply audio arrives
about **3.8–4.9 s** after `speech.end`, versus 6.3–7.3 s for the request/response
`POST /voice/turn`. The remainder is provider-bound: STT ≈ 1.1 s, model first token ≈ 1.5 s,
TTS first byte ≈ 1.0–1.7 s.

## Session lifecycle

```
client                                     server
  │ ── connect ───────────────────────────▶ │
  │ ── {"type":"session.start", ...} ─────▶ │  auth (≤ WS_AUTH_TIMEOUT_SECONDS)
  │ ◀──────────────── {"type":"session.ready"} │
  │ ── {"type":"speech.start"} ───────────▶ │
  │ ◀───────────── {"type":"speech.started"} │
  │ ══ binary audio chunks ═══════════════▶ │
  │ ── {"type":"speech.end"} ─────────────▶ │
  │ ◀─ speech.stopped, transcript.delta*, transcript.done
  │ ◀─ response.started, response.text.delta*, [tool.started, tool.completed]*
  │ ◀─ response.audio.started, ══ binary audio ══, response.audio.done
  │ ◀─ response.done
  │        … more turns …
  │ ── {"type":"session.end"} ────────────▶ │
  │ ◀── {"type":"session.closed","reason":"session_end"} + close 1000
```

## Client → server

| `type` | Fields | Notes |
|---|---|---|
| `session.start` | `token?`, `input_format` (`webm`\|`ogg`\|`mp4`\|`wav`\|`mp3`\|`flac`\|`pcm16`, default `webm`), `sample_rate` (pcm16 only, default 24000), `output_format` (`mp3`\|`opus`\|`aac`\|`pcm`, default `mp3`), `voice?`, `speed?`, `text_only` (default false) | **Must be the first message.** Authentication: an `Authorization: Bearer` header (native clients) or `token` here (browsers). Tokens are never accepted in the URL. |
| `speech.start` | | Begins an utterance. If a reply is in progress, it is **interrupted**. Audio frames sent without a prior `speech.start` start one implicitly. |
| *(binary)* | audio bytes | Chunks of the current utterance. For container formats, send the chunks of **one** recording per utterance (e.g. MediaRecorder output). `pcm16` is raw 16-bit little-endian mono. |
| `speech.end` | | Ends the utterance and starts the turn. |
| `text.message` | `text` | A typed message in the same session (interrupts a reply in progress). Replies stay voice-style unless `text_only`. |
| `response.cancel` | | Stop the current reply. |
| `confirmation.respond` | `confirmation_id`, `decision` (`approve`\|`reject`) | Decide on an action awaiting confirmation (see `tool.completed`). |
| `ping` | | Answered with `pong`. |
| `session.end` | | Close the session cleanly. |

## Server → client

| `type` | Fields |
|---|---|
| `session.ready` | `session_id`, `conversation_id` (null for `new`), `input_format`, `output_format`, `output_mime_type`, `output_sample_rate` (24000 for pcm), `limits{max_utterance_bytes, max_frame_bytes, idle_timeout_seconds}` |
| `speech.started` / `speech.stopped` | `bytes` (on stopped) |
| `transcript.delta` / `transcript.done` | `text` |
| `response.started` | `response_id` |
| `response.text.delta` | `response_id`, `text` |
| `tool.started` | `tool` |
| `tool.completed` | `action{execution_id, tool, status, error_code, confirmation{confirmation_id, summary, expires_at}?}` |
| `response.audio.started` | `response_id`, `format`, `mime_type`, `sample_rate` |
| *(binary)* | reply audio for the current response, in order |
| `response.audio.done` | `response_id` |
| `response.done` | `response_id`, `conversation`, `user_message`, `assistant_message`, `actions`, `timings{stt_ms, first_text_ms, first_audio_ms, total_ms}` |
| `response.cancelled` | `response_id`, `saved_text` (what was generated before the interruption) |
| `confirmation.result` | `execution` (same shape as `GET /tools/executions` items) |
| `error` | `code`, `message`, `fatal`, `response_id?` |
| `pong` | |
| `session.closed` | `reason`: `session_end` \| `idle_timeout` \| `max_duration` \| `client_disconnected` \| `not_found` \| `internal_error` |

**Playback.** Binary frames after `response.audio.started` belong to that response until
`response.audio.done`. After `response.cancelled`, discard any audio still buffered for that
`response_id`. For `mp3`/`opus`/`aac`, frames are pieces of one stream (one TTS segment per
sentence, concatenated), so feed them to a streaming decoder (e.g. MediaSource). `pcm` is raw
24 kHz 16-bit mono.

**Non-fatal errors** (`fatal: false`) leave the session open. Examples: `invalid_message`,
`no_audio`, `no_speech_detected`, `audio_too_large`, `frame_too_large`,
`unsupported_audio_format`, `rate_limited`, `speech_unavailable` (the text reply continues
without audio), `ai_unavailable`.

## Close codes

| Code | Meaning |
|---|---|
| 1000 | Normal: `session.end`, idle timeout, or max duration |
| 4400 | Protocol error (the first message wasn't a valid `session.start`) |
| 4401 | Authentication failed |
| 4404 | Conversation not found (or not yours) |
| 4408 | No `session.start` within `WS_AUTH_TIMEOUT_SECONDS` |
| 4429 | Too many concurrent voice sessions (`WS_MAX_SESSIONS_PER_USER`) |
| 1011 | Internal error |

## Limits (configurable)

| Setting | Default |
|---|---|
| `WS_AUTH_TIMEOUT_SECONDS` | 10 |
| `WS_IDLE_TIMEOUT_SECONDS` (no frames received) | 120 |
| `WS_MAX_SESSION_MINUTES` | 30 |
| `WS_MAX_SESSIONS_PER_USER` (tracked in Redis) | 3 |
| `WS_MAX_FRAME_BYTES` | 512 KB |
| Utterance size | `MAX_AUDIO_SIZE_MB` |

Turns share the per-user `CHAT_RATE_LIMIT_PER_MINUTE` budget with `/chat`.

## Manual test

```bash
python -m scripts.live_voice_ws_smoke --audio-dir DIR   # DIR contains 01-user.mp3, 02-user.mp3, 03-user.mp3
```

`python -m scripts.live_voice_smoke --out DIR` produces suitable clips. The script prints
per-turn latency, exercises barge-in and a tool call, and saves the reply audio.
