import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { chatStream, conversations as conversationsApi, tools } from "../api/endpoints";
import type { Message, ToolAction } from "../api/types";
import { useAppData } from "../components/AppData";
import { Icon } from "../components/Icon";
import { Timeline, type TimelineItem } from "../components/Timeline";
import { Spinner, useToast } from "../components/ui";
import { VoiceDock } from "../components/VoiceDock";
import { useAuth } from "../auth/AuthContext";
import { COMPANION_NAME } from "../lib/format";
import { VoiceSession, type ServerEvent, type VoiceStatus } from "../voice/VoiceSession";

const SUGGESTIONS = [
  "Remind me to call Mom tomorrow at 7pm",
  "What do you remember about me?",
  "Add a task: finish the project report by Friday",
  "What's on my list for this week?",
];

const VOICE_ERRORS: Record<string, string> = {
  no_speech_detected: "I didn't catch that — try again a little closer to the mic.",
  no_audio: "No audio came through. Check your microphone.",
  speech_unavailable: "Voice replies are unavailable right now; showing text instead.",
  ai_unavailable: `${COMPANION_NAME} is unavailable right now. Try again in a moment.`,
  rate_limited: "You're going a bit fast — wait a few seconds.",
  audio_too_large: "That was too long to process. Try a shorter message.",
};

let keySeq = 0;
const nextKey = () => `k${++keySeq}`;

function messageItem(m: Message): TimelineItem {
  return {
    kind: "message",
    key: m.id,
    role: m.role,
    content: m.content,
    voice: m.message_type === "voice",
    createdAt: m.created_at,
  };
}

