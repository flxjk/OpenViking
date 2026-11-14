#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline";
import { setTimeout as sleep } from "node:timers/promises";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = new Set(process.argv.slice(2));
const isPlan = args.has("--plan") || args.has("--dry-run");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const pathSep = path.delimiter;
// An optional health-probe credential stays in the supervisor; frontend build
// tools and the external DSH process must not inherit it.
const { OV_API_KEY: readinessApiKey, OPENWEBUI_API_KEY: openWebuiApiKey, ...childEnv } = process.env;
const supportedArgs = new Set(["--plan", "--dry-run", "--help", "-h"]);
for (const arg of args) {
  if (!supportedArgs.has(arg)) {
    console.error(`Unknown argument: ${arg}. Use --help for usage.`);
    process.exit(1);
  }
}

if (args.has("--help") || args.has("-h")) {
  console.log(`OpenViking local development stack

Usage:
  npm run dev       Start local services and package watchers; Ctrl+C stops children
  npm run dev:plan  Print the start plan without installing or starting anything

Environment:
  OV_CONFIG, OV_HOST, OV_PORT, BOT_PORT, DSH_DIR, DSH_PORT, DOCS_PORT, OPENWEBUI_PORT
  DSH_START_TIMEOUT_MS (default 900000), DSH_SKIP_BUILD=1 (only after a completed DSH artifact build)
  OV_API_KEY (optional; sent only to API/Bot health checks)
  OPENWEBUI_API_KEY (optional; scoped upstream key used only by OpenWebUI tools)`);
  process.exit(0);
}

const configPath = path.resolve(root, process.env.OV_CONFIG || "ov.conf");
const configExists = existsSync(configPath);
if (!configExists && !isPlan) {
  console.error(`Missing server config: ${configPath}`);
  console.error("Create it with `just ov-config-init` or set OV_CONFIG.");
  process.exit(1);
}

