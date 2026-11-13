#!/usr/bin/env node
/**
 * Run a Gemini 4 agent (Carbon / Argon) grounded in the local OpenViking memory.
 *
 * Uses the official `@google/genai` SDK with function calling. The model can
 * call two tools that hit the OpenViking REST API:
 *
 *   openviking_search(query, limit)  -> POST /api/v1/search/find
 *   openviking_read(uri)             -> GET  /api/v1/content/read
 *
 * Usage:
 *   node scripts/gemini_agent.mjs "What is my local AI stack?"
 *   node scripts/gemini_agent.mjs --model gemini-4-argon "List my skills"
 *   OV_URL=http://127.0.0.1:1933 node scripts/gemini_agent.mjs "Summarize my memories"
 *
 * Environment:
 *   GEMINI_API_KEY  Gemini API key (falls back to ~/.zshrc)
 *   OV_URL          OpenViking base URL (default http://127.0.0.1:1933)
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { GoogleGenAI } from "@google/genai";

const DEFAULT_MODEL = "gemini-4-carbon";
const DEFAULT_OV_URL = "http://127.0.0.1:1933";

function loadApiKey() {
  if (process.env.GEMINI_API_KEY) return process.env.GEMINI_API_KEY;
  for (const profile of [".zshrc", ".bashrc", ".profile"]) {
    try {
      const text = readFileSync(join(homedir(), profile), "utf8");
      for (const line of text.split("\n")) {
        const m = line.match(/^\s*export\s+GEMINI_API_KEY=["']?([^"'\s]+)["']?/);
        if (m) return m[1];
      }
    } catch {
      /* profile missing */
    }
  }
  throw new Error("GEMINI_API_KEY is not set");
}

function parseArgs(argv) {
  const args = { model: DEFAULT_MODEL, prompt: "" };
  const rest = [];
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--model") args.model = argv[++i];
    else if (argv[i] === "--ov-url") args.ovUrl = argv[++i];
    else rest.push(argv[i]);
  }
  args.prompt = rest.join(" ");
  if (!args.prompt) throw new Error("prompt is required");
  return args;
}

async function ovSearch(baseUrl, query, limit = 5) {
  const res = await fetch(`${baseUrl}/api/v1/search/find`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit }),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}

async function ovRead(baseUrl, uri) {
  const res = await fetch(
    `${baseUrl}/api/v1/content/read?uri=${encodeURIComponent(uri)}`,
  );
  if (!res.ok) throw new Error(`read failed: ${res.status}`);
  return res.json();
}

const tools = [
  {
    functionDeclarations: [
      {
        name: "openviking_search",
        description:
          "Semantic search over the user's OpenViking memory, resources and skills.",
        parameters: {
          type: "OBJECT",
          properties: {
            query: { type: "STRING", description: "Search query" },
            limit: { type: "NUMBER", description: "Max results (default 5)" },
          },
          required: ["query"],
        },
      },
      {
        name: "openviking_read",
        description: "Read a viking:// file by URI.",
        parameters: {
          type: "OBJECT",
          properties: {
            uri: { type: "STRING", description: "viking:// URI to read" },
          },
          required: ["uri"],
        },
      },
    ],
  },
];

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const apiKey = loadApiKey();
  const ovUrl = args.ovUrl || process.env.OV_URL || DEFAULT_OV_URL;
  const ai = new GoogleGenAI({ apiKey });

  const contents = [
    {
      role: "user",
      parts: [
        {
          text:
            "You are a local AI stack assistant. Use openviking_search and " +
            "openviking_read to ground every answer in the user's own memory. " +
            "Cite the viking:// URI you used. Be terse.\n\n" +
            args.prompt,
        },
      ],
    },
  ];

  for (let round = 0; round < 6; round += 1) {
    const response = await ai.models.generateContent({
      model: args.model,
      contents,
      config: { tools },
    });

    const calls = response.functionCalls ?? [];
    if (calls.length === 0) {
      console.log(response.text ?? "");
      return;
    }

    contents.push({ role: "model", parts: response.candidates[0].content.parts });
    const results = [];
    for (const call of calls) {
      let result;
      try {
        if (call.name === "openviking_search") {
          result = await ovSearch(ovUrl, call.args.query, call.args.limit ?? 5);
        } else if (call.name === "openviking_read") {
          result = await ovRead(ovUrl, call.args.uri);
        } else {
          result = { error: `unknown tool ${call.name}` };
        }
      } catch (error) {
        result = { error: String(error) };
      }
      results.push({
        functionResponse: { name: call.name, response: { result } },
      });
    }
    contents.push({ role: "user", parts: results });
  }

  throw new Error("agent exceeded the tool-call budget");
}

main().catch((error) => {
  console.error(error.message ?? error);
  process.exit(1);
});
