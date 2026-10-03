"use strict";

// Private stdio protocol only. Never print a browser endpoint or native error to
// stderr/logs. Public Playwright BrowserServer.process() owns the original child.
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const {createHash} = require("node:crypto");
let server, child, original, exited = false, exitPromise;
let identity, shuttingDown = false, initialized = false;

async function digestFile(filename) {
  const hash = createHash("sha256");
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  return hash.digest("hex");
}

function processIdentity(pid) {
  const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
  const fields = stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/);
  return {pid, start: fields[19], boot: fs.readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim()};
}

function sameProcess() {
  if (exited || !child || child.exitCode !== null || child.signalCode !== null) return false;
  try { return JSON.stringify(processIdentity(child.pid)) === JSON.stringify(original); }
  catch { return false; }
}

function bounded(promise, ms) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("deadline")), ms);
  })]).finally(() => clearTimeout(timer));
}

async function terminate() {
  if (!server || !child) throw new Error("absent");
  if (!exited) {
    try { await bounded(server.close(), 7000); }
    catch { await bounded(server.kill(), 7000); }
    await bounded(exitPromise, 5000);
  }
  if (!exited) throw new Error("unproven");
  // The original ChildProcess exit event is required; PID disappearance alone
  // and Playwright disconnect/close acknowledgement are insufficient.
  return {...original, identity, exited: true};
}

async function command(message) {
  if (message.op === "launch") {
    if (initialized || process.platform !== "linux" || !/^[a-f0-9]{64}$/.test(message.identity)) {
      throw new Error("invalid");
    }
    initialized = true;
    const packageRoot = path.resolve(process.argv[2]);
    const metadata = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"));
    if (metadata.version !== "1.63.0") throw new Error("version");
    const {chromium} = require(packageRoot);
    if (await digestFile(process.execPath) !== message.node_digest
        || await digestFile(chromium.executablePath()) !== message.chromium_digest) {
      throw new Error("binary");
    }
    identity = message.identity;
    server = await chromium.launchServer({
      executablePath: chromium.executablePath(),
      host: "127.0.0.1", headless: true, chromiumSandbox: true,
      handleSIGINT: false, handleSIGTERM: false, handleSIGHUP: false, timeout: 15000,
    });
    child = server.process();
    if (!child || !Number.isSafeInteger(child.pid) || child.pid <= 0) throw new Error("child");
    exitPromise = new Promise(resolve => child.once("exit", () => { exited = true; resolve(); }));
    if (child.exitCode !== null || child.signalCode !== null) throw new Error("exited");
    original = processIdentity(child.pid);
    if (!/^[0-9]+$/.test(original.start) || !sameProcess()) throw new Error("identity");
    return {...original, identity, endpoint: server.wsEndpoint(), exited: false};
  }
  if (!initialized || message.identity !== identity) throw new Error("identity");
  if (message.op === "status") {
    if (!sameProcess()) throw new Error("unavailable");
    return {...original, identity, exited: false};
  }
  if (message.op === "stop") return terminate();
  throw new Error("unsupported");
}

async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  try { if (server) await terminate(); }
  catch { /* Parent retains quarantine when native exit proof is unavailable. */ }
  process.exit(0);
}

const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
let queue = Promise.resolve();
input.on("line", line => {
  queue = queue.then(async () => {
    let message;
    try {
      if (Buffer.byteLength(line) > 4096) throw new Error("bound");
      message = JSON.parse(line);
      if (!Number.isSafeInteger(message.id) || message.id < 1) throw new Error("request");
      const result = await command(message);
      process.stdout.write(JSON.stringify({id: message.id, ok: true, result}) + "\n");
    } catch {
      process.stdout.write(JSON.stringify({id: message?.id ?? 0, ok: false, code: "unavailable"}) + "\n");
    }
  }).catch(() => shutdown());
});
input.on("close", () => { queue.finally(shutdown); });
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
process.on("uncaughtException", shutdown);
process.on("unhandledRejection", shutdown);
