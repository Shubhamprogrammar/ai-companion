"""Basic load check for the non-AI endpoints (no OpenAI calls, nothing billed).

    python -m scripts.load_check --base-url http://127.0.0.1:8000 --concurrency 50 --seconds 20

Registers one throwaway ``load-*@example.com`` user, then hammers a mix of
read/write endpoints with N concurrent clients and reports throughput, latency
percentiles and errors per endpoint. AI endpoints (chat, voice, memory search) are
deliberately excluded: their latency is the provider's, and they cost money.

The API has no account-deletion endpoint, so that user and the rows it created stay
behind: run this against a throwaway stack (see docs/deployment.md), not real data.

Note: auth endpoints are rate limited per IP (AUTH_RATE_LIMIT_PER_MINUTE), so login
is measured once rather than under load.
"""

import argparse
import asyncio
import random
import statistics
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx

API = "/api/v1"


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


async def main(base_url: str, concurrency: int, seconds: float) -> int:
    email = f"load-{uuid.uuid4().hex[:10]}@example.com"
    password = uuid.uuid4().hex
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=30, limits=limits) as client:
        r = await client.post(f"{API}/auth/register", json={"email": email, "password": password})
        r.raise_for_status()
        start = time.perf_counter()
        r = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        login_ms = (time.perf_counter() - start) * 1000
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        requests = [
            ("GET /health", "GET", f"{API}/health", None),
            ("GET /ready", "GET", f"{API}/ready", None),
            ("GET /users/me", "GET", f"{API}/users/me", None),
            ("GET /conversations", "GET", f"{API}/conversations", None),
            ("POST /conversations", "POST", f"{API}/conversations", {"title": "load"}),
            ("GET /reminders", "GET", f"{API}/reminders", None),
            ("POST /tasks", "POST", f"{API}/tasks", {"title": "load task"}),
            ("GET /tasks", "GET", f"{API}/tasks", None),
            (
                "POST /reminders",
                "POST",
                f"{API}/reminders",
                {"title": "load", "scheduled_at": when},
            ),
        ]
        latencies: dict[str, list[float]] = defaultdict(list)
        errors: dict[str, int] = defaultdict(int)
        deadline = time.perf_counter() + seconds

        async def worker() -> None:
            while time.perf_counter() < deadline:
                name, method, path, body = random.choice(requests)  # noqa: S311
                t0 = time.perf_counter()
                try:
                    response = await client.request(method, path, json=body, headers=headers)
                    ok = response.status_code < 400
                except httpx.HTTPError:
                    ok = False
                latencies[name].append((time.perf_counter() - t0) * 1000)
                if not ok:
                    errors[name] += 1

        began = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed = time.perf_counter() - began

    total = sum(len(v) for v in latencies.values())
    all_ms = [ms for v in latencies.values() for ms in v]
    print(f"concurrency={concurrency} duration={elapsed:.1f}s requests={total}")
    print(f"throughput={total / elapsed:.0f} req/s  errors={sum(errors.values())}")
    print(
        f"overall p50={_pct(all_ms, 0.5):.1f}ms p95={_pct(all_ms, 0.95):.1f}ms "
        f"p99={_pct(all_ms, 0.99):.1f}ms"
    )
    print(f"login (argon2id verify + token issue, single) = {login_ms:.0f}ms")
    print(f"{'endpoint':22} {'n':>6} {'p50':>8} {'p95':>8} {'max':>8} {'err':>5}")
    for name in sorted(latencies):
        v = latencies[name]
        print(
            f"{name:22} {len(v):>6} {statistics.median(v):>7.1f}ms {_pct(v, 0.95):>7.1f}ms "
            f"{max(v):>7.1f}ms {errors[name]:>5}"
        )
    print(f"(left behind: user {email})")
    return 1 if sum(errors.values()) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.base_url, args.concurrency, args.seconds)))
