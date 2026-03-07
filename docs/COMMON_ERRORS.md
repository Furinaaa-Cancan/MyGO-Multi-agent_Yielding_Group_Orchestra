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

### D4: 为什么 Dashboard 没有认证？
默认绑定 `127.0.0.1`，只有本机可访问。加认证会增加使用复杂度（需要记 token/密码）。CLI 已有 non-localhost 警告。
