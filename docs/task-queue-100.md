# 🚀 AgentOrchestra 一夜任务队列 — 100 条详细提示词

> 每条提示词都包含：实现要求、严格评审标准、验证步骤
> 用法: `ma go "提示词内容" --builder windsurf --reviewer cursor`

---

## 一、核心架构加固 (1-15)

### 1. graph.py — plan_node 集成 validate_preconditions

```
在 src/multi_agent/graph.py 的 plan_node 函数中集成 contract.py 的 validate_preconditions 函数。

实现要求:
1. 在 plan_node 中加载 contract 后，调用 validate_preconditions(contract, "RUNNING") 检查前置条件
2. 如果 preconditions 检查失败，返回 error + final_status="failed"，附上具体哪些 preconditions 不满足
3. 在返回的 conversation 中记录 precondition 失败事件，包含 timestamp
4. 不影响现有的重试逻辑——precondition 失败不应消耗 retry_budget

严格评审标准:
- 必须有对应的单元测试: test_graph.py 中新增 TestPlanNodePreconditions 类
- 测试覆盖: precondition 通过、precondition 失败、无 precondition 的 contract
- 确认 precondition 失败不进入 build 节点（通过 mock interrupt 验证 interrupt 未被调用）
- 运行 pytest tests/ -v 全量测试必须通过，无回归

验证步骤:
1. grep validate_preconditions src/multi_agent/graph.py 确认已集成
2. python -m pytest tests/test_graph.py -v -k precondition 确认新测试通过
3. python -m pytest tests/ -v 确认全量通过
```

### 2. graph.py — build_node 超时精确计算修复

```
修复 src/multi_agent/graph.py build_node 中的超时计算问题。

当前问题:
build_node 使用 time.time() - started_at 计算 elapsed，但 started_at 是在 plan_node 中设置的。
如果用户在 plan_node 之后等了很久才开始操作 IDE，elapsed 会包含用户思考时间，导致误报超时。

实现要求:
1. 在 WorkflowState 中新增 build_started_at 字段（可选 float）
2. build_node 开始时（interrupt 返回后）记录 build_started_at = time.time()
3. 超时计算改为 time.time() - build_started_at（而非 started_at）
4. 如果 build_started_at 为空（兼容旧状态），回退到 started_at
5. 同样在 review_node 中新增 review_started_at，修复同样的问题

严格评审标准:
- 修改 WorkflowState TypedDict，新增 build_started_at 和 review_started_at
- build_node 和 review_node 都需要更新超时逻辑
- 新增测试: 模拟 started_at 很早但 build_started_at 正常的场景，验证不误报超时
- 新增测试: build_started_at 为空时回退到 started_at
- 全量测试 pytest tests/ -v 必须通过

验证步骤:
1. 检查 WorkflowState 新增了 build_started_at 和 review_started_at
2. 检查 build_node 和 review_node 的超时逻辑是否使用新字段
3. 运行 pytest tests/test_graph.py -v 确认新测试通过
4. 运行 pytest tests/ -v 确认无回归
```

### 3. graph.py — decide_node 增加 request_changes 决策支持

```
在 src/multi_agent/graph.py 的 decide_node 中增加对 ReviewDecision.REQUEST_CHANGES 的支持。

当前问题:
schema.py 定义了 ReviewDecision 包含 approve/reject/request_changes 三种值，
但 decide_node 只处理 approve 和 reject，request_changes 被当作 reject 处理。

实现要求:
1. decide_node 区分 reject 和 request_changes:
   - reject: 消耗 retry_budget，可能触发 escalate
   - request_changes: 不消耗 retry_budget，总是重试（类似 soft reject）
2. dashboard 显示区分: reject 显示 "❌ 驳回"，request_changes 显示 "🔧 需修改"
3. conversation 记录中体现 decision 类型

严格评审标准:
- decide_node 必须正确区分三种决策的行为
- request_changes 不应消耗 retry_budget（关键测试点）
- 新增至少 3 个测试:
  a. request_changes 触发重试且不消耗 budget
  b. request_changes 即使 budget 为 0 也继续重试
  c. reject 在 budget 耗尽时 escalate
- dashboard 输出中包含正确的 emoji 和文案
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v -k request_changes
2. 检查 decide_node 中 request_changes 的分支逻辑
3. python -m pytest tests/ -v 全量通过
```

### 4. graph.py — 增加 cancelled 状态支持

```
完善 src/multi_agent/graph.py 中的取消状态处理。

当前问题:
cancel 命令在 cli.py 中只写 task YAML 和释放锁，但不更新 graph state。
如果 graph 在 interrupt 等待中，下次 invoke 可能继续运行而非终止。

实现要求:
1. 在 WorkflowState 中检查 cancelled 标记
2. build_node: interrupt 返回后检查是否已取消（读取 task YAML 状态），如果已取消直接返回 final_status="cancelled"
3. review_node: 同样检查取消标记
4. _route_after_build: cancelled 状态路由到 END
5. 新增辅助函数 _is_cancelled(task_id) -> bool，检查 tasks/{task_id}.yaml 的 status 字段

严格评审标准:
- build_node 和 review_node 都必须在 interrupt 返回后立即检查取消
- cancelled 状态不应消耗 retry_budget
- 新增测试: 模拟取消场景，验证 build_node 返回 cancelled
- 新增测试: 模拟取消场景，验证 review_node 返回 cancelled
- _route_after_build 处理 cancelled 路由到 END
- 全量 pytest tests/ -v 通过

验证步骤:
1. grep _is_cancelled src/multi_agent/graph.py
2. python -m pytest tests/test_graph.py -v -k cancel
3. python -m pytest tests/ -v
```

### 5. schema.py — SubTask 模型增加 priority 和 estimated_time

```
增强 src/multi_agent/schema.py 中的 SubTask 模型。

实现要求:
1. SubTask 新增字段:
   - priority: Priority = Priority.NORMAL（使用已有的 Priority 枚举）
   - estimated_minutes: int = 30（预估完成时间，分钟）
   - acceptance_criteria: list[str] = []（验收标准，比 done_criteria 更具体）
2. DecomposeResult 新增字段:
   - total_estimated_minutes: int = 0（所有子任务预估总时间）
3. 更新 decompose.py 的 DECOMPOSE_PROMPT，在 JSON 模板中加入新字段的说明
4. 更新 meta_graph.py 的 build_sub_task_state，把 acceptance_criteria 合入 done_criteria
5. 更新 meta_graph.py 的 aggregate_results，汇总 estimated vs actual 时间

严格评审标准:
- SubTask Pydantic 模型验证: priority 必须是有效的 Priority 枚举值
- 向后兼容: 不含新字段的旧 JSON 仍然可以解析（字段有默认值）
- 新增测试: test_schema.py 中测试 SubTask 新字段的默认值和自定义值
- 新增测试: test_decompose.py 中测试包含新字段的 JSON 解析
- 新增测试: test_meta_graph.py 中测试 acceptance_criteria 合入 done_criteria
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "from multi_agent.schema import SubTask; print(SubTask(id='test', description='t').priority)"
2. python -m pytest tests/test_schema.py tests/test_decompose.py tests/test_meta_graph.py -v
3. python -m pytest tests/ -v
```

### 6. config.py — 支持 .ma.yaml 项目级配置文件

```
在 src/multi_agent/config.py 中增加项目级配置文件支持。

实现要求:
1. 在项目根目录支持 .ma.yaml 配置文件（可选），格式:
   ```yaml
   workspace_dir: .multi-agent
   default_timeout: 1800
   default_retry_budget: 2
   default_builder: windsurf
   default_reviewer: cursor
   decompose_timeout: 900
   poll_interval: 2.0
   ```
2. 新增 load_project_config() -> dict 函数，读取 .ma.yaml
3. 如果 .ma.yaml 不存在，返回空 dict（全部使用硬编码默认值）
4. 在 cli.py 的 go 命令中，用项目配置覆盖默认参数（CLI 标志优先级最高）

严格评审标准:
- .ma.yaml 不存在时不报错，静默使用默认值
- 新增测试: 有 .ma.yaml 时正确读取所有字段
- 新增测试: .ma.yaml 不存在时返回空 dict
- 新增测试: .ma.yaml 格式错误时发出 warning 但不崩溃
- CLI 标志优先级 > .ma.yaml > 硬编码默认值
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "from multi_agent.config import load_project_config; print(load_project_config())"
2. 创建临时 .ma.yaml 测试读取
3. python -m pytest tests/ -v
```

### 7. workspace.py — 增加 outbox 文件完整性校验

```
在 src/multi_agent/workspace.py 的 read_outbox 函数中增加文件完整性校验。

当前问题:
IDE 可能写入不完整的 JSON（比如写到一半崩溃），read_outbox 只检查 json.load 是否成功，
不检查 JSON 内容是否符合预期 schema。

实现要求:
1. read_outbox 增加可选参数 validate: bool = False
2. 当 validate=True 时:
   - builder 角色: 检查必须有 status 和 summary 字段
   - reviewer 角色: 检查必须有 decision 字段
3. 校验失败返回 None（等同于文件不存在，watcher 下次重试）
4. 新增 validate_outbox_data(role: str, data: dict) -> list[str] 公共函数，
   返回错误列表（空列表 = 校验通过）

严格评审标准:
- 默认 validate=False，不影响现有行为
- 新增测试: builder 缺少 status 字段时校验失败
- 新增测试: reviewer 缺少 decision 字段时校验失败
- 新增测试: 完整数据校验通过
- 新增测试: validate=False 时不做校验（即使数据不完整也返回）
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_workspace.py -v -k validate
2. python -m pytest tests/ -v
```

