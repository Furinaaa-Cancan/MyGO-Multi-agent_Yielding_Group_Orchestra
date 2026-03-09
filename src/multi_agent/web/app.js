/**
 * MyGO Dashboard — Node.js/Express backend
 *
 * Real-time task monitoring with REST API + SSE event stream.
 * Launched by `my dashboard` CLI command, which passes workspace paths
 * via environment variables so we don't duplicate Python path logic.
 *
 * Usage:
 *   node app.js                          # defaults
 *   node app.js --port 9000 --host 0.0.0.0
 *
 * Env vars (set by CLI launcher):
 *   MYGO_WORKSPACE_DIR  — .multi-agent/ absolute path
 *   MYGO_ROOT_DIR       — project root absolute path
 *   MYGO_HISTORY_DIR    — history/ absolute path
 *   MYGO_AUTH_TOKEN     — optional Bearer token for API authentication
 */

const crypto = require("crypto");
const express = require("express");
const path = require("path");
const fs = require("fs");
const yaml = require("js-yaml");
const chokidar = require("chokidar");

// ── CLI args ────────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(name, fallback) {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && args[idx + 1] ? args[idx + 1] : fallback;
}

const PORT = parseInt(getArg("port", "8765"), 10);
const HOST = getArg("host", "127.0.0.1");
const WORKSPACE = getArg("workspace", process.env.MYGO_WORKSPACE_DIR || "");
const ROOT_DIR = getArg("root", process.env.MYGO_ROOT_DIR || process.cwd());
const HISTORY = getArg("history", process.env.MYGO_HISTORY_DIR || "");
const AUTH_TOKEN = getArg("token", process.env.MYGO_AUTH_TOKEN || "");

// Resolve paths (mutable for multi-project switching)
let wsDir = WORKSPACE || path.join(ROOT_DIR, ".multi-agent");
let historyDir = HISTORY || path.join(wsDir, "history");
let tasksDir = path.join(wsDir, "tasks");
let lockFile = path.join(wsDir, ".lock");
let dashboardMd = path.join(wsDir, "dashboard.md");
let currentRootDir = ROOT_DIR;

// ── Multi-project Registry ──────────────────────────────
const projectRegistry = new Map(); // name → rootDir
projectRegistry.set(path.basename(ROOT_DIR), ROOT_DIR);

function switchProject(rootDir) {
  if (!fs.existsSync(rootDir)) return false;
  currentRootDir = rootDir;
  wsDir = path.join(rootDir, ".multi-agent");
  historyDir = path.join(wsDir, "history");
  tasksDir = path.join(wsDir, "tasks");
  lockFile = path.join(wsDir, ".lock");
  dashboardMd = path.join(wsDir, "dashboard.md");
  return true;
}

// ── Security Constants ──────────────────────────────────
const MAX_YAML_FILE_SIZE = 5 * 1024 * 1024; // 5 MB cap for YAML reads
const MAX_SSE_CONNECTIONS = 10; // prevent resource exhaustion
let activeSSEConnections = 0;

// ── Validation ──────────────────────────────────────────
// Match Python side: [a-z0-9][a-z0-9-]{2,63} for task IDs
const SAFE_TASK_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/;

function isValidTaskId(id) {
  return SAFE_TASK_ID_RE.test(id) && !id.includes("..");
}

// ── Helpers ─────────────────────────────────────────────

function readFileSafe(filepath) {
  try {
    return fs.readFileSync(filepath, "utf-8");
  } catch {
    return null;
  }
}

function readLock() {
  const content = readFileSafe(lockFile);
  return content ? content.trim() || null : null;
}

function readDashboardMd() {
  return readFileSafe(dashboardMd) || "";
}

