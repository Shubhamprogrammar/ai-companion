import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { clearSession, getSession, onSessionChange, refreshSession } from "../api/client";
import { auth, users } from "../api/endpoints";
import type { UserMe } from "../api/types";

interface AuthState {
  /** undefined while the stored session is being checked on first load. */
  user: UserMe | null | undefined;
  login(email: string, password: string): Promise<void>;
  logout(): Promise<void>;
  reloadUser(): Promise<void>;
  setUser(user: UserMe): void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserMe | null | undefined>(getSession() ? undefined : null);

  const reloadUser = useCallback(async () => {
    try {
      setUser(await users.me());
    } catch {
      setUser(null);
    }
  }, []);

  // Restore a stored session on load (refreshing it if the access token is stale).
  useEffect(() => {
    if (!getSession()) return;
    void (async () => {
      const s = getSession();
      if (s && s.expiresAt - Date.now() < 60_000) await refreshSession(s.access).catch(() => null);
      await reloadUser();
    })();
  }, [reloadUser]);

  // Logged out here or in another tab, or the refresh token was rejected.
  useEffect(
    () =>
      onSessionChange((s) => {
        if (!s) setUser(null);
        else setUser((u) => (u === null ? undefined : u));
      }),
    [],
  );

  // A login in another tab: load that user.
  useEffect(() => {
    if (user === undefined && getSession()) void reloadUser();
  }, [user, reloadUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      await auth.login(email, password);
      setUser(await users.me());
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await auth.logout(getSession()?.refresh);
    } catch {
      /* the local session is cleared regardless */
    }
    clearSession();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, login, logout, reloadUser, setUser }),
    [user, login, logout, reloadUser],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