### 8. watcher.py — OutboxPoller 增加文件锁检测防止读取写入中文件

```
在 src/multi_agent/watcher.py 的 OutboxPoller 中增加防止读取正在写入的文件的机制。

当前问题:
check_once 在检测到文件 mtime 变化后立即读取，但 IDE 可能还在写入中。
当前的 partial write 处理只是 catch JSONDecodeError 然后跳过，
但如果 IDE 写了一个 JSON 头部 {"status":，这个不完整 JSON 会导致 JSONDecodeError。

实现要求:
1. check_once 在读取文件前先等待文件大小稳定（两次 stat 间隔 0.5 秒，文件大小不变才读）
2. 新增 _wait_stable(path: Path, settle_time: float = 0.5) -> bool 方法
3. 如果等待超过 3 秒文件仍在变化，跳过这次检测，下次重试
4. 保持现有的 JSONDecodeError 兜底处理

严格评审标准:
- _wait_stable 方法必须独立可测
- 新增测试: 文件大小稳定时返回 True
- 新增测试: 文件持续增长时返回 False
- 新增测试: 文件不存在时返回 False
- 现有 test_watcher.py 全部测试必须通过（不回归）
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_watcher.py -v
2. python -m pytest tests/ -v
```

### 9. driver.py — spawn_cli_agent 增加 stderr 实时日志

```
在 src/multi_agent/driver.py 的 spawn_cli_agent 中增加 CLI agent stderr 的实时日志记录。

当前问题:
CLI agent 的 stderr 只在失败时截取前 200 字符，正常运行时完全忽略。
用户无法看到 CLI agent 的进度输出。

实现要求:
1. 新增日志文件: .multi-agent/logs/{agent_id}-{role}-{timestamp}.log
2. 将 CLI agent 的 stderr 实时写入日志文件
3. 新增函数 get_latest_log(agent_id: str) -> Path | None 获取最新日志
4. 改用 subprocess.Popen 替代 subprocess.run，以支持实时读取 stderr
5. 保持超时机制: Popen.wait(timeout=timeout_sec)
6. outbox 写入逻辑不变

严格评审标准:
- 日志文件路径格式正确: .multi-agent/logs/{agent_id}-{role}-{timestamp}.log
- 日志目录不存在时自动创建
- 超时时日志文件仍然正确关闭（不泄漏文件句柄）
- 新增测试: spawn_cli_agent 成功时日志文件存在且包含 stderr 内容
- 新增测试: spawn_cli_agent 超时时日志文件存在
- 新增测试: get_latest_log 返回最新日志路径
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_driver.py -v
2. python -m pytest tests/ -v
```

### 10. driver.py — 增加 CLI agent 并发保护

```
在 src/multi_agent/driver.py 中增加 CLI agent 的并发保护机制。

当前问题:
如果同一个 CLI agent 被重复 spawn（比如 watcher 和 _show_waiting 同时触发），
会产生两个进程同时写同一个 outbox 文件，导致数据损坏。

实现要求:
1. 新增模块级 _active_threads: dict[str, threading.Thread] 记录活跃线程
2. spawn_cli_agent 启动前检查是否已有同 role 的活跃线程
3. 如果有活跃线程且仍在运行（thread.is_alive()），跳过 spawn 并返回现有线程
4. 如果活跃线程已结束，替换为新线程
5. 线程安全: 用 threading.Lock 保护 _active_threads 的读写

严格评审标准:
- _active_threads 和 _thread_lock 是模块级变量
- 重复 spawn 返回现有线程而非创建新线程
- 线程结束后可以 spawn 新线程
- 新增测试: 连续两次 spawn 同 role，第二次返回第一次的线程
- 新增测试: 第一个线程结束后，可以 spawn 新线程
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_driver.py -v
2. python -m pytest tests/ -v
```

### 11. graph.py — compile_graph 连接池优化

```
优化 src/multi_agent/graph.py 的 compile_graph 函数的 SQLite 连接管理。

当前问题:
每次调用 compile_graph() 都创建新的 SQLite 连接和 atexit 注册，
如果 CLI 代码多次调用（如 decompose 模式），会积累多个连接。

实现要求:
1. 使用单例模式: 模块级 _compiled_graph 缓存
2. compile_graph() 首次调用时创建连接并缓存，后续调用直接返回缓存
3. 新增 reset_graph() 函数用于测试清理
4. 连接关闭仍通过 atexit 注册，但只注册一次
5. db_path 参数变化时重新创建（用于测试不同数据库）

严格评审标准:
- 第二次调用 compile_graph() 返回同一个对象（用 `is` 判断）
- db_path 不同时创建新实例
- reset_graph() 清理缓存，下次 compile_graph() 创建新实例
- 新增测试: 连续两次 compile_graph() 返回相同对象
- 新增测试: reset_graph() 后返回新对象
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v
2. python -m pytest tests/ -v
```

### 12. schema.py — Task 模型增加 parent_task_id 支持

```
在 src/multi_agent/schema.py 的 Task 模型中增加 parent_task_id 字段，支持任务层级关系。

实现要求:
1. Task 新增字段: parent_task_id: str | None = None
2. SubTask 新增字段: parent_task_id: str | None = None
3. meta_graph.py 的 build_sub_task_state 设置 parent_task_id
4. graph.py 的 plan_node 将 parent_task_id 传入 Task 构造函数
5. WorkflowState 新增 parent_task_id 字段

严格评审标准:
- parent_task_id 为 None 时不影响 task_id 的验证
- parent_task_id 不为 None 时必须匹配 _ID_RE 正则（或改用更宽松的验证）
- 新增测试: Task 创建时 parent_task_id=None
- 新增测试: Task 创建时 parent_task_id="task-parent-01"
- 新增测试: build_sub_task_state 设置了 parent_task_id
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "from multi_agent.schema import Task; t = Task(task_id='task-test01', trace_id='0'*16, skill_id='code-implement', parent_task_id='task-parent-01'); print(t.parent_task_id)"
2. python -m pytest tests/ -v
```

### 13. graph.py — 增加 graph 执行事件钩子系统

```
在 src/multi_agent/graph.py 中增加一个轻量级的事件钩子系统。

实现要求:
1. 新增模块级事件注册表: _hooks: dict[str, list[Callable]] = {}
2. 支持的事件类型:
   - "plan_start": plan_node 开始时触发
   - "build_submit": builder 提交输出后触发
   - "review_submit": reviewer 提交输出后触发
   - "decide_approve": 审批通过时触发
   - "decide_reject": 驳回时触发
   - "task_failed": 任务失败时触发
3. 新增 register_hook(event: str, callback: Callable) 和 _fire(event: str, **kwargs)
4. 在各 node 函数的适当位置调用 _fire
5. hook 回调异常不影响主流程（try/except 包裹）

严格评审标准:
- 事件回调异常被捕获并静默（不影响 graph 执行）
- register_hook 可注册多个回调
- 新增测试: 注册回调后事件触发时回调被调用
- 新增测试: 回调抛异常不影响 node 执行
- 新增测试: 同一事件多个回调按注册顺序执行
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v -k hook
2. python -m pytest tests/ -v
```

### 14. workspace.py — 增加 workspace 磁盘空间检查

```
在 src/multi_agent/workspace.py 中增加磁盘空间检查。

实现要求:
1. 新增函数 check_disk_space(min_mb: int = 100) -> tuple[bool, int]
   返回 (是否足够, 当前可用 MB)
2. ensure_workspace() 调用时检查磁盘空间，不足时发出 warning
3. 不阻塞执行——只是 warning，因为用户可能清理空间后继续
4. 使用 shutil.disk_usage() 获取磁盘信息

严格评审标准:
- check_disk_space 返回正确的 tuple 类型
- 磁盘空间充足时返回 (True, actual_mb)
- 新增测试: mock shutil.disk_usage 测试空间不足场景
- 新增测试: mock shutil.disk_usage 测试空间充足场景
- ensure_workspace 调用 check_disk_space 并在不足时 warning
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "from multi_agent.workspace import check_disk_space; print(check_disk_space())"
2. python -m pytest tests/test_workspace.py -v
3. python -m pytest tests/ -v
```

### 15. cli.py — 增加 ma history 命令查看历史任务

```
在 src/multi_agent/cli.py 中新增 ma history 子命令。

实现要求:
1. 命令格式: ma history [--limit N] [--status STATUS]
2. 扫描 .multi-agent/tasks/ 目录下的所有 .yaml 文件
3. 按时间倒序显示:
   - task_id
   - status (active/approved/failed/cancelled)
   - 创建时间 (从文件 mtime)
4. 支持 --status 过滤: ma history --status failed
5. 支持 --limit 限制数量: ma history --limit 10
6. 无历史时显示友好提示

严格评审标准:
- 命令注册到 main group
- 无 task 时显示 "暂无历史任务记录"
- --status 过滤正确工作
- --limit 截断正确
- 新增测试: 使用 CliRunner 测试 history 命令输出
- 新增测试: 空目录时的输出
- 新增测试: --status 过滤
- 全量 pytest tests/ -v 通过

验证步骤:
1. ma history --help 确认命令存在
2. python -m pytest tests/ -v
```

---

## 二、Decompose 功能增强 (16-30)

### 16. decompose.py — 增加任务复杂度评估