function readYamlFile(filepath) {
  try {
    const stat = fs.statSync(filepath);
    if (stat.size > MAX_YAML_FILE_SIZE) return null;
  } catch { return null; }
  const text = readFileSafe(filepath);
  if (!text) return null;
  try {
    // SECURITY: Use JSON_SCHEMA to prevent !!js/function and other unsafe YAML tags
    return yaml.load(text, { schema: yaml.JSON_SCHEMA }) || {};
  } catch {
    return null;
  }
}

function parseJsonlFile(filepath) {
  const text = readFileSafe(filepath);
  if (!text) return [];
  const results = [];
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      results.push(JSON.parse(trimmed));
    } catch {
      // skip bad lines
    }
  }
  return results;
}

function readTraceEvents(taskId) {
  // Direct file match (supports: id.jsonl, task-id.jsonl, task-id.events.jsonl, id.events.jsonl)
  for (const pattern of [`${taskId}.events.jsonl`, `task-${taskId}.events.jsonl`, `${taskId}.jsonl`, `task-${taskId}.jsonl`]) {
    const fp = path.join(historyDir, pattern);
    if (fs.existsSync(fp)) {
      return parseJsonlFile(fp);
    }
  }
  // Fallback: scan all JSONL files
  const events = [];
  if (fs.existsSync(historyDir)) {
    for (const f of fs.readdirSync(historyDir)) {
      if (!f.endsWith(".jsonl")) continue;
      for (const evt of parseJsonlFile(path.join(historyDir, f))) {
        const tid = evt.task_id || "";
        if (tid === taskId || tid.endsWith(taskId)) {
          events.push(evt);
        }
      }
    }
  }
  return events;
}

function listTasks() {
  if (!fs.existsSync(tasksDir)) return [];
  const files = fs
    .readdirSync(tasksDir)
    .filter((f) => f.endsWith(".yaml"))
    .map((f) => {
      const fp = path.join(tasksDir, f);
      const stat = fs.statSync(fp);
      return { name: f, path: fp, mtime: stat.mtimeMs / 1000 };
    })
    .sort((a, b) => b.mtime - a.mtime);

  return files.map((f) => {
    const data = readYamlFile(f.path);
    if (!data) return { task_id: f.name.replace(".yaml", ""), file: f.name, error: "parse failed" };
    return {
      task_id: f.name.replace(".yaml", "").replace(/^task-/, ""),
      file: f.name,
      requirement: data.requirement || "",
      status: data.status || "unknown",
      current_agent: data.current_agent || "",
      modified: f.mtime,
    };
  });
}

// ── Express App ─────────────────────────────────────────

const app = express();

// ── CORS — restrict to localhost origins only ───────────
app.use((req, res, next) => {
  const origin = req.headers.origin || "";
  // Only allow requests from localhost origins (prevent CSRF from external sites)
  if (origin && !/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(origin)) {
    return res.status(403).json({ error: "CORS: origin not allowed" });
  }
  if (origin) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  }
  if (req.method === "OPTIONS") return res.sendStatus(204);
  next();
});

// Serve static files (index.html) — no auth required so login page loads
app.use(express.static(path.join(__dirname, "static")));

// ── Authentication ──────────────────────────────────────
// Timing-safe string comparison to prevent timing attacks on token
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
}

// If AUTH_TOKEN is set, require Bearer token on all /api/* routes.
// Static files are served without auth so the login UI can load.
function authMiddleware(req, res, next) {
  if (!AUTH_TOKEN) return next(); // auth disabled

  // Auth endpoints are public — frontend uses them to detect and perform auth
  // Note: when mounted via app.use("/api", ...), req.path is relative to mount point
  if (req.path === "/auth/check" || req.path === "/auth/login") return next();

  // SSE: accept token via query param (?token=xxx) since EventSource can't set headers
  if (req.path === "/events" && safeEqual(req.query.token || "", AUTH_TOKEN)) return next();

  const authHeader = req.headers.authorization || "";
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : "";
  if (!safeEqual(token, AUTH_TOKEN)) {
    return res.status(401).json({ error: "Unauthorized", auth_required: true });
  }
  next();
}
app.use("/api", authMiddleware);