let config;
try {
  config = configExists ? JSON.parse(readFileSync(configPath, "utf8")) : {};
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
const openWebuiPort = process.env.OPENWEBUI_PORT || "8765";
const origin = (port, bindHost = host) => {
  const connectHost = bindHost === "0.0.0.0" ? "127.0.0.1" : bindHost === "::" ? "::1" : bindHost;
  return `http://${connectHost.includes(":") && !connectHost.startsWith("[") ? `[${connectHost}]` : connectHost}:${port}`;
};
const venvBin = path.join(root, process.platform === "win32" ? ".venv/Scripts" : ".venv/bin");
const serverBin = path.join(venvBin, process.platform === "win32" ? "openviking-server.exe" : "openviking-server");
const pythonBin = path.join(venvBin, process.platform === "win32" ? "python.exe" : "python");
const serverEnv = {
  ...childEnv,
  PATH: `${venvBin}${pathSep}${process.env.PATH || ""}`,
};

const npmWorkspaces = ["web-studio", "frontend", "docs", "sdk/typescript", "examples/openclaw-plugin"];
const dshDir = path.resolve(
  process.env.DSH_DIR || path.join(os.homedir(), "git", "deepseek-harness"),
);
const dshAvailable =
  existsSync(path.join(dshDir, "apps/cli/src/bin.ts")) &&
  existsSync(path.join(dshDir, "scripts/dev-web.ts")) &&
  existsSync(path.join(dshDir, "package.json"));
const dshStartupTimeout = Number(process.env.DSH_START_TIMEOUT_MS || 900_000);
if (!Number.isSafeInteger(dshStartupTimeout) || dshStartupTimeout <= 0) {
  console.error("DSH_START_TIMEOUT_MS must be a positive integer.");
  process.exit(1);
}
const sharedPluginSync = {
  name: "Shared memory plugin runtime and skills",
  cwd: root,
  prepare: [process.execPath, "examples/memory-plugin-shared/sync.mjs"],
  env: childEnv,
};

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
    readyJson: (body) => body?.healthy === true && typeof body.version === "string",
    readyHeaders: readinessApiKey ? { "X-API-Key": readinessApiKey } : undefined,
    extraReadyUrls: [`${origin(ovPort)}/bot/v1/health`],
    timeoutMs: 90_000,
  },
  ...(dshAvailable
    ? [
        {
          name: "DeepSeek Harness Web",
          cwd: dshDir,
          command: [
            pnpm,
            "run",
            "dev:web",
            ...(process.env.DSH_SKIP_BUILD === "1" ? ["--skip-build"] : []),
            "--no-open",
            "--host",
            "127.0.0.1",
            "--port",
            dshPort,
          ],
          env: { ...childEnv, DSH_PORT: dshPort },
          readyUrl: `${origin(dshPort, "127.0.0.1")}/`,
          readyAuthChallenge: true,
          readyLog: /^dsh web: https?:\/\//,
          startupHint: process.env.DSH_SKIP_BUILD === "1"
            ? "Starting the official DSH client watchers and Web host using previously built artifacts."
            : "Building DSH artifacts, then starting its client watchers and Web host; build output follows.",
          progressIntervalMs: 30_000,
          timeoutMs: dshStartupTimeout,
        },
      ]
    : []),
  {
    name: "OpenWebUI tools",
    cwd: path.join(root, "examples/openwebui-plugin"),
    command: [pythonBin, "-m", "openviking_openwebui"],
    env: {
      ...childEnv,
      OV_ENDPOINT: origin(ovPort),
      OV_BIND: `127.0.0.1:${openWebuiPort}`,
      OV_API_KEY: openWebuiApiKey || "",
    },
    readyUrl: `${origin(openWebuiPort, "127.0.0.1")}/health`,
    readyJson: (body) => body?.status === "ok" && body.endpoint === origin(ovPort),
    extraReadyUrls: [`${origin(openWebuiPort, "127.0.0.1")}/openapi.json`],
    timeoutMs: 60_000,
  },
  {
    name: "Web Studio",
    cwd: path.join(root, "web-studio"),
    command: [npm, "run", "dev"],
    env: {
      ...childEnv,
      VITE_OV_BASE_URL: origin(ovPort),
    },
    readyUrl: `${origin(studioPort, "127.0.0.1")}/onboarding`,
    timeoutMs: 60_000,
  },
  {
    name: "React library watch + UI docs",
    cwd: path.join(root, "frontend"),
    command: [npm, "run", "dev"],
    env: childEnv,
    readyUrl: `${origin(uiDocsPort, "127.0.0.1")}/ui/`,
    timeoutMs: 120_000,
  },
  {
    name: "OpenViking product docs",
    cwd: path.join(root, "docs"),
    command: [npm, "run", "docs:dev", "--", "--host", host, "--port", docsPort],
    env: childEnv,
    readyUrl: `${origin(docsPort)}/`,
    timeoutMs: 90_000,
  },
  {
    name: "OpenClaw plugin watch",
    cwd: path.join(root, "examples/openclaw-plugin"),
    prepare: [npm, "run", "build"],
    command: [npm, "run", "dev"],
    env: childEnv,
    readyLog: /Found 0 errors\. Watching for file changes\./,
    timeoutMs: 120_000,
  },
  {
    name: "TypeScript SDK watch",
    cwd: path.join(root, "sdk/typescript"),
    prepare: [npm, "run", "build"],
    command: [npm, "run", "dev"],
    env: childEnv,
    readyLog: /DTS.*Build success/,
    timeoutMs: 120_000,
  },
];

const portEntries = [["OpenViking", ovPort], ["VikingBot", botPort], ["OpenWebUI tools", openWebuiPort], ["Web Studio", studioPort], ["UI docs", uiDocsPort], ["product docs", docsPort], ...(dshAvailable ? [["DSH", dshPort]] : [])];
const occupiedPorts = new Map();
for (const [name, value] of portEntries) {
  const port = Number(value);
  if (!/^[0-9]+$/.test(String(value)) || !Number.isInteger(port) || port < 1 || port > 65535) {
    console.error(`Invalid ${name} port: ${value}. Use a number from 1 to 65535.`);
    process.exit(1);
  }
  if (occupiedPorts.has(port)) {
    console.error(`${name} and ${occupiedPorts.get(port)} both use port ${port}. Set distinct service ports.`);
    process.exit(1);
  }
  occupiedPorts.set(port, name);
}

function displayCommand(service) {
  return `${service.command[0]} ${service.command.slice(1).join(" ")}`;
}