```
在 src/multi_agent/decompose.py 中增加自动任务复杂度评估功能。

实现要求:
1. 新增函数 estimate_complexity(requirement: str) -> str
   基于简单启发式规则返回 "simple" | "medium" | "complex":
   - simple: 需求 < 50 字，无 "和/且/以及" 等连接词
   - complex: 需求 > 200 字，或包含 3 个以上功能动词
   - medium: 其他情况
2. cli.py 的 go 命令中: 如果 estimate_complexity 返回 "complex" 且未指定 --decompose，
   显示建议: "⚠️ 需求较复杂，建议使用 --decompose 模式"
3. 不强制——只是建议

严格评审标准:
- estimate_complexity 对空字符串返回 "simple"
- estimate_complexity 对超长需求返回 "complex"
- 新增测试: 至少 5 种输入的复杂度判断
- cli.py 中仅显示建议，不阻塞执行
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "from multi_agent.decompose import estimate_complexity; print(estimate_complexity('实现完整的用户认证模块包括登录注册密码重置和中间件鉴权'))"
2. python -m pytest tests/ -v
```

### 17. decompose.py — DECOMPOSE_PROMPT 增加英文版本

```
在 src/multi_agent/decompose.py 中增加英文版 decompose prompt 支持。

实现要求:
1. 新增 DECOMPOSE_PROMPT_EN 常量，与 DECOMPOSE_PROMPT 功能相同但全英文
2. write_decompose_prompt 新增参数 lang: str = "zh"
3. lang="en" 时使用英文 prompt，lang="zh" 时使用中文 prompt
4. cli.py go 命令新增 --lang 选项（默认 "zh"）
5. 英文 prompt 中 JSON 字段名不变（保持 sub_tasks/deps 等）

严格评审标准:
- 英文 prompt 必须包含所有中文 prompt 相同的规则和约束
- JSON 示例格式完全一致（只是描述语言不同）
- 新增测试: lang="en" 生成英文 prompt
- 新增测试: lang="zh" 生成中文 prompt
- 新增测试: TASK.md 中 outbox 路径指引也是对应语言
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_decompose.py -v
2. python -m pytest tests/ -v
```

### 18. meta_graph.py — 增加 sub-task 间的产出传递

```
增强 src/multi_agent/meta_graph.py 的 sub-task 间上下文传递机制。

当前问题:
build_sub_task_state 只传递 prior_results 的 summary 和 changed_files，
但不传递具体的代码变更内容或 reviewer 反馈。

实现要求:
1. prior_results 中新增 reviewer_feedback 字段
2. build_sub_task_state 把前序 sub-task 的 reviewer 反馈也写入 requirement 上下文
3. 新增函数 format_prior_context(prior_results: list[dict]) -> str
   将 prior_results 格式化为可读的上下文文本
4. 上下文只包含最近 3 个 sub-task 的信息（避免上下文膨胀）

严格评审标准:
- format_prior_context 独立可测
- 最多包含 3 个 prior results
- prior_results 为空时返回空字符串
- 新增测试: format_prior_context 包含 reviewer_feedback
- 新增测试: 超过 3 个 prior results 时只保留最近 3 个
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_meta_graph.py -v
2. python -m pytest tests/ -v
```

### 19. decompose.py — topo_sort 增加并行组检测

```
增强 src/multi_agent/decompose.py 的 topo_sort 函数，返回可并行执行的任务组。

实现要求:
1. 新增函数 topo_sort_grouped(sub_tasks: list[SubTask]) -> list[list[SubTask]]
   返回分组列表，每组内的任务可以并行执行（无互相依赖）
   例如: [[A, B], [C], [D]] 表示 A 和 B 可并行，C 依赖 A 或 B，D 依赖 C
2. 原有 topo_sort 保持不变（兼容）
3. cli.py 的 _run_decomposed 中使用 topo_sort_grouped 显示并行信息:
   "组 1 (可并行): A, B"
   "组 2 (依赖组1): C"

严格评审标准:
- topo_sort_grouped 返回正确的分组
- 无依赖的任务全部在第一组
- 循环依赖检测仍然有效（抛 ValueError）
- 新增测试: 全部独立任务 → 一组
- 新增测试: 线性依赖 → 每组一个
- 新增测试: 菱形依赖 → 正确分组
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_decompose.py -v -k grouped
2. python -m pytest tests/ -v
```

### 20. decompose.py — read_decompose_result 增加 schema 验证

```
增强 src/multi_agent/decompose.py 的 read_decompose_result 函数的验证。

当前问题:
agent 可能返回格式正确但内容无意义的分解结果，比如:
- sub_task id 重复
- description 为空
- deps 引用不存在的 id

实现要求:
1. 新增函数 validate_decompose_result(result: DecomposeResult) -> list[str]
   返回验证错误列表（空 = 通过）
2. 检查项:
   - sub_task id 不重复
   - sub_task id 不为空
   - description 不为空
   - deps 引用的 id 必须存在于 sub_tasks 中
   - sub_tasks 数量在 1-10 之间
3. read_decompose_result 增加 validate=True 参数，校验失败返回 None
4. _run_decomposed 中显示具体的校验错误

严格评审标准:
- 每个检查项都有对应的单元测试
- 新增测试: id 重复
- 新增测试: description 为空
- 新增测试: deps 引用不存在 id
- 新增测试: 超过 10 个 sub_tasks
- 新增测试: 合法结果通过校验
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_decompose.py -v -k validate
2. python -m pytest tests/ -v
```

### 21. cli.py — _run_decomposed 增加 sub-task 失败后用户选择

```
在 src/multi_agent/cli.py 的 _run_decomposed 中，sub-task 失败后给用户选择。

当前行为:
依赖失败的 sub-task 被自动跳过。

改进:
1. sub-task 失败后（非依赖跳过），暂停并提示用户选择:
   a. 跳过并继续后续 sub-task
   b. 重试当前 sub-task
   c. 终止整个 decompose 流程
2. 使用 click.prompt 或 click.confirm 获取用户输入
3. 选择重试时: 重新调用 app.invoke + _run_watch_loop
4. 选择终止时: 保存已完成的结果并退出
5. 如果是 CLI 全自动模式（所有 agent 都是 cli driver），默认跳过

严格评审标准:
- 用户选择逻辑不影响自动跳过依赖失败的行为
- 重试时正确清理 runtime
- 终止时保存部分结果
- 新增测试: mock click.prompt 验证三种选择的行为
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 _run_decomposed 中的失败处理分支
2. python -m pytest tests/ -v
```

### 22. meta_graph.py — generate_sub_task_id 改用可读 ID

```
改进 src/multi_agent/meta_graph.py 的 generate_sub_task_id 函数生成更可读的 ID。

当前问题:
生成的 ID 是 task-{hash}，如 "task-a1b2c3"，不可读。

实现要求:
1. 新的 ID 格式: task-{parent_short}-{sub_id_short}
   例如: parent="task-auth-impl" sub_id="login" → "task-auth-login"
2. 确保总长度不超过 64 字符（_ID_RE 限制）
3. 确保 ID 只包含小写字母、数字、连字符（_ID_RE 要求）
4. 特殊字符替换: 空格→连字符, 大写→小写, 其他移除
5. 如果结果不匹配 _ID_RE，回退到 hash 方式

严格评审标准:
- 生成的 ID 必须通过 _ID_RE 验证
- 新增测试: 正常输入生成可读 ID
- 新增测试: 包含特殊字符时正确清理
- 新增测试: 超长输入时截断
- 新增测试: 回退到 hash 方式
- 确定性: 相同输入总是生成相同 ID
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "from multi_agent.meta_graph import generate_sub_task_id; print(generate_sub_task_id('task-auth-impl', 'login'))"
2. python -m pytest tests/test_meta_graph.py -v
3. python -m pytest tests/ -v
```

### 23-30. (Decompose 续)

### 23. decompose.py — 增加 decompose 结果缓存

```
在 src/multi_agent/decompose.py 中增加分解结果缓存，避免重复分解。

实现要求:
1. 分解结果缓存到 .multi-agent/cache/decompose-{hash}.json
   hash 基于 requirement 文本的 SHA-256
2. 新增函数 get_cached_decompose(requirement: str) -> DecomposeResult | None
3. 新增函数 cache_decompose(requirement: str, result: DecomposeResult) -> Path
4. _run_decomposed 启动时先检查缓存，命中缓存时直接使用（跳过等待 agent 分解）
5. 显示提示: "💾 使用缓存的分解结果 (原始需求相同)"
6. 新增 --no-cache 标志强制重新分解

严格评审标准:
- 缓存命中时不调用 write_decompose_prompt
- 缓存目录不存在时自动创建
- 新增测试: 缓存写入和读取
- 新增测试: 缓存 hash 对不同需求不同
- 新增测试: --no-cache 跳过缓存
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 24. cli.py — _run_decomposed 增加进度百分比显示

```
在 src/multi_agent/cli.py 的 _run_decomposed 中显示整体进度。

实现要求:
1. 每个 sub-task 开始时显示: "[2/5] 📦 auth-login (40% 完成)"
2. 每个 sub-task 完成时显示: "[2/5] ✅ auth-login 完成 (60%)"
3. 跳过的 sub-task 显示: "[3/5] ⏭️ auth-reset 跳过 (80%)"
4. 最终汇总时显示总耗时: "⏱️ 总耗时: 12 分 34 秒"
5. 进度计算: (已完成 + 已跳过) / 总数 * 100

严格评审标准:
- 进度百分比计算正确
- 跳过的 sub-task 也计入进度
- 总耗时格式: "X 分 Y 秒"（不足 1 分钟只显示秒）
- 检查所有 click.echo 输出包含进度信息
- 全量 pytest tests/ -v 通过

验证步骤:
1. 审查 _run_decomposed 中的 click.echo 调用
2. python -m pytest tests/ -v
```

### 25. decompose.py — DECOMPOSE_PROMPT 增加项目上下文注入

```
增强 src/multi_agent/decompose.py 的 DECOMPOSE_PROMPT，注入项目上下文信息。

