#!/usr/bin/env node
/**
 * Connectivity: what the server is, and what the SDK can reach.
 *
 *   node examples/01-connect.mjs
 */

import { Effect } from "effect";
import { Domains, OpenViking, endpoints, openVikingStack } from "../dist/index.js";

const baseUrl = process.env.OV_URL ?? "http://127.0.0.1:1933";

const program = Effect.gen(function* () {
  const ov = yield* Domains;
  const client = yield* OpenViking;

  const health = yield* ov.system.health();
  const status = yield* ov.system.status();
  const tasks = yield* ov.tasks.list({ limit: 3 });

  return {
    operations: Object.keys(endpoints).length,
    baseUrl: client.config.baseUrl,
    health,
    status,
    openTasks: tasks.length,
  };
});

const result = await Effect.runPromise(
  Effect.provide(program, openVikingStack({ baseUrl })),
);

console.log(JSON.stringify(result, null, 2));
