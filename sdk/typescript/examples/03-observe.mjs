#!/usr/bin/env node
/**
 * Observability: what the cache and the local metrics look like after a workload.
 *
 * Nothing here is exported anywhere — the counters live in this process.
 *
 *   node examples/03-observe.mjs
 */

import { Effect } from "effect";
import { Domains, OpenViking, openVikingStack } from "../dist/index.js";

const baseUrl = process.env.OV_URL ?? "http://127.0.0.1:1933";

const program = Effect.gen(function* () {
  const domains = yield* Domains;
  const client = yield* OpenViking;

  const root = "viking://user/default/memories";

  yield* Effect.all(
    [
      domains.system.health(),
      domains.system.status(),
      domains.fs.ls({ uri: root }),
      domains.fs.ls({ uri: root }),
      domains.fs.ls({ uri: root }),
      domains.skills.list(),
    ],
    { concurrency: "unbounded" },
  );

  const metrics = client.metrics ? yield* client.metrics.snapshot() : undefined;
  const cache = client.cache ? yield* client.cache.stats() : undefined;

  const slowest = Object.entries(metrics?.endpoints ?? {})
    .map(([name, bucket]) => ({ name, p95Ms: Number(bucket.p95Ms.toFixed(2)), calls: bucket.calls }))
    .sort((left, right) => right.p95Ms - left.p95Ms)
    .slice(0, 5);

  return {
    metrics: metrics && {
      totalCalls: metrics.totalCalls,
      totalErrors: metrics.totalErrors,
      tracked: Object.keys(metrics.endpoints).length,
    },
    cache,
    slowest,
  };
});

const result = await Effect.runPromise(
  Effect.provide(
    program,
    openVikingStack({ baseUrl, cache: { ttlMs: 30_000, capacity: 128 } }),
  ),
);

console.log(JSON.stringify(result, null, 2));