实现要求:
1. write_decompose_prompt 新增可选参数 project_context: str = ""
2. 如果不传，自动收集项目上下文:
   - 扫描 src/ 目录获取 Python 文件列表（前 20 个）
   - 读取 README.md 前 50 行
   - 读取 pyproject.toml 的 dependencies
3. 将上下文注入 prompt 的 "## 项目背景" 章节
4. 帮助 agent 更好地理解项目结构来分解任务

严格评审标准:
- 项目上下文收集不应该抛异常（文件不存在时跳过）
- 上下文总大小限制在 2000 字符内（截断）
- 新增测试: 有项目文件时上下文包含文件列表
- 新增测试: 空项目时上下文为空
- prompt 中包含 "## 项目背景" 章节
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_decompose.py -v
2. python -m pytest tests/ -v
```

### 26. meta_graph.py — aggregate_results 生成 Markdown 报告

```
增强 src/multi_agent/meta_graph.py 的 aggregate_results，生成详细 Markdown 报告。

实现要求:
1. 新增函数 generate_aggregate_report(agg: dict) -> str
   返回格式化的 Markdown 报告:
   ```
   # 任务分解执行报告
   ## 概要
   - 总子任务: 5
   - 完成: 4
   - 失败: 1
   - 总重试: 2

   ## 详情
   | # | 子任务 | 状态 | 重试 | 摘要 |
   |---|--------|------|------|------|
   | 1 | auth-login | ✅ 通过 | 0 | 实现登录 |
   ...

   ## 修改文件
   - /src/auth/login.py
   ...
   ```
2. _run_decomposed 完成后将报告写入 .multi-agent/report-{task_id}.md
3. 终端也显示报告路径

严格评审标准:
- 报告格式正确的 Markdown（表格对齐）
- 状态用 emoji 标识: ✅ 通过 / ❌ 失败 / ⏭️ 跳过
- 新增测试: generate_aggregate_report 输出包含所有 sub-task
- 新增测试: 空结果时报告仍然有效
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_meta_graph.py -v
2. python -m pytest tests/ -v
```

### 27-30. 更多 Decompose 增强

### 27. schema.py — DecomposeResult 增加 version 和 metadata

```
增强 src/multi_agent/schema.py 的 DecomposeResult 模型。

实现要求:
1. DecomposeResult 新增字段:
   - version: str = "1.0"（分解结果的 schema 版本）
   - metadata: dict[str, Any] = {}（附加元数据，如分解用时、使用的 agent）
   - created_at: str = Field(default_factory=_now_utc)（创建时间）
2. cache_decompose 时自动填充 metadata.agent 和 metadata.duration
3. 向后兼容: 没有新字段的旧 JSON 可以正常解析

严格评审标准:
- 所有新字段都有默认值
- 旧格式 JSON 解析不报错
- 新增测试: 新字段存在时正确解析
- 新增测试: 新字段缺失时使用默认值
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_schema.py -v
2. python -m pytest tests/ -v
```

### 28. decompose.py — 增加 decompose 结果的人工确认步骤

```
在 src/multi_agent/cli.py 的 _run_decomposed 中增加人工确认步骤。

实现要求:
1. 分解完成后显示所有子任务列表
2. 使用 click.confirm 询问: "确认执行这些子任务？[Y/n]"
3. 用户输入 n 时:
   - 提示用户可以修改 .multi-agent/outbox/decompose.json
   - 等待用户重新确认
4. 新增 --auto-confirm 标志跳过确认（用于全自动夜间运行）
5. 默认需要确认（安全第一）

严格评审标准:
- 默认行为是等待确认
- --auto-confirm 跳过确认
- 用户拒绝后可以修改文件重新确认
- 新增测试: mock click.confirm 测试确认和拒绝流程
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 --auto-confirm 标志注册
2. python -m pytest tests/ -v
```

### 29. decompose.py — 支持从文件读取分解结果

```
增加从外部文件读取分解结果的功能。

实现要求:
1. cli.py go 命令新增 --decompose-file PATH 选项
2. 指定时直接读取文件内容作为分解结果（跳过 agent 分解步骤）
3. 文件格式: 标准的 DecomposeResult JSON
4. 读取失败时清晰的错误提示
5. 支持 JSON 和 YAML 格式

严格评审标准:
- --decompose-file 和 --decompose 可以组合使用
- 文件不存在时清晰报错
- JSON 解析失败时清晰报错
- 新增测试: 从 JSON 文件读取
- 新增测试: 文件不存在时的错误
- 全量 pytest tests/ -v 通过

验证步骤:
1. 创建测试用 JSON 文件，验证读取
2. python -m pytest tests/ -v
```

### 30. meta_graph.py — 增加 sub-task 执行时间统计

```
在 src/multi_agent/meta_graph.py 中增加每个 sub-task 的执行时间统计。

实现要求:
1. _run_decomposed 中记录每个 sub-task 的开始和结束时间
2. prior_results 中新增 duration_sec 字段
3. aggregate_results 计算:
   - total_duration_sec: 总执行时间
   - avg_duration_sec: 平均每个 sub-task 时间
   - slowest_sub_task: 最慢的 sub-task ID 和时间
4. 最终报告中显示这些统计

严格评审标准:
- duration_sec 计算正确（结束时间 - 开始时间）
- 跳过的 sub-task duration 为 0
- 新增测试: aggregate_results 正确计算时间统计
- 新增测试: 空结果时统计为 0
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_meta_graph.py -v
2. python -m pytest tests/ -v
```

---

## 三、测试覆盖强化 (31-50)

### 31. test_graph.py — plan_node 全面测试

```
为 src/multi_agent/graph.py 的 plan_node 函数补充全面测试。

实现要求:
在 tests/test_graph.py 中新增 TestPlanNode 测试类，覆盖以下场景:
1. 首次运行: 正确解析 builder 和 reviewer
2. 重试运行: 使用已有的 builder_id 和 reviewer_id（不重新解析）
3. 明确指定 --builder 和 --reviewer 时优先使用
4. skill contract 不存在时抛 FileNotFoundError
5. 无 agent 配置时抛 ValueError
6. builder == reviewer 时抛 ValueError
7. conversation 包含正确的 timestamp
8. TASK.md 被正确写入
9. inbox/builder.md 被正确写入
10. dashboard.md 被正确写入

严格评审标准:
- 每个测试用例独立（不依赖其他测试的状态）
- 使用 mock 隔离外部依赖（文件系统、load_agents 等）
- 至少 10 个测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v -k PlanNode
2. python -m pytest tests/ -v
```

### 32. test_graph.py — build_node 全面测试

```
为 src/multi_agent/graph.py 的 build_node 函数补充全面测试。

实现要求:
在 tests/test_graph.py 中新增 TestBuildNode 测试类，覆盖以下场景:
1. 正常提交: 完整 JSON 通过验证，转入 reviewer
2. 超时: elapsed > timeout_sec 时返回 failed
3. 无效 JSON: 不是 dict 时返回 failed
4. 缺少 status 字段时返回 failed
5. 缺少 summary 字段时返回 failed
6. CLI error (status=error): 返回 failed
7. quality gate 通过: 无 gate_warnings
8. quality gate 失败: 有 gate_warnings 但不阻塞
9. reviewer prompt 正确生成
10. outbox/reviewer.json 被清空（准备新的 review）

严格评审标准:
- 所有 interrupt 调用都被 mock
- 每个 error 分支都有对应测试
- conversation 中有正确的 timestamp
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v -k BuildNode
2. python -m pytest tests/ -v
```

### 33. test_graph.py — review_node 全面测试

```
为 src/multi_agent/graph.py 的 review_node 函数补充全面测试。

实现要求:
在 tests/test_graph.py 中新增 TestReviewNode 测试类，覆盖:
1. approve 决策: decision=approve
2. reject 决策: decision=reject
3. request_changes 决策: decision=request_changes
4. 无效输出: 不是 dict 时 auto-reject
5. CLI error: status=error 时 auto-reject with feedback
6. 缺少 decision 字段: 默认为 reject
7. decision 值不在枚举中: 使用原始值
8. conversation 包含正确的 decision 和 timestamp

严格评审标准:
- 每个测试都 mock interrupt
- 至少 8 个测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v -k ReviewNode
2. python -m pytest tests/ -v
```

### 34. test_cli.py — go 命令参数组合测试

```
新建 tests/test_cli_go.py，对 ma go 命令的所有参数组合进行测试。

实现要求:
使用 CliRunner 测试以下场景:
1. 基本调用: ma go "requirement"
2. 指定 builder/reviewer: --builder windsurf --reviewer cursor
3. 指定 skill: --skill test-and-review
4. 指定 task-id: --task-id task-custom-01
5. --no-watch 标志
6. --decompose 标志
7. --retry-budget 3
8. --timeout 900
9. 已有活跃任务时报错
10. skill 不存在时报错
11. agent 不存在时报错（agents.yaml 为空）
12. builder == reviewer 时报错

严格评审标准:
- 每个参数组合一个测试方法
- 使用 mock 避免真正执行 graph
- 错误场景检查 exit code 和 error message
- 至少 12 个测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_cli_go.py -v
2. python -m pytest tests/ -v
```

### 35. test_cli.py — done 命令测试

```
新建 tests/test_cli_done.py，对 ma done 命令进行全面测试。

实现要求:
使用 CliRunner 测试:
1. 无活跃任务时报错
2. 自动检测 outbox 文件并提交
3. --file 指定文件
4. 文件不存在时报错
5. 文件内容不是有效 JSON 时报错
6. stdin 输入 JSON
7. 成功提交后显示进度

