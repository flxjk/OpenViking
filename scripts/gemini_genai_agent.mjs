#!/usr/bin/env node
/**
 * Run a Gemini prompt through the official @google/genai SDK.
 *
 * Usage:
 *   GEMINI_API_KEY=AIza... node scripts/gemini_genai_agent.mjs "your prompt"
 *   node scripts/gemini_genai_agent.mjs --model gemini-2.0-flash "your prompt"
 *
 * The key is read from the environment only; it is never printed.
 */

import { GoogleGenAI } from "@google/genai";

const DEFAULT_MODEL = "gemini-3.8-flash";
const DEFAULT_PROMPT = "Reply with exactly: OK";

function parseArgs(argv) {
  const args = { model: DEFAULT_MODEL, prompt: [] };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--model" && i + 1 < argv.length) {
      args.model = argv[i + 1];
      i += 1;
    } else {
      args.prompt.push(argv[i]);
    }
  }
  return args;
}

async function main() {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    console.error("GEMINI_API_KEY is not set");
    process.exit(2);
  }

  const { model, prompt } = parseArgs(process.argv.slice(2));
  const text = prompt.join(" ").trim() || DEFAULT_PROMPT;

  const ai = new GoogleGenAI({ apiKey });
  const response = await ai.models.generateContent({ model, contents: text });
  console.log(response.text ?? "");
}

main().catch((error) => {
  console.error(error?.message ?? String(error));
  process.exit(1);
});
