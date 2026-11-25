import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../api/client";
import { memories as api, users } from "../api/endpoints";
import type { Memory, MemoryCategory } from "../api/types";
import { Icon } from "../components/Icon";
import { ConfirmDialog, Dialog, EmptyState, Field, PageHeader, Spinner, useToast } from "../components/ui";
import { COMPANION_NAME, humanize, relativeTime } from "../lib/format";

const CATEGORIES: MemoryCategory[] = [
  "personal", "preference", "goal", "project", "routine", "learning", "decision", "conversation_summary", "other",
];
const EDITABLE: MemoryCategory[] = CATEGORIES.filter((c) => c !== "conversation_summary");
const IMPORTANCE = ["", "Trivia", "Minor", "Useful", "Important", "Essential"];

export function MemoriesPage() {
  const toast = useToast();
  const [items, setItems] = useState<Memory[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState<MemoryCategory | "">("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ memory: Memory; similarity: number }[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [editing, setEditing] = useState<Memory | "new" | null>(null);
  const [deleting, setDeleting] = useState<Memory | null>(null);
  const [wipe, setWipe] = useState(false);
  const [memoryEnabled, setMemoryEnabled] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.list({ category: category || undefined, limit: 50 });
      setItems(page.items);
      setHasMore(page.has_more);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setLoading(false);
    }
  }, [category, toast]);

  useEffect(() => void load(), [load]);
  useEffect(() => {
    users.preferences().then((p) => setMemoryEnabled(p.memory_enabled)).catch(() => {});
  }, []);

  const loadMore = async () => {
    const page = await api.list({ category: category || undefined, limit: 50, offset: items.length });
    setItems((all) => [...all, ...page.items]);
    setHasMore(page.has_more);
  };

  const search = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return setResults(null);
    setSearching(true);
    try {
      setResults((await api.search(query.trim(), 20)).results);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setSearching(false);
    }
  };

  const replace = (m: Memory) => {
    setItems((all) => (all.some((x) => x.id === m.id) ? all.map((x) => (x.id === m.id ? m : x)) : [m, ...all]));
    setResults((r) => r?.map((x) => (x.memory.id === m.id ? { ...x, memory: m } : x)) ?? null);
  };
  const drop = (id: string) => {
    setItems((all) => all.filter((x) => x.id !== id));
    setResults((r) => r?.filter((x) => x.memory.id !== id) ?? null);
  };

  const shown = results ? results.map((r) => r.memory) : items;

  return (
    <div className="page">
      <PageHeader
        title="Memories"
        subtitle={`What ${COMPANION_NAME} remembers about you. Edit or delete anything, anytime.`}
        actions={
          <>
            <button className="btn" onClick={() => setWipe(true)} disabled={!items.length}>
              <Icon name="trash" /> Delete all
            </button>
            <button className="btn btn-primary" onClick={() => setEditing("new")}>
              <Icon name="plus" /> Add memory
            </button>
          </>
        }
      />

      {memoryEnabled === false && (
        <div className="banner">
          Memory is turned off, so {COMPANION_NAME} isn't learning anything new.{" "}
          <Link to="/settings">Turn it on in Settings</Link>
        </div>
      )}

      <div className="toolbar">
        <form className="search" onSubmit={search}>
          <Icon name="search" />
          <input
            className="input"
            placeholder="Search by meaning, e.g. “my work”"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              if (!e.target.value) setResults(null);
            }}
          />
          {results && (
            <button type="button" className="link-btn" onClick={() => { setQuery(""); setResults(null); }}>
              Clear
            </button>
          )}
        </form>
        <select className="input select" value={category} onChange={(e) => setCategory(e.target.value as MemoryCategory | "")} disabled={!!results} aria-label="Filter by category">
          <option value="">All categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{humanize(c)}</option>
          ))}
        </select>
      </div>

      {(loading || searching) && <Spinner />}
      {!loading && !searching && shown.length === 0 && (
        <EmptyState icon="brain" title={results ? "No matching memories" : "No memories yet"}>
          {results ? "Try different words." : `As you talk, ${COMPANION_NAME} will remember things that matter. You can also add them yourself.`}
        </EmptyState>
      )}

      <ul className="card-grid">
        {shown.map((m) => {
          const sim = results?.find((r) => r.memory.id === m.id)?.similarity;
          return (
            <li key={m.id} className="card memory">
              <div className="card-top">
                <span className={`tag tag-${m.category}`}>{humanize(m.category)}</span>
                <span className="stars" title={`Importance: ${IMPORTANCE[m.importance]}`} aria-label={`Importance ${m.importance} of 5`}>
                  {"●".repeat(m.importance)}
                  <span className="dim">{"●".repeat(5 - m.importance)}</span>
                </span>
              </div>
              <p className="memory-text">{m.content}</p>
              <div className="card-foot">
                <span className="muted small">
                  {m.source === "user" ? "Added by you" : m.source === "summary" ? "Conversation summary" : "Learned in chat"}
                  {" · "}
                  {relativeTime(m.updated_at)}
                  {sim !== undefined && ` · ${Math.round(sim * 100)}% match`}
                </span>
                <div className="row-actions">
                  {m.source_conversation_id && (
                    <Link className="icon-btn" to={`/c/${m.source_conversation_id}`} title="Open conversation" aria-label="Open source conversation">
                      <Icon name="chat" size={16} />
                    </Link>
                  )}
                  <button className="icon-btn" title="Edit" aria-label="Edit memory" onClick={() => setEditing(m)}>
                    <Icon name="edit" size={16} />
                  </button>
                  <button className="icon-btn" title="Delete" aria-label="Delete memory" onClick={() => setDeleting(m)}>
                    <Icon name="trash" size={16} />
                  </button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      {hasMore && !results && (
        <div className="center">
          <button className="btn" onClick={() => void loadMore()}>Load more</button>
        </div>
      )}

      {editing && (
        <MemoryEditor
          memory={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(m) => {
            replace(m);
            toast(editing === "new" ? "Memory added" : "Memory updated", "success");
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Forget this?"
          message={`“${deleting.content}”`}
          confirmLabel="Forget"
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            try {
              await api.remove(deleting.id);
              drop(deleting.id);
            } catch (err) {
              toast(errorMessage(err), "error");
            }
          }}
        />
      )}
      {wipe && (
        <ConfirmDialog
          title="Forget everything?"
          message={`This permanently deletes every memory ${COMPANION_NAME} has about you. It can't be undone.`}
          confirmLabel="Delete all memories"
          onClose={() => setWipe(false)}
          onConfirm={async () => {
            try {
              const { deleted } = await api.removeAll();
              setItems([]);
              setResults(null);
              toast(`Deleted ${deleted} memories`, "success");
            } catch (err) {
              toast(errorMessage(err), "error");
            }
          }}
        />
      )}
    </div>
  );
}

function MemoryEditor({ memory, onClose, onSaved }: { memory: Memory | null; onClose: () => void; onSaved: (m: Memory) => void }) {
  const [content, setContent] = useState(memory?.content ?? "");
  const [category, setCategory] = useState<MemoryCategory>(memory?.category ?? "personal");
  const [importance, setImportance] = useState(memory?.importance ?? 4);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { content: content.trim(), category, importance };
      onSaved(memory ? await api.update(memory.id, body) : await api.create(body));
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={memory ? "Edit memory" : "Add a memory"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" form="memory-form" disabled={busy || content.trim().length < 3}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <form id="memory-form" className="form" onSubmit={save}>
        <Field label="What should I remember?">
          <textarea className="input" rows={3} autoFocus value={content} onChange={(e) => setContent(e.target.value)} placeholder="e.g. I'm vegetarian and allergic to peanuts" />
        </Field>
        <div className="form-row">
          <Field label="Category">
            <select className="input select" value={category} onChange={(e) => setCategory(e.target.value as MemoryCategory)}>
              {(memory?.category === "conversation_summary" ? CATEGORIES : EDITABLE).map((c) => (
                <option key={c} value={c}>{humanize(c)}</option>
              ))}
            </select>
          </Field>
          <Field label={`Importance: ${IMPORTANCE[importance]}`}>
            <input type="range" min={1} max={5} step={1} value={importance} onChange={(e) => setImportance(Number(e.target.value))} />
          </Field>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </Dialog>
  );
}