if (isPlan) {
  console.log(`OpenViking dev stack (API bind host ${host})`);
  console.log(`Server config: ${configPath}`);
  if (!configExists) console.log("Prerequisite: create the server config with just ov-config-init or set OV_CONFIG.");
  if (!existsSync(serverBin)) console.log("Prerequisite: install the Python server and bot with uv sync --extra bot.");
  console.log(`Bootstrap: ${sharedPluginSync.prepare.join(" ")}`);
  for (const dir of npmWorkspaces) {
    if (!existsSync(path.join(root, dir, "node_modules"))) {
      console.log(`Bootstrap if needed: npm ci --no-audit --no-fund (in ${dir}/)`);
    }
  }
  if (dshAvailable && !existsSync(path.join(dshDir, "node_modules"))) {
    console.log(`Bootstrap if needed: pnpm install --frozen-lockfile (in ${dshDir})`);
  }
  for (const service of services) {
    console.log(`\n${service.name}`);
    console.log(`  cwd: ${path.relative(root, service.cwd) || "."}`);
    if (service.prepare) console.log(`  build: ${service.prepare.join(" ")}`);
    console.log(`  run: ${displayCommand(service)}`);
    if (service.readyUrl) console.log(`  URL: ${service.readyUrl}`);
    for (const url of service.extraReadyUrls || []) console.log(`  URL: ${url}`);
  }
  if (!dshAvailable) {
    console.log(`\nDSH skipped: no supported checkout at ${dshDir} (set DSH_DIR to include it).`);
  }
  console.log("\nHost-loaded extensions run inside their own applications; the launcher does not sign in to those hosts.");
  process.exit(0);
}

// Every spawned process, including dependency installation, belongs to this run.
// A stop during startup aborts readiness checks immediately and prevents late spawns.
class StopRequested extends Error {}
const managed = [];
const stopping = new AbortController();
let resolveStop;
let shutdownStarted = false;
const stopSignal = new Promise((resolve) => { resolveStop = resolve; });

function requestStop(reason) {
  if (stopping.signal.aborted) return;
  stopping.abort(reason);
  resolveStop(reason);
}

function throwIfStopping() {
  stopping.signal.throwIfAborted();
}

function untilStopped(promise) {
  throwIfStopping();
  return Promise.race([promise, stopSignal.then((reason) => { throw reason; })]);
}

// npm and the terminal can both deliver the same signal. Keep handlers installed
// while shutting down so a forwarded second signal cannot bypass cleanup.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => requestStop(new StopRequested(signal)));
}
for (const stream of [process.stdout, process.stderr]) {
  stream.on("error", (error) => {
    requestStop(error.code === "EPIPE" || error.code === "EIO" ? new StopRequested("Terminal closed") : error);
  });
}
process.on("exit", () => {
  // Last-resort synchronous signalling if the supervisor exits unexpectedly.
  // Normal signals take the awaited shutdown path below.
  if (!shutdownStarted) for (const item of managed) signalProcess(item.child, "SIGTERM");
});

function startProcess(service, { persistent = true } = {}) {
  throwIfStopping();
  console.log(`[start] ${service.name}`);
  const child = spawn(service.command[0], service.command.slice(1), {
    cwd: service.cwd,
    env: service.env || childEnv,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
    shell: process.platform === "win32" && service.command[0].endsWith(".cmd"),
  });
  let resolveDone;
  const done = new Promise((resolve) => { resolveDone = resolve; });
  const result = { ...service, child, done };
  for (const [stream, label] of [[child.stdout, ""], [child.stderr, " stderr"]]) {
    const lines = readline.createInterface({ input: stream });
    lines.on("line", (line) => {
      if (service.readyLog?.test(line)) result.readyLogSeen = true;
      console.log(`[${service.name}${label}] ${line}`);
    });
  }
  const complete = (outcome) => {
    if (result.outcome) return;
    result.outcome = outcome;
    resolveDone(outcome);
    if (persistent && !shutdownStarted) {
      requestStop(outcome.error || new Error(`${service.name} exited unexpectedly (${outcome.signal || outcome.code}).`));
    }
  };
  child.once("error", (error) => complete({ error }));
  child.once("exit", (code, signal) => complete({ code, signal }));
  managed.push(result);
  return result;
}

async function installIfMissing(dir, { command = npm, args = ["ci", "--no-audit", "--no-fund"], lockfile = "package-lock.json" } = {}) {
  throwIfStopping();
  if (existsSync(path.join(dir, "node_modules"))) return;
  if (!existsSync(path.join(dir, lockfile))) {
    throw new Error(`Missing node_modules and ${lockfile} in ${path.relative(root, dir)}.`);
  }
  const service = startProcess({
    name: `${command} ${args[0]}: ${path.relative(root, dir)}`,
    cwd: dir,
    command: [command, ...args],
  }, { persistent: false });
  service.installDir = dir;
  const outcome = await untilStopped(service.done);
  if (outcome.error) throw outcome.error;
  if (outcome.code !== 0) throw new Error(`${service.name} exited ${outcome.signal || outcome.code}.`);
  service.installComplete = true;
  // Do not retain a completed install PID for a later shutdown: the OS may
  // reuse it while the development servers keep running for hours.
  managed.splice(managed.indexOf(service), 1);
}

