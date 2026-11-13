import * as Effect from "effect/Effect";
import * as Ref from "effect/Ref";

export interface CacheOptions {
  readonly capacity?: number;
  readonly ttlMs?: number;
}

export interface CacheStats {
  readonly hits: number;
  readonly misses: number;
  readonly size: number;
}

export interface CacheShape {
  readonly get: <A, E>(key: string, effect: Effect.Effect<A, E>) => Effect.Effect<A, E>;
  readonly invalidate: (key: string) => Effect.Effect<void>;
  readonly invalidateAll: () => Effect.Effect<void>;
  readonly size: () => Effect.Effect<number>;
  readonly stats: () => Effect.Effect<CacheStats>;
}

interface Entry {
  readonly value: unknown;
  readonly expiresAt: number;
}

interface State {
  readonly entries: ReadonlyMap<string, Entry>;
  readonly hits: number;
  readonly misses: number;
}

const DEFAULT_CAPACITY = 256;
const DEFAULT_TTL_MS = 5_000;

export const makeCache = (options: CacheOptions = {}): CacheShape => {
  const capacity = options.capacity ?? DEFAULT_CAPACITY;
  const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
  const state = Ref.makeUnsafe<State>({ entries: new Map(), hits: 0, misses: 0 });

  const get = <A, E>(key: string, effect: Effect.Effect<A, E>): Effect.Effect<A, E> =>
    Effect.gen(function* () {
      const now = Date.now();
      const current = yield* Ref.get(state);
      const hit = current.entries.get(key);
      if (hit && hit.expiresAt > now) {
        yield* Ref.update(state, (previous) => ({ ...previous, hits: previous.hits + 1 }));
        return hit.value as A;
      }

      const value = yield* effect;
      yield* Ref.update(state, (previous) => {
        const entries = new Map(previous.entries);
        entries.delete(key);
        entries.set(key, { value, expiresAt: now + ttlMs });
        while (entries.size > capacity) {
          const oldest = entries.keys().next();
          if (oldest.done) break;
          entries.delete(oldest.value);
        }
        return { entries, hits: previous.hits, misses: previous.misses + 1 };
      });
      return value;
    });

  const invalidate = (key: string): Effect.Effect<void> =>
    Ref.update(state, (previous) => {
      const entries = new Map(previous.entries);
      entries.delete(key);
      return { ...previous, entries };
    });

  const invalidateAll = (): Effect.Effect<void> =>
    Ref.update(state, (previous) => ({ ...previous, entries: new Map() }));

  const size = (): Effect.Effect<number> =>
    Ref.get(state).pipe(Effect.map((current) => current.entries.size));

  const stats = (): Effect.Effect<CacheStats> =>
    Ref.get(state).pipe(
      Effect.map((current) => ({
        hits: current.hits,
        misses: current.misses,
        size: current.entries.size,
      })),
    );

  return { get, invalidate, invalidateAll, size, stats };
};
