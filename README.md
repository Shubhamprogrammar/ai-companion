# Personal AI Companion

A voice-first personal AI companion ("Mira") with memory, reminders, tasks and actions.

```
personal-ai/
├── backend/    FastAPI + Postgres (pgvector) + Redis — see backend/README.md
├── frontend/   React + TypeScript website (Vite, served by nginx) — see frontend/README.md
└── docker-compose.yml   runs everything together
```

## Run everything

```bash
cp backend/.env.example backend/.env   # first time: add your OpenAI key etc.
docker compose up -d --build
```

Open **http://127.0.0.1:3000**. Create an account and start talking.

| URL | What |
|---|---|
| http://127.0.0.1:3000 | The website |
| http://127.0.0.1:8000/docs | API docs (development only) |
| http://127.0.0.1:8000/voice-test | Developer voice test page (development only) |

The website's nginx container proxies `/api` (including the voice WebSocket and the
streaming chat endpoint) to the API container, so the browser uses one origin and no
CORS setup is needed.

The backend can still run on its own: `cd backend && docker compose up -d`. Both compose
files use the same project name, so they share the same database volumes.

## Frontend development (hot reload)

```bash
docker compose up -d api          # backend on :8000
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to `127.0.0.1:8000`.

## Production

Layer the backend's production overrides and put your TLS reverse proxy in front of the
**web** container (port 3000) instead of the API. Websocket upgrades must be forwarded.

```bash
docker compose -f docker-compose.yml -f backend/docker-compose.prod.yml up -d --build
```

See [backend/docs/deployment.md](backend/docs/deployment.md) for the backend settings.
