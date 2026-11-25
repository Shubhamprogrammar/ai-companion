/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base path or URL. Default "/api/v1" (same origin, proxied). */
  readonly VITE_API_BASE?: string;
  /** The companion's display name. Default "Mira". */
  readonly VITE_COMPANION_NAME?: string;
}
