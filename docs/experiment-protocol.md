# MyGO 多 Agent 协作效果对照实验协议

> Version 1.0 | 2026-03-17

## 1. 研究问题 (Research Questions)

- **RQ1**: 多 Agent (builder + reviewer) 协作是否比单 Agent 产出更高质量代码？
- **RQ2**: 任务分解 (decompose) 是否在复杂任务上带来显著收益？
- **RQ3**: 严格审查模式 (strict mode) 是否能有效减少引入的缺陷？

## 2. 可验证假设 (Hypotheses)

| 假设 | 描述 | 检验方法 |
|------|------|----------|
| H1 | multi-agent (builder+reviewer) 的功能通过率 > single-agent | Wilcoxon signed-rank test, p<0.05 |
| H2 | decompose 模式在复杂任务上的通过率 > 单体模式 | Wilcoxon signed-rank test, p<0.05 |
| H3 | strict mode 的 lint violation 数 < 无 review 模式 | Mann-Whitney U test, p<0.05 |
| H4 | decompose 模式的峰值 context token 数 < 单体模式 | Paired t-test, p<0.05 |

## 3. 实验条件 (Experimental Conditions)

### 3.1 自变量 (Independent Variables)

| 条件 | Builder | Reviewer | Mode | Decompose |
|------|---------|----------|------|-----------|
| **A: Single** | Claude CLI | (无) | — | 否 |
| **B: Multi** | Claude CLI | Claude CLI | strict | 否 |
| **C: Multi+Decompose** | Claude CLI | Claude CLI | strict | 是 |

### 3.2 控制变量 (Controlled Variables)

| 变量 | 控制方法 |
|------|----------|
| LLM 模型版本 | 记录实验时 Claude 模型 ID，写入 results metadata |
| 代码库初始状态 | 每个任务在 clean git state 上执行 (git stash / worktree) |
| Prompt 模板版本 | 记录 git commit hash |
| 重试预算 | 固定 retry_budget=2 |
| 超时 | 固定 timeout_sec=1800 |
| 随机种子 | LLM API 层面不可控，通过重复实验抵消 |

### 3.3 因变量 (Dependent Variables / Metrics)

| 指标 | 采集方式 | 类型 |
|------|----------|------|
| `functional_pass` | 任务附带的 pytest 测试用例通过率 | 主指标 (Primary) |
| `lint_violations` | `ruff check --output-format json` 计数 | 质量指标 |
| `type_errors` | `mypy --no-error-summary` 错误数 | 质量指标 |
| `retry_count` | 从 graph state 提取 | 效率指标 |
| `token_usage` | finops 模块记录 (builder_tokens + reviewer_tokens) | 成本指标 |
| `duration_sec` | 任务开始到结束的壁钟时间 | 效率指标 |
| `context_peak_tokens` | 从 conversation 长度估算 | 效率指标 |
| `changed_files_count` | git diff --stat 提取 | 辅助指标 |

## 4. 任务集设计 (Task Set)

### 4.1 任务来源

从 `task-templates/` 选取 3 个复杂度梯度的模板，每个模板实例化 3 个变体：

| 复杂度 | 模板 | 变体示例 | 预期 decompose 子任务数 |
|--------|------|----------|----------------------|
| Simple | `bugfix` | 修复 3 个不同类型的 bug | 1 (不分解) |
| Medium | `api-endpoint` | 3 个不同资源的 CRUD endpoint | 2-3 |
| Complex | `auth` | 3 个不同的认证方案 (JWT/OAuth/Session) | 4-6 |

**总任务数**: 3 复杂度 x 3 变体 = **9 个独立任务**

### 4.2 每个任务必须包含

```
tasks/experiment/
  task-{id}/
    requirement.txt     # 需求描述
    test_ground_truth.py  # 功能正确性测试 (ground truth)
    expected_files.txt    # 预期修改的文件列表 (可选)
```

### 4.3 Ground Truth 测试要求

- 每个任务至少 3 个 test case
- 测试必须在任务完成前 FAIL，完成后 PASS (红-绿验证)
- 测试独立于实现细节 (测试行为，不测试内部结构)

## 5. 实验流程 (Procedure)

### 5.1 单次运行流程

