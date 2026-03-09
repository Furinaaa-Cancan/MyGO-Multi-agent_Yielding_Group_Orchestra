# MyGO 开发技能速查表

> 写代码时常用的模式、最佳实践和经验总结。每次开发前快速浏览，避免重复踩坑。

---

## 一、文件操作

### 原子写入（防止 watcher 读到半写文件）
```python
import tempfile, os
from pathlib import Path

fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    Path(tmp).replace(path)  # POSIX 原子替换
except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise
```

### 文件锁（防止并发写入）
```python
import fcntl  # POSIX only
with open(path, "a+") as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    try:
        # 读写操作
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

### 原子 Lock（O_EXCL 防竞态）
```python
import os
fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
try:
    os.write(fd, task_id.encode("utf-8"))
finally:
    os.close(fd)
```

---

## 二、LangGraph 模式

### interrupt + resume 暂停恢复
```python
from langgraph.types import interrupt
result = interrupt({"role": "builder", "agent": agent_id})
# 图暂停，外部调用 app.invoke(Command(resume=data), config) 恢复
```

### GraphInterrupt 是正常流程
```python
try:
    app.invoke(initial_state, config)
except GraphInterrupt:
    pass  # 正常 — 图在 interrupt() 处暂停
```

### 状态图 + checkpoint
```python
from langgraph.checkpoint.sqlite import SqliteSaver
conn = sqlite3.connect(db_path, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
checkpointer = SqliteSaver(conn)
compiled = graph.compile(checkpointer=checkpointer)
```

---

## 三、CLI 模式

### Click 命令结构
```python
@main.command()
@click.option("--flag", default="value", help="说明")
@handle_errors
def my_command(flag: str) -> None:
    """命令说明文档."""
    pass
```

### Agent 驱动分发（单一入口）
```python
from multi_agent.driver import dispatch_agent
result = dispatch_agent(agent_id, role, timeout_sec=600, visible=visible)
# result.mode: "auto" | "manual" | "degraded"
# result.message: 用户可读状态
```

---

## 四、EventHooks 模式

### 注册钩子（闭包缓存配置）
```python
def _make_hook(cfg: Config) -> Callable:
    def hook(state, result=None):
        if not cfg.enabled:
            return
        # 使用缓存的 cfg，不重新读磁盘
    return hook

graph_hooks.on_node_exit("build", _make_hook(cfg))
```

### fire 时静默错误（不中断主流程）
```python
def fire_exit(self, node, state, result=None):
    for cb in self._exit.get(node, []):
        try:
            cb(state, result)
        except Exception as e:
            _log.warning("Hook error: %s", e)  # 不 raise
```

---

## 五、Web Dashboard 模式

### Node.js SSE（byte-offset 增量读取）
```javascript
const fd = fs.openSync(filepath, "r");
const buf = Buffer.alloc(stat.size - prevSize);
fs.readSync(fd, buf, 0, buf.length, prevSize);
fs.closeSync(fd);
```

### Python SSE（seek 增量）
```python
with f.open("r") as fh:
    fh.seek(prev_size)
    new_content = fh.read()
```

### chokidar 文件监听
```javascript
const watcher = chokidar.watch(dirs, {
    ignoreInitial: true,
    awaitWriteFinish: { stabilityThreshold: 300 },
});
watcher.on("change", filepath => { ... });
```

---

## 六、i18n 模式

### 静态文本用 data-i18n
```html
<span data-i18n="key">English Default</span>
```

### 动态文本不用 data-i18n（在 JS 中直接调用 t/tf）
```javascript
el.textContent = tf('events_count', allEvents.length);
```

### localStorage 持久化
```javascript
let lang = localStorage.getItem('mygo-lang') || 'en';
localStorage.setItem('mygo-lang', lang);
```

---

## 七、测试模式

### monkeypatch 替换模块函数
```python
monkeypatch.setattr("multi_agent.git_ops.auto_commit", MagicMock())
```

### 临时目录隔离
```python
def test_xxx(tmp_path, monkeypatch):
    monkeypatch.setattr("multi_agent.config.workspace_dir", lambda: tmp_path)
```

### 工厂函数测试
```python
cfg = GitConfig(auto_commit=True, commit_on=("build",))
hook = _make_on_build_submit(cfg)
hook({"task_id": "t1"}, {"builder_output": {...}})
```

---

## 八、安全模式

### 路径遍历防护
```python
import re
SAFE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
def validate(id: str) -> bool:
    return bool(SAFE_RE.match(id)) and ".." not in id
```

### 模板注入防护
```python
# Jinja2 autoescape
env = Environment(autoescape=select_autoescape([]))
# 截断长文本
sanitized[field] = val[:MAX_CHARS] + " [TRUNCATED]"
```

### CLI 命令注入防护
```python
import shlex
cmd_list = shlex.split(cmd_str)  # 不用 shell=True
subprocess.Popen(cmd_list, shell=False)
```

---

## 六、Express.js / Node.js 后端模式

### 中间件挂载路径 — req.path 会被剥离前缀
```javascript
// app.use("/api", middleware) 挂载后，req.path 是相对路径
// ❌ req.path === "/api/auth/check"  — 永远不匹配
// ✅ req.path === "/auth/check"      — 正确
app.use("/api", (req, res, next) => {
  if (req.path === "/auth/check") return next();  // 白名单
  // ... auth 检查
});
```

### Timing-safe token 比较
```javascript
const crypto = require("crypto");
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
}
```

### parseInt 始终指定基数
```javascript
parseInt(value, 10);  // ✅ 明确十进制
parseInt(value);      // ❌ "08" 在旧引擎会被解析为八进制
```

---

## 七、Windows 跨平台模式

### .bat 文件安全 — 清理 shell 元字符
```python
import re
safe_label = re.sub(r'[&|<>^()!%"]', "", label)[:60] or "Fallback"
# 用在 title / start 命令中
```

### cmd.exe start 命令语法
```batch
:: start 第一个带引号的参数被视为窗口标题
start "Window Title" myapp.bat
:: 不加引号的标题含空格会出错
```

### fcntl 跨平台降级
```python
try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None
fcntl = _fcntl