严格评审标准:
- mock compile_graph 避免数据库操作
- 至少 7 个测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_cli_done.py -v
2. python -m pytest tests/ -v
```

### 36. test_cli.py — cancel 命令测试

```
新建 tests/test_cli_cancel.py，对 ma cancel 命令进行测试。

实现要求:
使用 CliRunner 测试:
1. 无活跃任务时: "No active task to cancel."
2. 有活跃任务: 正确取消，释放锁，清理 runtime
3. 孤立锁检测: 有锁但无 graph state 时仍能取消
4. --task-id 指定任务 ID
5. --reason 自定义原因
6. 取消后 task YAML 状态为 cancelled
7. 取消后锁文件不存在

严格评审标准:
- mock 隔离文件系统操作
- 至少 7 个测试方法
- 检查 release_lock 和 clear_runtime 被调用
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_cli_cancel.py -v
2. python -m pytest tests/ -v
```

### 37. test_cli.py — status 命令测试

```
新建 tests/test_cli_status.py，对 ma status 命令进行测试。

实现要求:
使用 CliRunner 测试:
1. 无活跃任务时: "No active tasks."
2. builder 等待中: 显示 builder agent 和 waiting 状态
3. reviewer 等待中: 显示 reviewer agent 和 waiting 状态
4. 有错误时显示错误信息
5. 已完成时显示 final_status
6. 重试计数显示正确
7. 锁状态显示正确

严格评审标准:
- mock graph state 模拟各种状态
- 至少 7 个测试方法
- 输出包含正确的 emoji 和格式
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_cli_status.py -v
2. python -m pytest tests/ -v
```

### 38. test_cli.py — watch 命令测试

```
新建 tests/test_cli_watch.py，对 ma watch 命令进行测试。

实现要求:
使用 CliRunner 测试:
1. 无活跃任务时报错
2. 锁不一致时报错
3. 检测到 outbox 文件时自动提交
4. --interval 参数传递正确
5. Ctrl-C 中断后提示恢复命令

严格评审标准:
- mock _run_watch_loop 避免真正轮询
- 至少 5 个测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_cli_watch.py -v
2. python -m pytest tests/ -v
```

### 39. test_config.py — 配置模块测试

```
新建 tests/test_config.py，对 src/multi_agent/config.py 进行全面测试。

实现要求:
1. test_find_root_with_env: MA_ROOT 环境变量生效
2. test_find_root_cwd: 从当前目录向上查找
3. test_find_root_fallback: 找不到时回退到 CWD 并 warning
4. test_all_path_functions: workspace_dir, skills_dir, inbox_dir 等路径正确
5. test_load_yaml: 正常 YAML 文件读取
6. test_load_yaml_empty: 空文件返回 {}
7. test_root_dir_cached: lru_cache 生效

严格评审标准:
- 使用 monkeypatch 设置和清除环境变量
- 每个路径函数都有测试
- lru_cache 测试需要调用 cache_clear()
- 至少 7 个测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_config.py -v
2. python -m pytest tests/ -v
```

### 40-50. 更多测试

### 40. test_prompt.py — 模板渲染边界测试

```
增强 tests/test_prompt.py 的测试覆盖。

实现要求:
新增以下测试:
1. 空 done_criteria 时 prompt 不包含 "完成标准" 章节
2. input_payload 为 None 时不报错
3. 超长 requirement (>5000 字符) 时 prompt 正确渲染
4. 特殊字符 (引号、反引号、HTML 标签) 不被转义
5. retry_count=0 时不包含 "重试" 章节
6. retry_feedback 包含 markdown 格式时正确嵌入
7. quality_gates 为空时不包含 "质量门禁" 章节
8. reviewer prompt 包含 builder 的所有输出字段
9. reviewer prompt 包含 builder_id
10. 两个模板都不包含 Jinja2 未渲染的 {{ }} 标记

严格评审标准:
- 每个测试点一个方法
- 至少 10 个新测试方法
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 41. test_router.py — 角色解析边界测试

```
增强 tests/test_router.py 的测试覆盖。

新增测试:
1. 只有 1 个 agent 时: builder 可以分配，reviewer 报错
2. 3+ agent 时: 按 reliability 排序选择
3. defaults 中 builder 和 reviewer 相同时: 忽略 reviewer default
4. contract.supported_agents 限制时: 只从限制列表中选
5. capabilities 不匹配时: 跳过不匹配的 agent
6. 所有 agent 都在 exclude 中: 报错
7. load_registry 返回 v1 格式时兼容

严格评审标准:
- 至少 7 个新测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_router.py -v
2. python -m pytest tests/ -v
```

### 42. test_dashboard.py — dashboard 渲染测试

```
增强 tests/test_dashboard.py 的测试覆盖。

新增测试:
1. 空 conversation 时表格只有 header
2. 多个 conversation 条目按顺序渲染
3. error 状态显示红色标记
4. timeout_remaining 显示正确
5. done_criteria 包含 markdown 特殊字符时转义
6. status_msg 为空时显示默认 emoji 格式
7. write_dashboard 创建父目录
8. write_dashboard 自定义 path

严格评审标准:
- 至少 8 个新测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_dashboard.py -v
2. python -m pytest tests/ -v
```

### 43. test_watcher.py — watcher 边界测试

```
增强 tests/test_watcher.py 的 OutboxPoller 测试。

新增测试:
1. 并发写入: 文件在 check_once 读取中被修改
2. 文件权限错误: 不可读时静默跳过
3. 符号链接: outbox 中有符号链接时正确处理
4. 非 JSON 文件: outbox 中有 .txt 文件时忽略
5. watch 方法: stop_after=1 时检测一个文件后停止
6. watch 方法: callback 异常时继续运行
7. 空 JSON 文件: {} 时返回空 dict
8. 大文件: 1MB JSON 时正确解析

严格评审标准:
- 至少 8 个新测试
- 使用 tmp_path 创建测试文件
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_watcher.py -v
2. python -m pytest tests/ -v
```

### 44. test_driver.py — driver 边界测试

```
增强 tests/test_driver.py 的测试覆盖。

新增测试:
1. get_agent_driver 未知 agent: 返回 file 模式
2. get_agent_driver 已知 agent: 返回正确的 driver 和 command
3. _try_extract_json: JSON 被 markdown 包裹
4. _try_extract_json: 无 JSON 内容
5. _try_extract_json: 多个 JSON 块（取第一个）
6. _write_error: 目录不存在时创建
7. spawn_cli_agent: command_template 包含 {task_file} 和 {outbox_file} 占位符
8. can_use_cli: 路径中的二进制文件

严格评审标准:
- 至少 8 个新测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_driver.py -v
2. python -m pytest tests/ -v
```

### 45. test_schema.py — schema 验证测试

```
增强 tests/test_schema.py 的 Pydantic 模型验证测试。

新增测试:
1. Task.task_id 不匹配 _ID_RE 时报错
2. Task.trace_id 不匹配 _TRACE_RE 时报错
3. Task.task_id 边界长度: 3 字符和 64 字符
4. SkillContract.from_yaml 处理 supported_agents
5. SkillContract.from_yaml 无 compatibility 字段
6. BuilderOutput 允许额外字段
7. ReviewerOutput.decision 枚举验证
8. SubTask 默认值
9. DecomposeResult 空 sub_tasks
10. Priority 枚举所有值

严格评审标准:
- 每个模型至少 2 个新测试
- 至少 10 个新测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_schema.py -v
2. python -m pytest tests/ -v
```

### 46-50. 集成测试

### 46. test_integration.py — 端到端 graph 集成测试

```
新建 tests/test_integration.py，测试完整的 4-node graph 流程。

实现要求:
使用内存 SQLite 创建完整 graph，模拟完整流程:
1. test_approve_flow: plan → build → review(approve) → END
2. test_reject_retry_flow: plan → build → review(reject) → plan → build → review(approve)
3. test_budget_exhausted: 多次 reject 直到预算耗尽 → escalated
4. test_build_error: builder 返回 status=error → 直接 END
5. test_timeout: builder 超时 → 直接 END

严格评审标准:
- 使用 mock interrupt 模拟 agent 输出
- 完整流经所有 4 个 node
- 验证最终 state 的 final_status
- 验证 conversation 包含所有事件
- 至少 5 个测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_integration.py -v
2. python -m pytest tests/ -v
```

### 47. test_integration.py — decompose 端到端集成测试

```
在 tests/test_integration.py 中新增 decompose 流程的端到端测试。

实现要求:
1. test_decompose_two_tasks: 2 个无依赖 sub-task 顺序执行
2. test_decompose_with_deps: 有依赖关系的 sub-task 按序执行
3. test_decompose_dep_failure: 依赖失败时跳过后续 sub-task
4. test_decompose_all_approve: 全部通过时汇总 approved

严格评审标准:
- mock interrupt 和 compile_graph
- 验证 aggregate_results 的正确性
- 至少 4 个测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_integration.py -v -k decompose
2. python -m pytest tests/ -v
```

### 48. test_contract.py — contract 加载边界测试

```
增强 tests/test_contract.py 的测试覆盖。

新增测试:
1. load_contract 正常加载
2. load_contract 文件不存在时报错
3. load_contract YAML 格式错误
4. list_skills 返回正确的 skill 列表
5. list_skills 空目录
6. list_skills 有子目录但无 contract.yaml
7. validate_preconditions 多个 precondition
8. validate_preconditions 空 preconditions

严格评审标准:
- 至少 8 个测试
- 使用 tmp_path 创建测试目录结构
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_contract.py -v
2. python -m pytest tests/ -v
```

### 49. test_workspace.py — workspace 边界测试

