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
