"""Manual end-to-end tool-calling check against a RUNNING server with real OpenAI (cents).

Each step prints what the user said, what the companion replied, which actions ran,
and the resulting server state (reminders/tasks), so you can judge both the
conversation and whether it told the truth.

    python -m scripts.live_tools_smoke [--base-url http://localhost:8000]
"""

import argparse
import asyncio
import uuid

import httpx

PASSWORD = "smoke-test-password"  # noqa: S105 - throwaway test account

STEPS = [
    "Remind me tomorrow at 9 AM to study Python.",
    "Remind me later to call mom.",
    "Kal shaam 6 baje gym jaana hai, yaad dila dena.",
    "Add 'finish the FastAPI course' to my to-do list, due next Friday.",
    "What reminders do I have?",
    "Gym wala reminder delete kar do.",
    "Haan, kar do.",
    "Mark the FastAPI course task as done.",
    "Can you call my mom for me?",
    "What's the weather in Pune right now?",
]


async def main(base_url: str) -> None:
    email = f"tools-smoke-{uuid.uuid4().hex[:8]}@example.com"
    async with httpx.AsyncClient(base_url=f"{base_url}/api/v1", timeout=90) as client:
        await client.post(
            "/auth/register",
            json={"email": email, "password": PASSWORD, "timezone": "Asia/Kolkata"},
        )
        login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        cid = None
        for message in STEPS:
            body = {"message": message, **({"conversation_id": cid} if cid else {})}
            response = await client.post("/chat", json=body)
            data = response.json()
            if response.status_code != 200:
                print(f"YOU : {message}\n  !! {response.status_code} {data}\n")
                continue
            cid = data["conversation"]["id"]
            print(f"YOU : {message}\nMIRA: {data['assistant_message']['content']}")
            for a in data["actions"]:
                extra = f" — {a['confirmation']['summary']}" if a["confirmation"] else ""
                code = f" {a['error_code']}" if a["error_code"] else ""
                print(f"  ⚙ {a['tool']}: {a['status']}{code}{extra}")
            print()

        reminders = (await client.get("/reminders", params={"status": "pending"})).json()["items"]
        tasks = (await client.get("/tasks")).json()["items"]
        print("FINAL STATE")
        for r in reminders:
            print(f"  reminder: {r['title']!r} at {r['scheduled_at']} ({r['status']})")
        for t in tasks:
            print(f"  task: {t['title']!r} due {t['due_date']} ({t['status']})")
        print(f"\n(cleanup: delete throwaway user {email})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    asyncio.run(main(parser.parse_args().base_url))
