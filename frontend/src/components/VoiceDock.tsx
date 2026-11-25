import { useEffect, useRef } from "react";
import { COMPANION_NAME } from "../lib/format";
import type { VoiceStatus } from "../voice/VoiceSession";
import { Icon } from "./Icon";

const STATUS_TEXT: Record<VoiceStatus, string> = {
  connecting: "Connecting…",
  idle: "Ready",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: `${COMPANION_NAME} is speaking`,
  closed: "Disconnected",
};

export function VoiceDock({
  status,
  level,
  micAvailable,
  handsFree,
  onHandsFree,
  onTalkStart,
  onTalkEnd,
  onStopReply,
  onEnd,
}: {
  status: VoiceStatus;
  level: number;
  micAvailable: boolean;
  handsFree: boolean;
  onHandsFree: (on: boolean) => void;
  onTalkStart: () => void;
  onTalkEnd: () => void;
  onStopReply: () => void;
  onEnd: () => void;
}) {
  const ready = status !== "connecting" && status !== "closed";
  const talkRef = useRef<HTMLButtonElement>(null);

  // Hold Space to talk (when not typing in a field).
  useEffect(() => {
    if (!ready || handsFree || !micAvailable) return;
    const typing = () => {
      const el = document.activeElement;
      return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
    };
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !typing()) {
        e.preventDefault();
        onTalkStart();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && !typing()) onTalkEnd();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [ready, handsFree, micAvailable, onTalkStart, onTalkEnd]);

  const scale = 1 + Math.min(0.5, level * 6);

  return (
    <div className={`voice-dock status-${status}`} aria-live="polite">
      <div className="orb-wrap">
        <div className="orb" style={{ transform: status === "listening" ? `scale(${scale})` : undefined }} />
      </div>
      <div className="voice-info">
        <div className="voice-status">{STATUS_TEXT[status]}</div>
        <div className="voice-hint muted small">
          {!micAvailable
            ? "Microphone off — type below"
            : handsFree
              ? "Hands-free: just start talking"
              : "Hold the button or Space to talk"}
        </div>
      </div>

      <div className="voice-controls">
        {micAvailable && !handsFree && (
          <button
            ref={talkRef}
            className={`btn talk-btn ${status === "listening" ? "live" : ""}`}
            disabled={!ready}
            onPointerDown={(e) => {
              e.preventDefault();
              talkRef.current?.setPointerCapture(e.pointerId);
              onTalkStart();
            }}
            onPointerUp={onTalkEnd}
            onPointerCancel={onTalkEnd}
            onContextMenu={(e) => e.preventDefault()}
          >
            <Icon name="mic" /> {status === "listening" ? "Release to send" : "Hold to talk"}
          </button>
        )}
        {micAvailable && (
          <label className="switch" title="Talk without holding a button">
            <input type="checkbox" checked={handsFree} onChange={(e) => onHandsFree(e.target.checked)} />
            <span className="switch-track" />
            <span className="small">Hands-free</span>
          </label>
        )}
        {(status === "speaking" || status === "thinking") && (
          <button className="icon-btn" title="Stop reply" aria-label="Stop reply" onClick={onStopReply}>
            <Icon name="stop" />
          </button>
        )}
        <button className="icon-btn end-call" title="End voice" aria-label="End voice session" onClick={onEnd}>
          <Icon name="phoneOff" />
        </button>
      </div>
    </div>
  );
}
