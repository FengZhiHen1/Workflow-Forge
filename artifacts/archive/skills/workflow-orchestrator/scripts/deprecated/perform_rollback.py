#!/usr/bin/env python3
"""
Perform Rollback

执行完整的回退流程：Git checkout + Instance 状态机重置 + .agent/ 备份恢复。
封装 Workflow 规范 5.2 的全部步骤。

用法:
    python perform_rollback.py --instance wf-001 --target-stage s2_refactor
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def run_git(args_list: list, cwd: Path = None) -> tuple:
    try:
        result = subprocess.run(
            ["git"] + args_list,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            encoding="utf-8",
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git command not found"


def find_git_root() -> Path:
    cwd = Path.cwd()
    code, stdout, _ = run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if code == 0 and stdout:
        return Path(stdout)
    return cwd


def find_paths() -> tuple:
    """返回 (instances_dir, backups_dir, agent_dir)"""
    cwd = Path.cwd()
    candidate_agent = cwd / ".agent"
    if candidate_agent.exists():
        return (
            candidate_agent / "workflows" / "instances",
            candidate_agent / "backups",
            candidate_agent,
        )
    for parent in [cwd.parent, cwd.parent.parent]:
        a = parent / ".agent"
        if a.exists():
            return (
                a / "workflows" / "instances",
                a / "backups",
                a,
            )
    return (
        cwd / ".agent" / "workflows" / "instances",
        cwd / ".agent" / "backups",
        cwd / ".agent",
    )


def atomic_write_json(filepath: Path, data: dict):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(str(tmp), str(filepath))


def cmd_rollback(args):
    instances_dir, backups_dir, agent_dir = find_paths()
    inst_path = instances_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    # 查找目标 stage
    target_stage = None
    for s in instance["stages"]:
        if s["stage_id"] == args.target_stage:
            target_stage = s
            break

    if not target_stage:
        print(json.dumps({"error": f"Target stage not found: {args.target_stage}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    tag = target_stage.get("git_anchor_tag")
    if not tag:
        print(json.dumps({"error": f"No git anchor tag for stage {args.target_stage}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 1. 备份 .agent/
    backup_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backups_dir / args.instance / backup_ts
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    if agent_dir.exists():
        shutil.copytree(agent_dir, backup_path, dirs_exist_ok=True)

    # 2. Git checkout 业务代码
    git_root = find_git_root()
    code, _, stderr = run_git(["checkout", tag, "--", "."], cwd=git_root)
    if code != 0:
        print(json.dumps({
            "error": f"Git checkout failed: {stderr}",
            "tag": tag,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 3. 恢复 .agent/（git checkout 不应影响 .gitignore 目录，但按规范执行保险步骤）
    if agent_dir.exists() and backup_path.exists():
        # 比较关键文件数量，若 .agent/ 被意外清空则恢复
        agent_files = list(agent_dir.rglob("*"))
        backup_files = list(backup_path.rglob("*"))
        if len(agent_files) < len(backup_files) * 0.5:
            shutil.rmtree(agent_dir, ignore_errors=True)
            shutil.copytree(backup_path, agent_dir, dirs_exist_ok=True)

    # 4. 重置 Instance 状态机
    reset_started = False
    for s in instance["stages"]:
        if s["stage_id"] == args.target_stage:
            reset_started = True
        if reset_started:
            s["status"] = "PENDING"
            s["output_message_id"] = None
            s["assigned_agent_id"] = None
            s["start_time"] = None
            s["end_time"] = None
            s["blocked_by_confirm"] = False
            s["loop_counter"] = 0
            s["attempt_count"] = 0
            if s["stage_id"] != args.target_stage:
                s["deviation_flag"] = False

    instance["status"] = "EXECUTING"
    instance["current_stage"] = args.target_stage
    instance["updated_at"] = now_iso()
    instance["execution_summary"]["completed_stages"] = len([s for s in instance["stages"] if s["status"] in ("DONE", "SKIPPED")])
    instance["execution_summary"]["active_agents"] = 0

    # 追加回退记录到 deviation_log
    instance["deviation_log"].append({
        "timestamp": now_iso(),
        "type": "USER_ROLLBACK",
        "reason": f"回退到 stage {args.target_stage}",
        "user_confirmed": True,
        "original_stage_id": args.target_stage,
        "impact_stages": [s["stage_id"] for s in instance["stages"] if s["status"] == "PENDING" and s["stage_id"] != args.target_stage],
        "resolution": f"Git checkout {tag}, Instance 状态机重置",
        "reported_in_summary": True,
    })

    atomic_write_json(inst_path, instance)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "target_stage": args.target_stage,
        "git_tag": tag,
        "git_root": str(git_root).replace("\\", "/"),
        "backup_path": str(backup_path).replace("\\", "/"),
        "stages_reset": len([s for s in instance["stages"] if s["status"] == "PENDING"]),
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Perform full workflow rollback")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--target-stage", required=True)
    args = parser.parse_args()
    cmd_rollback(args)


if __name__ == "__main__":
    main()
