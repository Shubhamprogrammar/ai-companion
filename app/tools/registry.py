from collections.abc import Iterable

from app.ai.llm import ToolSpec
from app.tools.base import Tool
from app.tools.schema import strict_json_schema


class ToolRegistry:
    """The closed set of actions the companion may take. Anything else is rejected."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        strict_json_schema(tool.Args)  # fail fast at startup if the schema isn't strict-safe
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=t.name, description=t.description, parameters=strict_json_schema(t.Args))
            for t in self._tools.values()
        ]

    def __len__(self) -> int:
        return len(self._tools)
