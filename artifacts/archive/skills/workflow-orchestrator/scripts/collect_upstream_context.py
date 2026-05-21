#!/usr/bin/env python3
"""
上游上下文收集器

为指定 stage 收集所有直接前置依赖（edges 中指向本 stage 的 from）的产出摘要，
供编排器生成 [STAGE_DIRECTION] 时引用，避免编排器自己遍历 Message 文件浪费 token。

用法:
    python collect_upstream_context.py --instance wf-xxx --stage s2_refactor
    python collect_upstream_context.py --instance wf-xxx --stage s2_refactor --max-report-length 300

输出格式:
    {
        "instance_id": "wf-xxx",
        "stage_id": "s2_refactor",
        "upstream_stages": [
            {
                "stage_id": "s1_analyze",
                "status": "DONE",
                "message_id": "20260509-001-d2e4",
                "report_summary": "分析完成，发现3处循环依赖...",
                "modified_files": ["src/a.py"],
                "output_files": ["results/deps.json"],
                "skill_id": "analyze-deps"
            }
        ],
        "total_upstream": 1
    }
"""

import argparse
import json
from pathlib import Path


def find_paths() -> tuple:
    """返回 (instances_dir, messages_dir)。"""
    cwd = Path.cwd()
    candidate_agent = cwd / ".agent"
    if candidate_agent.exists():
        return (
            candidate_agent / "workflows" / "instances",
            candidate_agent / "messages",
        )
    for parent in [cwd.parent, cwd.parent.parent]:
        a = parent / ".agent"
        if a.exists():
            return (
                a / "workflows" / "instances",
                a / "messages",
            )
    return (
        cwd / ".agent" / "workflows" / "instances",
        cwd / ".agent" / "messages",
    )


def find_message_path(message_id: str, messages_dir: Path) -> Path:
    """按日期分区查找 message 文件。"""
    if not messages_dir.exists():
        return None
    date_prefix = message_id[:4] + "-" + message_id[4:6] + "-" + message_id[6:8]
    date_dir = messages_dir / date_prefix
    if date_dir.exists():
        path = date_dir / f"{message_id}.json"
        if path.exists():
            return path
    for subdir in messages_dir.iterdir():
        if subdir.is_dir():
            path = subdir / f"{message_id}.json"
            if path.exists():
                return path
    return None


def summarize_report(report: str, max_length: int) -> str:
    """截断或精简 report 文本。"""
    if not report:
        return ""
    report = report.strip()
    if len(report) <= max_length:
        return report
    # 截断到最近的一个句号或换行，避免切断单词
    truncated = report[:max_length]
    for delim in ("\n", "。", ". ", "; ", ";", ", ", ","):
        idx = truncated.rfind(delim)
        if idx > max_length * 0.6:
            return truncated[:idx + len(delim)].rstrip() + " ..."
    return truncated.rstrip() + " ..."


def collect(instance_id: str, stage_id: str, messages_dir: Path, instances_dir: Path, max_report_length: int) -> dict:
    inst_path = instances_dir / f"{instance_id}.json"
    if not inst_path.exists():
        return {"error": f"Instance not found: {instance_id}", "upstream_stages": []}

    try:
        instance = json.loads(inst_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"Failed to read instance: {e}", "upstream_stages": []}

    edges = instance.get("edges", [])
    all_stages = instance.get("stages", [])

    # 查找直接前置依赖：edges 中 to == stage_id 且 condition 为 always/success
    prereq_stage_ids = []
    for e in edges:
        if e.get("to") == stage_id and e.get("condition") in ("always", "success"):
            prereq_stage_ids.append(e.get("from"))

    upstream_stages = []
    for prereq_id in prereq_stage_ids:
        # 收集该 stage_id 的全部实例（支持多实例并发）
        matching = [s for s in all_stages if s.get("stage_id") == prereq_id]
        if not matching:
            upstream_stages.append({
                "stage_id": prereq_id,
                "status": "UNKNOWN",
                "error": "Stage not found in instance",
            })
            continue

        for prereq_stage in matching:
            siid = prereq_stage.get("stage_instance_id") or prereq_stage.get("stage_id", "unknown")
            entry = {
                "stage_id": prereq_id,
                "stage_instance_id": siid,
                "status": prereq_stage.get("status", "UNKNOWN"),
                "skill_id": prereq_stage.get("skill_id", ""),
                "message_id": prereq_stage.get("output_message_id") or "",
                "report_summary": "",
                "modified_files": [],
                "output_files": [],
            }

            msg_id = prereq_stage.get("output_message_id")
            if msg_id:
                msg_path = find_message_path(msg_id, messages_dir)
                if msg_path:
                    try:
                        msg = json.loads(msg_path.read_text(encoding="utf-8"))
                        entry["report_summary"] = summarize_report(msg.get("report", ""), max_report_length)
                        entry["modified_files"] = msg.get("modified_files", []) or []
                        entry["output_files"] = msg.get("output_files", []) or []
                    except Exception:
                        entry["error"] = f"Failed to read message: {msg_id}"
                else:
                    entry["error"] = f"Message file not found: {msg_id}"
            else:
                entry["error"] = "No output_message_id"

            upstream_stages.append(entry)

    return {
        "instance_id": instance_id,
        "stage_id": stage_id,
        "upstream_stages": upstream_stages,
        "total_upstream": len(upstream_stages),
    }


def main():
    parser = argparse.ArgumentParser(description="Collect upstream stage context for generating stage_direction")
    parser.add_argument("--instance", required=True, help="Workflow instance ID")
    parser.add_argument("--stage", required=True, help="Current stage ID")
    parser.add_argument("--max-report-length", type=int, default=500, help="Max length for report summary (default: 500)")
    args = parser.parse_args()

    instances_dir, messages_dir = find_paths()
    result = collect(args.instance, args.stage, messages_dir, instances_dir, args.max_report_length)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
