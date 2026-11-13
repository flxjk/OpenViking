import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Ref from "effect/Ref";

import { Domains, type DomainsShape } from "./domains.js";
import type { OpenVikingError } from "./errors.js";
import type { SearchResult, SearchHit } from "./responses.js";

export interface FeedbackRecord {
  readonly query: string;
  readonly uri: string;
  readonly score: number;
  readonly used: boolean;
  readonly at: number;
}

export interface AdaptivePolicy {
  readonly limit: number;
  readonly minScore: number;
  readonly samples: number;
  readonly hitRate: number;
}

export interface FeedbackStats {
  readonly samples: number;
  readonly used: number;
  readonly hitRate: number;
  readonly meanScore: number;
  readonly policy: AdaptivePolicy;
}

export interface FeedbackOptions {
  readonly initialLimit?: number;
  readonly initialMinScore?: number;
  readonly minLimit?: number;
  readonly maxLimit?: number;
  readonly minScoreFloor?: number;
  readonly maxScoreCeiling?: number;
  readonly smoothing?: number;
  readonly load?: () => readonly FeedbackRecord[];
  readonly persist?: (record: FeedbackRecord) => void;
}

interface State {
  readonly records: readonly FeedbackRecord[];
  readonly pending: ReadonlyMap<string, readonly SearchHit[]>;
  readonly hitRate: number;
  readonly meanScore: number;
}

const DEFAULTS = {
  initialLimit: 10,
  initialMinScore: 0.2,
  minLimit: 3,
  maxLimit: 50,
  minScoreFloor: 0.05,
  maxScoreCeiling: 0.7,
  smoothing: 0.2,
} as const;

const clamp = (value: number, low: number, high: number): number =>
  Math.min(high, Math.max(low, value));

export interface FeedbackShape {
  readonly record: (record: FeedbackRecord) => Effect.Effect<void>;
  readonly policy: () => Effect.Effect<AdaptivePolicy>;
  readonly stats: () => Effect.Effect<FeedbackStats>;
  readonly find: (
    query: string,
    options?: { readonly limit?: number; readonly minScore?: number },
  ) => Effect.Effect<SearchResult, OpenVikingError>;
  readonly markUsed: (uri: string) => Effect.Effect<boolean>;
  readonly reset: () => Effect.Effect<void>;
}

export class Feedback extends Context.Service<Feedback, FeedbackShape>()("Feedback") {}

const derivePolicy = (
  state: State,
  options: Required<Omit<FeedbackOptions, "load" | "persist">>,
): AdaptivePolicy => {
  const samples = state.records.length;
  const hitRate = state.hitRate;
  const meanScore = state.meanScore;

  const limit = clamp(
    Math.round(options.initialLimit * (1.6 - hitRate)),
    options.minLimit,
    options.maxLimit,
  );
  const minScore = clamp(
    options.initialMinScore + (hitRate - 0.5) * 0.4 + (meanScore - 0.5) * 0.2,
    options.minScoreFloor,
    options.maxScoreCeiling,
  );

  return { limit, minScore, samples, hitRate };
};

export const makeFeedback = (
  domains: DomainsShape,
  overrides: FeedbackOptions = {},
): Effect.Effect<FeedbackShape> =>
  Effect.gen(function* () {
    const options = { ...DEFAULTS, ...overrides };
    const state = yield* Ref.make<State>({
      records: [],
      pending: new Map(),
      hitRate: 0.5,
      meanScore: 0.5,
    });

    const loaded = (options.load?.() ?? []).filter((entry) => Number.isFinite(entry.score));
    if (loaded.length > 0) {
      yield* Ref.update(state, (current) => {
        const smoothing = options.smoothing;
        let hitRate = current.hitRate;
        let meanScore = current.meanScore;
        for (const entry of loaded) {
          hitRate = hitRate * (1 - smoothing) + (entry.used ? 1 : 0) * smoothing;
          meanScore = meanScore * (1 - smoothing) + entry.score * smoothing;
        }
        return { ...current, records: [...current.records, ...loaded], hitRate, meanScore };
      });
    }

    const policy = (): Effect.Effect<AdaptivePolicy> =>
      Ref.get(state).pipe(Effect.map((current) => derivePolicy(current, options)));

    const record = (entry: FeedbackRecord): Effect.Effect<void> =>
      Ref.update(state, (current) => {
        const smoothing = options.smoothing;
        const hitRate = current.hitRate * (1 - smoothing) + (entry.used ? 1 : 0) * smoothing;
        const meanScore = current.meanScore * (1 - smoothing) + entry.score * smoothing;
        return {
          ...current,
          records: [...current.records, entry],
          hitRate,
          meanScore,
        };
      }).pipe(
        Effect.tap(() =>
          Effect.sync(() => {
            options.persist?.(entry);
          }),
        ),
      );

    const find = (
      query: string,
      findOptions?: { readonly limit?: number; readonly minScore?: number },
    ): Effect.Effect<SearchResult, OpenVikingError> =>
      Effect.gen(function* () {
        const current = yield* policy();
        const limit = findOptions?.limit ?? current.limit;
        const minScore = findOptions?.minScore ?? current.minScore;
        const result = yield* domains.search.find({ query, limit });
        const hits = [...result.memories, ...result.resources, ...result.skills].filter(
          (hit) => hit.score >= minScore,
        );
        yield* Ref.update(state, (previous) => {
          const pending = new Map(previous.pending);
          pending.set(query, hits);
          return { ...previous, pending };
        });
        return {
          memories: result.memories.filter((hit) => hit.score >= minScore),
          resources: result.resources.filter((hit) => hit.score >= minScore),
          skills: result.skills.filter((hit) => hit.score >= minScore),
          total: hits.length,
        };
      });

    const markUsed = (uri: string): Effect.Effect<boolean> =>
      Effect.gen(function* () {
        const current = yield* Ref.get(state);
        for (const [query, hits] of current.pending) {
          const hit = hits.find((candidate) => candidate.uri === uri);
          if (!hit) continue;
          yield* record({ query, uri, score: hit.score, used: true, at: Date.now() });
          yield* Ref.update(state, (previous) => {
            const pending = new Map(previous.pending);
            pending.delete(query);
            return { ...previous, pending };
          });
          return true;
        }
        return false;
      });

    const stats = (): Effect.Effect<FeedbackStats> =>
      Effect.gen(function* () {
        const current = yield* Ref.get(state);
        const used = current.records.filter((entry) => entry.used).length;
        const meanScore =
          current.records.length === 0
            ? 0
            : current.records.reduce((sum, entry) => sum + entry.score, 0) / current.records.length;
        return {
          samples: current.records.length,
          used,
          hitRate: current.hitRate,
          meanScore,
          policy: derivePolicy(current, options),
        };
      });

    const reset = (): Effect.Effect<void> =>
      Ref.set(state, {
        records: [],
        pending: new Map(),
        hitRate: 0.5,
        meanScore: 0.5,
      });

    return { record, policy, stats, find, markUsed, reset };
  });

export const layer = (overrides: FeedbackOptions = {}) =>
  Layer.effect(
    Feedback,
    Effect.gen(function* () {
      const domains = yield* Domains;
      return yield* makeFeedback(domains, overrides);
    }),
  );