export function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const app = useAppData();

  const [items, setItems] = useState<TimelineItem[]>([]);
  const [title, setTitle] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");

  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>("closed");
  const [level, setLevel] = useState(0);
  const [micAvailable, setMicAvailable] = useState(true);
  const [handsFree, setHandsFree] = useState(() => localStorage.getItem("pa.handsFree") === "1");
  const voiceOn = voiceStatus !== "closed";

  const abortRef = useRef<AbortController | null>(null);
  const voiceRef = useRef<VoiceSession | null>(null);
  const convIdRef = useRef<string | undefined>(conversationId);
  const adoptedRef = useRef<string | null>(null);
  const appRef = useRef(app);
  useLayoutEffect(() => {
    appRef.current = app;
  });

  // ---------------------------------------------------------------- timeline helpers
  const push = useCallback((...add: TimelineItem[]) => setItems((all) => [...all, ...add]), []);
  const patch = useCallback(
    (key: string, fn: (item: TimelineItem) => TimelineItem) =>
      setItems((all) => all.map((i) => (i.key === key ? fn(i) : i))),
    [],
  );
  const remove = useCallback((key: string) => setItems((all) => all.filter((i) => i.key !== key)), []);
  const insertBefore = useCallback((beforeKey: string | null, item: TimelineItem) => {
    setItems((all) => {
      const at = beforeKey ? all.findIndex((i) => i.key === beforeKey) : -1;
      if (at < 0) return [...all, item];
      return [...all.slice(0, at), item, ...all.slice(at)];
    });
  }, []);
  const appendText = useCallback(
    (key: string, text: string) =>
      patch(key, (i) => (i.kind === "message" ? { ...i, content: i.content + text } : i)),
    [patch],
  );

  /** A new conversation got its id from the server: move the URL without reloading. */
  const adopt = useCallback(
    (id: string) => {
      if (convIdRef.current === id) return;
      convIdRef.current = id;
      adoptedRef.current = id;
      navigate(`/c/${id}`, { replace: true });
    },
    [navigate],
  );

  const confirmItems = (actions: ToolAction[], have: TimelineItem[]): TimelineItem[] =>
    actions
      .filter((a) => a.confirmation)
      .filter((a) => !have.some((i) => i.kind === "confirm" && i.confirmationId === a.confirmation!.confirmation_id))
      .map((a) => ({
        kind: "confirm" as const,
        key: nextKey(),
        confirmationId: a.confirmation!.confirmation_id,
        summary: a.confirmation!.summary,
        expiresAt: a.confirmation!.expires_at,
        state: "pending" as const,
      }));

  const pushConfirmations = useCallback((actions: ToolAction[]) => {
    if (!actions.some((a) => a.confirmation)) return;
    setItems((all) => [...all, ...confirmItems(actions, all)]);
    void appRef.current.reloadPending();
  }, []);

  // ---------------------------------------------------------------- load conversation
  useEffect(() => {
    if (conversationId && adoptedRef.current === conversationId) {
      adoptedRef.current = null; // we just created it; the timeline is already right
      return;
    }
    convIdRef.current = conversationId;
    abortRef.current?.abort();
    voiceRef.current?.end();
    setItems([]);
    setTitle(null);
    setDraft("");
    if (!conversationId) return;

    const ac = new AbortController();
    setLoading(true);
    Promise.all([conversationsApi.get(conversationId), conversationsApi.allMessages(conversationId, ac.signal)])
      .then(([c, messages]) => {
        if (ac.signal.aborted) return;
        setTitle(c.title);
        setItems(messages.map(messageItem));
      })
      .catch((err) => {
        if (ac.signal.aborted) return;
        if (err instanceof ApiError && (err.status === 404 || err.status === 422)) {
          toast("That conversation doesn't exist anymore.", "error");
          navigate("/", { replace: true });
        } else {
          toast(errorMessage(err), "error");
        }
      })
      .finally(() => !ac.signal.aborted && setLoading(false));
    return () => ac.abort();
  }, [conversationId, navigate, toast]);

  // Leaving the chat page ends any voice session.
  useEffect(
    () => () => {
      voiceRef.current?.end();
      abortRef.current?.abort();
    },
    [],
  );

  // ---------------------------------------------------------------- scroll
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [items]);
  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  // ---------------------------------------------------------------- text turns (SSE)
  const sendText = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setDraft("");
    stickRef.current = true;

    if (voiceRef.current?.isOpen) {
      push({ kind: "message", key: nextKey(), role: "user", content: message, voice: false });
      voiceRef.current.sendText(message);
      return;
    }

    setBusy(true);
    const userKey = nextKey();
    const asstKey = nextKey();
    push(
      { kind: "message", key: userKey, role: "user", content: message, voice: false },
      { kind: "message", key: asstKey, role: "assistant", content: "", voice: false, streaming: true },
    );
    const running = new Map<string, string>();
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      for await (const ev of chatStream(message, convIdRef.current ?? null, ac.signal)) {
        switch (ev.event) {
          case "start":
            adopt(ev.data.conversation_id);
            break;
          case "delta":
            appendText(asstKey, ev.data.text);
            break;
          case "tool_started": {
            const key = nextKey();
            running.set(ev.data.tool, key);
            insertBefore(asstKey, { kind: "tool", key, tool: ev.data.tool, status: "running" });
            break;
          }
          case "tool": {
            const a = ev.data;
            const key = running.get(a.tool);
            running.delete(a.tool);
            const chip: TimelineItem = { kind: "tool", key: key ?? nextKey(), tool: a.tool, status: a.status, errorCode: a.error_code };
            if (key) patch(key, () => chip);
            else insertBefore(asstKey, chip);
            break;
          }
          case "done": {
            const d = ev.data;
            patch(userKey, () => ({ ...messageItem(d.user_message), key: userKey }));
            patch(asstKey, () => ({ ...messageItem(d.assistant_message), key: asstKey }));
            adopt(d.conversation.id);
            setTitle(d.conversation.title);
            appRef.current.upsertConversation(d.conversation);
            pushConfirmations(d.actions);
            break;
          }
          case "error":
            patch(asstKey, (i) =>
              i.kind === "message" ? { ...i, streaming: false, failed: ev.data.message || "Something went wrong." } : i,
            );
            break;
        }
      }
    } catch (err) {
      const aborted = ac.signal.aborted;
      patch(asstKey, (i) =>
        i.kind === "message"
          ? aborted
            ? { ...i, streaming: false, interrupted: true }
            : { ...i, streaming: false, failed: errorMessage(err) }
          : i,
      );
    } finally {
      // Drop the empty placeholder if nothing came back at all.
      setItems((all) =>
        all
          .map((i) => (i.key === asstKey && i.kind === "message" ? { ...i, streaming: false } : i))
          .filter((i) => !(i.key === asstKey && i.kind === "message" && !i.content && !i.failed && !i.interrupted)),
      );
      for (const key of running.values()) remove(key);
      abortRef.current = null;
      setBusy(false);
    }
  };

  // ---------------------------------------------------------------- voice turns (WebSocket)
  const voiceKeys = useRef<{ user: string | null; asst: string | null; tools: Map<string, string> }>({
    user: null,
    asst: null,
    tools: new Map(),
  });

  const onVoiceEvent = useCallback(
    (e: ServerEvent) => {
      const k = voiceKeys.current;
      switch (e.type) {
        case "session.ready":
          if (e.conversation_id) adopt(e.conversation_id);
          break;
        case "speech.stopped":
          k.user = nextKey();
          push({ kind: "message", key: k.user, role: "user", content: "", voice: true, streaming: true });
          stickRef.current = true;
          break;
        case "transcript.delta":
          if (!k.user) {
            k.user = nextKey();
            push({ kind: "message", key: k.user, role: "user", content: "", voice: true, streaming: true });
          }
          appendText(k.user, e.text);
          break;
        case "transcript.done":
          if (k.user) {
            const key = k.user;
            patch(key, (i) => (i.kind === "message" ? { ...i, content: e.text, streaming: false } : i));
          }
          k.user = null;
          break;
        case "response.started":
          k.asst = null;
          k.tools = new Map();
          break;
        case "response.text.delta":
          if (!k.asst) {
            k.asst = nextKey();
            push({ kind: "message", key: k.asst, role: "assistant", content: "", voice: true, streaming: true });
          }
          appendText(k.asst, e.text);
          break;
        case "tool.started": {
          const key = nextKey();
          k.tools.set(e.tool, key);
          insertBefore(k.asst, { kind: "tool", key, tool: e.tool, status: "running" });
          break;
        }
        case "tool.completed": {
          const a = e.action;
          const key = k.tools.get(a.tool);
          k.tools.delete(a.tool);
          const chip: TimelineItem = { kind: "tool", key: key ?? nextKey(), tool: a.tool, status: a.status, errorCode: a.error_code };
          if (key) patch(key, () => chip);
          else insertBefore(k.asst, chip);
          break;
        }
        case "response.done": {
          if (k.asst) {
            const key = k.asst;
            patch(key, () => ({ ...messageItem(e.assistant_message), key }));
          } else {
            push(messageItem(e.assistant_message));
          }
          for (const key of k.tools.values()) remove(key);
          k.asst = null;
          k.tools = new Map();
          adopt(e.conversation.id);
          setTitle(e.conversation.title);
          appRef.current.upsertConversation(e.conversation);
          pushConfirmations(e.actions);
          break;
        }
        case "response.cancelled":
          if (k.asst) {
            const key = k.asst;
            patch(key, (i) =>
              i.kind === "message"
                ? { ...i, content: e.saved_text || i.content, streaming: false, interrupted: true }
                : i,
            );
          }
          for (const key of k.tools.values()) remove(key);
          k.asst = null;
          k.tools = new Map();
          break;
        case "error": {
          if (k.user) {
            const key = k.user;
            setItems((all) =>
              all.filter((i) => !(i.key === key && i.kind === "message" && !i.content)),
            );
            k.user = null;
          }
          if (e.fatal) break; // onClose reports it
          push({
            kind: "notice",
            key: nextKey(),
            text: VOICE_ERRORS[e.code] ?? e.message,
            tone: e.code === "speech_unavailable" ? "info" : "warn",
          });
          break;
        }
        default:
          break;
      }
    },
    [adopt, appendText, insertBefore, patch, push, pushConfirmations, remove],
  );

  const startVoice = async () => {
    if (voiceRef.current || busy) return;
    const session = new VoiceSession(
      convIdRef.current ?? "new",
      { handsFree, threshold: 0.02 },
      {
        onStatus: setVoiceStatus,
        onLevel: setLevel,
        onEvent: onVoiceEvent,
        onMicError: (message) => {
          setMicAvailable(false);
          toast(message, "info");
        },
        onClose: ({ code, message }) => {
          if (voiceRef.current === session) voiceRef.current = null;
          setLevel(0);
          voiceKeys.current = { user: null, asst: null, tools: new Map() };
          setItems((all) =>
            all
              .map((i) => (i.kind === "message" && i.streaming ? { ...i, streaming: false } : i))
              .filter((i) => !(i.kind === "message" && !i.content)),
          );
          if (code !== 1000 && code !== 1005) toast(message, "error");
        },
      },
    );
    voiceRef.current = session;
    setMicAvailable(true);
    await session.connect();
    setMicAvailable(session.micAvailable);
  };

  const endVoice = () => voiceRef.current?.end();

  const toggleHandsFree = (on: boolean) => {
    setHandsFree(on);
    localStorage.setItem("pa.handsFree", on ? "1" : "0");
    voiceRef.current?.setHandsFree(on);
  };

  const talkStart = useCallback(() => voiceRef.current?.startTalking(), []);
  const talkEnd = useCallback(() => voiceRef.current?.stopTalking(), []);

  // ---------------------------------------------------------------- confirmations
  const decide = async (confirmationId: string, decision: "approve" | "reject") => {
    const setState = (state: Extract<TimelineItem, { kind: "confirm" }>["state"]) =>
      setItems((all) =>
        all.map((i) => (i.kind === "confirm" && i.confirmationId === confirmationId ? { ...i, state } : i)),
      );
    setState("working");
    try {
      const result = decision === "approve" ? await tools.approve(confirmationId) : await tools.reject(confirmationId);
      setState(result.status);
      if (decision === "approve") {
        toast(result.status === "succeeded" ? "Done" : `That didn't go through (${result.status}).`, result.status === "succeeded" ? "success" : "error");
      }
    } catch (err) {
      setState("pending");
      toast(errorMessage(err), "error");
    } finally {
      void app.reloadPending();
    }
  };

  // Approvals still waiting from earlier in this conversation.
  const earlier = useMemo<TimelineItem[]>(
    () =>
      app.pending
        .filter((p) => p.conversation_id && p.conversation_id === conversationId)
        .filter((p) => !items.some((i) => i.kind === "confirm" && i.confirmationId === p.id))
        .map((p) => ({
          kind: "confirm",
          key: `pending-${p.id}`,
          confirmationId: p.id,
          summary: p.confirmation_summary ?? p.tool_name,
          expiresAt: p.expires_at ?? new Date(Date.now() + 60_000).toISOString(),
          state: "pending",
        })),
    [app.pending, conversationId, items],
  );

  // ---------------------------------------------------------------- render
  const name = user?.profile.display_name?.split(" ")[0];
  const showWelcome = !loading && items.length === 0;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  return (
    <div className="chat">
      <header className="chat-header">
        <h1 className="chat-title">{conversationId ? title || "Untitled conversation" : "New conversation"}</h1>
      </header>

      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="chat-inner">
          {loading && <Spinner label="Loading conversation…" />}
          {showWelcome && (
            <div className="welcome">
              <div className="welcome-mark" aria-hidden="true">
                {COMPANION_NAME.charAt(0)}
              </div>
              <h2>{name ? `How can I help, ${name}?` : "How can I help?"}</h2>
              <p className="muted">
                Start by voice or text. {COMPANION_NAME} can recall context, and manage your reminders and tasks.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="suggestion" onClick={() => void sendText(s)}>
                    {s}
                  </button>
                ))}
              </div>
              <button className="btn btn-primary btn-lg" onClick={() => void startVoice()}>
                <Icon name="mic" /> Start voice session
              </button>
            </div>
          )}
          <Timeline items={[...items, ...earlier]} onDecide={(id, d) => void decide(id, d)} />
        </div>
      </div>

      <div className="composer-wrap">
        {voiceOn && (
          <VoiceDock
            status={voiceStatus}
            level={level}
            micAvailable={micAvailable}
            handsFree={handsFree}
            onHandsFree={toggleHandsFree}
            onTalkStart={talkStart}
            onTalkEnd={talkEnd}
            onStopReply={() => voiceRef.current?.cancelReply()}
            onEnd={endVoice}
          />
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void sendText(draft);
          }}
        >
          <textarea
            ref={textareaRef}
            className="composer-input"
            rows={1}
            maxLength={8000}
            placeholder={voiceOn ? "Or type here…" : `Message ${COMPANION_NAME}…`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void sendText(draft);
              }
            }}
            aria-label="Message"
          />
          {busy ? (
            <button type="button" className="icon-btn composer-btn" title="Stop" aria-label="Stop generating" onClick={() => abortRef.current?.abort()}>
              <Icon name="stop" />
            </button>
          ) : draft.trim() ? (
            <button type="submit" className="icon-btn composer-btn send" title="Send" aria-label="Send">
              <Icon name="send" />
            </button>
          ) : (
            !voiceOn && (
              <button type="button" className="icon-btn composer-btn mic" title="Talk" aria-label="Start voice" onClick={() => void startVoice()}>
                <Icon name="mic" />
              </button>
            )
          )}
        </form>
        <p className="composer-note muted small">
          {COMPANION_NAME} can make mistakes. Actions that change things ask for your OK first.
        </p>
      </div>
    </div>
  );
}
