export interface OpenVikingConfig {
  readonly baseUrl: string;
  readonly account?: string;
  readonly user?: string;
  readonly apiKey?: string;
  readonly actorPeerId?: string;
  readonly timeoutMs?: number;
  readonly retries?: number;
  readonly concurrency?: number;
  readonly headers?: Readonly<Record<string, string>>;
  readonly cache?: { readonly capacity?: number; readonly ttlMs?: number } | false;
  readonly metrics?: { readonly samplesPerEndpoint?: number } | false;
}

export const DEFAULT_BASE_URL = "http://127.0.0.1:1933";
export const DEFAULT_TIMEOUT_MS = 30_000;
export const DEFAULT_RETRIES = 3;
export const DEFAULT_CONCURRENCY = 8;

export const makeConfig = (overrides: Partial<OpenVikingConfig> = {}): OpenVikingConfig => ({
  baseUrl: DEFAULT_BASE_URL,
  timeoutMs: DEFAULT_TIMEOUT_MS,
  retries: DEFAULT_RETRIES,
  concurrency: DEFAULT_CONCURRENCY,
  ...overrides,
});

export const identityHeaders = (config: OpenVikingConfig): Record<string, string> => {
  const headers: Record<string, string> = { ...config.headers };
  if (config.apiKey) headers["X-API-Key"] = config.apiKey;
  if (config.account) headers["X-OpenViking-Account"] = config.account;
  if (config.user) headers["X-OpenViking-User"] = config.user;
  if (config.actorPeerId) headers["X-OpenViking-Actor-Peer"] = config.actorPeerId;
  return headers;
};
