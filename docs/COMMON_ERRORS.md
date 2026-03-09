# MyGO 常见错误与解决方案

> 开发过程中遇到过的所有坑，记录下来避免下次踩。

---

## 一、构建/运行时错误

### E1: `task 'xxx' is already active`
**原因**: `.multi-agent/.lock` 文件存在，另一个任务正在运行。
```bash
my cancel           # 取消当前任务并释放锁
# 或手动删除
rm .multi-agent/.lock
```

### E2: `Port 8765 already in use`
**原因**: 之前的 dashboard 进程没有正确关闭。
```bash
kill $(lsof -ti:8765)    # 杀掉占用端口的进程
my dashboard             # 重新启动
```

### E3: `ModuleNotFoundError: No module named 'multi_agent'`
**原因**: PYTHONPATH 没有设置。
```bash
PYTHONPATH=src python -m pytest tests/
# 或
pip install -e .
```

### E4: `FileNotFoundError: Cannot find templates/ directory`
**原因**: 不在项目根目录运行，或没有 `my init`。
```bash
cd /path/to/project
my init                  # 初始化项目
# 或设置环境变量
export MA_ROOT=/path/to/project
```

---

## 二、测试错误

### E5: pytest 测试挂起不动
**原因**: SSE 端点测试用了 `TestClient` 同步请求无限流。
**解决**: 不要集成测试 SSE 无限流，改为单元测试 `_sse_format` 等辅助函数。
```python
# 错误做法
response = client.get("/api/events")  # 永远不会返回

# 正确做法
def test_sse_format():
    result = _sse_format("heartbeat", {"ts": 123})
    assert "event: heartbeat" in result
```

### E6: pytest `--timeout=30` 报 `unrecognized arguments`
**原因**: `pytest-timeout` 没有安装。
```bash
pip install pytest-timeout
# 或不用 --timeout 参数
```

### E7: 测试 `ImportError: cannot import name '_on_build_submit'`
**原因**: 函数被重构为工厂模式 `_make_on_build_submit(cfg)`。
**解决**: 用工厂函数创建 hook 实例再测试。
```python
# 旧写法（已失效）
from multi_agent.git_ops import _on_build_submit

# 新写法
from multi_agent.git_ops import _make_on_build_submit, GitConfig
cfg = GitConfig(auto_commit=True, commit_on=("build",))
hook = _make_on_build_submit(cfg)
hook(state, result)
```

---

## 三、ruff/lint 错误

### E8: `I001 Import block is un-sorted`
**原因**: 添加了新 import 但位置不对。
```bash
ruff check --fix src/multi_agent/xxx.py
```

### E9: `SIM105 Use contextlib.suppress instead of try-except-pass`
**原因**: 空的 `except: pass` 块。
```python
# 错误
try:
    do_something()
except Exception:
    pass

# 正确
import contextlib
with contextlib.suppress(Exception):
    do_something()
```

### E10: `F821 Undefined name`
**原因**: 函数内部用了模块级名称但没 import。
**教训**: 添加新函数时，检查所有引用的名称是否已导入。

---

## 四、Web Dashboard 错误

### E11: SSE 事件重复推送
**原因**: 用魔法数字估算新事件数（如 `allEvents.length - 5`）。
**解决**: 用 byte-offset 只读新增字节。
```javascript
// 错误: 估算
const newEvents = allEvents.slice(allEvents.length - 5);

// 正确: byte-offset seek
const fd = fs.openSync(filepath, "r");
const buf = Buffer.alloc(stat.size - prevSize);
fs.readSync(fd, buf, 0, buf.length, prevSize);
fs.closeSync(fd);
```

### E12: i18n data-i18n 与动态内容冲突
**原因**: `applyLang()` 遍历所有 `data-i18n` 元素并覆盖 textContent，包括已被 JS 动态更新的元素。
**解决**: 动态更新的元素不要加 `data-i18n`，在 `applyLang()` 中手动刷新。
```html
<!-- 不要这样 -->
<span id="event-count" data-i18n="event_count_zero">0 events</span>

<!-- 这样 -->
<span id="event-count">0 events</span>
```
```javascript
function applyLang() {
    // 处理静态 data-i18n 元素...
    // 手动刷新动态元素
    document.getElementById('event-count').textContent = tf('events_count', allEvents.length);
}
```

