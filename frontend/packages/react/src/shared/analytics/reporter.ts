import type { RefObject } from 'react';
import type {
  OpenVikingAnalyticsEventMap,
  OpenVikingAnalyticsEventName,
  OpenVikingAnalyticsOptions,
} from '../../client/analytics/types.js';

export type EventKey =
  OpenVikingAnalyticsEventName extends `openviking_${infer K}` ? K : never;
export type Params<K extends EventKey> =
  OpenVikingAnalyticsEventMap[`openviking_${K}`];
export type ShowKey =
  Extract<EventKey, `${string}_show`> | 'directory_tour_start';

export function createReporter(
  _options: OpenVikingAnalyticsOptions,
  _instance: RefObject<string | undefined>,
) {
  return {
    enabled: (_key: EventKey) => false,
    emit<K extends EventKey>(_key: K, _params: () => Params<K>) {
      // Preserve the host API without forwarding analytics.
    },
  };
}
export type AnalyticsReporter = ReturnType<typeof createReporter>;

const createId = () =>
  globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);

/** Only created while collection is enabled, and never contains business input. */
export function startOperation() {
  const operation_id = createId();
  const start = performance.now();
  return {
    operation_id,
    finish: () => ({ operation_id, duration_ms: performance.now() - start }),
  };
}

export function errorKind(
  error: unknown,
): 'network' | 'http' | 'business' | 'permission' | 'unknown' {
  if (error instanceof Error && error.name === 'NotAllowedError')
    return 'permission';
  if (typeof error !== 'object' || !error) return 'unknown';
  const value = error as { code?: unknown; statusCode?: number };
  if (value.code === 'NETWORK_ERROR') return 'network';
  if (value.statusCode && value.statusCode >= 400) return 'http';
  return typeof value.code === 'string' ? 'business' : 'unknown';
}
