#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline";
import { setTimeout as sleep } from "node:timers/promises";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = new Set(process.argv.slice(2));
const isPlan = args.has("--plan") || args.has("--dry-run");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const pathSep = path.delimiter;

if (args.has("--help") || args.has("-h")) {
  console.log(`OpenViking local development stack

Usage:
  npm run dev       Start local services and package watchers; Ctrl+C stops children
  npm run dev:plan  Print the start plan without installing or starting anything

Environment:
  OV_CONFIG, OV_HOST, OV_PORT, BOT_PORT, DSH_DIR, DSH_PORT, DOCS_PORT`);
  process.exit(0);
}

const configPath = path.resolve(root, process.env.OV_CONFIG || "ov.conf");
if (!existsSync(configPath)) {
  console.error(`Missing server config: ${configPath}`);
  console.error("Create it with `just ov-config-init` or set OV_CONFIG.");
  process.exit(1);
}

let config;
try {
  config = JSON.parse(readFileSync(configPath, "utf8"));
} catch (error) {
  console.error(`Could not parse server config: ${configPath}`);
  console.error(error.message);
  process.exit(1);
}

const host = process.env.OV_HOST || "127.0.0.1";
const ovPort = process.env.OV_PORT || String(config.server?.port || 1933);
const botPort = process.env.BOT_PORT || "18790";
const studioPort = "3000";
const uiDocsPort = "3002";
const docsPort = process.env.DOCS_PORT || "5173";
const dshPort = process.env.DSH_PORT || "3090";
const origin = (port) => `http://${host}:${port}`;
const venvBin = path.join(root, process.platform === "win32" ? ".venv/Scripts" : ".venv/bin");
const serverBin = path.join(venvBin, process.platform === "win32" ? "openviking-server.exe" : "openviking-server");
const serverEnv = {
  ...process.env,
  PATH: `${venvBin}${pathSep}${process.env.PATH || ""}`,
};

if (!existsSync(serverBin)) {
  console.error(`Missing ${serverBin}. Run [1muv sync --extra bot[0m first.`);
  process.exit(1);
}

const npmWorkspaces = ["web-studio", "frontend", "docs", "sdk/typescript"];
const dshDir = path.resolve(
  process.env.DSH_DIR || path.join(os.homedir(), "git", "deepseek-harness"),
);
const dshAvailable =
  existsSync(path.join(dshDir, "apps/cli/src/bin.ts")) &&
  existsSync(path.join(dshDir, "node_modules/tsx"));

const services = [
  {
    name: "OpenViking + VikingBot",
    cwd: root,
    command: [
      serverBin,
      "--config",
      configPath,
      "--host",
      host,
      "--port",
      ovPort,
      "--with-bot",
      "--bot-port",
      botPort,
    ],
    env: serverEnv,
    readyUrl: `${origin(ovPort)}/health`,
    extraReadyUrls: [`${origin(ovPort)}/bot/v1/health`],
    timeoutMs: 90_000,
  },
  ...(dshAvailable
    ? [
        {
          name: "DeepSeek Harness Web",
          cwd: dshDir,
          command: [
            process.execPath,
            "--import",
            "tsx/esm",
            "apps/cli/src/bin.ts",
            "web",
          ],
          env: { ...process.env, DSH_PORT: dshPort },
          readyUrl: `${origin(dshPort)}/`,
          timeoutMs: 120_000,
        },
      ]
    : []),
  {
    name: "Web Studio",
    cwd: path.join(root, "web-studio"),
    command: [npm, "run", "dev"],
    env: {
      ...process.env,
      VITE_OV_BASE_URL: origin(ovPort),
    },
    readyUrl: `${origin(studioPort)}/onboarding`,
    timeoutMs: 60_000,
  },
  {
    name: "React library watch + UI docs",
    cwd: path.join(root, "frontend"),
    command: [npm, "run", "dev"],
    env: process.env,
    readyUrl: `${origin(uiDocsPort)}/ui/`,
    timeoutMs: 120_000,
  },
  {
    name: "OpenViking product docs",
    cwd: path.join(root, "docs"),
    command: [npm, "run", "docs:dev", "--", "--host", host, "--port", docsPort],
    env: process.env,
    readyUrl: `${origin(docsPort)}/`,
    timeoutMs: 90_000,
  },
  {
    name: "TypeScript SDK watch",
    cwd: path.join(root, "sdk/typescript"),
    command: [npm, "run", "dev"],
    env: process.env,
    readyDelayMs: 1_500,
  },
];

