import { fetchRaw, http, send, setTokens } from "./client";
import type {
  AuthUser,
  ChatResponse,
  Conversation,
  ConversationStatus,
  Language,
  Memory,
  MemoryCategory,
  MemorySearchResult,
  Message,
  Page,
  Preferences,
  Reminder,
  ReminderStatus,
  Task,
  TaskStatus,
  TokenPair,
  ToolAction,
  ToolExecution,
  ToolExecutionStatus,
  ToolInfo,
  UserMe,
  UUID,
} from "./types";

// ------------------------------------------------------------------ auth
export const auth = {
  async login(email: string, password: string): Promise<void> {
    const pair = await send<TokenPair>("POST", "/auth/login", {
      body: { email, password },
      auth: false,
    });
    setTokens(pair);
  },
  register(body: {
    email: string;
    password: string;
    display_name?: string;
    preferred_language?: Language;
    timezone?: string;
  }): Promise<AuthUser> {
    return send<AuthUser>("POST", "/auth/register", { body, auth: false });
  },
  logout(refreshToken: string | undefined): Promise<void> {
    return http.post<void>("/auth/logout", { refresh_token: refreshToken ?? null });
  },
};

// ------------------------------------------------------------------ users
export type ProfileUpdate = Partial<{
  display_name: string | null;
  preferred_language: Language;
  timezone: string;
}>;
export type PreferencesUpdate = Partial<{
  voice: string | null;
  speech_speed: number;
  memory_enabled: boolean;
  conversation_retention_days: number | null;
}>;

export const users = {
  me: () => http.get<UserMe>("/users/me"),
  updateMe: (body: ProfileUpdate) => http.patch<UserMe>("/users/me", body),
  preferences: () => http.get<Preferences>("/users/me/preferences"),
  updatePreferences: (body: PreferencesUpdate) =>
    http.patch<Preferences>("/users/me/preferences", body),
};

// ------------------------------------------------------------------ conversations
export const conversations = {
  list: (q: { status?: ConversationStatus; limit?: number; offset?: number } = {}) =>
    http.get<Page<Conversation>>("/conversations", q),
  get: (id: UUID) => http.get<Conversation>(`/conversations/${id}`),
  update: (id: UUID, body: { title?: string | null; status?: ConversationStatus }) =>
    http.patch<Conversation>(`/conversations/${id}`, body),
  remove: (id: UUID) => http.delete(`/conversations/${id}`),
  messages: (id: UUID, q: { limit?: number; offset?: number } = {}, signal?: AbortSignal) =>
    http.get<Page<Message>>(`/conversations/${id}/messages`, q, signal),

  /** Every message (oldest first), fetched page by page. */
  async allMessages(id: UUID, signal?: AbortSignal): Promise<Message[]> {
    const out: Message[] = [];
    for (let offset = 0; offset < 5_000; offset += 100) {
      const page = await conversations.messages(id, { limit: 100, offset }, signal);
      out.push(...page.items);
      if (!page.has_more) break;
    }
    return out;
  },
};

// ------------------------------------------------------------------ chat (SSE)
export type ChatStreamEvent =
  | { event: "start"; data: { conversation_id: UUID } }
  | { event: "delta"; data: { text: string } }
  | { event: "tool_started"; data: { tool: string } }
  | { event: "tool"; data: ToolAction }
  | { event: "done"; data: ChatResponse }
  | { event: "error"; data: { code: string; message: string } };

/** POST /chat/stream and yield its Server-Sent Events as they arrive. */
export async function* chatStream(
  message: string,
  conversationId: UUID | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const body: Record<string, unknown> = { message };
  if (conversationId) body.conversation_id = conversationId;
  const response = await fetchRaw("POST", "/chat/stream", { body, signal });
  if (!response.body) throw new Error("Streaming is not supported by this browser.");

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n?/g, "\n");
    let split: number;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue; // comment / keep-alive
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const val = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = val;
        else if (field === "data") data.push(val);
      }
      if (!data.length) continue;
      yield { event, data: JSON.parse(data.join("\n")) } as ChatStreamEvent;
    }
  }
}

// ------------------------------------------------------------------ memories
export const memories = {
  list: (q: { category?: MemoryCategory; limit?: number; offset?: number } = {}) =>
    http.get<Page<Memory>>("/memories", q),
  create: (body: { content: string; category: MemoryCategory; importance: number }) =>
    http.post<Memory>("/memories", body),
  update: (id: UUID, body: Partial<{ content: string; category: MemoryCategory; importance: number }>) =>
    http.patch<Memory>(`/memories/${id}`, body),
  remove: (id: UUID) => http.delete(`/memories/${id}`),
  removeAll: () => http.delete<{ deleted: number }>("/memories", { confirm: true }),
  search: (query: string, limit = 10) =>
    http.post<{ results: MemorySearchResult[] }>("/memories/search", { query, limit }),
};

// ------------------------------------------------------------------ reminders
export const reminders = {
  list: (q: { status?: ReminderStatus; from?: string; to?: string; limit?: number; offset?: number } = {}) =>
    http.get<Page<Reminder>>("/reminders", q),
  create: (body: { title: string; description?: string | null; scheduled_at: string }) =>
    http.post<Reminder>("/reminders", body),
  update: (
    id: UUID,
    body: Partial<{ title: string; description: string | null; scheduled_at: string; status: ReminderStatus }>,
  ) => http.patch<Reminder>(`/reminders/${id}`, body),
  remove: (id: UUID) => http.delete(`/reminders/${id}`),
};

// ------------------------------------------------------------------ tasks
export const tasks = {
  list: (q: { status?: TaskStatus; limit?: number; offset?: number } = {}) =>
    http.get<Page<Task>>("/tasks", q),
  create: (body: { title: string; description?: string | null; due_date?: string | null }) =>
    http.post<Task>("/tasks", body),
  update: (
    id: UUID,
    body: Partial<{ title: string; description: string | null; status: TaskStatus; due_date: string | null }>,
  ) => http.patch<Task>(`/tasks/${id}`, body),
  remove: (id: UUID) => http.delete(`/tasks/${id}`),
};

// ------------------------------------------------------------------ tools
export const tools = {
  list: () => http.get<ToolInfo[]>("/tools"),
  executions: (q: { status?: ToolExecutionStatus; limit?: number; offset?: number } = {}) =>
    http.get<Page<ToolExecution>>("/tools/executions", q),
  pending: () => http.get<ToolExecution[]>("/tools/confirmations"),
  approve: (confirmationId: UUID) =>
    http.post<ToolExecution>(`/tools/confirmations/${confirmationId}/approve`),
  reject: (confirmationId: UUID) =>
    http.post<ToolExecution>(`/tools/confirmations/${confirmationId}/reject`),
};

// ------------------------------------------------------------------ voice
export const voice = {
  /** Synthesize speech (for previewing a voice). Returns an MP3 blob. */
  async synthesize(text: string, opts: { voice?: string | null; speed?: number } = {}): Promise<Blob> {
    const body: Record<string, unknown> = { text, format: "mp3" };
    if (opts.voice) body.voice = opts.voice;
    if (opts.speed) body.speed = opts.speed;
    const response = await fetchRaw("POST", "/voice/synthesize", { body });
    return response.blob();
  },
};

/** Voices the backend's TTS accepts (backend/app/core/config.py: openai_tts_voices). */
export const TTS_VOICES = [
  "alloy", "ash", "ballad", "coral", "echo", "fable", "onyx",
  "nova", "sage", "shimmer", "verse", "marin", "cedar",
];
