export const COMPANION_NAME: string = import.meta.env.VITE_COMPANION_NAME ?? "Mira";

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** "just now", "5 min ago", "yesterday", or a short date. */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = (then - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 7) return rtf.format(Math.round(diff / 86400), "day");
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function timeOnly(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** YYYY-MM-DD as a local date label (no timezone shift). */
export function dateLabel(ymd: string): string {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export function todayYmd(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** ISO string → value for <input type="datetime-local"> in local time. */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** <input type="datetime-local"> value (local time) → ISO with offset. */
export function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}

export function humanize(value: string): string {
  const s = value.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const TOOL_LABELS: Record<string, string> = {
  confirm_pending_action: "Confirming an action",
  create_reminder: "Creating a reminder",
  list_reminders: "Checking reminders",
  update_reminder: "Updating a reminder",
  delete_reminder: "Deleting a reminder",
  get_preferences: "Checking your settings",
  update_preferences: "Updating your settings",
  find_memories: "Searching memories",
  delete_memory: "Forgetting a memory",
  search_web: "Searching the web",
  create_task: "Adding a task",
  list_tasks: "Checking tasks",
  update_task: "Updating a task",
  delete_task: "Deleting a task",
};

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? humanize(tool);
}