const managed = [];

function displayCommand(service) {
  return `${service.command[0]} ${service.command.slice(1).join(" ")}`;
}

if (isPlan) {
  console.log(`OpenViking dev stack (loopback host ${host})`);
  console.log(`Server config: ${configPath}`);
  for (const dir of npmWorkspaces) {
    if (!existsSync(path.join(root, dir, "node_modules"))) {
      console.log(`Bootstrap if needed: npm ci --no-audit --no-fund (in ${dir}/)`);
    }
  }
  for (const service of services) {
    console.log(`\n${service.name}`);
    console.log(`  cwd: ${path.relative(root, service.cwd) || "."}`);
    console.log(`  run: ${displayCommand(service)}`);
    if (service.readyUrl) console.log(`  URL: ${service.readyUrl}`);
    for (const url of service.extraReadyUrls || []) console.log(`  URL: ${url}`);
  }
  if (!dshAvailable) {
    console.log(`\nDSH skipped: no ready checkout at ${dshDir} (set DSH_DIR to include it).`);
  }
  console.log("\nHost-loaded extensions run inside their own applications; the launcher does not sign in to those hosts.");
  process.exit(0);
}

async function installIfMissing(dir) {
  if (existsSync(path.join(dir, "node_modules"))) return;
  if (!existsSync(path.join(dir, "package-lock.json"))) {
    throw new Error(`Missing node_modules and package-lock.json in ${path.relative(root, dir)}.`);
  }
  console.log(`[bootstrap] npm ci in ${path.relative(root, dir)}/`);
  await runOneShot(npm, ["ci", "--no-audit", "--no-fund"], dir);
}

