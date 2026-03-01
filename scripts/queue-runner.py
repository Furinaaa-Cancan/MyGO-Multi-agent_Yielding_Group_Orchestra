#!/usr/bin/env python3
"""
AgentOrchestra 一夜任务队列运行器
从 docs/task-queue-100.md 提取提示词并依次执行。

用法:
    python scripts/queue-runner.py              # 运行全部
    python scripts/queue-runner.py --start 31   # 从第 31 条开始
    python scripts/queue-runner.py --only 1,5,9 # 只运行指定编号
    python scripts/queue-runner.py --dry-run    # 只打印不执行
    python scripts/queue-runner.py --list       # 列出所有任务标题
"""
import re
import subprocess
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime


def extract_tasks(md_path: Path) -> list[tuple[int, str, str]]:
    """从 markdown 文件提取 (编号, 标题, 提示词) 列表。"""
    text = md_path.read_text(encoding="utf-8")
    # 匹配 ### N. 标题\n\n```\n内容\n```
    pattern = r"### (\d+)\.\s+(.+?)\n\n```\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    tasks = []
    for num_str, title, prompt in matches:
        tasks.append((int(num_str), title.strip(), prompt.strip()))
    return tasks


def run_task(num: int, title: str, prompt: str, builder: str, reviewer: str) -> bool:
    """执行单个任务，返回是否成功。"""
    task_id = f"task-queue-{num:03d}"
    print(f"\n{'='*60}")
    print(f"[{num}/100] 🚀 {title}")
    print(f"  task_id: {task_id}")
    print(f"  builder: {builder} | reviewer: {reviewer}")
    print(f"  started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    cmd = [
        "ma", "go", prompt,
        "--task-id", task_id,
        "--builder", builder,
        "--reviewer", reviewer,
    ]
    try:
        result = subprocess.run(cmd, timeout=3600)  # 1 hour max per task
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⏰ TIMEOUT after 1 hour, skipping #{num}")
        subprocess.run(["ma", "cancel"], capture_output=True)
        return False
    except KeyboardInterrupt:
        print(f"\n  🛑 User interrupted at task #{num}")
        subprocess.run(["ma", "cancel"], capture_output=True)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="AgentOrchestra 一夜任务队列")
    parser.add_argument("--start", type=int, default=1, help="从第 N 条开始")
    parser.add_argument("--end", type=int, default=100, help="到第 N 条结束")
    parser.add_argument("--only", type=str, help="只运行指定编号 (逗号分隔)")
    parser.add_argument("--dry-run", action="store_true", help="只打印不执行")
    parser.add_argument("--list", action="store_true", help="列出所有任务标题")
    parser.add_argument("--builder", default="windsurf", help="Builder agent")
    parser.add_argument("--reviewer", default="cursor", help="Reviewer agent")
    parser.add_argument("--md", default=None, help="Markdown 文件路径")
    args = parser.parse_args()

    # 定位 markdown 文件
    if args.md:
        md_path = Path(args.md)
    else:
        md_path = Path(__file__).parent.parent / "docs" / "task-queue-100.md"
    if not md_path.exists():
        print(f"❌ 找不到: {md_path}")
        sys.exit(1)

    tasks = extract_tasks(md_path)
    print(f"📋 从 {md_path.name} 提取了 {len(tasks)} 条任务")

    # 过滤
    if args.only:
        only_set = {int(x) for x in args.only.split(",")}
        tasks = [t for t in tasks if t[0] in only_set]
    else:
        tasks = [t for t in tasks if args.start <= t[0] <= args.end]

    if args.list:
        for num, title, _ in tasks:
            print(f"  [{num:3d}] {title}")
        return

    if args.dry_run:
        for num, title, prompt in tasks:
            print(f"\n[{num}] {title}")
            print(f"  prompt 长度: {len(prompt)} 字符")
            print(f"  前 80 字: {prompt[:80]}...")
        print(f"\n共 {len(tasks)} 条任务 (dry-run)")
        return

    # 执行
    results = {"passed": [], "failed": [], "skipped": []}
    start_time = time.time()

    for num, title, prompt in tasks:
        ok = run_task(num, title, prompt, args.builder, args.reviewer)
        if ok:
            results["passed"].append(num)
        else:
            results["failed"].append(num)
        # 任务间等 5 秒让系统稳定
        time.sleep(5)

    elapsed = time.time() - start_time
    hours, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)

    print(f"\n{'='*60}")
    print(f"📊 执行完成")
    print(f"  ✅ 通过: {len(results['passed'])}")
    print(f"  ❌ 失败: {len(results['failed'])}")
    if results["failed"]:
        print(f"  失败编号: {results['failed']}")
    print(f"  ⏱️ 总耗时: {hours}h {mins}m {secs}s")
    print(f"{'='*60}")

    # 保存结果
    report = md_path.parent / "queue-results.json"
    import json
    report.write_text(json.dumps(results, indent=2))
    print(f"  结果保存到: {report}")


if __name__ == "__main__":
    main()