# 使用时检查
if fcntl is not None:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
```

---

## 八、TF-IDF 语义检索（零依赖）

### 轻量级文本搜索（无需向量数据库）
```python
import math, re
from collections import Counter

def tokenize(text):
    return [t for t in re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text.lower()) if len(t) > 1]

def build_idf(docs):
    n = len(docs)
    df = Counter()
    for doc in docs: df.update(set(doc))
    return {t: math.log((n+1)/(f+1))+1 for t, f in df.items()}

def cosine_sim(a, b):
    keys = set(a) & set(b)
    if not keys: return 0.0
    dot = sum(a[k]*b[k] for k in keys)
    na = math.sqrt(sum(v*v for v in a.values()))
    nb = math.sqrt(sum(v*v for v in b.values()))
    return dot/(na*nb) if na and nb else 0.0
```
**适用**: 500-5000 条内存条目，响应 <50ms，无需安装任何依赖。

---

## 九、Webhook 通知配置

```yaml
# .ma.yaml
notify:
  enabled: true
  webhook_url: "https://hooks.slack.com/services/T00/B00/xxx"
  webhook_format: auto    # auto | slack | discord | generic
  webhook_retries: 2      # 0-5, exponential backoff (1s→2s→4s→8s)
```

- `auto` 自动从 URL 检测格式（`hooks.slack.com` → Slack，`discord.com/api/webhooks` → Discord）
- Slack：带颜色标记的 attachment + Task/Status/Retries fields
- Discord：embed + color + fields + description
- Generic：原始 JSON `{event, task_id, status, summary, retries}`
- Decompose 完成时发送摘要通知（总/完成/失败子任务数 + 耗时）

## 十、Auth 系统设计检查清单

1. ✅ login 端点必须在 auth 白名单中（否则用户无法登录）
2. ✅ auth check 端点必须公开（前端需要知道是否需要认证）
3. ✅ SSE EventSource 不能设 header → 用 query param 传 token
4. ✅ token 比较用 timing-safe 函数
5. ✅ token 显示时掩码处理，考虑 len < 4 边界
6. ✅ CORS 允许 Authorization header
7. ✅ 静态文件无需 auth（否则 login 页面加载不了）

---

## 十一、双后端搜索 + 自动降级 (v0.13.0)

```python
def _get_backend() -> str:
    cfg = _get_memory_config()
    if cfg.get("backend") == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "tfidf"

