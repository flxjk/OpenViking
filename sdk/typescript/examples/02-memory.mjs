#!/usr/bin/env node
/**
 * Memory: recall, read, and close the feedback loop.
 *
 * The policy printed at the end is the adaptive state: the SDK widens the
 * search when results go unused and narrows it when they land.
 *
 *   node examples/02-memory.mjs "local AI stack"
 */

import { Effect } from "effect";
import { Domains, Feedback, openVikingStack } from "../dist/index.js";

const baseUrl = process.env.OV_URL ?? "http://127.0.0.1:1933";
const query = process.argv[2] ?? "local AI stack";

const program = Effect.gen(function* () {
  const domains = yield* Domains;
  const feedback = yield* Feedback;

  const before = yield* feedback.policy();
  const hits = yield* feedback.find(query);

  const ranked = [...hits.memories, ...hits.resources, ...hits.skills]
    .sort((left, right) => right.score - left.score)
    .slice(0, 3);

  const top = ranked[0];
  const used = top ? yield* feedback.markUsed(top.uri) : false;

  const excerpt = top
    ? (yield* domains.content.read({ uri: top.uri, limit: 200 })).slice(0, 200)
    : "";

  const after = yield* feedback.policy();

  return {
    query,
    policyBefore: { limit: before.limit, minScore: Number(before.minScore.toFixed(3)) },
    policyAfter: { limit: after.limit, minScore: Number(after.minScore.toFixed(3)) },
    total: hits.total,
    used,
    ranked: ranked.map((hit) => ({
      uri: hit.uri,
      score: Number(hit.score.toFixed(3)),
      type: hit.context_type,
    })),
    excerpt,
  };
});

const result = await Effect.runPromise(
  Effect.provide(program, openVikingStack({ baseUrl })),
);

console.log(JSON.stringify(result, null, 2));
