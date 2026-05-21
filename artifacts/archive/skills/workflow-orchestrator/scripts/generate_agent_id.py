#!/usr/bin/env python3
"""
Agent ID 生成器

为工作流 Stage 生成全局唯一的逻辑 agent_id，供编排器注入 SubAgent prompt 使用。

用法:
    python generate_agent_id.py --stage s1_analyze
    python generate_agent_id.py --stage s2_refactor --instance wf-refactor-20260509-001-a7f3

输出格式:
    {
        "agent_id": "s1_analyze-20260509-230050-a7f3",
        "stage_id": "s1_analyze",
        "instance_id": "...",
        "timestamp": "20260509-230050",
        "random_suffix": "a7f3"
    }
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime


def sanitize_stage_id(stage_id: str) -> str:
    """清理 stage_id，保留字母、数字、下划线和横线，超长则截断。"""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", stage_id)
    return safe[:20]  # 最长 20 字符


def generate_agent_id(stage_id: str, instance_id: str = "") -> dict:
    """按规则生成 agent_id。"""
    safe_stage = sanitize_stage_id(stage_id)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rand = os.urandom(2).hex()  # 4 位 hex

    parts = [safe_stage, timestamp, rand]
    agent_id = "-".join(parts)

    return {
        "agent_id": agent_id,
        "stage_id": stage_id,
        "instance_id": instance_id,
        "timestamp": timestamp,
        "random_suffix": rand,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate logical agent_id for workflow stage")
    parser.add_argument("--stage", required=True, help="Stage ID (e.g. s1_analyze)")
    parser.add_argument("--instance", default="", help="Optional instance ID for traceability")
    args = parser.parse_args()

    result = generate_agent_id(args.stage, args.instance)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
