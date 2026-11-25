# Frontend

React 19 + TypeScript + Vite. No UI framework; styles are in `src/index.css`, with light
and dark themes.

## Scripts

```bash
npm run dev       # http://localhost:5173, proxies /api → http://127.0.0.1:8000
npm run build     # type-check + production build into dist/
npm run lint
```

Set `API_TARGET` to proxy a different backend (`API_TARGET=http://10.0.0.5:8000 npm run dev`).
Build-time options are listed in `.env.example`.

## Layout

| Path | Purpose |
|---|---|
| `src/api/client.ts` | fetch wrapper, error type, token session (refresh rotation is serialized across tabs) |
| `src/api/endpoints.ts` | typed calls for every backend endpoint, plus the `/chat/stream` SSE reader |
| `src/api/types.ts` | mirrors `backend/app/schemas` (keep in sync) |
| `src/voice/VoiceSession.ts` | real-time voice over `/ws/voice/{id}`: mic → pcm16 upload, streamed 24 kHz playback, barge-in, hands-free voice detection |
| `src/pages/` | Chat, Memories, Reminders, Tasks, Activity, Settings, Login/Register |
| `src/components/` | layout/sidebar, timeline, voice dock, dialogs, toasts |

## Notes

- **Sessions:** the refresh token is kept in `localStorage` so you stay signed in across
  reloads and tabs. Signing out revokes it on the server.
- **Voice** needs microphone permission, which browsers only grant on `https://` or
  `localhost`/`127.0.0.1`. Hold the button or Space to talk, or turn on hands-free.
- **Docker:** `Dockerfile` builds the site and serves it with nginx (`nginx.conf`), which
  also proxies `/api` to the `api` service.
