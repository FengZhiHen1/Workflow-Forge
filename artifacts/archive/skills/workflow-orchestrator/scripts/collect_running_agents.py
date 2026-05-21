#!/usr/bin/env python3
"""
Running Agent 收集器

扫描所有活跃工作流实例，收集状态为 RUNNING 的 stage 的 agent 信息，
供编排器通过平台能力查询这些 SubAgent 是否仍然存活。

用法:
    python collect_running_agents.py              # 扫描所有活跃实例
    python collect_running_agents.py --instance wf-xxx  # 只扫描指定实例

输出格式:
    {
        "total": 2,
        "agents": [
            {
                "instance_id": "wf-refactor-pipeline-20260509-001-a7f3",
                "stage_id": "s1_analyze",
                "assigned_agent_id": "s1_analyze-20260509-230050-a7f3",
                "system_agent_id": "agent-abc-123-xyz",
                "skill_id": "analyze-deps",
                "start_time": "2026-05-10T09:15:15+08:00",
                "elapsed_seconds": 120
            }
        ]
    }
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def find_paths() -> Path:
    """返回 instances_dir。"""
    cwd = Path.cwd()
    candidate_agent = cwd / ".agent"
    if candidate_agent.exists():
        return candidate_agent / "workflows" / "instances"
    for parent in [cwd.parent, cwd.parent.parent]:
        a = parent / ".agent"
        if a.exists():
            return a / "workflows" / "instances"
    return cwd / ".agent" / "workflows" / "instances"


def calc_elapsed_seconds(start_time: str) -> int:
    """计算从 start_time 到当前时间的秒数。"""
    if not start_time:
        return 0
    try:
        s = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc).astimezone()
        return int((now - s).total_seconds())
    except Exception:
        return 0


def collect(instances_dir: Path, filter_instance: str = None) -> dict:
    if not instances_dir.exists():
        return {"total": 0, "agents": []}

    agents = []
    instance_files = list(instances_dir.glob("*.json"))
    if filter_instance:
        instance_files = [f for f in instance_files if f.stem == filter_instance]

    for inst_path in instance_files:
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        inst_id = instance.get("instance_id", inst_path.stem)
        inst_status = instance.get("status", "UNKNOWN")
        if inst_status not in ("PLANNING", "EXECUTING", "SUSPENDED"):
            continue

        for stage in instance.get("stages", []):
            if stage.get("status") != "RUNNING":
                continue

            start = stage.get("start_time", "")
            entry = {
                "instance_id": inst_id,
                "stage_id": stage.get("stage_id", "unknown"),
                "assigned_agent_id": stage.get("assigned_agent_id") or "",
                "system_agent_id": stage.get("system_agent_id") or "",
                "skill_id": stage.get("skill_id", ""),
                "start_time": start,
                "elapsed_seconds": calc_elapsed_seconds(start),
            }
            agents.append(entry)

    return {"total": len(agents), "agents": agents}


def main():
    parser = argparse.ArgumentParser(description="Collect running agent info from active workflow instances")
    parser.add_argument("--instance", default="", help="Filter to a specific instance")
    args = parser.parse_args()

    instances_dir = find_paths()
    result = collect(instances_dir, args.instance or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
