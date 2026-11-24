"""Manual end-to-end memory check against a RUNNING server with real OpenAI (costs cents).

Creates a throwaway user, talks to the companion, inspects what it remembered,
checks recall in a new conversation, a correction, a sensitive detail that must not be
stored, semantic search and the memory off switch. The throwaway user is deleted afterwards.

    docker compose up -d   # or: uvicorn app.main:app
    python -m scripts.live_memory_smoke [--base-url http://localhost:8000]
"""

import argparse
import asyncio
import time
import uuid

import httpx

PASSWORD = "smoke-test-password"  # noqa: S105 - throwaway test account


async def wait_for_memories(client: httpx.AsyncClient, predicate, wait_seconds: float = 30) -> list:
    deadline = time.monotonic() + wait_seconds
    memories: list = []
    while time.monotonic() < deadline:
        memories = (await client.get("/memories")).json()["items"]
        if predicate(memories):
            return memories
        await asyncio.sleep(1.5)
    return memories


def show(memories: list) -> None:
    for m in memories:
        print(f"    [{m['category']}/{m['importance']}] {m['content']}")
    if not memories:
        print("    (none)")


async def chat(client: httpx.AsyncClient, message: str, cid: str | None = None) -> dict:
    body = {"message": message, **({"conversation_id": cid} if cid else {})}
    response = await client.post("/chat", json=body, timeout=60)
    response.raise_for_status()
    data = response.json()
    print(f"  YOU : {message}\n  MIRA: {data['assistant_message']['content']}")
    return data


async def main(base_url: str) -> None:
    email = f"memory-smoke-{uuid.uuid4().hex[:8]}@example.com"
    async with httpx.AsyncClient(base_url=f"{base_url}/api/v1", timeout=30) as client:
        await client.post(
            "/auth/register",
            json={"email": email, "password": PASSWORD, "timezone": "Asia/Kolkata"},
        )
        tokens = (
            await client.post("/auth/login", json={"email": email, "password": PASSWORD})
        ).json()
        client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
        try:
            print("\n1) Sharing facts")
            first = await chat(
                client,
                "Hi! Main Rohan hoon, Pune se. Backend developer hoon aur AI "
                "engineer banna chahta hoon. Aur haan, chai bahut pasand hai.",
            )
            memories = await wait_for_memories(client, lambda ms: len(ms) >= 2)
            print("  remembered:")
            show(memories)

            print("\n2) New conversation: is it recalled?")
            await chat(client, "Kuch evening snack suggest karo")
            await chat(client, "What do you know about me so far?")

            print("\n3) Correction (should update, not duplicate)")
            before = len(memories)
            await chat(
                client,
                "Actually, I moved from Pune to Bengaluru last month.",
                first["conversation"]["id"],
            )
            memories = await wait_for_memories(
                client, lambda ms: any("Bengaluru" in m["content"] for m in ms)
            )
            show(memories)
            stale = any("lives in Pune" in m["content"] for m in memories)
            print(f"  count before={before} after={len(memories)}; stale 'lives in Pune': {stale}")

            print("\n4) Sensitive detail (must NOT be stored)")
            await chat(client, "My bank PIN is 4321 and my password is hunter2, don't tell anyone.")
            await asyncio.sleep(8)
            memories = (await client.get("/memories")).json()["items"]
            leaked = [m for m in memories if "4321" in m["content"] or "hunter2" in m["content"]]
            print(f"  secrets stored: {bool(leaked)}")

            print("\n5) Semantic search: 'what drinks do I like'")
            results = (
                await client.post("/memories/search", json={"query": "what drinks do I like"})
            ).json()
            for r in results["results"][:3]:
                print(f"    {r['similarity']:.2f}  {r['memory']['content']}")

            print("\n6) Memory switched off")
            await client.patch("/users/me/preferences", json={"memory_enabled": False})
            await chat(client, "Mera naam kya hai?")
        finally:
            await client.delete("/memories", params={"confirm": "true"})
            print(f"\n(cleanup: delete the throwaway user {email} from the DB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    asyncio.run(main(parser.parse_args().base_url))
