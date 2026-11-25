import { useState } from "react";
import type { ToolExecutionStatus } from "../api/types";
import { COMPANION_NAME, dateTime, timeOnly, toolLabel } from "../lib/format";
import { Icon } from "./Icon";

export type TimelineItem =
  | {
      kind: "message";
      key: string;
      role: "user" | "assistant";
      content: string;
      voice: boolean;
      createdAt?: string;
      streaming?: boolean;
      interrupted?: boolean;
      failed?: string;
    }
  | { kind: "tool"; key: string; tool: string; status: ToolExecutionStatus | "running"; errorCode?: string | null }
  | {
      kind: "confirm";
      key: string;
      confirmationId: string;
      summary: string;
      expiresAt: string;
      state: "pending" | "working" | ToolExecutionStatus;
    }
  | { kind: "notice"; key: string; text: string; tone: "info" | "warn" | "error" };

const TOOL_STATUS_TEXT: Partial<Record<ToolExecutionStatus, string>> = {
  failed: "failed",
  invalid: "couldn't run",
  cancelled: "cancelled",
  expired: "expired",
  awaiting_confirmation: "needs your OK",
};

export function Timeline({
  items,
  onDecide,
}: {
  items: TimelineItem[];
  onDecide: (confirmationId: string, decision: "approve" | "reject") => void;
}) {
  return (
    <ol className="timeline">
      {items.map((item) => {
        switch (item.kind) {
          case "message":
            return <MessageBubble key={item.key} item={item} />;
          case "tool":
            return (
              <li key={item.key} className={`tool-chip tool-${item.status}`}>
                {item.status === "running" ? <span className="dot-pulse" /> : <Icon name={item.status === "succeeded" ? "check" : "activity"} size={14} />}
                <span>
                  {toolLabel(item.tool)}
                  {item.status !== "running" && item.status !== "succeeded" && ` — ${TOOL_STATUS_TEXT[item.status] ?? item.status}`}
                </span>
              </li>
            );
          case "confirm":
            return <ConfirmCard key={item.key} item={item} onDecide={onDecide} />;
          case "notice":
            return (
              <li key={item.key} className={`notice notice-${item.tone}`}>
                {item.text}
              </li>
            );
        }
      })}
    </ol>
  );
}

function MessageBubble({ item }: { item: Extract<TimelineItem, { kind: "message" }> }) {
  const mine = item.role === "user";
  const [showTime, setShowTime] = useState(false);
  const empty = !item.content;
  return (
    <li className={`msg ${mine ? "msg-user" : "msg-assistant"} ${item.interrupted ? "interrupted" : ""}`}>
      {!mine && (
        <div className="msg-avatar" aria-hidden="true">
          {COMPANION_NAME.charAt(0)}
        </div>
      )}
      <div className="msg-col">
        <div
          className="bubble"
          onClick={() => item.createdAt && setShowTime(!showTime)}
          title={item.createdAt ? dateTime(item.createdAt) : undefined}
        >
          <span className="sr-only">{mine ? "You" : COMPANION_NAME}: </span>
          {empty && item.streaming ? (
            <span className="typing" aria-label="Typing">
              <i />
              <i />
              <i />
            </span>
          ) : (
            item.content
          )}
          {item.streaming && !empty && <span className="caret" />}
        </div>
        <div className="msg-meta">
          {item.voice && (
            <span className="meta-voice" title="Spoken">
              <Icon name="wave" size={12} /> voice
            </span>
          )}
          {item.interrupted && <span>interrupted</span>}
          {item.failed && <span className="meta-error">{item.failed}</span>}
          {showTime && item.createdAt && <span>{timeOnly(item.createdAt)}</span>}
        </div>
      </div>
    </li>
  );
}

function ConfirmCard({
  item,
  onDecide,
}: {
  item: Extract<TimelineItem, { kind: "confirm" }>;
  onDecide: (confirmationId: string, decision: "approve" | "reject") => void;
}) {
  const expired = item.state === "pending" && new Date(item.expiresAt).getTime() < Date.now();
  const state = expired ? "expired" : item.state;
  return (
    <li className={`confirm-card state-${state}`}>
      <div className="confirm-head">
        <Icon name="checkSquare" size={14} />
        <span>Approval required</span>
      </div>
      <p className="confirm-summary">{item.summary}</p>
      {state === "pending" || state === "working" ? (
        <div className="confirm-actions">
          <button className="btn" disabled={state === "working"} onClick={() => onDecide(item.confirmationId, "reject")}>
            Not now
          </button>
          <button
            className="btn btn-primary"
            disabled={state === "working"}
            onClick={() => onDecide(item.confirmationId, "approve")}
          >
            {state === "working" ? "Working…" : "Approve"}
          </button>
        </div>
      ) : (
        <p className="confirm-result">
          {state === "succeeded" && "✓ Done"}
          {state === "cancelled" && "Declined"}
          {state === "expired" && "This request expired"}
          {state === "failed" && "It didn't work — try asking again"}
          {state === "invalid" && "This request is no longer valid"}
          {state === "awaiting_confirmation" && "Waiting…"}
        </p>
      )}
    </li>
  );
}
