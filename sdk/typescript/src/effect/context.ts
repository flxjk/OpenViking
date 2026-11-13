import * as Effect from "effect/Effect";

import type { EndpointBody } from "../generated/endpoints.js";
import type { RecallApiV1SearchRecallPostResponse } from "../generated/responses.js";
import type { DomainsShape } from "./domains.js";
import type { OpenVikingError } from "./errors.js";

export interface AssembleOptions {
  readonly query: string;
  readonly maxTokens?: number;
  readonly detail?: "auto" | "abstract" | "overview" | "full";
  readonly purpose?: "chat" | "coding";
  readonly minScore?: number;
  readonly sessionId?: string;
}

export interface ContextEntry {
  readonly uri: string;
  readonly category: string;
  readonly score: number;
  readonly text: string;
}

export interface ContextBlock {
  readonly text: string;
  readonly digest: string;
  readonly citations: readonly string[];
  readonly entries: readonly ContextEntry[];
  readonly tokensUsed: number;
  readonly truncated: boolean;
}

export const DEFAULT_MAX_TOKENS = 2000;
export const CHARS_PER_TOKEN = 4;
export const TRUNCATION_MARKER = "\n…[truncated]";

const clip = (text: string, budget: number): string => {
  const head = text.slice(0, budget);
  const boundary = head.lastIndexOf("\n");
  return boundary > 0 ? head.slice(0, boundary) : head;
};

const rank = (entries: RecallApiV1SearchRecallPostResponse["entries"]): readonly ContextEntry[] =>
  [...entries]
    .sort((left, right) => right.score - left.score)
    .map((entry) => ({
      uri: entry.uri,
      category: entry.category,
      score: entry.score,
      text: entry.text,
    }));

const cite = (entries: readonly ContextEntry[]): readonly string[] => {
  const seen = new Set<string>();
  const citations: string[] = [];
  for (const entry of entries) {
    if (seen.has(entry.uri)) continue;
    seen.add(entry.uri);
    citations.push(entry.uri);
  }
  return citations;
};

export const assembleContext = (
  domains: DomainsShape,
  options: AssembleOptions,
): Effect.Effect<ContextBlock, OpenVikingError> =>
  Effect.gen(function* () {
    const maxTokens = options.maxTokens ?? DEFAULT_MAX_TOKENS;
    const body: EndpointBody<"recallApiV1SearchRecallPost"> = {
      query: options.query,
      ...(options.maxTokens === undefined ? {} : { max_tokens: options.maxTokens }),
      ...(options.detail === undefined ? {} : { detail: options.detail }),
      ...(options.purpose === undefined ? {} : { purpose: options.purpose }),
      ...(options.minScore === undefined ? {} : { min_score: options.minScore }),
      ...(options.sessionId === undefined ? {} : { session_id: options.sessionId }),
    };

    const result = (yield* domains.search.recall(body)) as RecallApiV1SearchRecallPostResponse;
    const budget = maxTokens * CHARS_PER_TOKEN;
    const truncated = result.rendered.length > budget;
    const text = truncated ? clip(result.rendered, budget) + TRUNCATION_MARKER : result.rendered;
    const entries = rank(result.entries);

    return {
      text,
      digest: result.digest,
      citations: cite(entries),
      entries,
      tokensUsed: Math.ceil(text.length / CHARS_PER_TOKEN),
      truncated,
    };
  });