```
增强 tests/test_workspace.py 的测试覆盖。

新增测试:
1. write_outbox 包含 unicode 内容
2. read_outbox 文件损坏（非 JSON）
3. save_task_yaml 包含嵌套 dict
4. archive_conversation 空列表
5. archive_conversation 大量条目 (100+)
6. ensure_workspace 已存在时幂等
7. clear_runtime 部分文件不存在时不报错
8. _lock_path 返回正确路径

严格评审标准:
- 至少 8 个新测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_workspace.py -v
2. python -m pytest tests/ -v
```

### 50. 测试 README 中的代码示例

```
新建 tests/test_readme_examples.py，验证 README.md 中的代码示例可运行。

实现要求:
1. 提取 README.md 中的所有 bash 和 python 代码块
2. 验证 ma --help 可运行
3. 验证 ma go --help 包含所有选项
4. 验证版本号一致: __version__ == pyproject.toml version
5. 验证 pytest tests/ -v 的测试数量与 README badge 一致

严格评审标准:
- 至少 5 个测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_readme_examples.py -v
2. python -m pytest tests/ -v
```

---

## 四、模板与 Prompt 工程 (51-60)

### 51. builder.md.j2 — 增加结构化输出指引

```
改进 src/multi_agent/templates/builder.md.j2 模板。

实现要求:
1. 在 prompt 末尾增加明确的 JSON 输出格式说明
2. 增加每个字段的详细解释:
   - status: "completed" | "blocked"（明确什么情况用 blocked）
   - summary: 用一句话概括做了什么（不超过 100 字）
   - changed_files: 列出所有修改过的文件路径
   - check_results: 每个 quality_gate 的检查结果
   - risks: 潜在风险列表
3. 增加示例输出（一个好的示例 + 一个差的示例对比）
4. 增加 "常见错误" 章节，提醒 agent 避免常见问题

严格评审标准:
- 模板渲染后包含 JSON 格式说明
- 示例输出是有效的 JSON
- 新增测试: 渲染后的 prompt 包含 "status" 和 "summary" 字段说明
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 52. reviewer.md.j2 — 增加评审检查清单

```
改进 src/multi_agent/templates/reviewer.md.j2 模板。

实现要求:
1. 增加详细的评审检查清单:
   - [ ] 代码是否实现了所有 done_criteria
   - [ ] 是否有未处理的错误路径
   - [ ] 是否有安全风险
   - [ ] 代码风格是否一致
   - [ ] 是否有遗漏的测试
2. 增加 "严格评审" 指引: 如果有任何一项不满足，必须 reject
3. 增加 feedback 的写作指引: 反馈必须具体、可操作、指向具体代码
4. 增加 decision 的判断标准:
   - approve: 所有检查项通过
   - request_changes: 小问题，修改后可通过
   - reject: 严重问题，需要重新实现

严格评审标准:
- 模板渲染后包含检查清单
- 新增测试: reviewer prompt 包含 "检查清单"
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 53-60. 更多 Prompt 优化

### 53. 新增 test-and-review skill 的模板

```
为 test-and-review skill 创建专用的 builder 和 reviewer 模板。

实现要求:
1. 新建 src/multi_agent/templates/test-builder.md.j2
   专注于测试编写: 包含测试策略、覆盖要求、测试命名规范
2. 新建 src/multi_agent/templates/test-reviewer.md.j2
   专注于测试评审: 检查覆盖率、边界条件、mock 正确性
3. prompt.py 根据 skill_id 选择不同的模板
4. 回退: 如果专用模板不存在，使用通用 builder.md.j2

严格评审标准:
- 模板选择逻辑正确
- 回退逻辑正确
- 新增测试: skill_id="test-and-review" 时使用专用模板
- 新增测试: 专用模板不存在时回退到通用模板
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 54. prompt.py — 增加 prompt 长度控制

```
在 src/multi_agent/prompt.py 中增加 prompt 长度控制机制。

实现要求:
1. 新增常量 MAX_PROMPT_CHARS = 50000
2. render_builder_prompt 和 render_reviewer_prompt 返回前检查长度
3. 超长时自动截断 input_payload 和 retry_feedback（保留核心指令）
4. 截断时在末尾添加: "(内容已截断，完整内容见 .multi-agent/inbox/builder.md)"
5. 日志 warning: "Prompt truncated from {original} to {MAX_PROMPT_CHARS} chars"

严格评审标准:
- 截断不破坏 prompt 的 JSON 输出格式说明
- 核心指令（任务要求、done_criteria）永远不被截断
- 新增测试: 正常长度 prompt 不截断
- 新增测试: 超长 prompt 正确截断
- 新增测试: 截断后仍包含核心指令
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 55. 新增 task-decompose skill 的模板

```
为 task-decompose skill 创建专用模板。

实现要求:
1. 新建 src/multi_agent/templates/decompose-builder.md.j2
   包含: 任务分解原则、JSON 格式、示例
2. 集成到 decompose.py: 使用 Jinja2 模板替代硬编码的 DECOMPOSE_PROMPT
3. 模板支持变量: requirement, project_context, max_sub_tasks
4. 保留 DECOMPOSE_PROMPT 作为回退（模板不存在时）

严格评审标准:
- Jinja2 模板渲染正确
- 回退逻辑正确
- 新增测试: 使用模板渲染
- 新增测试: 模板不存在时回退到硬编码
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_decompose.py -v
2. python -m pytest tests/ -v
```

### 56. prompt.py — 增加 prompt 版本追踪

```
在 prompt 中嵌入版本信息，便于调试。

实现要求:
1. 每个渲染的 prompt 在末尾添加 HTML 注释:
   <!-- AgentOrchestra v{version} | prompt: builder/reviewer | rendered: {timestamp} -->
2. 不影响 IDE AI 的解析（HTML 注释在 markdown 中不可见）
3. 新增函数 get_prompt_metadata() -> str

严格评审标准:
- 版本号从 __version__ 获取
- timestamp 使用 UTC
- 新增测试: 渲染后包含版本注释
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 57-60. Prompt 优化续

### 57. builder.md.j2 — 增加 retry 上下文增强

```
改进 builder.md.j2 模板中的重试指引。

实现要求:
1. retry_count > 0 时，在 prompt 开头显示醒目的重试标记:
   "⚠️ 这是第 {retry_count} 次重试 (共 {retry_budget} 次机会)"
2. 包含 reviewer 的完整反馈（不截断）
3. 包含上一次提交的 summary（让 builder 知道上次做了什么）
4. 增加 "重试策略" 指引: "只修改 reviewer 指出的问题，不要重新实现"

严格评审标准:
- retry_count=0 时不显示重试相关内容
- retry_count>0 时显示完整反馈
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 58. reviewer.md.j2 — 增加 builder 输出的结构化展示

```
改进 reviewer.md.j2 中 builder 输出的展示方式。

实现要求:
1. builder_output 展示为结构化的 markdown 表格
2. changed_files 以列表形式展示
3. check_results 以表格形式展示（gate | result）
4. risks 以列表形式展示（如果有）
5. gate_warnings 以醒目格式展示

严格评审标准:
- 所有 builder_output 字段都有展示
- 字段为空时不崩溃
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_prompt.py -v
2. python -m pytest tests/ -v
```

### 59. 增加 outbox JSON 格式的示例文件

```
在 .multi-agent/ 目录下创建示例 outbox JSON 文件。

实现要求:
1. 创建 docs/examples/builder-output-example.json
2. 创建 docs/examples/reviewer-output-approve.json
3. 创建 docs/examples/reviewer-output-reject.json
4. 创建 docs/examples/decompose-result-example.json
5. 每个示例文件包含注释说明（JSON5 格式或单独的 .md 说明）
6. TASK.md 模板中引用这些示例: "输出格式参考: docs/examples/"

严格评审标准:
- 每个示例都是有效的 JSON（可以用 json.loads 解析）
- 示例内容现实且有教育意义
- 新增测试: 验证所有示例文件是有效 JSON
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -c "import json; json.load(open('docs/examples/builder-output-example.json'))"
2. python -m pytest tests/ -v
```

### 60. 增加 prompt 渲染的干运行命令

```
在 cli.py 中新增 ma render 命令，用于预览 prompt 而不执行。

实现要求:
1. 命令格式: ma render "requirement" [--skill SKILL] [--role builder|reviewer]
2. 渲染 prompt 并输出到 stdout（不写入 inbox，不启动 graph）
3. 用于调试和优化 prompt
4. 支持 --role reviewer 时需要提供 --builder-output FILE

严格评审标准:
- 不修改任何文件（纯只读操作）
- 输出格式清晰可读
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. ma render --help
2. python -m pytest tests/ -v
```

---

## 五、健壮性与错误处理 (61-75)

### 61-75. 错误处理增强

### 61. cli.py — 所有命令增加统一异常处理装饰器

```
在 src/multi_agent/cli.py 中增加统一的异常处理装饰器。

实现要求:
1. 新增装饰器 @handle_errors，捕获所有未预期的异常
2. 显示用户友好的错误信息（不显示 traceback）
3. 严重错误时释放锁和清理 runtime
4. 支持 --verbose 全局标志显示完整 traceback
5. 记录错误到 .multi-agent/logs/error-{timestamp}.log

严格评审标准:
- 所有命令都使用 @handle_errors
- 锁和 runtime 正确清理
- --verbose 显示 traceback
- 新增测试: 模拟异常验证错误处理
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 62. graph.py — 所有 node 增加异常兜底

```
在所有 graph node 函数中增加顶层 try/except 兜底。

实现要求:
1. plan_node, build_node, review_node, decide_node 都增加:
   ```python
   try:
       ... (现有逻辑)
   except Exception as e:
       return {"error": f"{node_name}: {e}", "final_status": "failed", ...}
   ```
