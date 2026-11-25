// Mirrors the backend's Pydantic schemas (backend/app/schemas/*). Keep in sync.

export type UUID = string;
export type ISODateTime = string;

export type Language = "auto" | "en" | "hi" | "hinglish";
export type PersonalityStyle = "warm_friend";
export type ConversationStatus = "active" | "archived";
export type MessageRole = "user" | "assistant";
export type MessageType = "text" | "voice";
export type MemoryCategory =
  | "preference"
  | "personal"
  | "goal"
  | "learning"
  | "project"
  | "decision"
  | "routine"
  | "conversation_summary"
  | "other";
export type MemorySource = "user" | "conversation" | "summary";
export type ReminderStatus = "pending" | "done" | "cancelled";
export type TaskStatus = "todo" | "in_progress" | "done";
export type ToolExecutionStatus =
  | "succeeded"
  | "failed"
  | "invalid"
  | "awaiting_confirmation"
  | "cancelled"
  | "expired";

export interface Page<T> {
  items: T[];
  limit: number;
  offset: number;
  has_more: boolean;
}

// --- auth / users
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface AuthUser {
  id: UUID;
  email: string;
  is_active: boolean;
  created_at: ISODateTime;
}

export interface Profile {
  display_name: string | null;
  preferred_language: Language;
  timezone: string;
  updated_at: ISODateTime;
}

export interface UserMe extends AuthUser {
  profile: Profile;
}

export interface Preferences {
  personality: PersonalityStyle;
  voice: string | null;
  speech_speed: number;
  memory_enabled: boolean;
  conversation_retention_days: number | null;
  updated_at: ISODateTime;
}

// --- conversations / chat
export interface Conversation {
  id: UUID;
  title: string | null;
  summary: string | null;
  status: ConversationStatus;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface Message {
  id: UUID;
  conversation_id: UUID;
  role: MessageRole;
  content: string;
  message_type: MessageType;
  created_at: ISODateTime;
}

export interface PendingConfirmation {
  confirmation_id: UUID;
  summary: string;
  expires_at: ISODateTime;
}

export interface ToolAction {
  execution_id: UUID;
  tool: string;
  status: ToolExecutionStatus;
  error_code: string | null;
  confirmation: PendingConfirmation | null;
}

export interface ChatResponse {
  conversation: Conversation;
  user_message: Message;
  assistant_message: Message;
  actions: ToolAction[];
}

// --- memories
export interface Memory {
  id: UUID;
  content: string;
  category: MemoryCategory;
  importance: number;
  source: MemorySource;
  source_conversation_id: UUID | null;
  last_used_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface MemorySearchResult {
  memory: Memory;
  similarity: number;
}

// --- reminders / tasks
export interface Reminder {
  id: UUID;
  title: string;
  description: string | null;
  scheduled_at: ISODateTime;
  status: ReminderStatus;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface Task {
  id: UUID;
  title: string;
  description: string | null;
  status: TaskStatus;
  due_date: string | null; // YYYY-MM-DD
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

// --- tools
export interface ToolInfo {
  name: string;
  description: string;
  confirmation: "never" | "always" | "conditional";
}

export interface ToolExecution {
  id: UUID;
  tool_name: string;
  status: ToolExecutionStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error_code: string | null;
  confirmation_summary: string | null;
  expires_at: ISODateTime | null;
  conversation_id: UUID | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}