```
1. git checkout -b experiment-{condition}-{task_id}-{run_idx}
2. 验证 ground truth 测试当前 FAIL (红)
3. 执行任务 (single / multi / multi+decompose)
4. 采集所有指标
5. 运行 ground truth 测试 → 记录 pass/fail
6. 运行 ruff check → 记录 violation 数
7. 运行 mypy → 记录 error 数
8. 保存结果到 results/{condition}/{task_id}/run_{idx}.json
9. git checkout main (恢复 clean state)
```

### 5.2 重复次数

每个 (条件, 任务) 组合重复 **n=3** 次，以应对 LLM 输出随机性。

**总运行次数**: 3 条件 x 9 任务 x 3 重复 = **81 次**

### 5.3 执行顺序

- 使用 Latin Square 设计对条件顺序进行平衡
- 或者：按条件分块执行 (block design)，每块内任务顺序随机打乱

## 6. 统计分析计划 (Statistical Analysis)

### 6.1 主分析

| 比较 | 指标 | 检验方法 | 理由 |
|------|------|----------|------|
| A vs B | functional_pass | Wilcoxon signed-rank | 配对，非正态，小样本 |
| B vs C | functional_pass | Wilcoxon signed-rank | 同上 |
| A vs B | lint_violations | Mann-Whitney U | 独立样本 |
| B vs C | context_peak_tokens | Paired t-test | 若近似正态 |

### 6.2 效应量 (Effect Size)

报告 Cliff's delta (非参数效应量)：
- |d| < 0.147: negligible
- |d| < 0.33: small
- |d| < 0.474: medium
- |d| >= 0.474: large

### 6.3 多重比较校正

3 个主假设 → Bonferroni 校正：显著性阈值调整为 p < 0.05/3 = 0.0167

## 7. 效度威胁 (Threats to Validity)

### 7.1 内部效度 (Internal Validity)

| 威胁 | 缓解措施 |
|------|----------|
| Order effect | 每个任务在 clean branch 上执行，互不影响 |
| Learning effect (语义记忆) | 实验期间禁用 semantic_memory (或每次清空) |
| LLM 版本漂移 | 记录模型 ID；短时间内完成全部实验 |
| 网络/API 抖动 | 重复 3 次；记录是否因超时失败 |

### 7.2 外部效度 (External Validity)

| 威胁 | 承认 & 未来工作 |
|------|----------------|
| 单一项目 | 本实验仅在 MyGO 代码库上验证，跨项目泛化性待考察 |
| 自设计任务 | 未使用 SWE-bench 等公开 benchmark，可能存在选择偏差 |
| 单一 LLM | 仅使用 Claude，不同 LLM 可能表现不同 |

### 7.3 构念效度 (Construct Validity)

| 威胁 | 缓解措施 |
|------|----------|
| "通过率" 是否真正衡量代码质量 | 结合 lint + type check 多维度评估 |
| Ground truth 测试覆盖不全 | 每个任务至少 3 个测试；审查测试本身的充分性 |

## 8. 结果报告格式

每次运行产出一个 JSON：

```json
{
  "experiment_version": "1.0",
  "timestamp": "2026-03-17T10:30:00Z",
  "condition": "multi",
  "task_id": "task-auth-jwt",
  "run_idx": 1,
  "git_commit": "abc1234",
  "model_id": "claude-opus-4-6",
  "metrics": {
    "functional_pass": true,
    "functional_tests_total": 5,
    "functional_tests_passed": 5,
    "lint_violations": 0,
    "type_errors": 0,
    "retry_count": 1,
    "token_usage": {
      "builder_input": 3200,
      "builder_output": 1500,
      "reviewer_input": 2800,
      "reviewer_output": 800
    },
    "duration_sec": 245,
    "context_peak_tokens": 4200,
    "changed_files_count": 3,
    "decompose_sub_tasks": 0
  }
}
```

## 9. 参考文献

1. Jimenez, C. E., et al. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" ICLR 2024.
2. Jain, N., et al. "MASAI: Modular Architecture for Software-engineering AI Agents." NeurIPS 2024.
3. Xia, C. S., et al. "Agentless: Demystifying LLM-based Software Engineering Agents." FSE 2025.
4. Islam, M. S., et al. "MapCoder: Multi-Agent Code Generation through Planning, Verification." ACL 2024.
5. Yang, J., et al. "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering." ICLR 2025.
6. Mialon, G., et al. "GAIA: A Benchmark for General AI Assistants." ICLR 2024.
7. Cohen, J. "Statistical Power Analysis for the Behavioral Sciences." 2nd ed., 1988.
