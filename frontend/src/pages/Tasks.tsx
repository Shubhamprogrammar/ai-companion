import { useCallback, useEffect, useState, type FormEvent } from "react";
import { errorMessage } from "../api/client";
import { tasks as api } from "../api/endpoints";
import type { Task, TaskStatus } from "../api/types";
import { Icon } from "../components/Icon";
import { ConfirmDialog, Dialog, EmptyState, Field, PageHeader, Spinner, useToast } from "../components/ui";
import { COMPANION_NAME, dateLabel, todayYmd } from "../lib/format";

const COLUMNS: { status: TaskStatus; title: string }[] = [
  { status: "todo", title: "To do" },
  { status: "in_progress", title: "In progress" },
  { status: "done", title: "Done" },
];
const NEXT: Record<TaskStatus, TaskStatus> = { todo: "in_progress", in_progress: "done", done: "todo" };

export function TasksPage() {
  const toast = useToast();
  const [items, setItems] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [quick, setQuick] = useState("");
  const [editing, setEditing] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState<Task | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const all: Task[] = [];
      for (let offset = 0; offset < 1000; offset += 100) {
        const page = await api.list({ limit: 100, offset });
        all.push(...page.items);
        if (!page.has_more) break;
      }
      setItems(all);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);
  useEffect(() => void load(), [load]);

  const upsert = (t: Task) => setItems((all) => (all.some((x) => x.id === t.id) ? all.map((x) => (x.id === t.id ? t : x)) : [t, ...all]));

  const quickAdd = async (e: FormEvent) => {
    e.preventDefault();
    const title = quick.trim();
    if (!title) return;
    try {
      upsert(await api.create({ title }));
      setQuick("");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  };

  const move = async (t: Task, status: TaskStatus) => {
    const before = t;
    upsert({ ...t, status }); // optimistic
    try {
      upsert(await api.update(t.id, { status }));
    } catch (err) {
      upsert(before);
      toast(errorMessage(err), "error");
    }
  };

  const today = todayYmd();
  const sortTasks = (a: Task, b: Task) =>
    (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999") || b.updated_at.localeCompare(a.updated_at);

  return (
    <div className="page page-wide">
      <PageHeader title="Tasks" subtitle={`Your to-do list. ${COMPANION_NAME} can add and update these when you ask.`} />

      <form className="quick-add" onSubmit={quickAdd}>
        <Icon name="plus" />
        <input className="input" placeholder="Add a task and press Enter" maxLength={200} value={quick} onChange={(e) => setQuick(e.target.value)} />
      </form>

      {loading && <Spinner />}
      {!loading && items.length === 0 && (
        <EmptyState icon="checkSquare" title="No tasks yet">
          Add one above, or tell {COMPANION_NAME} “add a task to renew my passport”.
        </EmptyState>
      )}

      {!loading && items.length > 0 && (
        <div className="board">
          {COLUMNS.map((col) => {
            const list = items.filter((t) => t.status === col.status).sort(sortTasks);
            return (
              <section
                key={col.status}
                className={`column column-${col.status}`}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  const id = e.dataTransfer.getData("text/task");
                  const t = items.find((x) => x.id === id);
                  if (t && t.status !== col.status) void move(t, col.status);
                }}
              >
                <h2 className="column-title">
                  {col.title} <span className="count">{list.length}</span>
                </h2>
                <ul className="column-list">
                  {list.map((t) => {
                    const overdue = t.due_date && t.due_date < today && t.status !== "done";
                    return (
                      <li
                        key={t.id}
                        className="card task"
                        draggable
                        onDragStart={(e) => e.dataTransfer.setData("text/task", t.id)}
                      >
                        <div className="task-row">
                          <button
                            className={`check ${t.status === "done" ? "checked" : ""}`}
                            aria-label={t.status === "done" ? "Mark as to do" : "Mark as done"}
                            onClick={() => void move(t, t.status === "done" ? "todo" : "done")}
                          >
                            {t.status === "done" && <Icon name="check" size={14} />}
                          </button>
                          <button className="task-title" onClick={() => setEditing(t)}>
                            {t.title}
                          </button>
                        </div>
                        {t.description && <p className="list-desc muted">{t.description}</p>}
                        <div className="card-foot">
                          {t.due_date ? (
                            <span className={`due ${overdue ? "danger-text" : ""}`}>
                              <Icon name="calendar" size={13} /> {t.due_date === today ? "Today" : dateLabel(t.due_date)}
                            </span>
                          ) : (
                            <span />
                          )}
                          <div className="row-actions">
                            {t.status !== "done" && (
                              <button className="link-btn" onClick={() => void move(t, NEXT[t.status])}>
                                {t.status === "todo" ? "Start" : "Finish"}
                              </button>
                            )}
                            <button className="icon-btn" aria-label="Delete task" title="Delete" onClick={() => setDeleting(t)}>
                              <Icon name="trash" size={15} />
                            </button>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      {editing && (
        <TaskEditor
          task={editing}
          onClose={() => setEditing(null)}
          onSaved={(t) => {
            upsert(t);
            toast("Task updated", "success");
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Delete task?"
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

function TaskEditor({ task, onClose, onSaved }: { task: Task; onClose: () => void; onSaved: (t: Task) => void }) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [status, setStatus] = useState<TaskStatus>(task.status);
  const [due, setDue] = useState(task.due_date ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(
        await api.update(task.id, {
          title: title.trim(),
          description: description.trim() || null,
          status,
          due_date: due || null,
        }),
      );
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Edit task"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" form="task-form" disabled={busy || !title.trim()}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <form id="task-form" className="form" onSubmit={save}>
        <Field label="Title">
          <input className="input" autoFocus maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Notes">
          <textarea className="input" rows={3} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional" />
        </Field>
        <div className="form-row">
          <Field label="Status">
            <select className="input select" value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
              {COLUMNS.map((c) => (
                <option key={c.status} value={c.status}>{c.title}</option>
              ))}
            </select>
          </Field>
          <Field label="Due date">
            <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </Field>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Dialog>
  );
}