// ── REST API ────────────────────────────────────────────

app.use(express.json({ limit: "1mb" }));

// Auth check endpoint — tells frontend whether auth is required
app.get("/api/auth/check", (_req, res) => {
  res.json({ auth_required: !!AUTH_TOKEN });
});

// Auth login endpoint — validate token
app.post("/api/auth/login", (req, res) => {
  if (!AUTH_TOKEN) return res.json({ ok: true });
  const { token } = req.body || {};
  if (safeEqual(token || "", AUTH_TOKEN)) {
    res.json({ ok: true });
  } else {
    res.status(401).json({ ok: false, error: "Invalid token" });
  }
});

// ── Multi-project API ──────────────────────────────────

app.get("/api/projects", (_req, res) => {
  const projects = [];
  for (const [name, rootDir] of projectRegistry) {
    const ws = path.join(rootDir, ".multi-agent");
    const lockPath = path.join(ws, ".lock");
    let active = null;
    try { active = fs.readFileSync(lockPath, "utf-8").trim() || null; } catch { /* no lock */ }
    // Don't leak absolute paths — show relative or basename only
    projects.push({ name, active_task: active, current: rootDir === currentRootDir });
  }
  res.json({ projects, count: projects.length });
});

app.post("/api/projects/add", (req, res) => {
  const { name, root } = req.body || {};
  if (!name || !root) return res.status(400).json({ error: "name and root required" });
  // Validate project name (alphanumeric + hyphens, max 64 chars)
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(name)) {
    return res.status(400).json({ error: "invalid project name" });
  }
  if (!fs.existsSync(root)) return res.status(400).json({ error: "path not found" });
  // Validate project has .multi-agent workspace (prevent arbitrary dir access)
  const wsPath = path.join(root, ".multi-agent");
  if (!fs.existsSync(wsPath)) return res.status(400).json({ error: "not a MyGO project (no .multi-agent directory)" });
  projectRegistry.set(name, root);
  res.json({ status: "added", name, root });
});

app.post("/api/projects/switch", (req, res) => {
  const { name } = req.body || {};
  if (!name) return res.status(400).json({ error: "name required" });
  const rootDir = projectRegistry.get(name);
  if (!rootDir) return res.status(404).json({ error: `project not found: ${name}` });
  if (!switchProject(rootDir)) return res.status(400).json({ error: `path not accessible: ${rootDir}` });
  res.json({ status: "switched", name, root: rootDir });
});

// ── Agent Health API ────────────────────────────────────

app.get("/api/agents", (_req, res) => {
  const agentsFile = path.join(currentRootDir, "agents", "agents.yaml");
  if (!fs.existsSync(agentsFile)) return res.json({ agents: [], count: 0 });
  const data = readYamlFile(agentsFile);
  if (!data || !Array.isArray(data.agents)) return res.json({ agents: [], count: 0 });

  const { execFileSync } = require("child_process");
  const SAFE_BINARY_RE = /^[a-zA-Z0-9._-]+$/;
  const results = data.agents.map(a => {
    const info = { id: a.id || "?", driver: a.driver || "file", capabilities: a.capabilities || [], issues: [] };
    if (a.driver === "cli") {
      const binary = (a.command || "").split(" ")[0];
      info.cli_binary = binary;
      if (!binary || !SAFE_BINARY_RE.test(binary)) {
        info.cli_available = false;
        info.issues.push("invalid CLI binary name");
      } else {
        try { execFileSync("which", [binary], { stdio: "pipe" }); info.cli_available = true; }
        catch { info.cli_available = false; info.issues.push(`CLI binary '${binary}' not found`); }
      }
    } else if (a.driver === "gui") {
      info.app_name = a.app_name || "";
    }
    info.status = info.issues.length === 0 ? "healthy" : "degraded";
    return info;
  });
  res.json({ agents: results, count: results.length });
});