### E13: trace 文件名匹配失败
**原因**: trace 文件实际命名是 `task-xxx.events.jsonl`，但代码只查找 `xxx.jsonl`。
**解决**: 按优先级匹配多种模式。
```python
for pattern in [
    f"{task_id}.events.jsonl",      # 优先
    f"task-{task_id}.events.jsonl",
    f"{task_id}.jsonl",
    f"task-{task_id}.jsonl",
]:
```

---

## 五、Git 集成错误

### E14: decide hook 在 reject 时也触发 commit
**原因**: `_on_decide_approve` 没检查 `final_status`。
**解决**: 第一行检查 `result.get("final_status") != "approved"` 则 return。

### E15: Git hooks 每次调用重新读 YAML
**原因**: 直接在 hook 函数体内调用 `load_git_config()`。
**解决**: 用闭包工厂模式，注册时缓存配置。

---

## 六、并发/竞态错误

### E16: Lock 文件 TOCTOU 竞态
**原因**: `if not exists(): create()` 有时间窗口。
**解决**: 用 `os.O_CREAT | os.O_EXCL` 原子创建。

### E17: Watcher 读到半写的 JSON 文件
**原因**: IDE 正在写文件时 watcher 检测到变化。
**解决**: `OutboxPoller._wait_stable()` 等待文件大小稳定后再读。

### E18: 多线程并行 resume 破坏全局状态
**原因**: 多个 subtask 的 watch loop 同时调用 `resume_task`。
**解决**: `_resume_lock = threading.Lock()` 序列化 resume 调用。

---

## 七、设计决策记录

### D1: 为什么超时包含等待时间？
`build_node` 的 timeout 从 `started_at`（plan 分配时刻）开始计算，包含用户思考/操作时间。这是设计决策而非 bug — 防止"分配了永远不做"的 DoW 攻击。`MAX_TASK_DURATION_SEC = 7200` (2h) 是兜底。

### D2: 为什么 EventHooks 不加锁？
当前使用模式安全：hooks 在 `register_git_hooks()` 时一次性注册完毕，运行时只读不写。加锁会降低性能且无实际收益。

### D3: 为什么 Windows 上 trace 没有文件锁？
`fcntl` 是 POSIX-only。Windows 可用 `msvcrt.locking` 替代，但当前用户全在 macOS。留作未来兼容性改进。

### D4: ~~为什么 Dashboard 没有认证？~~ (v0.9.2 已加)
v0.9.2 新增可选 token 认证。`my dashboard --token auto` 自动生成 token。

---

## 八、v0.9.2 Code Review 发现的 Bug（7 个）

### E19: Windows .bat 命令注入
**文件**: `driver.py` `_open_terminal_window()`
**原因**: `label` 参数直接拼入 `.bat` 文件的 `title` 命令，若含 `&|<>^()!%"` 等 shell 元字符可执行任意命令。
**修复**: `re.sub(r'[&|<>^()!%"]', "", label)[:60] or "MyGO"` 过滤元字符。
**教训**: **任何拼入 shell/bat 的字符串都必须清理元字符，即使看起来是"内部"变量。**

### E20: cmd.exe `start` 第一个引号参数是窗口标题
**文件**: `driver.py`
**原因**: `start label bat_path` — 如果 `label` 含空格，`start` 会把它当命令而非标题。
**修复**: `start "title" bat_path` — 第一个带引号的参数必须是窗口标题。
**教训**: **Windows `start` 命令的参数语义和 Unix 完全不同，必须查文档。**