function runOneShot(command, commandArgs, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, commandArgs, { cwd, stdio: "inherit", env: process.env });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited ${signal || code} in ${cwd}`));
    });
  });
}

function httpResponds(url) {
  return fetch(url, { signal: AbortSignal.timeout(1_500), redirect: "follow" })
    .then((response) => response.status < 500 && response.status !== 404)
    .catch(() => false);
}

function startProcess(service) {
  console.log(`[start] ${service.name}`);
  const child = spawn(service.command[0], service.command.slice(1), {
    cwd: service.cwd,
    env: service.env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  for (const [stream, label] of [
    [child.stdout, ""],
    [child.stderr, " stderr"],
  ]) {
    const lines = readline.createInterface({ input: stream });
    lines.on("line", (line) => console.log(`[${service.name}${label}] ${line}`));
  }
  const result = { ...service, child };
  child.once("error", (error) => {
    result.error = error;
  });
  child.once("exit", (code, signal) => {
    result.exit = { code, signal };
  });
  managed.push(result);
  return result;
}

async function waitUntilReady(service) {
  const deadline = Date.now() + service.timeoutMs;
  const endpoints = [service.readyUrl, ...(service.extraReadyUrls || [])].filter(Boolean);
  while (Date.now() < deadline) {
    if (service.error) throw service.error;
    if (service.exit) {
      throw new Error(`${service.name} exited before ready (${service.exit.signal || service.exit.code}).`);
    }
    const allReady = await Promise.all(endpoints.map(httpResponds));
    if (allReady.every(Boolean)) {
      for (const url of endpoints) console.log(`[ready] ${service.name}: ${url}`);
      return;
    }
    await sleep(500);
  }
  throw new Error(`${service.name} did not become ready at ${service.readyUrl}.`);
}

async function startService(service) {
  if (service.readyUrl && (await httpResponds(service.readyUrl))) {
    for (const url of service.extraReadyUrls || []) {
      if (!(await httpResponds(url))) {
        throw new Error(`${service.name} is already listening, but required endpoint is not ready: ${url}`);
      }
    }
    console.log(`[reuse] ${service.name} already responds at ${service.readyUrl}`);
    return;
  }
  const processInfo = startProcess(service);
  if (service.readyUrl) await waitUntilReady(processInfo);
  else {
    await sleep(service.readyDelayMs || 1_000);
    if (processInfo.error) throw processInfo.error;
    if (processInfo.exit) {
      throw new Error(`${service.name} exited during startup (${processInfo.exit.signal || processInfo.exit.code}).`);
    }
    console.log(`[ready] ${service.name} watcher started`);
  }
}

function signalProcess(child, signal) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (process.platform !== "win32" && child.pid) process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch {
    try {
      child.kill(signal);
    } catch {
      // The process may have exited between the check and the signal.
    }
  }
}

async function shutdown() {
  if (!managed.length) return;
  console.log("\n[stop] stopping processes started by npm run dev");
  for (const item of managed) signalProcess(item.child, "SIGTERM");
  await Promise.race([
    Promise.all(managed.map((item) => new Promise((resolve) => {
      if (item.child.exitCode !== null || item.child.signalCode !== null) return resolve();
      item.child.once("exit", resolve);
    }))),
    sleep(5_000),
  ]);
  for (const item of managed) signalProcess(item.child, "SIGKILL");
}

let resolveStop;
const stopSignal = new Promise((resolve) => {
  resolveStop = resolve;
});
process.once("SIGINT", () => resolveStop("SIGINT"));
process.once("SIGTERM", () => resolveStop("SIGTERM"));

try {
  for (const dir of npmWorkspaces) await installIfMissing(path.join(root, dir));

  const serverService = services.find((service) => service.name === "OpenViking + VikingBot");
  await startService(serverService);

  const otherServices = services.filter((item) => item.name !== "OpenViking + VikingBot");
  const startupResults = await Promise.allSettled(otherServices.map(startService));
  const startupFailure = startupResults.find((result) => result.status === "rejected");
  if (startupFailure) throw startupFailure.reason;

  console.log("\nOpenViking dev stack is ready.");
  console.log(`  Web Studio: ${origin(studioPort)}/onboarding`);
  console.log(`  React UI:   ${origin(uiDocsPort)}/ui/`);
  console.log(`  Docs:       ${origin(docsPort)}/`);
  console.log(`  API:        ${origin(ovPort)}`);
  console.log(`  Bot API:    ${origin(ovPort)}/bot/v1/health`);
  if (dshAvailable) console.log(`  DSH Web:    ${origin(dshPort)}`);
  console.log("Press Ctrl+C to stop processes started by this command.");

  const alreadyExited = managed.find((item) => item.exit || item.error);
  if (alreadyExited) {
    throw new Error(`${alreadyExited.name} exited unexpectedly during startup.`);
  }
  const childExit = Promise.race(managed.map((item) => {
    if (item.exit) return Promise.resolve({ item, ...item.exit });
    return new Promise((resolve) => {
      item.child.once("exit", (code, signal) => resolve({ item, code, signal }));
    });
  }));
  const outcome = await Promise.race([stopSignal, childExit]);
  if (typeof outcome === "object") {
    throw new Error(`${outcome.item.name} exited unexpectedly (${outcome.signal || outcome.code}).`);
  }
} catch (error) {
  console.error(`\n[dev] ${error.message}`);
  process.exitCode = 1;
} finally {
  await shutdown();
}
