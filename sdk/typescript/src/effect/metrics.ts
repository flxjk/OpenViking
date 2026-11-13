import * as Effect from "effect/Effect";
import * as Ref from "effect/Ref";

export interface EndpointMetrics {
  readonly calls: number;
  readonly errors: number;
  readonly meanMs: number;
  readonly p50Ms: number;
  readonly p95Ms: number;
  readonly p99Ms: number;
}

export interface MetricsSnapshot {
  readonly totalCalls: number;
  readonly totalErrors: number;
  readonly endpoints: Readonly<Record<string, EndpointMetrics>>;
}

export interface MetricsSample {
  readonly endpoint: string;
  readonly durationMs: number;
  readonly ok: boolean;
  readonly at: number;
}

export interface MetricsOptions {
  readonly samplesPerEndpoint?: number;
  readonly sink?: (sample: MetricsSample) => void;
}

export interface MetricsShape {
  readonly record: (key: string, durationMs: number, ok: boolean) => Effect.Effect<void>;
  readonly track: <A, E>(key: string, effect: Effect.Effect<A, E>) => Effect.Effect<A, E>;
  readonly snapshot: () => Effect.Effect<MetricsSnapshot>;
  readonly reset: () => Effect.Effect<void>;
}

interface Bucket {
  readonly calls: number;
  readonly errors: number;
  readonly samples: readonly number[];
}

const DEFAULT_SAMPLES = 256;

const percentile = (sorted: readonly number[], fraction: number): number => {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(fraction * sorted.length) - 1));
  return sorted[index] ?? 0;
};

const summarise = (bucket: Bucket): EndpointMetrics => {
  const sorted = [...bucket.samples].sort((left, right) => left - right);
  const mean =
    sorted.length === 0 ? 0 : sorted.reduce((sum, value) => sum + value, 0) / sorted.length;
  return {
    calls: bucket.calls,
    errors: bucket.errors,
    meanMs: mean,
    p50Ms: percentile(sorted, 0.5),
    p95Ms: percentile(sorted, 0.95),
    p99Ms: percentile(sorted, 0.99),
  };
};

export const makeMetrics = (options: MetricsOptions = {}): MetricsShape => {
  const limit = options.samplesPerEndpoint ?? DEFAULT_SAMPLES;
  const state = Ref.makeUnsafe<ReadonlyMap<string, Bucket>>(new Map());

  const record = (key: string, durationMs: number, ok: boolean): Effect.Effect<void> =>
    Ref.update(state, (previous) => {
      const bucket = previous.get(key) ?? { calls: 0, errors: 0, samples: [] };
      const samples = [...bucket.samples, durationMs];
      const trimmed = samples.length > limit ? samples.slice(samples.length - limit) : samples;
      const next = new Map(previous);
      next.set(key, {
        calls: bucket.calls + 1,
        errors: bucket.errors + (ok ? 0 : 1),
        samples: trimmed,
      });
      return next;
    }).pipe(
      Effect.tap(() =>
        Effect.sync(() => {
          options.sink?.({ endpoint: key, durationMs, ok, at: Date.now() });
        }),
      ),
    );

  const track = <A, E>(key: string, effect: Effect.Effect<A, E>): Effect.Effect<A, E> =>
    Effect.gen(function* () {
      const started = Date.now();
      const outcome = yield* Effect.match(effect, {
        onFailure: (error) => ({ ok: false as const, error }),
        onSuccess: (value) => ({ ok: true as const, value }),
      });
      yield* record(key, Date.now() - started, outcome.ok);
      if (outcome.ok) return outcome.value;
      return yield* Effect.fail(outcome.error);
    });

  const snapshot = (): Effect.Effect<MetricsSnapshot> =>
    Ref.get(state).pipe(
      Effect.map((current) => {
        const endpoints: Record<string, EndpointMetrics> = {};
        let totalCalls = 0;
        let totalErrors = 0;
        for (const [key, bucket] of current) {
          endpoints[key] = summarise(bucket);
          totalCalls += bucket.calls;
          totalErrors += bucket.errors;
        }
        return { totalCalls, totalErrors, endpoints };
      }),
    );

  const reset = (): Effect.Effect<void> => Ref.set(state, new Map());

  return { record, track, snapshot, reset };
};