2. 兜底不影响正常的 GraphInterrupt（interrupt 异常不应被捕获）
3. 错误信息包含 node 名称，便于定位

严格评审标准:
- GraphInterrupt 不被捕获
- 每个 node 的兜底都返回正确的 error + final_status
- 新增测试: mock 内部函数抛异常，验证兜底返回
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_graph.py -v
2. python -m pytest tests/ -v
```

### 63. workspace.py — 文件操作增加重试机制

```
为 workspace.py 中的文件操作增加重试机制。

实现要求:
1. 新增装饰器 @retry_io(max_retries=3, delay=0.1)
2. 应用到 write_inbox, write_outbox, save_task_yaml 等写入操作
3. 只重试 OSError 和 PermissionError（不重试逻辑错误）
4. 最后一次仍失败时抛出原始异常
5. 每次重试之间等待 delay 秒（指数退避: 0.1, 0.2, 0.4）

严格评审标准:
- 装饰器独立可测
- 只重试 IO 相关异常
- 新增测试: 第一次失败第二次成功
- 新增测试: 所有重试都失败后抛异常
- 新增测试: 非 IO 异常不重试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_workspace.py -v
2. python -m pytest tests/ -v
```

### 64. driver.py — CLI agent stderr 分级处理

```
改进 driver.py 中 CLI agent 的 stderr 处理。

实现要求:
1. 分级处理 CLI 输出:
   - returncode=0 + 有 stdout: 正常
   - returncode=0 + 无 stdout + 有 stderr: warning（可能的提示信息）
   - returncode!=0: error
2. stderr 中包含 "warning" 时只记录 warning
3. stderr 中包含 "error" 或 "fatal" 时记录 error
4. 所有 stderr 都写入日志文件

严格评审标准:
- 新增测试: returncode=0 + warning stderr
- 新增测试: returncode=1 + error stderr
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_driver.py -v
2. python -m pytest tests/ -v
```

### 65. config.py — root_dir 检测增加诊断信息

```
改进 config.py 的 root_dir 检测，提供更好的诊断信息。

实现要求:
1. _find_root 失败时，warning 包含:
   - 当前 CWD
   - 扫描过的目录列表（前 5 个）
   - 建议: "运行 ma init 初始化项目" 或 "设置 MA_ROOT 环境变量"
2. MA_ROOT 目录不存在时抛 FileNotFoundError（而非 warning）
3. MA_ROOT 不是绝对路径时转换为绝对路径

严格评审标准:
- 新增测试: MA_ROOT 不存在时的错误
- 新增测试: 相对路径 MA_ROOT 转换
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 66-75. 更多健壮性改进

### 66. schema.py — 所有模型增加 model_config 严格模式

```
为 schema.py 中的 Pydantic 模型启用严格模式。

实现要求:
1. 对 Task 和 SkillContract 启用 model_config = ConfigDict(extra="forbid")
   防止意外的额外字段
2. 对 BuilderOutput 和 ReviewerOutput 保持 extra="allow"（IDE 输出可能有额外字段）
3. 对 SubTask 和 DecomposeResult 启用 extra="ignore"（忽略但不报错）

严格评审标准:
- Task 传入未知字段时抛 ValidationError
- BuilderOutput 传入额外字段时保留
- SubTask 传入额外字段时忽略
- 新增测试: 每种 extra 模式的行为
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_schema.py -v
2. python -m pytest tests/ -v
```

### 67. watcher.py — check_once 增加文件大小限制

```
OutboxPoller.check_once 增加文件大小限制。

实现要求:
1. 常量 MAX_OUTBOX_SIZE = 10 * 1024 * 1024（10MB）
2. 超过限制时跳过文件并 warning
3. 防止恶意或错误的超大文件导致内存溢出

严格评审标准:
- 新增测试: 超大文件被跳过
- 新增测试: 正常大小文件正常处理
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_watcher.py -v
2. python -m pytest tests/ -v
```

### 68. workspace.py — read_outbox 增加编码检测

```
增强 read_outbox 的编码处理。

实现要求:
1. 先尝试 UTF-8
2. UTF-8 失败时尝试 UTF-8-BOM（Windows 常见）
3. 都失败时尝试 latin-1（最宽松的编码）
4. 所有编码都失败时返回 None

严格评审标准:
- 新增测试: UTF-8 文件
- 新增测试: UTF-8-BOM 文件
- 新增测试: 二进制文件返回 None
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_workspace.py -v
2. python -m pytest tests/ -v
```

### 69-75. (继续健壮性改进)

### 69. cli.py — 增加 SIGTERM 信号处理

```
在 cli.py 中增加优雅的 SIGTERM 信号处理。

实现要求:
1. 注册 signal.SIGTERM 处理器
2. 收到 SIGTERM 时:
   - 释放锁
   - 清理 runtime
   - 保存当前状态
   - 退出进程
3. 防止 Docker/K8s 环境中强制终止导致的锁泄漏

严格评审标准:
- 信号处理器正确注册
- 清理逻辑完整
- 新增测试: 模拟 SIGTERM
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 70. graph.py — 增加 state 快照保存

```
在每个 node 执行后保存 state 快照到 .multi-agent/snapshots/。

实现要求:
1. 每个 node 返回前保存快照: snapshots/{task_id}-{node}-{timestamp}.json
2. 只保留最近 10 个快照（自动清理旧的）
3. 用于调试和状态恢复

严格评审标准:
- 快照包含完整 state（去除不可序列化的字段）
- 自动清理逻辑正确
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 71. decompose.py — 增加分解结果的 diff 检测

```
当缓存的分解结果与新的分解结果不同时，提示用户。

实现要求:
1. 比较新旧分解结果的 sub_task id 和 description
2. 有差异时显示 diff
3. 让用户选择使用新结果还是缓存

严格评审标准:
- diff 显示清晰
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 72. router.py — 增加 agent 健康检查

```
在 router.py 中增加 agent 健康检查功能。

实现要求:
1. 新增函数 check_agent_health(agent: AgentProfile) -> dict
   返回 {"available": bool, "driver": str, "binary_found": bool}
2. CLI 驱动: 检查二进制是否存在
3. File 驱动: 总是 available
4. cli.py 新增 ma agents 命令显示所有 agent 状态

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 73. workspace.py — 增加 workspace 健康检查

```
新增 workspace 健康检查功能。

实现要求:
1. 新增函数 check_workspace_health() -> list[str]
   返回问题列表（空 = 健康）
2. 检查:
   - 所有必需目录存在
   - store.db 可读写
   - 无孤立锁
   - 无超大文件
3. cli.py 新增 ma doctor 命令

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 74. graph.py — 增加 conversation 大小限制

```
限制 graph state 中 conversation 列表的大小。

实现要求:
1. 常量 MAX_CONVERSATION_ENTRIES = 50
2. 超过限制时保留最近 50 条，归档旧条目
3. 在 decide_node 中检查和截断

严格评审标准:
- 新增测试: 超过限制时截断
- 归档旧条目到 history
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 75. cli.py — 增加 ma init 命令

```
新增 ma init 命令初始化项目。

实现要求:
1. 创建完整的项目结构:
   - skills/code-implement/contract.yaml
   - agents/agents.yaml（带示例 agent 配置）
   - .multi-agent/ 工作目录
2. 支持 --force 覆盖已有文件
3. 显示初始化结果和下一步操作

严格评审标准:
- 幂等: 已初始化时提示
- --force 覆盖
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. ma init --help
2. python -m pytest tests/ -v
```

---

## 六、文档与示例 (76-85)

### 76-85. 文档改进

### 76. README.md — 增加架构图 ASCII art

```
在 README.md 中增加详细的架构图。

实现要求:
1. 增加 "## Architecture Overview" 章节（英文版）和 "## 架构概览" 章节（中文版）
2. 包含: 完整的 4-node graph 流程图（已有）+ decompose 流程图（新增）
3. decompose 流程图:
   需求 → 分解 → [sub-task₁ build-review] → [sub-task₂ build-review] → 汇总
4. 包含文件交互图: CLI ↔ TASK.md ↔ IDE AI ↔ outbox.json ↔ Watcher

严格评审标准:
- ASCII art 对齐正确
- 中英文版本内容一致
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 README.md 中包含 decompose 流程图
2. python -m pytest tests/ -v
```

### 77. README.md — 增加 Quick Start 教程

```
在 README.md 中增加详细的 Quick Start 教程。

实现要求:
1. 分步骤说明从安装到第一个任务完成的全过程:
   - Step 1: pip install
   - Step 2: ma init
   - Step 3: 配置 agents.yaml
   - Step 4: ma go "你的第一个需求"
   - Step 5: 在 IDE 中完成任务
   - Step 6: 查看结果
2. 每步配图（用代码块展示终端输出）
3. 常见问题 FAQ

严格评审标准:
- 每个步骤的命令都可运行
- FAQ 至少 5 个问题
- 中英文版本
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 README.md 中包含 Quick Start
2. python -m pytest tests/ -v
```

### 78. 新建 CONTRIBUTING.md

```
创建 CONTRIBUTING.md 文件。

实现要求:
1. 开发环境设置: venv, pip install -e ".[dev]"
2. 代码风格: 遵循现有风格
3. 测试要求: 新功能必须有测试，覆盖率不低于现有水平
4. 提交规范: feat/fix/docs/test/refactor 前缀
5. PR 流程: fork → branch → test → PR

严格评审标准:
- 所有命令可运行
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 CONTRIBUTING.md 存在且内容完整
2. python -m pytest tests/ -v
```

### 79. 新建 CHANGELOG.md

