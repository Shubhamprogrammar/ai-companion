import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../api/client";
import { tools as api } from "../api/endpoints";
import type { ToolExecution, ToolExecutionStatus, ToolInfo } from "../api/types";
import { useAppData } from "../components/AppData";
import { Icon } from "../components/Icon";
import { EmptyState, PageHeader, Spinner, useToast } from "../components/ui";
import { COMPANION_NAME, dateTime, humanize, relativeTime, toolLabel } from "../lib/format";

const STATUS_FILTERS: (ToolExecutionStatus | "")[] = ["", "succeeded", "awaiting_confirmation", "failed", "invalid", "cancelled", "expired"];

export function ActivityPage() {
  const toast = useToast();
  const { pending, reloadPending } = useAppData();
  const [status, setStatus] = useState<ToolExecutionStatus | "">("");
  const [items, setItems] = useState<ToolExecution[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [toolList, setToolList] = useState<ToolInfo[] | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.executions({ status: status || undefined, limit: 50 });
      setItems(page.items);
      setHasMore(page.has_more);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setLoading(false);
    }
  }, [status, toast]);
  useEffect(() => void load(), [load]);
  useEffect(() => void reloadPending(), [reloadPending]);

  const loadMore = async () => {
    const page = await api.executions({ status: status || undefined, limit: 50, offset: items.length });
    setItems((all) => [...all, ...page.items]);
    setHasMore(page.has_more);
  };

  const decide = async (p: ToolExecution, decision: "approve" | "reject") => {
    setWorking(p.id);
    try {
      const result = decision === "approve" ? await api.approve(p.id) : await api.reject(p.id);
      toast(
        decision === "reject" ? "Declined" : result.status === "succeeded" ? "Done" : `Didn't go through (${humanize(result.status)})`,
        result.status === "succeeded" || decision === "reject" ? "success" : "error",
      );
      await Promise.all([reloadPending(), load()]);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setWorking(null);
    }
  };

  return (
    <div className="page">
      <PageHeader title="Activity" subtitle={`Every action ${COMPANION_NAME} took or tried for you.`} />

      <section className="group">
        <h2 className="group-title">Waiting for your OK</h2>
        {pending.length === 0 ? (
          <p className="muted small">Nothing needs approval right now.</p>
        ) : (
          <ul className="list">
            {pending.map((p) => (
              <li key={p.id} className="list-item approval">
                <Icon name="checkSquare" />
                <div className="list-body">
                  <div className="list-title">{p.confirmation_summary ?? toolLabel(p.tool_name)}</div>
                  <div className="list-meta muted">
                    {p.expires_at && `Expires ${relativeTime(p.expires_at)}`}
                    {p.conversation_id && (
                      <>
                        {" · "}
                        <Link to={`/c/${p.conversation_id}`}>Open conversation</Link>
                      </>
                    )}
                  </div>
                </div>
                <div className="row-actions">
                  <button className="btn" disabled={working === p.id} onClick={() => void decide(p, "reject")}>Not now</button>
                  <button className="btn btn-primary" disabled={working === p.id} onClick={() => void decide(p, "approve")}>
                    {working === p.id ? "Working…" : "Approve"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="group">
        <div className="group-head">
          <h2 className="group-title">History</h2>
          <select className="input select select-sm" value={status} onChange={(e) => setStatus(e.target.value as ToolExecutionStatus | "")} aria-label="Filter by status">
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>{s ? humanize(s) : "All statuses"}</option>
            ))}
          </select>
        </div>
        {loading && <Spinner />}
        {!loading && items.length === 0 && (
          <EmptyState icon="activity" title="No activity yet">
            When {COMPANION_NAME} sets a reminder or adds a task, it shows up here.
          </EmptyState>
        )}
        <ul className="list">
          {items.map((x) => (
            <li key={x.id} className="list-item log">
              <button className="log-row" onClick={() => setOpen(open === x.id ? null : x.id)} aria-expanded={open === x.id}>
                <span className={`status-dot s-${x.status}`} title={humanize(x.status)} />
                <span className="list-body">
                  <span className="list-title">{x.confirmation_summary ?? toolLabel(x.tool_name)}</span>
                  <span className="list-meta muted">
                    {humanize(x.status)}
                    {x.error_code && ` (${x.error_code})`} · {dateTime(x.created_at)}
                  </span>
                </span>
                <Icon name="chevronDown" className={open === x.id ? "rot" : ""} />
              </button>
              {open === x.id && (
                <div className="log-detail">
                  <div>
                    <h4>Input</h4>
                    <pre>{JSON.stringify(x.input, null, 2)}</pre>
                  </div>
                  {x.output && (
                    <div>
                      <h4>Result</h4>
                      <pre>{JSON.stringify(x.output, null, 2)}</pre>
                    </div>
                  )}
                  {x.conversation_id && <Link to={`/c/${x.conversation_id}`}>Open conversation →</Link>}
                </div>
              )}
            </li>
          ))}
        </ul>
        {hasMore && (
          <div className="center">
            <button className="btn" onClick={() => void loadMore()}>Load more</button>
          </div>
        )}
      </section>

      <section className="group">
        <div className="group-head">
          <h2 className="group-title">What {COMPANION_NAME} can do</h2>
          {!toolList && (
            <button className="link-btn" onClick={() => api.list().then(setToolList).catch((e) => toast(errorMessage(e), "error"))}>
              Show
            </button>
          )}
        </div>
        {toolList && (
          <ul className="tool-list">
            {toolList.map((t) => (
              <li key={t.name}>
                <strong>{toolLabel(t.name)}</strong>
                <span className={`tag ${t.confirmation === "never" ? "" : "tag-goal"}`}>
                  {t.confirmation === "never" ? "automatic" : t.confirmation === "always" ? "asks first" : "sometimes asks"}
                </span>
                <p className="muted small">{t.description}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
