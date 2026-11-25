// HTTP client: JSON requests, consistent errors, and the token session.
//
// Tokens: the backend rotates refresh tokens and treats a reused one as theft (it revokes
// every session for the user). So refreshes are serialized across tabs with a Web Lock,
// and the session lives in localStorage so every tab sees the newest pair.

import type { TokenPair } from "./types";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api/v1";

const STORAGE_KEY = "pa.session";
const REFRESH_MARGIN_MS = 60_000;

export interface Session {
  access: string;
  refresh: string;
  expiresAt: number; // epoch ms
}

export interface ErrorDetail {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetail[] | undefined;

  constructor(status: number, code: string, message: string, details?: ErrorDetail[]) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** Field-level messages from a 422, keyed by the field name. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const d of this.details ?? []) {
      const field = String(d.loc.at(-1) ?? "");
      out[field] = d.msg.replace(/^Value error, /, "");
    }
    return out;
  }
}

/** A readable message for any thrown value. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const details = err.details
      ?.map((d) => `${d.loc.at(-1)}: ${d.msg.replace(/^Value error, /, "")}`)
      .join("; ");
    return details ? `${err.message} (${details})` : err.message;
  }
  if (err instanceof TypeError) return "Can't reach the server. Check your connection.";
  if (err instanceof Error) return err.message;
  return String(err);
}

// ------------------------------------------------------------------ session store

function readStored(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s.access && s.refresh && s.expiresAt ? s : null;
  } catch {
    return null;
  }
}

let session: Session | null = readStored();
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<(s: Session | null) => void>();

function applySession(next: Session | null, persist: boolean): void {
  session = next;
  if (persist) {
    if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORAGE_KEY);
  }
  clearTimeout(refreshTimer);
  if (next) {
    const wait = Math.max(5_000, next.expiresAt - Date.now() - REFRESH_MARGIN_MS);
    refreshTimer = setTimeout(() => void refreshSession(next.access).catch(() => {}), wait);
  }
  for (const fn of listeners) fn(next);
}

function sessionFromPair(pair: TokenPair): Session {
  return {
    access: pair.access_token,
    refresh: pair.refresh_token,
    expiresAt: Date.now() + pair.expires_in * 1000,
  };
}

// Another tab logged in, refreshed or logged out.
window.addEventListener("storage", (e) => {
  if (e.key === STORAGE_KEY) applySession(readStored(), false);
});

if (session) applySession(session, false); // schedules the refresh timer

export function getSession(): Session | null {
  return session;
}

export function onSessionChange(fn: (s: Session | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setTokens(pair: TokenPair): void {
  applySession(sessionFromPair(pair), true);
}

export function clearSession(): void {
  applySession(null, true);
}

async function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  if (navigator.locks?.request) {
    return navigator.locks.request("pa-auth-refresh", fn) as Promise<T>;
  }
  return fn();
}

let inflight: Promise<Session | null> | null = null;

/**
 * Get a fresh access token. `stale` is the token being replaced: if another tab already
 * rotated past it, that tab's newer session is adopted instead of refreshing again.
 */
export function refreshSession(stale?: string): Promise<Session | null> {
  inflight ??= withRefreshLock(async () => {
    const stored = readStored();
    if (!stored) {
      applySession(null, false);
      return null;
    }
    if (stored.access !== stale && stored.expiresAt - Date.now() > REFRESH_MARGIN_MS) {
      if (stored.access !== session?.access) applySession(stored, false);
      return stored;
    }
    try {
      const pair = await send<TokenPair>("POST", "/auth/refresh", {
        body: { refresh_token: stored.refresh },
        auth: false,
      });
      const next = sessionFromPair(pair);
      applySession(next, true);
      return next;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        applySession(null, true);
        return null;
      }
      throw err;
    }
  }).finally(() => {
    inflight = null;
  });
  return inflight;
}

/** A valid access token, refreshing first if it is about to expire. */
export async function accessToken(): Promise<string | null> {
  if (!session) return null;
  if (session.expiresAt - Date.now() < REFRESH_MARGIN_MS) {
    return (await refreshSession(session.access))?.access ?? null;
  }
  return session.access;
}

// ------------------------------------------------------------------ requests

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  body?: unknown;
  query?: Query;
  auth?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

async function toApiError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null);
  const err = body?.error;
  return new ApiError(
    response.status,
    err?.code ?? "http_error",
    err?.message ?? (response.statusText || `Request failed (${response.status})`),
    err?.details,
  );
}

/** Low-level fetch with auth + one retry after a 401. Returns the raw Response. */
export async function fetchRaw(
  method: string,
  path: string,
  { body, query, auth = true, signal }: RequestOptions = {},
): Promise<Response> {
  const attempt = async (token: string | null) => {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["content-type"] = "application/json";
    if (token) headers.authorization = `Bearer ${token}`;
    return fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  };

  const token = auth ? await accessToken() : null;
  if (auth && !token) throw new ApiError(401, "unauthenticated", "Please sign in again.");
  let response = await attempt(token);

  if (response.status === 401 && auth) {
    const fresh = await refreshSession(token ?? undefined);
    if (!fresh) throw new ApiError(401, "unauthenticated", "Your session has ended. Please sign in again.");
    response = await attempt(fresh.access);
  }
  if (!response.ok) throw await toApiError(response);
  return response;
}

export async function send<T>(method: string, path: string, options?: RequestOptions): Promise<T> {
  const response = await fetchRaw(method, path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const http = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    send<T>("GET", path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query) => send<T>("POST", path, { body, query }),
  patch: <T>(path: string, body: unknown) => send<T>("PATCH", path, { body }),
  delete: <T = void>(path: string, query?: Query) => send<T>("DELETE", path, { query }),
};
