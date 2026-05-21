#!/usr/bin/env python3
"""
Message 反向查找器

编排器收到 SubAgent 完成通知时，若 stage 的 `output_message_id` 为空（SubAgent 尚未回填），
通过本脚本扫描 Message 目录，按 `agent_id`（逻辑编号或系统编号）反向查找对应的 Message。

用法:
    python find_message_by_agent.py --agent-id <agent_id>
    python find_message_by_agent.py --agent-id <agent_id> --instance <instance_id>

输出格式:
    {
        "found": true,
        "message_id": "20260509-003-a7f3",
        "path": ".agent/messages/2026-05-09/20260509-003-a7f3.json",
        "status": "DONE",
        "workflow_instance_id": "wf-xxx",
        "skill_id": "analyze-deps",
        "timestamp": "2026-05-09T11:00:00+08:00"
    }
"""

import argparse
import json
from pathlib import Path

from _common import find_message_path, load_message, messages_dir


def find_by_agent_id(agent_id: str, instance_id: str = "") -> dict:
    """
    扫描所有 Message，查找 agent_id 匹配的记录。
    若提供 instance_id，优先匹配同属该 instance 的 Message。
    """
    msgs_dir = messages_dir()
    if not msgs_dir.exists():
        return {"found": False, "reason": "Messages directory not found"}

    candidates = []

    for subdir in msgs_dir.iterdir():
        if not subdir.is_dir():
            continue
        for msg_file in subdir.glob("*.json"):
            try:
                msg = json.loads(msg_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            # 匹配 agent_id（支持逻辑 assigned_agent_id 和系统 system_agent_id）
            msg_agent_id = msg.get("agent_id", "")
            if msg_agent_id != agent_id:
                continue

            # 若指定了 instance_id，检查归属
            msg_instance_id = msg.get("workflow_instance_id", "")
            if instance_id and msg_instance_id != instance_id:
                continue

            candidates.append({
                "message_id": msg.get("message_id"),
                "path": str(msg_file).replace("\\", "/"),
                "status": msg.get("status"),
                "workflow_instance_id": msg_instance_id,
                "skill_id": msg.get("skill_id", ""),
                "timestamp": msg.get("timestamp", ""),
            })

    if not candidates:
        return {"found": False, "reason": f"No message found for agent_id={agent_id}"}

    # 按时间戳降序，取最新的一条
    candidates.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    best = candidates[0]
    best["found"] = True
    best["total_matches"] = len(candidates)
    return best


def main():
    parser = argparse.ArgumentParser(description="Find message by agent_id")
    parser.add_argument("--agent-id", required=True, help="Agent ID (logical or system)")
    parser.add_argument("--instance", default="", help="Optional instance ID filter")
    args = parser.parse_args()

    result = find_by_agent_id(args.agent_id, args.instance or "")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