def search(query, **kw):
    backend = _get_backend()
    if backend == "openai":
        try:
            return _search_openai(query, **kw)
        except Exception:
            return _search_tfidf(query, **kw)  # 自动降级
    return _search_tfidf(query, **kw)
```
**要点**: 外部 API 调用**必须** try/except + fallback。缓存要设上限 + 裁剪。

---

## 十二、YAML Manifest 加载验证模式 (v0.14.0)

```python
_MAX_FILE_SIZE = 256 * 1024
_MAX_ITEMS = 50

def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(...)
    if path.stat().st_size > _MAX_FILE_SIZE:
        raise ValidationError("too large")
    data = yaml.safe_load(path.read_text("utf-8"))
    items = data.get("tasks")
    if not isinstance(items, list) or not items:
        raise ValidationError("non-empty list required")
    if len(items) > _MAX_ITEMS:
        raise ValidationError(f"Too many: {len(items)} > {_MAX_ITEMS}")
    for i, item in enumerate(items):
        if not item.get("requirement") and not item.get("template"):
            raise ValidationError(f"Item #{i+1} needs requirement or template")
    return items
```
**要点**: 文件大小限制 → YAML 解析 → 结构校验 → 逐项校验。**每一层都防御。**

---

## 十三、Config Profiles 模式 (v0.15.0)

```yaml
# .ma.yaml
profiles:
  fast:
    retry_budget: 0
    timeout: 600
    builder: windsurf
    reviewer: windsurf
  thorough:
    retry_budget: 5
    timeout: 3600
    reviewer: codex
```

```python
# CLI: 显式 flag 永远覆盖 profile
if not builder and prof.get("builder"):
    builder = prof["builder"]
if retry_budget == 2 and "retry_budget" in prof:  # 2 是 Click 默认值
    retry_budget = prof["retry_budget"]
```
**要点**: Profile 只提供默认值，CLI flag 优先。用 `_PROFILE_FIELDS` frozenset 过滤未知字段。

---

## 十四、优先级队列 + Daemon 模式 (v0.16.0)

```python
# 优先级排序: high(0) > normal(1) > low(2), 同优先级按提交时间 FIFO
_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}
queued.sort(key=lambda e: (
    _PRIORITY_ORDER.get(e.get("priority", "normal"), 1),
    e.get("submitted_at", 0),
))

# Daemon 优雅退出
_shutdown = [False]  # 用 list 避免 nonlocal
def _handle_signal(signum, frame):
    _shutdown[0] = True
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)
```
**要点**: JSONL 存储 → 优先级+FIFO → signal 优雅退出 → `--once` 单次模式方便测试。

---

## 十五、新模块 Import 检查清单 (v0.14-v0.16 教训)

每次创建新 `.py` 模块后，检查：
1. ✅ 每个 import 都有实际使用（E39/E40/E41 连续踩坑）
2. ✅ 不要从模板复制 `import os, logging, time` 全家桶
3. ✅ CLI 子命令中的 `from pathlib import Path` 要在函数内导入
4. ✅ Click Choice 列表要和 elif 分支对应（E42：export/import/prune 漏加）
