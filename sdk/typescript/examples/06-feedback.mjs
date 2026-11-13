#!/usr/bin/env node
/**
 * Persistence: the adaptive policy survives the process that learned it.
 *
 * Run it twice against the same OV_FEEDBACK_PATH — the second run starts from
 * the records the first one wrote, so samplesBefore is no longer zero.
 *
 *   node examples/06-feedback.mjs
 *   node examples/06-feedback.mjs
 */

import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Effect } from "effect";
import { Feedback, feedbackFile, openVikingStack } from "../dist/index.js";

const baseUrl = process.env.OV_URL ?? "http://127.0.0.1:1933";
const path = process.env.OV_FEEDBACK_PATH ?? join(tmpdir(), "ov-sdk-feedback.jsonl");
const run = process.env.OV_RUN;
const query = "local AI stack";

const store = feedbackFile({ path });

const fileLines = () => {
  try {
    return readFileSync(path, "utf8")
      .split("\n")
      .filter((line) => line.trim().length > 0).length;
  } catch {
    return 0;
  }
};

const program = Effect.gen(function* () {
  const feedback = yield* Feedback;

  const before = yield* feedback.policy();
  const hits = yield* feedback.find(query);

  const ranked = [...hits.memories, ...hits.resources, ...hits.skills].sort(
    (left, right) => right.score - left.score,
  );

  const top = ranked[0];
  if (top) yield* feedback.markUsed(top.uri);

  const after = yield* feedback.policy();

  return {
    ...(run === undefined ? {} : { run }),
    path,
    samplesBefore: before.samples,
    samplesAfter: after.samples,
    hitRate: Number(after.hitRate.toFixed(3)),
    limit: after.limit,
    minScore: Number(after.minScore.toFixed(3)),
    fileLines: fileLines(),
  };
});

const result = await Effect.runPromise(
  Effect.provide(
    program,
    openVikingStack({ baseUrl }, { load: store.load, persist: store.persist }),
  ),
);

console.log(JSON.stringify(result, null, 2));
