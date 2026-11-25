// App-wide data the sidebar and pages share: the conversation list and pending approvals.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { conversations as conversationsApi, tools } from "../api/endpoints";
import type { Conversation, ConversationStatus, ToolExecution } from "../api/types";

interface AppData {
  conversations: Conversation[];
  conversationsLoaded: boolean;
  hasMoreConversations: boolean;
  showArchived: boolean;
  setShowArchived(v: boolean): void;
  reloadConversations(): Promise<void>;
  loadMoreConversations(): Promise<void>;
  upsertConversation(c: Conversation): void;
  removeConversation(id: string): void;
  pending: ToolExecution[];
  reloadPending(): Promise<void>;
}

const Ctx = createContext<AppData | null>(null);
const PAGE = 30;

export function AppDataProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [pending, setPending] = useState<ToolExecution[]>([]);

  const status: ConversationStatus = showArchived ? "archived" : "active";

  const reloadConversations = useCallback(async () => {
    try {
      const page = await conversationsApi.list({ status, limit: PAGE });
      setList(page.items);
      setHasMore(page.has_more);
    } finally {
      setLoaded(true);
    }
  }, [status]);

  const loadMoreConversations = useCallback(async () => {
    const page = await conversationsApi.list({ status, limit: PAGE, offset: list.length });
    setList((l) => [...l, ...page.items.filter((c) => !l.some((x) => x.id === c.id))]);
    setHasMore(page.has_more);
  }, [status, list.length]);

  const upsertConversation = useCallback(
    (c: Conversation) => {
      setList((l) => {
        const others = l.filter((x) => x.id !== c.id);
        if (c.status !== status) return others;
        return [c, ...others].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      });
    },
    [status],
  );

  const removeConversation = useCallback((id: string) => {
    setList((l) => l.filter((x) => x.id !== id));
  }, []);

  const reloadPending = useCallback(async () => {
    try {
      setPending(await tools.pending());
    } catch {
      /* non-critical */
    }
  }, []);

  useEffect(() => {
    setLoaded(false);
    void reloadConversations();
  }, [reloadConversations]);

  useEffect(() => {
    void reloadPending();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void reloadPending();
    }, 30_000);
    return () => clearInterval(t);
  }, [reloadPending]);

  const value = useMemo<AppData>(
    () => ({
      conversations: list,
      conversationsLoaded: loaded,
      hasMoreConversations: hasMore,
      showArchived,
      setShowArchived,
      reloadConversations,
      loadMoreConversations,
      upsertConversation,
      removeConversation,
      pending,
      reloadPending,
    }),
    [list, loaded, hasMore, showArchived, reloadConversations, loadMoreConversations, upsertConversation, removeConversation, pending, reloadPending],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAppData(): AppData {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppData must be used inside <AppDataProvider>");
  return ctx;
}
