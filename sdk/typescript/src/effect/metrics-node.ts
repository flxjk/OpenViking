import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import type { MetricsSample } from "./metrics.js";

export interface JsonlSinkOptions {
  readonly path: string;
  readonly flushEvery?: number;
}

export const jsonlSink = (options: JsonlSinkOptions): ((sample: MetricsSample) => void) => {
  const flushEvery = options.flushEvery ?? 1;
  let buffer: string[] = [];

  try {
    mkdirSync(dirname(options.path), { recursive: true });
  } catch {
    // the write below surfaces the real failure
  }

  return (sample: MetricsSample) => {
    buffer.push(JSON.stringify(sample));
    if (buffer.length < flushEvery) return;
    const chunk = buffer.join("\n") + "\n";
    buffer = [];
    try {
      appendFileSync(options.path, chunk, "utf8");
    } catch {
      // metrics must never break a request path
    }
  };
};
