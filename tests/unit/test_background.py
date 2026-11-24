import asyncio

from app.core.background import BackgroundTaskRunner, InlineTaskRunner


async def test_background_runner_runs_jobs_and_survives_failures(caplog) -> None:
    runner = BackgroundTaskRunner()
    done: list[str] = []

    async def ok() -> None:
        await asyncio.sleep(0)
        done.append("ok")

    async def boom() -> None:
        raise RuntimeError("kaboom")

    runner.submit("ok", ok)
    runner.submit("boom", boom)
    await runner.drain(grace_seconds=1)
    assert done == ["ok"]
    assert "background job failed" in caplog.text


async def test_drain_cancels_jobs_that_overrun() -> None:
    runner = BackgroundTaskRunner()

    async def slow() -> None:
        await asyncio.sleep(10)

    runner.submit("slow", slow)
    await runner.drain(grace_seconds=0.01)  # returns promptly


async def test_inline_runner_defers_until_run_pending() -> None:
    runner = InlineTaskRunner()
    done: list[int] = []

    async def job() -> None:
        done.append(1)

    runner.submit("job", job)
    assert done == []
    await runner.run_pending()
    assert done == [1]
