"""Delete conversations older than each user's `conversation_retention_days`.

Run periodically (e.g. daily via cron / a systemd timer):
    python -m scripts.purge_conversations
    docker compose exec api python -m scripts.purge_conversations
"""

import asyncio

from app.core.config import get_settings
from app.core.database import Database
from app.core.logging import configure_logging
from app.services.conversations import ConversationService


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            deleted = await ConversationService(session).purge_expired()
    finally:
        await database.dispose()
    print(f"Deleted {deleted} expired conversation(s).")
    return deleted


if __name__ == "__main__":
    asyncio.run(main())
