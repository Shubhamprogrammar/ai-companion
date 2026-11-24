"""Rolling summaries of conversation turns that no longer fit the context window."""

from collections.abc import Sequence

from app.ai.llm import ChatMessage, LLMProvider, LLMRequest
from app.models.conversation import Message
from app.models.enums import MessageRole

MAX_SUMMARY_CHARS = 2000

SUMMARY_INSTRUCTIONS = """\
You maintain a running summary of a conversation between a user and their AI companion.
Merge the previous summary (if any) with the new messages into one updated summary.

- Keep what matters for continuing the conversation: the user's situation, feelings,
  plans, decisions, open questions, and anything the companion promised or suggested.
- Write concise plain English prose, at most about 150 words, even if the conversation
  is in Hindi or Hinglish. Keep names and specific details exactly.
- Do not invent anything. Do not include greetings or filler.
- Output only the summary text.
"""


class ConversationSummarizer:
    def __init__(self, llm: LLMProvider, model: str | None) -> None:
        self._llm = llm
        self._model = model

    async def summarize(self, previous_summary: str | None, messages: Sequence[Message]) -> str:
        transcript = "\n".join(
            f"{'User' if m.role is MessageRole.USER else 'Companion'}: {m.content}"
            for m in messages
        )
        prompt = (
            f"<previous_summary>\n{previous_summary or '(none)'}\n</previous_summary>\n\n"
            f"<new_messages>\n{transcript}\n</new_messages>"
        )
        response = await self._llm.generate(
            LLMRequest(
                instructions=SUMMARY_INSTRUCTIONS,
                messages=[ChatMessage("user", prompt)],
                model=self._model,
                max_output_tokens=800,
            )
        )
        return response.text.strip()[:MAX_SUMMARY_CHARS]