// ── Core API ───────────────────────────────────────────

app.get("/api/status", (_req, res) => {
  res.json({
    active_task: readLock(),
    root_dir: currentRootDir,
    project: path.basename(currentRootDir),
    dashboard_md: readDashboardMd(),
  });
});

app.get("/api/tasks", (_req, res) => {
  const tasks = listTasks();
  res.json({ tasks, count: tasks.length });
});

app.get("/api/tasks/:taskId", (req, res) => {
  const { taskId } = req.params;
  if (!isValidTaskId(taskId)) {
    return res.status(400).json({ error: "invalid task_id" });
  }

  // Find task YAML
  let taskData = {};
  for (const name of [`task-${taskId}.yaml`, `${taskId}.yaml`]) {
    const fp = path.join(tasksDir, name);
    const data = readYamlFile(fp);
    if (data) {
      taskData = data;
      break;
    }
  }

  const traceEvents = readTraceEvents(taskId);
  res.json({ task_id: taskId, task_data: taskData, trace_events: traceEvents });
});

app.get("/api/tasks/:taskId/trace", (req, res) => {
  const { taskId } = req.params;
  if (!isValidTaskId(taskId)) {
    return res.status(400).json({ error: "invalid task_id" });
  }
  res.json({ task_id: taskId, events: readTraceEvents(taskId) });
});

// ── FinOps API ───────────────────────────────────────────

app.get("/api/finops", (_req, res) => {
  const usageFile = path.join(wsDir, "logs", "token-usage.jsonl");
  if (!fs.existsSync(usageFile)) {
    return res.json({ total_tokens: 0, input_tokens: 0, output_tokens: 0, total_cost: 0, task_count: 0, entry_count: 0, by_task: {}, by_node: {}, by_agent: {} });
  }
  try {
    const stat = fs.statSync(usageFile);
    if (stat.size > 10 * 1024 * 1024) return res.status(413).json({ error: "Usage log too large" });
  } catch { return res.json({ total_tokens: 0, input_tokens: 0, output_tokens: 0, total_cost: 0, task_count: 0, entry_count: 0, by_task: {}, by_node: {}, by_agent: {} }); }

  const entries = parseJsonlFile(usageFile);
  const totals = { total_tokens: 0, input_tokens: 0, output_tokens: 0, total_cost: 0 };
  const byTask = {}, byNode = {}, byAgent = {};
  const taskIds = new Set();

  for (const e of entries) {
    const inp = parseInt(e.input_tokens || 0, 10);
    const out = parseInt(e.output_tokens || 0, 10);
    const tot = parseInt(e.total_tokens || 0, 10) || (inp + out);
    const cost = parseFloat(e.cost || 0);
    const tid = e.task_id || "unknown";
    const node = e.node || "unknown";
    const agent = e.agent_id || "unknown";
    taskIds.add(tid);

    totals.total_tokens += tot;
    totals.input_tokens += inp;
    totals.output_tokens += out;
    totals.total_cost += cost;

    for (const [bucket, key] of [[byTask, tid], [byNode, node], [byAgent, agent]]) {
      if (!bucket[key]) bucket[key] = { total_tokens: 0, input_tokens: 0, output_tokens: 0, cost: 0, count: 0 };
      const b = bucket[key];
      b.total_tokens += tot; b.input_tokens += inp; b.output_tokens += out; b.cost += cost; b.count++;
    }
  }

  totals.total_cost = Math.round(totals.total_cost * 1e6) / 1e6;
  for (const bucket of [byTask, byNode, byAgent]) {
    for (const v of Object.values(bucket)) v.cost = Math.round(v.cost * 1e6) / 1e6;
  }

  res.json({ ...totals, task_count: taskIds.size, entry_count: entries.length, by_task: byTask, by_node: byNode, by_agent: byAgent });
});

// ── SSE Event Stream ────────────────────────────────────

