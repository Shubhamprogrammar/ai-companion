import { useCallback, useEffect, useState, type FormEvent } from "react";
import { errorMessage } from "../api/client";
import { reminders as api } from "../api/endpoints";
import type { Reminder, ReminderStatus } from "../api/types";
import { Icon } from "../components/Icon";
import { ConfirmDialog, Dialog, EmptyState, Field, PageHeader, Segmented, Spinner, useToast } from "../components/ui";
import { COMPANION_NAME, dateTime, fromLocalInput, relativeTime, toLocalInput } from "../lib/format";

type Filter = ReminderStatus | "all";

function group(r: Reminder): string {
  if (r.status !== "pending") return r.status === "done" ? "Done" : "Cancelled";
  const at = new Date(r.scheduled_at);
  const now = new Date();
  if (at < now) return "Overdue";
  const endOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  if (at < endOfToday) return "Today";
  const endOfTomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2);
  if (at < endOfTomorrow) return "Tomorrow";
  return "Later";
}
const ORDER = ["Overdue", "Today", "Tomorrow", "Later", "Done", "Cancelled"];

export function RemindersPage() {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("pending");
  const [items, setItems] = useState<Reminder[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Reminder | "new" | null>(null);
  const [deleting, setDeleting] = useState<Reminder | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.list({ status: filter === "all" ? undefined : filter, limit: 100 });
      setItems(page.items);
      setHasMore(page.has_more);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setLoading(false);
    }
  }, [filter, toast]);
  useEffect(() => void load(), [load]);

  const loadMore = async () => {
    const page = await api.list({ status: filter === "all" ? undefined : filter, limit: 100, offset: items.length });
    setItems((all) => [...all, ...page.items]);
    setHasMore(page.has_more);
  };

  const upsert = (r: Reminder) =>
    setItems((all) => {
      const others = all.filter((x) => x.id !== r.id);
      return filter === "all" || r.status === filter ? [...others, r] : others;
    });

  const setStatus = async (r: Reminder, status: ReminderStatus) => {
    try {
      upsert(await api.update(r.id, { status }));
      toast(status === "done" ? "Nice — marked done" : status === "cancelled" ? "Reminder cancelled" : "Reminder restored", "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  };

  const sorted = [...items].sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));
  const groups = ORDER.map((g) => ({ name: g, items: sorted.filter((r) => group(r) === g) })).filter((g) => g.items.length);

  return (
    <div className="page">
      <PageHeader
        title="Reminders"
        subtitle={`Ask ${COMPANION_NAME} in chat (“remind me to…”) or add them here.`}
        actions={
          <button className="btn btn-primary" onClick={() => setEditing("new")}>
            <Icon name="plus" /> New reminder
          </button>
        }
      />
      <div className="toolbar">
        <Segmented
          label="Show"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "pending", label: "Upcoming" },
            { value: "done", label: "Done" },
            { value: "cancelled", label: "Cancelled" },
            { value: "all", label: "All" },
          ]}
        />
      </div>

      {loading && <Spinner />}
      {!loading && items.length === 0 && (
        <EmptyState icon="bell" title={filter === "pending" ? "Nothing coming up" : "No reminders here"}>
          {filter === "pending" && "Try saying “Remind me to drink water at 4pm”."}
        </EmptyState>
      )}

      {groups.map((g) => (
        <section key={g.name} className="group">
          <h2 className={`group-title ${g.name === "Overdue" ? "danger-text" : ""}`}>{g.name}</h2>
          <ul className="list">
            {g.items.map((r) => (
              <li key={r.id} className={`list-item status-${r.status}`}>
                <button
                  className={`check ${r.status === "done" ? "checked" : ""}`}
                  aria-label={r.status === "done" ? "Mark as pending" : "Mark as done"}
                  onClick={() => void setStatus(r, r.status === "done" ? "pending" : "done")}
                  disabled={r.status === "cancelled"}
                >
                  {r.status === "done" && <Icon name="check" size={14} />}
                </button>
                <div className="list-body">
                  <div className="list-title">{r.title}</div>
                  {r.description && <div className="list-desc muted">{r.description}</div>}
                  <div className="list-meta">
                    <Icon name="calendar" size={13} /> {dateTime(r.scheduled_at)}
                    <span className="muted"> · {relativeTime(r.scheduled_at)}</span>
                  </div>
                </div>
                <div className="row-actions">
                  {r.status === "pending" && (
                    <button className="link-btn" onClick={() => void setStatus(r, "cancelled")}>Cancel</button>
                  )}
                  {r.status === "cancelled" && (
                    <button className="link-btn" onClick={() => void setStatus(r, "pending")}>Restore</button>
                  )}
                  <button className="icon-btn" aria-label="Edit reminder" title="Edit" onClick={() => setEditing(r)}>
                    <Icon name="edit" size={16} />
                  </button>
                  <button className="icon-btn" aria-label="Delete reminder" title="Delete" onClick={() => setDeleting(r)}>
                    <Icon name="trash" size={16} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {hasMore && (
        <div className="center">
          <button className="btn" onClick={() => void loadMore()}>Load more</button>
        </div>
      )}

      {editing && (
        <ReminderEditor
          reminder={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(r) => {
            upsert(r);
            toast(editing === "new" ? "Reminder set" : "Reminder updated", "success");
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Delete reminder?"
          message={`“${deleting.title}” will be removed permanently.`}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            try {
              await api.remove(deleting.id);
              setItems((all) => all.filter((x) => x.id !== deleting.id));
            } catch (err) {
              toast(errorMessage(err), "error");
            }
          }}
        />
      )}
    </div>
  );
}

function defaultTime(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  return toLocalInput(d.toISOString());
}

function ReminderEditor({ reminder, onClose, onSaved }: { reminder: Reminder | null; onClose: () => void; onSaved: (r: Reminder) => void }) {
  const [title, setTitle] = useState(reminder?.title ?? "");
  const [description, setDescription] = useState(reminder?.description ?? "");
  const [when, setWhen] = useState(reminder ? toLocalInput(reminder.scheduled_at) : defaultTime());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!when) return setError("Pick a date and time.");
    setBusy(true);
    setError(null);
    try {
      const body = { title: title.trim(), description: description.trim() || null, scheduled_at: fromLocalInput(when) };
      onSaved(reminder ? await api.update(reminder.id, body) : await api.create(body));
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={reminder ? "Edit reminder" : "New reminder"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" form="reminder-form" disabled={busy || !title.trim()}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <form id="reminder-form" className="form" onSubmit={save}>
        <Field label="Remind me to…">
          <input className="input" autoFocus maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Call the dentist" />
        </Field>
        <Field label="When">
          <input className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} required />
        </Field>
        <Field label="Notes">
          <textarea className="input" rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional" />
        </Field>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Dialog>
  );
}