async function probeHttp(url, acceptsJson, headers, acceptsAuthChallenge = false) {
  throwIfStopping();
  try {
    const response = await fetch(url, {
      signal: AbortSignal.any([stopping.signal, AbortSignal.timeout(1_500)]),
      // Do not forward an API key to a redirect destination, even on localhost.
      redirect: headers ? "manual" : "follow",
      headers,
    });
    let ready = response.ok;
    if (response.status === 401 && acceptsAuthChallenge) {
      // DSH serves its authenticated UI only after its browser-cookie exchange.
      // Its exact anonymous challenge is a ready listener, not a failed API key.
      ready = (await response.text()).trim() === "unauthorized";
    } else if (ready && acceptsJson) {
      try { ready = acceptsJson(await response.json()); }
      catch { ready = false; }
    } else {
      await response.body?.cancel().catch(() => {});
    }
    return { status: response.status, ready };
  } catch {
    throwIfStopping();
    return { ready: false };
  }
}

function readinessChecks(service) {
  return [
    ...(service.readyUrl ? [{ url: service.readyUrl, acceptsJson: service.readyJson, headers: service.readyHeaders, acceptsAuthChallenge: service.readyAuthChallenge }] : []),
    ...(service.extraReadyUrls || []).map((url) => ({ url, headers: service.readyHeaders })),
  ];
}

async function waitUntilReady(service) {
  const deadline = Date.now() + service.timeoutMs;
  const startedAt = Date.now();
  let nextProgress = startedAt + (service.progressIntervalMs || service.timeoutMs);
  const checks = readinessChecks(service);
  while (Date.now() < deadline) {
    throwIfStopping();
    const results = await Promise.all(checks.map((check) => probeHttp(check.url, check.acceptsJson, check.headers, check.acceptsAuthChallenge)));
    const deniedIndex = results.findIndex((result) => !result.ready && (result.status === 401 || result.status === 403));
    if (deniedIndex !== -1) {
      throw new Error(`${service.name} health authentication failed (HTTP ${results[deniedIndex].status}): ${checks[deniedIndex].url}. Check OV_API_KEY.`);
    }
    if (results.every((result) => result.ready) && (!service.readyLog || service.readyLogSeen)) {
      for (const { url } of checks) console.log(`[ready] ${service.name}: ${url}`);
      if (!checks.length) console.log(`[ready] ${service.name}: initial compilation completed; watching for changes`);
      return;
    }
    if (Date.now() >= nextProgress) {
      console.log(`[wait] ${service.name} is building or starting (${Math.round((Date.now() - startedAt) / 1000)}s of ${Math.round(service.timeoutMs / 1000)}s)`);
      nextProgress += service.progressIntervalMs;
    }
    await sleep(500, undefined, { signal: stopping.signal });
  }
  throw new Error(`${service.name} did not become ready${service.readyUrl ? ` at ${service.readyUrl}` : " after its initial compilation"}.`);
}

async function prepareService(service) {
  if (!service.prepare) return;
  const build = startProcess({
    name: `${service.name} initial build`,
    cwd: service.cwd,
    command: service.prepare,
    env: service.env,
  }, { persistent: false });
  const timer = setTimeout(() => requestStop(new Error(`${service.name} initial build timed out.`)), 180_000);
  try {
    const outcome = await untilStopped(build.done);
    if (outcome.error) throw outcome.error;
    if (outcome.code !== 0) throw new Error(`${service.name} initial build exited ${outcome.signal || outcome.code}.`);
    managed.splice(managed.indexOf(build), 1);
  } finally {
    clearTimeout(timer);
  }
}

async function startService(service) {
  throwIfStopping();
  if (service.readyUrl) {
    const current = await probeHttp(service.readyUrl, service.readyJson, service.readyHeaders, service.readyAuthChallenge);
    if (current.status !== undefined) {
      if (!current.ready) {
        throw new Error(`${service.name} URL is occupied but failed readiness (HTTP ${current.status}): ${service.readyUrl}. Stop that service or select a different port.`);
      }
      for (const url of service.extraReadyUrls || []) {
        const extra = await probeHttp(url, undefined, service.readyHeaders);
        if (!extra.ready) {
          throw new Error(`${service.name} is already listening, but its required endpoint is not healthy${extra.status ? ` (HTTP ${extra.status})` : ""}: ${url}`);
        }
      }
      console.log(`[reuse] ${service.name} already responds at ${service.readyUrl}; its existing configuration is retained`);
      return;
    }
  }
  await prepareService(service);
  const processInfo = startProcess(service);
  if (service.startupHint) console.log(`[start] ${service.startupHint}`);
  if (service.readyUrl || service.readyLog) await waitUntilReady(processInfo);
  else {
    await sleep(service.readyDelayMs || 1_000, undefined, { signal: stopping.signal });
    throwIfStopping();
    console.log(`[ready] ${service.name} watcher started`);
  }
}