function sseFormat(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

app.get("/api/events", (req, res) => {
  // SSE connection limit to prevent resource exhaustion
  if (activeSSEConnections >= MAX_SSE_CONNECTIONS) {
    return res.status(503).json({ error: "Too many SSE connections" });
  }
  activeSSEConnections++;

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  // Send initial connected event
  res.write(sseFormat("connected", { ts: Date.now() / 1000 }));

  // Watch workspace for changes
  const watchPaths = [wsDir];
  if (fs.existsSync(historyDir)) watchPaths.push(historyDir);

  let lastDashboardMtime = 0;
  const traceSizes = {};

  const watcher = chokidar.watch(watchPaths, {
    ignoreInitial: true,
    persistent: true,
    depth: 2,
    awaitWriteFinish: { stabilityThreshold: 300 },
  });

  watcher.on("change", (filepath) => {
    try {
      // Dashboard update
      if (filepath === dashboardMd || filepath.endsWith("dashboard.md")) {
        const stat = fs.statSync(filepath);
        if (stat.mtimeMs > lastDashboardMtime) {
          lastDashboardMtime = stat.mtimeMs;
          const content = readFileSafe(filepath);
          if (content) {
            res.write(sseFormat("dashboard_update", { content, mtime: stat.mtimeMs / 1000 }));
          }
        }
      }

      // Trace file update — read only new bytes (no duplicates)
      if (filepath.endsWith(".jsonl")) {
        const stat = fs.statSync(filepath);
        const fname = path.basename(filepath);
        const prevSize = traceSizes[fname] || 0;
        if (stat.size > prevSize) {
          traceSizes[fname] = stat.size;
          // Read only the new portion of the file
          const fd = fs.openSync(filepath, "r");
          const buf = Buffer.alloc(stat.size - prevSize);
          fs.readSync(fd, buf, 0, buf.length, prevSize);
          fs.closeSync(fd);
          const newContent = buf.toString("utf-8");
          const newEvents = [];
          for (const line of newContent.split("\n")) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try { newEvents.push(JSON.parse(trimmed)); } catch { /* skip */ }
          }
          if (newEvents.length > 0) {
            res.write(sseFormat("trace_update", { file: fname, new_events: newEvents, size: stat.size }));
          }
        }
      }

      // Lock file change
      if (filepath === lockFile || filepath.endsWith(".lock")) {
        const activeTask = readLock();
        res.write(sseFormat("status_update", { active_task: activeTask, ts: Date.now() / 1000 }));
      }
    } catch (err) {
      // Swallow errors to keep SSE alive
    }
  });

  watcher.on("add", (filepath) => {
    if (filepath.endsWith(".lock")) {
      const activeTask = readLock();
      res.write(sseFormat("status_update", { active_task: activeTask, ts: Date.now() / 1000 }));
    }
  });

  watcher.on("unlink", (filepath) => {
    if (filepath.endsWith(".lock")) {
      res.write(sseFormat("status_update", { active_task: null, ts: Date.now() / 1000 }));
    }
  });

  // Heartbeat every 15s
  const heartbeat = setInterval(() => {
    res.write(sseFormat("heartbeat", { ts: Date.now() / 1000 }));
  }, 15000);

  // Cleanup on disconnect
  req.on("close", () => {
    activeSSEConnections--;
    clearInterval(heartbeat);
    watcher.close();
  });
});

// ── Start Server ────────────────────────────────────────

const server = app.listen(PORT, HOST, () => {
  console.log(`\n  🎸 MyGO Dashboard running at http://${HOST}:${PORT}`);
  console.log(`     Workspace: ${wsDir}`);
  console.log(`     Auth: ${AUTH_TOKEN ? "enabled (token required)" : "disabled"}`);
  console.log(`     Press Ctrl+C to stop\n`);
});

// Graceful shutdown
function shutdown() {
  console.log("\n  Shutting down...");
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 3000);
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
