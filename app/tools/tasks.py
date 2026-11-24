import uuid
from typing import Annotated, Any, Literal

from pydantic import Field

from app.models.enums import TaskStatus
from app.models.productivity import Task
from app.services.productivity import TaskService
from app.tools.base import Tool, ToolArgs, ToolContext
from app.tools.timeparse import parse_date

DUE = "Due date in the user's local calendar, YYYY-MM-DD, or null"


def task_view(task: Task) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


class CreateTask(Tool["CreateTask.Args"]):
    name = "create_task"
    description = "Add an item to the user's personal to-do list."

    class Args(ToolArgs):
        title: Annotated[str, Field(min_length=1, max_length=200)]
        description: Annotated[str | None, Field(max_length=2000)]
        due_date: str | None = Field(description=DUE)

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        task = await TaskService(ctx.session).create(
            ctx.user,
            title=args.title,
            description=args.description,
            due_date=parse_date(args.due_date) if args.due_date else None,
        )
        return task_view(task)


class ListTasks(Tool["ListTasks.Args"]):
    name = "list_tasks"
    description = "List the user's to-do items."

    class Args(ToolArgs):
        status: Literal["open", "todo", "in_progress", "done", "all"] = Field(
            description="'open' means not done yet"
        )

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        service = TaskService(ctx.session)
        if args.status == "open":
            todo, _ = await service.list_for_user(ctx.user, status=TaskStatus.TODO, limit=20)
            doing, _ = await service.list_for_user(
                ctx.user, status=TaskStatus.IN_PROGRESS, limit=20
            )
            items, more = [*doing, *todo][:20], len(doing) + len(todo) > 20
        else:
            status = None if args.status == "all" else TaskStatus(args.status)
            items, more = await service.list_for_user(ctx.user, status=status, limit=20)
        return {"tasks": [task_view(t) for t in items], "more": more}


class UpdateTask(Tool["UpdateTask.Args"]):
    name = "update_task"
    description = (
        "Change a to-do item or its status (e.g. mark it done). Get task_id from list_tasks or "
        "a previous result. Pass null for fields to keep."
    )

    class Args(ToolArgs):
        task_id: uuid.UUID
        title: Annotated[str | None, Field(max_length=200)]
        description: Annotated[str | None, Field(max_length=2000)]
        status: Literal["todo", "in_progress", "done"] | None
        due_date: str | None = Field(description=DUE)

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if args.title:
            changes["title"] = args.title
        if args.description is not None:
            changes["description"] = args.description
        if args.status:
            changes["status"] = TaskStatus(args.status)
        if args.due_date:
            changes["due_date"] = parse_date(args.due_date)
        return task_view(await TaskService(ctx.session).update(ctx.user, args.task_id, changes))


class DeleteTask(Tool["DeleteTask.Args"]):
    name = "delete_task"
    description = (
        "Permanently delete a to-do item. Needs the user's confirmation (the result will say "
        "so). To finish a task, prefer update_task status=done."
    )
    confirmation = "always"

    class Args(ToolArgs):
        task_id: uuid.UUID

    async def confirmation_summary(self, ctx: ToolContext, args: Args) -> str:
        task = await TaskService(ctx.session).get(ctx.user, args.task_id)
        return f'Delete task "{task.title}"'

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        await TaskService(ctx.session).delete(ctx.user, args.task_id)
        return {"deleted": True, "task_id": str(args.task_id)}
