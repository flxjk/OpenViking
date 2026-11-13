import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";

import { makeMetrics } from "../src/effect/metrics.js";

describe("metrics", () => {
  it("counts calls and errors per endpoint", async () => {
    const metrics = makeMetrics();
    await Effect.runPromise(metrics.track("a", Effect.succeed(1)));
    await Effect.runPromise(metrics.track("a", Effect.succeed(2)));
    await Effect.runPromise(
      Effect.exit(metrics.track("a", Effect.fail(new Error("boom")))),
    );

    const snapshot = await Effect.runPromise(metrics.snapshot());
    expect(snapshot.totalCalls).toBe(3);
    expect(snapshot.totalErrors).toBe(1);
    expect(snapshot.endpoints["a"]?.calls).toBe(3);
    expect(snapshot.endpoints["a"]?.errors).toBe(1);
  });

  it("reports latency percentiles", async () => {
    const metrics = makeMetrics();
    for (const value of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) {
      await Effect.runPromise(metrics.record("b", value, true));
    }
    const snapshot = await Effect.runPromise(metrics.snapshot());
    const bucket = snapshot.endpoints["b"];
    expect(bucket?.p50Ms).toBe(5);
    expect(bucket?.p95Ms).toBe(10);
    expect(bucket?.meanMs).toBeCloseTo(5.5, 5);
  });

  it("keeps only the configured number of samples", async () => {
    const metrics = makeMetrics({ samplesPerEndpoint: 3 });
    for (const value of [1, 2, 3, 4, 5]) {
      await Effect.runPromise(metrics.record("c", value, true));
    }
    const snapshot = await Effect.runPromise(metrics.snapshot());
    expect(snapshot.endpoints["c"]?.calls).toBe(5);
    expect(snapshot.endpoints["c"]?.p50Ms).toBe(4);
  });

  it("resets", async () => {
    const metrics = makeMetrics();
    await Effect.runPromise(metrics.record("d", 1, true));
    await Effect.runPromise(metrics.reset());
    const snapshot = await Effect.runPromise(metrics.snapshot());
    expect(snapshot.totalCalls).toBe(0);
  });
});

describe("metrics sink", () => {
  it("emits every sample to the sink", async () => {
    const seen: Array<{ endpoint: string; ok: boolean }> = [];
    const metrics = makeMetrics({
      sink: (sample) => seen.push({ endpoint: sample.endpoint, ok: sample.ok }),
    });
    await Effect.runPromise(metrics.record("a", 1, true));
    await Effect.runPromise(Effect.exit(metrics.track("b", Effect.fail(new Error("x")))));

    expect(seen).toEqual([
      { endpoint: "a", ok: true },
      { endpoint: "b", ok: false },
    ]);
  });
});

describe("jsonl sink", () => {
  it("appends one json line per sample", async () => {
    const { jsonlSink } = await import("../src/effect/metrics-node.js");
    const { readFileSync, rmSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");

    const path = join(tmpdir(), `ov-metrics-${Date.now()}.jsonl`);
    const sink = jsonlSink({ path });
    const metrics = makeMetrics({ sink });

    await Effect.runPromise(metrics.record("a", 3, true));
    await Effect.runPromise(metrics.record("b", 5, false));

    const lines = readFileSync(path, "utf8").trim().split("\n");
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0]!).endpoint).toBe("a");
    expect(JSON.parse(lines[1]!).ok).toBe(false);
    rmSync(path, { force: true });
  });
});
