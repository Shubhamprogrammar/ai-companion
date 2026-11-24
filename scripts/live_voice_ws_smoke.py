"""Manual real-time voice check over a REAL WebSocket against a running server (cents).

Streams recorded utterances (mp3/wav/...) in small chunks like a microphone would,
and reports latency from speech.end to transcript, first text, and first audio.
Also exercises barge-in and a tool call. Reply audio is saved to --out.

    python -m scripts.live_voice_ws_smoke --audio-dir DIR   # DIR has 01/02/03-user.mp3
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from httpx_ws import aconnect_ws
from wsproto.events import BytesMessage

PASSWORD = "smoke-test-password"  # noqa: S105 - throwaway test account
CHUNK = 4096


async def send_utterance(ws, audio: bytes) -> float:
    await ws.send_json({"type": "speech.start"})
    for i in range(0, len(audio), CHUNK):
        await ws.send_bytes(audio[i : i + CHUNK])
    await ws.send_json({"type": "speech.end"})
    return time.perf_counter()


async def collect(ws, t0: float, until: set[str], stop_after_audio: bool = False) -> dict:
    marks: dict = {"audio": bytearray(), "text": ""}
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=60)
        now = round((time.perf_counter() - t0) * 1000)
        if isinstance(msg, BytesMessage):
            marks.setdefault("first_audio_ms", now)
            marks["audio"] += msg.data
            if stop_after_audio:
                return marks
            continue
        ev = json.loads(msg.data)
        kind = ev["type"]
        if kind == "transcript.done":
            marks["transcript_ms"], marks["transcript"] = now, ev["text"]
        elif kind == "response.text.delta":
            marks.setdefault("first_text_ms", now)
            marks["text"] += ev["text"]
        elif kind in ("tool.started", "tool.completed"):
            marks.setdefault("tools", []).append(ev.get("tool") or ev["action"]["status"])
        elif kind == "error":
            marks.setdefault("errors", []).append(ev["code"])
        if kind in until:
            marks["end"], marks["end_ms"], marks["event"] = kind, now, ev
            return marks


def report(label: str, m: dict) -> None:
    print(f"\n== {label}")
    if "transcript" in m:
        print(f"  heard     : {m['transcript']}")
    print(f"  mira      : {m['text']}")
    for key in ("transcript_ms", "first_text_ms", "first_audio_ms", "end_ms"):
        if key in m:
            print(f"  {key:<14}: {m[key]} ms")
    if m.get("tools"):
        print(f"  tools     : {m['tools']}")
    if m.get("errors"):
        print(f"  errors    : {m['errors']}")


async def main(base_url: str, audio_dir: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    clips = [(audio_dir / f"0{i}-user.mp3").read_bytes() for i in (1, 2, 3)]
    email = f"ws-smoke-{uuid.uuid4().hex[:8]}@example.com"
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as http:
        await http.post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "timezone": "Asia/Kolkata"},
        )
        token = (
            await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        ).json()["access_token"]
        ws_url = base_url.replace("http", "ws", 1) + "/api/v1/ws/voice/new"
        async with aconnect_ws(ws_url, http, max_message_size_bytes=4 * 1024 * 1024) as ws:
            await ws.send_json({"type": "session.start", "token": token, "input_format": "mp3"})
            print("ready:", (await ws.receive_json())["output_format"])

            t0 = await send_utterance(ws, clips[0])
            m = await collect(ws, t0, {"response.done"})
            (out / "ws-turn1.mp3").write_bytes(m["audio"])
            report("Turn 1 (Hinglish, streamed reply)", m)
            print(f"  server timings: {m['event']['timings']}")

            t0 = await send_utterance(ws, clips[1])
            m = await collect(ws, t0, set(), stop_after_audio=True)
            print(f"\n== Turn 2: first audio at {m['first_audio_ms']} ms → barging in")
            t1 = await send_utterance(ws, clips[2])
            cancelled = await collect(ws, t1, {"response.cancelled"})
            print(
                f"  cancelled after {cancelled['end_ms']} ms; saved partial: "
                f"{cancelled['event']['saved_text'][:80]!r}"
            )
            m = await collect(ws, t1, {"response.done"})
            (out / "ws-turn3.mp3").write_bytes(m["audio"])
            report("Turn 3 (Hindi, after barge-in)", m)

            t0 = time.perf_counter()
            await ws.send_json(
                {
                    "type": "text.message",
                    "text": "Kal subah 8 baje Priya ko call karne ka reminder laga do.",
                }
            )
            m = await collect(ws, t0, {"response.done"})
            report("Turn 4 (typed, tool call)", m)
            await ws.send_json({"type": "session.end"})
        reminders = (
            await http.get("/api/v1/reminders", headers={"Authorization": f"Bearer {token}"})
        ).json()["items"]
        print("\nreminders:", [(r["title"], r["scheduled_at"]) for r in reminders])
        print(f"(cleanup: delete throwaway user {email}; reply audio in {out})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("voice_smoke_output"))
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.audio_dir, args.out))
