import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";

import type { FeedbackRecord } from "./self-improve.js";

const isFeedbackRecord = (value: unknown): value is FeedbackRecord => {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as { readonly score?: unknown; readonly used?: unknown };
  return typeof candidate.score === "number" && typeof candidate.used === "boolean";
};

export const feedbackFile = (options: {
  readonly path: string;
}): {
  load: () => FeedbackRecord[];
  persist: (record: FeedbackRecord) => void;
} => {
  const load = (): FeedbackRecord[] => {
    let raw: string;
    try {
      raw = readFileSync(options.path, "utf8");
    } catch {
      return [];
    }

    const records: FeedbackRecord[] = [];
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (trimmed.length === 0) continue;
      try {
        const parsed: unknown = JSON.parse(trimmed);
        if (isFeedbackRecord(parsed)) records.push(parsed);
      } catch {
        // one corrupt line must not hide the records around it
      }
    }
    return records;
  };

  const persist = (record: FeedbackRecord): void => {
    try {
      mkdirSync(dirname(options.path), { recursive: true });
      appendFileSync(options.path, `${JSON.stringify(record)}\n`, "utf8");
    } catch {
      // feedback must never break a request path
    }
  };

  return { load, persist };
};