```
创建 CHANGELOG.md 文件，记录所有版本变更。

实现要求:
1. 格式: Keep a Changelog (https://keepachangelog.com/)
2. 记录 v0.1.0 到 v0.6.0 的所有变更
3. 分类: Added / Changed / Fixed / Removed

严格评审标准:
- 格式符合 Keep a Changelog 规范
- 所有版本都有记录
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 CHANGELOG.md 格式
2. python -m pytest tests/ -v
```

### 80. docs/ — 新建 skills 开发指南

```
创建 docs/skill-development.md 文件。

实现要求:
1. 说明如何创建新的 skill:
   - contract.yaml 格式说明
   - 每个字段的含义
   - 示例: 创建一个 "code-review" skill
2. 说明 quality_gates 的工作原理
3. 说明 preconditions/postconditions
4. 模板自定义指南

严格评审标准:
- 示例 contract.yaml 是有效的 YAML
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 docs/skill-development.md 存在
2. python -m pytest tests/ -v
```

### 81-85. 更多文档

### 81. docs/ — 新建 agent 配置指南

```
创建 docs/agent-configuration.md 文件。

实现要求:
1. agents.yaml 完整格式说明
2. 每个 IDE 的配置示例: Windsurf, Cursor, Claude CLI, Codex
3. CLI 驱动模式 vs 文件驱动模式
4. command_template 占位符说明: {task_file}, {outbox_file}
5. 故障排除: 常见配置错误

严格评审标准:
- 所有示例配置是有效的 YAML
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 82. docs/ — 新建 decompose 使用指南

```
创建 docs/task-decomposition.md 文件。

实现要求:
1. --decompose 功能说明
2. 什么时候应该使用 decompose
3. 分解结果的 JSON 格式说明
4. 依赖关系和执行顺序
5. 故障处理: 子任务失败后的行为
6. 示例: 完整的 decompose 流程

严格评审标准:
- 示例 JSON 是有效的
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 83. docs/ — 新建 API 参考

```
创建 docs/api-reference.md 文件。

实现要求:
1. 列出所有公共模块和函数
2. 每个函数: 签名、参数说明、返回值、示例
3. 按模块组织: config, schema, graph, router, driver, workspace, decompose, meta_graph

严格评审标准:
- 所有公共函数都有记录
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 84. pyproject.toml — 增加 dev dependencies

```
在 pyproject.toml 中增加开发依赖。

实现要求:
1. 新增 [project.optional-dependencies] dev 组:
   - pytest >= 9.0
   - pytest-cov >= 5.0
   - ruff >= 0.3（linter）
   - mypy >= 1.8（类型检查）
2. 新增 [tool.ruff] 配置
3. 新增 [tool.mypy] 配置
4. README 中更新开发环境设置

严格评审标准:
- pip install -e ".[dev]" 可正常安装
- ruff check src/ 无错误
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 85. 增加 .github/workflows/ci.yml

```
创建 GitHub Actions CI 配置。

实现要求:
1. 触发: push to main, PR
2. Matrix: Python 3.11, 3.12, 3.13
3. Steps:
   - checkout
   - setup-python
   - pip install -e ".[dev]"
   - ruff check src/
   - pytest tests/ -v --cov
4. 上传覆盖率到 Codecov（可选）

严格评审标准:
- YAML 语法正确
- 全量 pytest tests/ -v 通过

验证步骤:
1. 检查 .github/workflows/ci.yml 格式
2. python -m pytest tests/ -v
```

---

## 七、性能与可观察性 (86-95)

### 86-95. 性能和监控

### 86. 增加执行时间日志

```
在所有 graph node 中增加执行时间日志。

实现要求:
1. 每个 node 开始和结束时记录时间
2. 日志写入 .multi-agent/logs/timing-{task_id}.jsonl（JSON Lines 格式）
3. 每行: {"node": "plan", "start": timestamp, "end": timestamp, "duration_ms": int}
4. 新增函数 log_timing(task_id, node, start, end)

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 87. 增加 LRU 缓存统计

```
为 config.py 和 prompt.py 中的 lru_cache 增加统计。

实现要求:
1. 新增 ma cache-stats 命令显示缓存命中率
2. root_dir, _env 的 cache_info() 信息

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 88. workspace.py — 增加 workspace 大小统计

```
新增 workspace 大小统计功能。

实现要求:
1. 新增函数 get_workspace_stats() -> dict
   返回: total_size_mb, file_count, largest_file, oldest_file
2. cli.py 的 ma status 命令显示 workspace 大小
3. 超过 100MB 时 warning

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 89. watcher.py — 增加轮询性能优化

```
优化 OutboxPoller 的轮询性能。

实现要求:
1. 自适应轮询间隔: 检测到活动后缩短间隔（0.5s），空闲时延长（5s）
2. 新增参数 min_interval 和 max_interval
3. 连续 10 次空轮询后自动延长间隔

严格评审标准:
- 新增测试: 自适应间隔逻辑
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/test_watcher.py -v
2. python -m pytest tests/ -v
```

### 90. graph.py — 增加 graph 执行统计

```
新增 graph 执行统计收集。

实现要求:
1. 统计每个 node 的执行次数、平均时间、错误率
2. 保存到 .multi-agent/stats.json
3. ma status 显示累计统计

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 91-95. 更多性能优化

### 91. driver.py — CLI agent 输出流式处理

```
将 CLI agent 输出从 capture 改为流式处理。

实现要求:
1. 实时输出 CLI agent 的 stdout 到终端（使用 subprocess.PIPE + readline）
2. 同时捕获输出用于 JSON 提取
3. 不影响现有的 outbox 写入逻辑

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 92. workspace.py — 增加自动清理旧文件

```
增加 workspace 自动清理功能。

实现要求:
1. 新增函数 cleanup_old_files(max_age_days: int = 7)
2. 清理 tasks/, history/, logs/ 中超过 max_age_days 的文件
3. 不清理活跃任务的文件
4. cli.py 新增 ma cleanup 命令

严格评审标准:
- 不误删活跃文件
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 93. schema.py — 增加 JSON schema 导出

```
为所有 Pydantic 模型增加 JSON Schema 导出功能。

实现要求:
1. 新增 cli 命令 ma schema [MODEL] 导出 JSON Schema
2. 支持: Task, BuilderOutput, ReviewerOutput, SubTask, DecomposeResult
3. 输出标准 JSON Schema 格式

严格评审标准:
- JSON Schema 有效
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 94. 增加 Python 类型检查

```
为所有源文件增加完整的类型标注。

实现要求:
1. 所有函数参数和返回值都有类型标注
2. 运行 mypy src/multi_agent/ 无错误
3. 在 pyproject.toml 中配置 mypy

严格评审标准:
- mypy 检查通过
- 新增 CI 步骤
- 全量 pytest tests/ -v 通过

验证步骤:
1. mypy src/multi_agent/
2. python -m pytest tests/ -v
```

### 95. 增加代码风格检查

```
配置并运行 ruff linter。

实现要求:
1. 在 pyproject.toml 中配置 ruff
2. 修复所有 linting 错误
3. 配置规则: E, F, W, I (import sorting)
4. 排除 tests/ 的部分规则

严格评审标准:
- ruff check src/ 无错误
- ruff check tests/ 无错误
- 全量 pytest tests/ -v 通过

验证步骤:
1. ruff check src/
2. python -m pytest tests/ -v
```

---

## 八、新功能 (96-100)

### 96. 增加 ma list-skills 命令

```
新增 ma list-skills 命令列出所有可用 skill。

实现要求:
1. 扫描 skills/ 目录
2. 每个 skill 显示: id, version, description, quality_gates
3. 表格格式输出

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. ma list-skills
2. python -m pytest tests/ -v
```

### 97. 增加 ma agents 命令列出所有 agent

```
新增 ma agents 命令列出所有配置的 agent。

实现要求:
1. 读取 agents.yaml
2. 显示: id, driver, capabilities, binary status
3. CLI driver: 检查二进制是否可用

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. ma agents
2. python -m pytest tests/ -v
```

### 98. 增加 ma export 命令导出任务结果

```
新增 ma export 命令导出任务执行结果。

实现要求:
1. 格式: ma export TASK_ID [--format json|markdown|html]
2. 导出: 任务配置、conversation、最终结果
3. markdown 格式可直接粘贴到 PR 或文档

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 99. 增加 ma replay 命令重放任务

```
新增 ma replay 命令从历史中重放任务。

实现要求:
1. 格式: ma replay TASK_ID
2. 从 history/{task_id}.json 读取 conversation
3. 显示完整的事件时间线
4. 支持 --from-step N 从指定步骤开始

严格评审标准:
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. python -m pytest tests/ -v
```

### 100. 增加 ma version 命令和版本一致性检查

```
新增 ma version 命令并增加版本一致性检查。

实现要求:
1. ma version 显示: 版本号、Python 版本、安装路径
2. 新增测试: __init__.py 和 pyproject.toml 的版本号一致
3. 新增测试: README badge 中的测试数量与 pytest 实际一致

严格评审标准:
- 版本号一致性测试必须通过
- 新增测试
- 全量 pytest tests/ -v 通过

验证步骤:
1. ma version
2. python -m pytest tests/ -v
```

---

## 使用方式

批量运行（每条独立执行）:
```bash
# 示例: 逐条执行
ma go "第1条提示词内容" --builder windsurf --reviewer cursor
ma go "第2条提示词内容" --builder windsurf --reviewer cursor
# ...

# 或者用 decompose 模式处理更复杂的任务:
ma go "第1条提示词内容" --decompose --builder windsurf --reviewer cursor
```

> 每条任务完成后自动进入下一条
> 审查不通过会自动重试（最多 2 次）
> 所有修改完成后运行 `pytest tests/ -v` 确认无回归
