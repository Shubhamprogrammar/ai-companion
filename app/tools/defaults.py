"""The default tool set, filtered by configuration."""

from app.core.config import Settings
from app.memory.embeddings import EmbeddingProvider
from app.tools.confirmations import ConfirmationService, ConfirmPendingAction
from app.tools.memories import DeleteMemory, FindMemories
from app.tools.preferences import GetPreferences, UpdatePreferences
from app.tools.registry import ToolRegistry
from app.tools.reminders import CreateReminder, DeleteReminder, ListReminders, UpdateReminder
from app.tools.tasks import CreateTask, DeleteTask, ListTasks, UpdateTask
from app.tools.web_search import SearchWeb, WebSearchProvider


def build_registry(
    settings: Settings,
    embeddings: EmbeddingProvider,
    web_search: WebSearchProvider | None,
) -> ToolRegistry:
    registry = ToolRegistry()
    if not settings.tools_enabled:
        return registry
    tools = [
        CreateReminder(),
        ListReminders(),
        UpdateReminder(),
        DeleteReminder(),
        CreateTask(),
        ListTasks(),
        UpdateTask(),
        DeleteTask(),
        GetPreferences(),
        UpdatePreferences(),
        FindMemories(embeddings),
        DeleteMemory(embeddings),
    ]
    if web_search is not None:
        tools.append(SearchWeb(web_search))
    for tool in tools:
        if tool.name not in settings.tools_disabled:
            registry.register(tool)
    if any(t.confirmation != "never" for t in registry.tools()):
        registry.register(
            ConfirmPendingAction(lambda session: ConfirmationService(session, registry, settings))
        )
    return registry