### E21: Express 挂载路径剥离 — `req.path` 不含前缀
**文件**: `app.js` auth 中间件
**原因**: `app.use("/api", middleware)` 挂载后，`req.path` 是 `/auth/check` 而非 `/api/auth/check`。写成后者导致 auth check 被拦截返回 401。
**修复**: 改为 `req.path === "/auth/check"`。
**教训**: **Express 中间件挂载在子路径时，`req.path` 会被剥离挂载前缀。这是 Express 最常见的坑之一。**
```javascript
// ❌ 错误
app.use("/api", (req, res, next) => {
  if (req.path === "/api/auth/check") return next(); // 永远不会匹配！
});
// ✅ 正确
app.use("/api", (req, res, next) => {
  if (req.path === "/auth/check") return next();
});
```

### E22: Login 端点被自己的 auth 中间件拦截
**文件**: `app.js`
**原因**: `/api/auth/login` 是 POST 端点（用户还没 token 才会调），但 auth 中间件只白名单了 `/auth/check`，没有白名单 `/auth/login`。
**修复**: `if (req.path === "/auth/check" || req.path === "/auth/login") return next();`
**教训**: **auth 系统的 login 端点必须在 auth 白名单中——这是逻辑循环，很容易遗漏。**

### E23: JSONL 并发写入行交错
**文件**: `finops.py` `record_task_usage()`
**原因**: 多个并行 agent 进程同时 append 到同一 JSONL 文件，没有文件锁，可能导致行交错（两行混在一起）。
**修复**: 加 `fcntl.flock(LOCK_EX)` / `LOCK_UN`，Windows 降级为无锁（`fcntl` 不可用时跳过）。
**教训**: **任何多进程 append 文件都必须加锁。参考 `trace.py` 的已有模式。**

### E24: Token 比较时序攻击
**文件**: `app.js` auth 中间件
**原因**: `token === AUTH_TOKEN` 使用 `===` 比较，字符串不等长时立即返回 false，可通过响应时间差逐字节猜测 token。
**修复**: `crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b))` 恒定时间比较。
**教训**: **密钥/token 比较永远用 timing-safe 函数，即使是本地工具。**

### E25: Token 掩码溢出 (v0.9.2)
**文件**: `cli.py` `_launch_dashboard_node()`
**原因**: `token[:4] + '*' * (len(token) - 4)` — 当 token 长度 < 4 时，负数乘法产生空字符串，直接暴露全部 token。
**修复**: `if len(token) > 4 else "****"` — 短 token 全部掩码。
**教训**: **字符串切片和掩码操作要考虑边界长度。**

---

## 九、v0.10.0 Code Review 发现的 Bug（6 个）

### E26: 用户输入字段无长度限制
**文件**: `app.js` + `server.py` actions 端点
**原因**: `reason`/`feedback`/`summary` 字段直接写入磁盘（YAML/JSON），无长度上限，攻击者可发送 1MB 字符串。
**修复**: `.slice(0, 500)` / `.slice(0, 2000)` 限制各字段长度。
**教训**: **所有写入磁盘的用户输入都要有长度上限，即使 body parser 有全局限制。**

### E27: Starlette CORS 不支持通配符端口
**文件**: `server.py` CORSMiddleware
**原因**: `allow_origins=["http://localhost:*"]` — Starlette 的 CORS 中间件不支持 glob 模式，`*` 不会匹配端口号。
**修复**: 改用 `allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"`。
**教训**: **不同框架的 CORS 配置语法不同，Express 和 Starlette 的 origin 匹配机制完全不一样。**

### E28: semantic_memory.py 内容无长度限制
**文件**: `semantic_memory.py` `store()`
**原因**: `content` 参数无最大长度检查，可能存入超大文本条目。
**修复**: `if len(content) > _MAX_CONTENT_LENGTH: content = content[:_MAX_CONTENT_LENGTH]`
**教训**: **存储层的每个字段都要有大小上限。**

### E29: _load_entries() 重复 stat 调用
**文件**: `semantic_memory.py`
**原因**: `path.stat().st_size` 调用两次（检查 + 日志），浪费 I/O 且存在 TOCTOU 风险。
**修复**: `file_size = path.stat().st_size` 存到变量复用。
**教训**: **文件元数据操作结果要缓存到局部变量。**

