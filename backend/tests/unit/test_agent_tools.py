import uuid
from dataclasses import dataclass, replace

import pytest

from app.ai.agent import AgentReply, CompanionAgent, ToolFinished, ToolStarted
from app.ai.llm import TextDelta, ToolCall, ToolResultItem
from app.core.exceptions import AIUnavailableError
from tests.conftest import make_settings
from tests.fakes import FakeLLM, tool_results
from tests.unit.test_agent import _turn


@dataclass
class _Outcome:
    result: ToolResultItem
    activity: str


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def run(self, call: ToolCall) -> _Outcome:
        self.calls.append(call)
        return _Outcome(ToolResultItem(call.call_id, '{"status": "succeeded"}'), f"did:{call.name}")


def _prepared(agent: CompanionAgent):
    from app.ai.llm import ToolSpec

    spec = ToolSpec(
        "create_reminder",
        "x",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )
    return agent.prepare(replace(_turn(uuid.uuid4()), tools=[spec]))


async def test_tool_round_then_text_reply() -> None:
    llm = FakeLLM([[("create_reminder", {"title": "Study"})], "Done, kal 9 baje!"])
    agent, runner = CompanionAgent(llm, make_settings()), FakeRunner()
    reply = await agent.run(_prepared(agent), runner)

    assert reply.response.text == "Done, kal 9 baje!"
    assert reply.activities == ["did:create_reminder"]
    assert [c.name for c in runner.calls] == ["create_reminder"]
    second = llm.requests[1]
    assert tool_results(second) == [{"status": "succeeded"}]
    assert second.tools  # tools still offered after the first round


async def test_max_rounds_forces_a_text_answer() -> None:
    step = [("create_reminder", {})]
    llm = FakeLLM([step, step, "Okay, stopping here."])
    agent = CompanionAgent(llm, make_settings(tool_max_rounds=2))
    reply = await agent.run(_prepared(agent), FakeRunner())
    assert reply.response.text == "Okay, stopping here."
    assert llm.requests[-1].tool_choice == "none"
    assert len(llm.requests) == 3


async def test_without_runner_tool_calls_are_not_executed() -> None:
    llm = FakeLLM([[("create_reminder", {})]])
    agent = CompanionAgent(llm, make_settings())
    with pytest.raises(AIUnavailableError):  # no text to return
        await agent.run(_prepared(agent), None)


async def test_stream_emits_tool_events() -> None:
    llm = FakeLLM([[("create_reminder", {})], "Ho gaya yaar"])
    agent = CompanionAgent(llm, make_settings())
    events = [e async for e in agent.run_stream(_prepared(agent), FakeRunner())]
    kinds = [type(e).__name__ for e in events]
    assert kinds[:2] == ["ToolStarted", "ToolFinished"]
    assert isinstance(events[0], ToolStarted) and events[0].tool == "create_reminder"
    assert isinstance(events[1], ToolFinished)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Ho gaya yaar"
    assert isinstance(events[-1], AgentReply)


def test_capabilities_reflect_available_tools() -> None:
    agent = CompanionAgent(FakeLLM(), make_settings())
    with_tools = _prepared(agent).request.instructions
    assert "save, list, change and delete reminders" in with_tools
    assert "# Using your tools" in with_tools
    assert "browse the internet" in with_tools  # no search_web tool → still unavailable

    plain = agent.prepare(_turn(uuid.uuid4())).request.instructions
    assert "You cannot (yet): set reminders or manage tasks" in plain
    assert "# Using your tools" not in plain
