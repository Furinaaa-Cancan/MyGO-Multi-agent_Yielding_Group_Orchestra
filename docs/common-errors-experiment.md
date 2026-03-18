# Experiment Common Errors & Solutions

记录实验过程中遇到的问题和解决方案，避免重复踩坑。

## 1. LangGraph "任务正在进行中" 冲突

**现象**: `❌ 任务 'exp-xxx' 正在进行中`，新 run 无法启动。

**原因**: 前一个 run 的 LangGraph checkpoint 残留在 `store.db` 中。

**解决**: 在 `run_single_experiment()` 中每次 run 前删除 `store.db*`：
```python
for db_file in (PROJECT_ROOT / ".multi-agent").glob("store.db*"):
    db_file.unlink(missing_ok=True)
```

**状态**: 已修复 (commit 65af69d)

## 2. task_id 下划线格式错误

**现象**: `invalid task_id: 'exp-fixed_decompose-...'` — 下划线不符合 ID 正则。

**原因**: condition 名如 `fixed_decompose` 含下划线，直接拼入 task_id。

**解决**: `condition.replace('_', '-')` 转换。

**状态**: 已修复 (experiment_runner_v2.py)

## 3. Bugfix 任务修改源码残留

**现象**: bugfix-02 在 bugfix-01 后跑时 0.5s 完成（GT test 直接通过，没实际构建）。

**原因**: bugfix 任务修改 `src/multi_agent/workspace.py` 和 `trace.py`，多次 run 之间文件状态残留。

**解决**: `_reset_artifacts()` 中加 `git checkout HEAD -- src/multi_agent/workspace.py src/multi_agent/trace.py`。

**状态**: 已修复 (commit 60d385b)

## 4. 外部硬盘休眠导致 "Device not configured"

**现象**: 长时间 batch 运行后突然 `Device not configured`。

**原因**: macOS 外部 Seagate 硬盘自动休眠，中断 I/O。

**解决**:
- 使用 `caffeinate -s` 防止系统休眠
- 或在 System Settings > Energy 中禁用硬盘休眠
- 或改用 experiment_runner_v2.py 的 resume 功能（跳过已完成的 run）

**状态**: 通过 resume 功能缓解

## 5. 复杂度分类器精度不足 (67%)

**现象**: bugfix 任务被分为 "complex"，API 任务被分为 "complex"。

**原因**: 原始特征（token_count, verb_count）对中文长文本区分度不够。

**解决**: 添加两个关键特征：
- `function_sig_count`: 统计 requirement 中函数签名数量（最强区分因子）
- `is_bugfix`: 检测"修复""fix"等关键词（强负向信号 -6.0 分）

**状态**: 已修复，校准后 9/9 (100%) 精度 (commit 2e4f62f)

## 6. Experiment Runner 覆盖已有结果

**现象**: `--runs 3` 重新运行时覆盖了已有的 run_1.json。

**原因**: 没有跳过已存在的有效结果。

**解决**: 在 `run_single_experiment()` 开头检查 `existing.exists() and wall_clock_sec > 5`，跳过有效结果。

**状态**: 已修复 (commit 433c3c8)

## 7. Condition Single 执行路径不同

**现象**: C1 (single) 不传 `--reviewer`，系统默认分配 IDE reviewer，导致卡在手动审查步骤。

**原因**: 原 experiment_runner.py 中 C1 用不同的执行路径（不传 reviewer）。

**解决**: 所有 condition 统一使用 `--builder claude --reviewer claude --mode strict`，通过相同的 orchestration pipeline，只在 decompose/bridge flags 上区分。

**状态**: 已修复 (commit c0129c8)

## 8. Token 追踪显示 $0

**现象**: `cost_usd: 0.0000`，token 计数为 0。

**原因**: `finops.py` 的 `record_task_usage()` 在 graph node 中调用，但 CLI subprocess (claude CLI) 的 token 使用不经过此代码路径。

**解决**: 待解决。可能方案：
- 从 claude CLI 的 `--output-format json` 中解析 token
- 或使用 Anthropic API usage endpoint

**状态**: 未修复（不影响核心指标 resolve_rate 和 duration）

## 9. test_trace.py 被实验 run 意外修改

**现象**: bugfix-03 实验 run 向 `tests/test_trace.py` 添加了测试代码。

**原因**: builder agent 在修复 trace.py 时也修改了对应测试文件，这些修改没被 `_reset_artifacts()` 恢复。

**解决**: 在实验结束后用 `git checkout HEAD -- tests/` 恢复。或在 `_reset_artifacts()` 中增加测试文件的恢复。

**状态**: 手动修复，待完善自动化
