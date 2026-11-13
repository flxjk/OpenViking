#!/usr/bin/env node
/**
 * Context assembly: recall rendered into one prompt-ready block under a budget.
 *
 * The server ranks and renders; the SDK clips to maxTokens and reports what it
 * kept, so the block can be pasted into a prompt unchanged.
 *
 *   node examples/05-context.mjs "local AI stack"
 */

import { Effect } from "effect";
import { Domains, assembleContext, openVikingStack } from "../dist/index.js";

const baseUrl = process.env.OV_URL ?? "http://127.0.0.1:1933";
const maxTokens = Number(process.env.OV_MAX_TOKENS ?? 1200);
const query = process.argv[2] ?? "local AI stack";

const program = Effect.gen(function* () {
  const domains = yield* Domains;
  const block = yield* assembleContext(domains, { query, maxTokens });

  return {
    query,
    citations: block.citations,
    tokensUsed: block.tokensUsed,
    truncated: block.truncated,
    entries: block.entries.slice(0, 3).map((entry) => ({
      uri: entry.uri,
      score: Number(entry.score.toFixed(3)),
      category: entry.category,
    })),
    preview: block.text.slice(0, 300),
  };
});

const result = await Effect.runPromise(
  Effect.provide(program, openVikingStack({ baseUrl })),
);

console.log(JSON.stringify(result, null, 2));
