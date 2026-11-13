import { describe, expect, it } from "vitest";
import * as Effect from "effect/Effect";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { DomainsShape } from "../src/effect/domains.js";
import { feedbackFile } from "../src/effect/feedback-node.js";
import {
  makeFeedback,
  type FeedbackOptions,
  type FeedbackRecord,
} from "../src/effect/self-improve.js";

const domains = {} as DomainsShape;

const feedback = (overrides: FeedbackOptions = {}) =>
  Effect.runPromise(makeFeedback(domains, overrides));

const record = (used: boolean, score: number): FeedbackRecord => ({
  query: "q",
  uri: "viking://user/default/memories/a",
  score,
  used,
  at: 1_700_000_000_000,
});

const withTempDir = <A>(body: (dir: string) => A): A => {
  const dir = mkdtempSync(join(tmpdir(), "ov-feedback-"));
  try {
    return body(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
};

describe("feedback load seeding", () => {
  it("seeds the policy from loaded records", async () => {
    const service = await feedback({ load: () => [record(true, 0.9), record(true, 0.8)] });
    const policy = await Effect.runPromise(service.policy());
    expect(policy.samples).toBe(2);
    expect(policy.hitRate).toBeGreaterThan(0.5);
  });

  it("keeps recording on top of loaded records", async () => {
    const service = await feedback({ load: () => [record(true, 0.9)] });
    await Effect.runPromise(service.record(record(false, 0.1)));
    const stats = await Effect.runPromise(service.stats());
    expect(stats.samples).toBe(2);
    expect(stats.used).toBe(1);
  });

  it("skips loaded records with a non-finite score", async () => {
    const service = await feedback({
      load: () => [
        record(true, 0.9),
        record(true, Number.NaN),
        record(false, Number.POSITIVE_INFINITY),
      ],
    });
    const policy = await Effect.runPromise(service.policy());
    expect(policy.samples).toBe(1);
  });

  it("clears loaded records on reset", async () => {
    const service = await feedback({ load: () => [record(true, 0.9), record(true, 0.8)] });
    await Effect.runPromise(service.reset());
    const policy = await Effect.runPromise(service.policy());
    expect(policy.samples).toBe(0);
    expect(policy.hitRate).toBe(0.5);
  });
});

describe("feedback file", () => {
  it("round trips records through a jsonl file", () => {
    withTempDir((dir) => {
      const path = join(dir, "nested", "feedback.jsonl");
      const first = record(true, 0.75);
      const second = record(false, 0.25);
      const file = feedbackFile({ path });
      file.persist(first);
      file.persist(second);

      expect(feedbackFile({ path }).load()).toEqual([first, second]);
    });
  });

  it("skips a corrupt line in the middle", () => {
    withTempDir((dir) => {
      const path = join(dir, "feedback.jsonl");
      const first = record(true, 0.6);
      const second = record(false, 0.4);
      writeFileSync(
        path,
        [
          JSON.stringify(first),
          "{ not json",
          JSON.stringify({ score: "high", used: true }),
          "",
          JSON.stringify(second),
          "",
        ].join("\n"),
        "utf8",
      );

      expect(feedbackFile({ path }).load()).toEqual([first, second]);
    });
  });

  it("returns an empty array for a missing file", () => {
    withTempDir((dir) => {
      const path = join(dir, "missing", "feedback.jsonl");
      expect(feedbackFile({ path }).load()).toEqual([]);
    });
  });

  it("does not throw when the target cannot be written", () => {
    withTempDir((dir) => {
      const path = join(dir, "a-directory");
      mkdirSync(path);
      expect(() => feedbackFile({ path }).persist(record(true, 0.5))).not.toThrow();
    });
  });
});