### E30: 未使用的 import
**文件**: `semantic_memory.py` (`os`)、`server.py` (`hashlib`)
**原因**: 复制粘贴遗留的死代码。
**修复**: 删除未使用的 import。
**教训**: **每次新建模块后跑 `ruff` 检查 unused imports。**

### E31: 文档声称支持 OpenAI embeddings 但未实现
**文件**: `semantic_memory.py` docstring
**原因**: 模块注释提到"Optionally supports OpenAI embeddings"但代码中没有实现。
**修复**: 删除误导性文档。
**教训**: **docstring 必须与实际实现一致，不能写"计划实现"的功能。**

---

## 十、v0.11.0 Code Review 发现的 Bug（2 个）

### E32: Smart Retry query 无长度限制
**文件**: `graph.py` `plan_node` Smart Retry 注入
**原因**: `state.get('requirement', '') + retry_feedback` 可能产生多 KB 的查询字符串，传给 TF-IDF 引擎浪费计算资源。
**修复**: `req_short = requirement[:300]` + `fb_short = retry_feedback[:300]` 截断到 ~600 字符。
**教训**: **传给搜索引擎的查询字符串要有长度上限，否则大文本会拖慢检索。**

### E33: 未使用的 import (contextlib in mcp_server.py)
**文件**: `mcp_server.py`
**原因**: 添加 write tools 时引入了 `contextlib` 但实际没有使用。
**修复**: 删除 `import contextlib`。
**教训**: **每次修改后检查 unused imports。**

---

## 十一、v0.12.0 Code Review 发现的 Bug（2 个）

### E34: test_graph.py 4 个测试缺少 load_contract mock
**文件**: `tests/test_graph.py`
**原因**: `build_node` 成功路径调用 `_enrich_builder_result` → `load_contract(skill_id)`，但 4 个测试未 mock 此函数，导致在 temp 目录找不到 contract 文件。
**修复**: 为 4 个测试添加 `@patch("multi_agent.graph.load_contract")` 并设置 `quality_gates=[]`。
**教训**: **当被测函数的执行路径变长时，需要审查所有相关测试是否 mock 了新增的外部依赖。**

### E35: _detect_webhook_format 匹配范围过宽
**文件**: `notify.py` `_detect_webhook_format()`
**原因**: `"slack" in url_lower` 会匹配任何包含 "slack" 的 URL（如 `example.com/slack-alternative`），导致误判。
**修复**: 只匹配 `hooks.slack.com` 和 `discord.com/api/webhooks`。
**教训**: **URL 格式检测要匹配具体域名，不要用通用子串匹配。**

---

## 十二、v0.13.0 Code Review 发现的 Bug（3 个）

### E36: OpenAI embed 输入无长度限制
**文件**: `semantic_memory.py` `_openai_embed()`
**原因**: 直接传入用户内容到 OpenAI API，text-embedding-3-small 有 ~8191 token 限制，长文本会失败或浪费 token。
**修复**: `capped = [t[:_MAX_EMBED_CHARS] for t in texts]`，`_MAX_EMBED_CHARS = 8000`。
**教训**: **调用外部 API 前必须截断输入到 API 限制范围内。**

### E37: _search_openai else 分支未包裹 try/except
**文件**: `semantic_memory.py` `_search_openai()`
**原因**: 当所有 entry 已缓存但 query 需要新 embed 时，`_openai_embed([query])` 在 try/except 外，失败会直接 crash 而非 fallback。
**修复**: 将 else 分支的 `_openai_embed` 调用也包裹在 try/except 中。
**教训**: **所有外部 API 调用都必须有异常处理，特别是有 fallback 路径时。**

### E38: Embeddings cache 无大小限制
**文件**: `semantic_memory.py` embeddings cache
**原因**: cache 会随 entry 增长无限膨胀，占用磁盘空间。
**修复**: `_MAX_EMBED_CACHE_ENTRIES = 10000`，超过时按 key 排序裁剪最旧的。
**教训**: **所有持久化缓存都要有大小上限和淘汰策略。**
