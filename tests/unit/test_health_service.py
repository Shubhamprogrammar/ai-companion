import asyncio

from app.services.health import HealthService


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("postgresql://user:secret@host/db unreachable")


async def _hang() -> None:
    await asyncio.sleep(10)


async def test_all_checks_pass() -> None:
    result = await HealthService({"database": _ok, "redis": _ok}).readiness()
    assert result.status == "ready"
    assert result.checks == {"database": "ok", "redis": "ok"}


async def test_one_failing_check_marks_not_ready() -> None:
    result = await HealthService({"database": _fail, "redis": _ok}).readiness()
    assert result.status == "not_ready"
    assert result.checks == {"database": "error", "redis": "ok"}


async def test_failure_details_are_not_leaked_to_logs(caplog) -> None:
    await HealthService({"database": _fail}).readiness()
    assert "secret" not in caplog.text


async def test_slow_check_times_out() -> None:
    result = await HealthService({"redis": _hang}, timeout_seconds=0.05).readiness()
    assert result.checks == {"redis": "error"}
