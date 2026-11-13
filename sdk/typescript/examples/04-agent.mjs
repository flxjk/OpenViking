#!/usr/bin/env node
/**
 * AI-first loop: recall context, answer with a model, record what was used.
 *
 * The model is reached through the OpenViking gateway, so this example needs no
 * provider SDK. Set OV_GW_KEY, or write the key to ~/.openviking/gateway.key.
 *
 *   node examples/04-agent.mjs "what is my local AI stack?"
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { Effect } from "effect";
import { Domains, Feedback, openVikingStack } from "../dist/index.js";

const baseUrl = process.env.OV_URL ?? "http://127.0.0.1:1933";
const question = process.argv[2] ?? "what is my local AI stack?";

const gatewayKey = () => {
  if (process.env.OV_GW_KEY) return process.env.OV_GW_KEY;
  try {
    return readFileSync(join(homedir(), ".openviking", "gateway.key"), "utf8").trim();
  } catch {
    return "";
  }
};

const key = gatewayKey();
if (!key) {
  console.error("set OV_GW_KEY or write ~/.openviking/gateway.key");
  process.exit(1);
}

const askGateway = async (prompt) => {
  const response = await fetch(`${baseUrl}/gateway/v1/chat/completions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model: process.env.OV_GW_MODEL ?? "deepseek/deepseek-chat",
      messages: [{ role: "user", content: prompt }],
    }),
  });
  if (!response.ok) throw new Error(`gateway ${response.status}`);
  const body = await response.json();
  return body.choices?.[0]?.message?.content ?? "";
};

const program = Effect.gen(function* () {
  const domains = yield* Domains;
  const feedback = yield* Feedback;

  const hits = yield* feedback.find(question);
  const ranked = [...hits.memories, ...hits.resources, ...hits.skills].sort(
    (left, right) => right.score - left.score,
  );

  const excerpts = [];
  for (const hit of ranked.slice(0, 3)) {
    const text = yield* domains.content.read({ uri: hit.uri, limit: 600 }).pipe(
      Effect.catch(() => Effect.succeed("")),
    );
    excerpts.push(`### ${hit.uri}\n${text}`);
  }

  const answer = yield* Effect.tryPromise({
    try: () =>
      askGateway(
        [
          "Answer using only the context below. Cite the viking:// URIs you used.",
          "",
          ...excerpts,
          "",
          `Question: ${question}`,
        ].join("\n"),
      ),
    catch: (cause) => new Error(String(cause)),
  });

  const cited = ranked.find((hit) => answer.includes(hit.uri));
  const marked = cited ? yield* feedback.markUsed(cited.uri) : false;

  return {
    question,
    recalled: ranked.length,
    cited: cited?.uri ?? null,
    markedUsed: marked,
    answer,
  };
});

const result = await Effect.runPromise(
  Effect.provide(program, openVikingStack({ baseUrl })),
);

console.log(JSON.stringify(result, null, 2));