function groupExists(child) {
  if (!child.pid) return false;
  if (process.platform === "win32") return child.exitCode === null && child.signalCode === null;
  try { process.kill(-child.pid, 0); return true; }
  catch (error) { return error.code === "EPERM"; }
}

function signalProcess(child, signal) {
  if (!child.pid) return;
  try {
    // The group may still contain descendants after its leader has exited.
    if (process.platform !== "win32") process.kill(-child.pid, signal);
    else if (signal === "SIGKILL") spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" }).on("error", () => {});
    else child.kill(signal);
  } catch (error) {
    if (error.code !== "ESRCH") console.error(`[stop] ${error.message}`);
  }
}

async function shutdown() {
  shutdownStarted = true;
  if (!managed.length) return;
  console.log("\n[stop] stopping processes started by npm run dev");
  for (const item of managed) signalProcess(item.child, "SIGTERM");
  const deadline = Date.now() + 5_000;
  while (managed.some((item) => groupExists(item.child)) && Date.now() < deadline) await sleep(100);
  for (const item of managed) if (groupExists(item.child)) signalProcess(item.child, "SIGKILL");
  await Promise.race([Promise.all(managed.map((item) => item.done)), sleep(1_000, undefined, { ref: false })]);
  // These directories were absent before this run. A cancelled npm ci can leave
  // a partial tree that would otherwise be mistaken for an installed workspace.
  for (const item of managed) {
    if (item.installDir && !item.installComplete) {
      console.log(`[bootstrap] removing incomplete dependencies in ${path.relative(root, item.installDir)}/`);
      rmSync(path.join(item.installDir, "node_modules"), { recursive: true, force: true });
    }
  }
}

async function stopOnFailure(work) {
  try { return await work(); }
  catch (error) { requestStop(error); throw error; }
}

try {
  if (!existsSync(serverBin)) throw new Error(`Missing ${serverBin}. Run uv sync --extra bot first.`);
  if (dshAvailable) {
    const [major, minor] = process.versions.node.split(".").map(Number);
    if (major < 24 || (major === 24 && minor < 9)) throw new Error("DSH dev:web requires Node >=24.9. Use a supported Node version or select a checkout without DSH_DIR.");
  }
  await stopOnFailure(() => prepareService(sharedPluginSync));
  await Promise.all([
    ...npmWorkspaces.map((dir) => stopOnFailure(() => installIfMissing(path.join(root, dir)))),
    ...(dshAvailable ? [stopOnFailure(() => installIfMissing(dshDir, { command: pnpm, args: ["install", "--frozen-lockfile"], lockfile: "pnpm-lock.yaml" }))] : []),
  ]);

  const serverService = services.find((service) => service.name === "OpenViking + VikingBot");
  await stopOnFailure(() => startService(serverService));
  await Promise.all(services.filter((service) => service !== serverService).map((service) => stopOnFailure(() => startService(service))));
  throwIfStopping();

  console.log("\nOpenViking dev stack is ready.");
  console.log(`  Web Studio: ${origin(studioPort, "127.0.0.1")}/onboarding`);
  console.log(`  React UI:   ${origin(uiDocsPort, "127.0.0.1")}/ui/`);
  console.log(`  Docs:       ${origin(docsPort)}/`);
  console.log(`  Tool API:   ${origin(openWebuiPort, "127.0.0.1")}/docs`);
  console.log(`  API:        ${origin(ovPort)}`);
  console.log(`  Bot API:    ${origin(ovPort)}/bot/v1/health`);
  if (dshAvailable) console.log(`  DSH Web:    ${origin(dshPort, "127.0.0.1")}`);
  console.log("Press Ctrl+C to stop processes started by this command.");
  throw await stopSignal;
} catch (error) {
  const reason = stopping.signal.aborted ? stopping.signal.reason : error;
  if (!(reason instanceof StopRequested)) {
    console.error(`\n[dev] ${reason.message}`);
    process.exitCode = 1;
  }
} finally {
  await shutdown();
}
