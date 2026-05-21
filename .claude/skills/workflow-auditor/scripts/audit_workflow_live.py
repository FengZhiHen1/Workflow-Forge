#!/usr/bin/env python3
"""
Workflow v3.0.0 实时审计引擎（live 模式）。

在 .tmp/ 下搭建隔离沙箱，真实调用 wfctl 命令，模拟 6 种攻击场景，
将 wfctl 实际行为与规范预期对比，报告偏差。

前置条件:
    - git 可用
    - wfctl 模块可导入（artifacts/scripts/wfctl/）

调用方式:
    python audit_workflow_live.py \\
        --workflow-yaml <path/to/WORKFLOW.yaml> \\
        --skills-dir <path/to/skills/> \\
        [--contracts-dir <path/to/contracts/>] \\
        [--wfctl-path <path/to/wfctl/>] \\
        [--attacks sm1,sm2,choice,timeout,sw1,conflict]

输出 JSON:
    {"findings": [...], "summary": {...}, "attacks_run": [...]}
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

# ─── constants ──────────────────────────────────────────────

START_STAGE = "s00-workflow-start"
END_STAGE = "s99-workflow-end"
TZ = timezone(timedelta(hours=8))

ALL_ATTACKS = ["sm1", "sm2", "choice", "timeout", "sw1", "conflict"]


# ─── helpers ────────────────────────────────────────────────


def _find_wfctl_parent() -> Path:
    """定位 wfctl 模块的父目录（需加入 sys.path 才能 import wfctl）。"""
    candidates = [
        Path.cwd() / "artifacts" / "scripts",
        Path(__file__).resolve().parents[5] / "artifacts" / "scripts",
    ]
    for c in candidates:
        if (c / "wfctl" / "__init__.py").exists():
            return c.resolve()
    return Path.cwd() / "artifacts" / "scripts"


WFCTL_PARENT = _find_wfctl_parent()
WFCTL_DIR = WFCTL_PARENT / "wfctl"


def _run(cmd: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _run_wfctl(args: list[str], sandbox_cwd: Path, timeout: int = 30) -> dict:
    """运行 wfctl 命令并解析 JSON 输出。

    CWD = sandbox（确保 wfctl 的 find_root() 找到沙箱的 .claude/）。
    PYTHONPATH = wfctl 目录（确保 `from core.errors import ...` 可用）。
    """
    cmd = [sys.executable, str(WFCTL_DIR / "main.py")] + args
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(WFCTL_DIR)}
    result = subprocess.run(cmd, cwd=str(sandbox_cwd), capture_output=True, text=True,
                            timeout=timeout, env=env)
    try:
        return json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"_raw": result.stdout, "_stderr": result.stderr, "_rc": result.returncode}


def _write_message(instance_dir: Path, status: str, stage_id: str,
                   report: str = "mock", choice: str | None = None,
                   confirm_questions: list | None = None) -> str:
    """向消息池写入模拟 Message 文件。返回 message_id。"""
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    msg = {
        "schema_version": "3.0.0",
        "message_id": msg_id,
        "instance_id": instance_dir.name,
        "stage_id": stage_id,
        "stage_instance_id": stage_id,
        "status": status,
        "report": report,
        "checkpoint_summary": f"mock checkpoint for {stage_id}",
        "confirm_questions": confirm_questions or [],
        "parallel_targets": None,
        "modified_files": [],
        "timestamp": datetime.now(TZ).isoformat(),
    }
    messages_dir = instance_dir / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    msg_path = messages_dir / f"{msg_id}.json"
    msg_path.write_text(json.dumps(msg, ensure_ascii=False, indent=2), encoding="utf-8")
    return msg_id


def _read_instance(instance_dir: Path) -> dict | None:
    """读取 instance.json。"""
    path = instance_dir / "instance.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_for_terminal(wfctl_cwd: Path, instance_id: str, instance_dir: Path,
                       max_rounds: int = 30) -> str:
    """循环调用 wfctl next 直到实例终态或达到最大轮数。返回终态原因。"""
    for _ in range(max_rounds):
        result = _run_wfctl(["next", "--instance", instance_id], wfctl_cwd)
        inst = _read_instance(instance_dir)
        if inst and inst.get("status") in ("COMPLETED", "FAILED"):
            return inst["status"]
        actions = result.get("actions", result.get("_raw", ""))
        if isinstance(actions, str) and "no actions" in actions.lower():
            return "IDLE"
        if not actions:
            inst2 = _read_instance(instance_dir)
            if inst2 and inst2.get("status") in ("COMPLETED", "FAILED"):
                return inst2["status"]
    inst = _read_instance(instance_dir)
    return inst.get("status", "TIMEOUT") if inst else "TIMEOUT"


# ─── sandbox ─────────────────────────────────────────────────


def _copy_workflow_to_sandbox(wf_dir: Path, sandbox: Path,
                              dest_skills_dir: Path, global_skills_dir: Path | None,
                              workflows_dir: Path | None, copied: set, depth: int = 1) -> None:
    """将一个工作流目录复制到沙箱中，递归处理子工作流引用。

    copied: 已复制的工作流名称集合（防止循环引用和重复复制）。
    """
    MAX_DEPTH = 3
    if depth > MAX_DEPTH:
        return

    wf_name = wf_dir.name
    if wf_name in copied:
        return
    copied.add(wf_name)

    dest_wf_dir = sandbox / ".claude" / "workflows" / wf_name
    dest_wf_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = wf_dir / "WORKFLOW.yaml"
    if not yaml_path.exists():
        return
    shutil.copy2(yaml_path, dest_wf_dir / "WORKFLOW.yaml")

    # 工作流级 references/
    wf_refs = wf_dir / "references"
    if wf_refs.is_dir():
        dest_refs = dest_wf_dir / "references"
        if not dest_refs.exists():
            shutil.copytree(wf_refs, dest_refs)

    # 工作流局部 skills/
    wf_skills = wf_dir / "skills"
    if wf_skills.is_dir():
        for skill_dir in wf_skills.iterdir():
            if skill_dir.is_dir():
                dest = dest_skills_dir / skill_dir.name
                if not dest.exists():
                    shutil.copytree(skill_dir, dest)

    # 发现子工作流引用并递归复制
    if workflows_dir:
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for stage in data.get("stages", []):
            if isinstance(stage, dict) and stage.get("workflow"):
                wf_ref = stage["workflow"]
                child_wf_dir = workflows_dir / wf_ref
                if child_wf_dir.is_dir():
                    _copy_workflow_to_sandbox(child_wf_dir, sandbox,
                                              dest_skills_dir, global_skills_dir,
                                              workflows_dir, copied, depth + 1)


def setup_sandbox(workflow_yaml_path: Path, skills_dir: Path,
                  contracts_dir: Path | None = None,
                  workflows_dir: Path | None = None,
                  sandbox_dir: Path | None = None) -> Path:
    """搭建隔离沙箱，返回沙箱根路径。含子工作流的递归复制。"""
    if sandbox_dir:
        sandbox = sandbox_dir
        sandbox.mkdir(parents=True, exist_ok=True)
    else:
        sandbox = Path(tempfile.mkdtemp(prefix="audit-live-", dir=Path.cwd() / ".tmp"))
    (sandbox / ".gitignore").write_text(".agent/\n.tmp/\n__pycache__/\n*.pyc\n")
    (sandbox / "README.md").write_text("# audit sandbox\n")

    # 预创建运行时目录结构（wfctl 和消息写入依赖）
    (sandbox / ".agent" / "instances").mkdir(parents=True, exist_ok=True)
    (sandbox / ".tmp").mkdir(parents=True, exist_ok=True)

    # git init
    _run(["git", "init"], sandbox)
    _run(["git", "config", "user.email", "auditor@localhost"], sandbox)
    _run(["git", "config", "user.name", "auditor"], sandbox)
    _run(["git", "add", "-A"], sandbox)
    _run(["git", "commit", "--allow-empty", "-m", "init"], sandbox)

    dest_skills_dir = sandbox / ".claude" / "skills"
    dest_skills_dir.mkdir(parents=True, exist_ok=True)

    copied: set[str] = set()

    # 复制主工作流（含递归子工作流）
    _copy_workflow_to_sandbox(workflow_yaml_path.parent, sandbox,
                              dest_skills_dir, skills_dir,
                              workflows_dir, copied, depth=1)

    # 复制全局 skills/（只复制未被工作流局部 Skill 覆盖的）
    if skills_dir and skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            dest = dest_skills_dir / skill_dir.name
            if skill_dir.is_dir() and not dest.exists():
                shutil.copytree(skill_dir, dest)

    # 契约
    if contracts_dir and contracts_dir.is_dir():
        dest_contracts = sandbox / ".claude" / "contracts"
        dest_contracts.mkdir(parents=True, exist_ok=True)
        for f in contracts_dir.glob("*.md"):
            shutil.copy2(f, dest_contracts / f.name)

    return sandbox


# ─── attack scenarios ───────────────────────────────────────

_findings: list[dict] = []


def _f(severity: str, attack_id: str, attack_desc: str,
       expected: str, actual: str, stages: list[str] | None = None) -> None:
    _findings.append({
        "severity": severity,
        "category": "live_audit",
        "attack": f"[{attack_id}] {attack_desc}",
        "stages_involved": stages or [],
        "finding": f"预期: {expected}；实际: {actual}",
        "expected": expected,
        "recommendation": "检查 wfctl 实现是否与规范一致",
    })


def _save_findings(sandbox: Path) -> None:
    """每次攻击后保存中间结果。"""
    report = sandbox / "live_findings.json"
    report.write_text(json.dumps(_findings, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def attack_sm1_loop_exhaustion(sandbox: Path, data: dict) -> None:
    """SM-1: 在每个确认点反复选择'继续完善'，验证 loop_exceeded 触发。"""
    stages = data.get("stages", [])
    edges = data.get("edges", [])
    # 找第一个 confirmation_point=true 且有 self-loop 的 stage
    cp_stages = [s for s in stages if isinstance(s, dict)
                 and s.get("confirmation_point")
                 and s.get("stage_id", "") not in (START_STAGE, END_STAGE)]

    for stage in cp_stages:
        sid = stage["stage_id"]
        self_loops = [e for e in edges if isinstance(e, dict)
                      and e.get("from") == sid and e.get("to") == sid
                      and e.get("condition") == "confirmed" and e.get("max_loop")]
        if not self_loops:
            continue

        max_loop = self_loops[0]["max_loop"]
        choice_val = self_loops[0].get("choice", "继续完善")

        # 创建实例
        result = _run_wfctl(["create", "--workflow",
                             f"{data['workflow_id']}@{data['version']}"], sandbox)
        instance_id = result.get("instance_id", "")
        if not instance_id:
            _f("critical", "SM-1", f"stage '{sid}' 循环到底",
               "wfctl create 成功", f"wfctl create 失败: {result}", [sid])
            continue

        instance_dir = sandbox / ".agent" / "instances" / instance_id

        # 驱动到目标 stage
        _drive_to_stage(sandbox, instance_id, instance_dir, sid)

        # 注入: 反复选"继续完善"直到 loop_counter 超限
        loop_exceeded_triggered = False
        for loop_i in range(max_loop + 2):
            # 模拟 SubAgent 产出 AWAITING_CONFIRM
            _write_message(instance_dir, "AWAITING_CONFIRM", sid,
                           confirm_questions=[{"question": "继续?", "options": [choice_val, "通过", "放弃"]}])
            _run_wfctl(["next", "--instance", instance_id], sandbox)

            # wfctl confirm --choice "继续完善"
            confirm_result = _run_wfctl(
                ["confirm", "--instance", instance_id, "--stage", sid,
                 "--choice", choice_val], sandbox)
            _run_wfctl(["next", "--instance", instance_id], sandbox)

            inst = _read_instance(instance_dir)
            if inst is None:
                break
            # 检查是否触发了 loop_exceeded
            if inst.get("status") == "FAILED":
                # 检查是否是 loop_exceeded 导致的
                stage_state = None
                for ss in inst.get("stages", []):
                    if isinstance(ss, dict):
                        stage_state = ss
                loop_exceeded_triggered = True
                break

        if not loop_exceeded_triggered:
            _f("critical", "SM-1", f"stage '{sid}' 循环到底 (max_loop={max_loop})",
               f"loop_counter >= {max_loop} 时触发 loop_exceeded → instance FAILED",
               f"循环 {max_loop + 1} 轮后未触发 loop_exceeded，实例状态异常",
               [sid])

        _run_wfctl(["terminate", "--instance", instance_id], sandbox)


def attack_sm2_all_reject(sandbox: Path, data: dict) -> None:
    """SM-2: 在所有确认点全选'放弃'，验证 rejected → 终态。"""
    stages = data.get("stages", [])
    # 构建 stage_id → confirmation_point 映射
    cp_map: dict[str, bool] = {}
    for s in stages:
        if isinstance(s, dict):
            cp_map[s.get("stage_id", "")] = s.get("confirmation_point", False)

    result = _run_wfctl(["create", "--workflow",
                         f"{data['workflow_id']}@{data['version']}"], sandbox)
    instance_id = result.get("instance_id", "")
    if not instance_id:
        _f("critical", "SM-2", "全部放弃",
           "wfctl create 成功", f"wfctl create 失败: {result}", [])
        return

    instance_dir = sandbox / ".agent" / "instances" / instance_id
    rejected_count = 0

    for _ in range(20):  # 安全上限
        next_result = _run_wfctl(["next", "--instance", instance_id], sandbox)
        actions = next_result.get("actions", [])

        confirm_action = None
        for a in actions:
            if isinstance(a, dict) and a.get("action") == "confirm":
                confirm_action = a
                break

        if not confirm_action:
            # 没有 confirm action，检查是否已经终态
            inst = _read_instance(instance_dir)
            if inst and inst.get("status") in ("COMPLETED", "FAILED"):
                break
            # 模拟非确认 stage 的 DONE；确认点 stage 写 AWAITING_CONFIRM
            spawn_action = None
            for a in actions:
                if isinstance(a, dict) and a.get("action") == "spawn":
                    spawn_action = a
                    break
            if spawn_action:
                sid = spawn_action.get("stage_id", "")
                if cp_map.get(sid, False):
                    # 确认点：写 AWAITING_CONFIRM 让 next 返回 confirm action
                    _write_message(instance_dir, "AWAITING_CONFIRM", sid,
                                   confirm_questions=[{"question": "继续?", "options": ["通过", "继续完善", "放弃"]}])
                else:
                    _write_message(instance_dir, "DONE", sid,
                                   report=f"mock done for {sid}")
                _run_wfctl(["next", "--instance", instance_id], sandbox)
                continue
            break

        # 有确认 action → 选第一个 rejected choice
        pending = confirm_action.get("pending", [])
        for p in pending:
            sid = p.get("stage_id", "")
            questions = p.get("questions", [])
            # 模拟 AWAITING_CONFIRM
            reject_choice = "放弃"
            for q in questions:
                if isinstance(q, dict):
                    opts = q.get("options", [])
                    for opt in opts:
                        if "弃" in str(opt) or "终止" in str(opt):
                            reject_choice = str(opt)
                            break

            _write_message(instance_dir, "AWAITING_CONFIRM", sid,
                           confirm_questions=questions)
            _run_wfctl(["next", "--instance", instance_id], sandbox)

            cr = _run_wfctl(["confirm", "--instance", instance_id,
                             "--stage", sid, "--choice", reject_choice], sandbox)
            _run_wfctl(["next", "--instance", instance_id], sandbox)
            rejected_count += 1

    inst = _read_instance(instance_dir)
    final_status = inst.get("status", "UNKNOWN") if inst else "NO_INSTANCE"
    if final_status not in ("COMPLETED", "FAILED"):
        _f("warning", "SM-2", "全部放弃",
           "全部 rejected 后实例应进入终态 (COMPLETED 或 FAILED)",
           f"终态={final_status}", [])

    _run_wfctl(["terminate", "--instance", instance_id], sandbox)


def attack_choice_mismatch(sandbox: Path, data: dict) -> None:
    """Choice 不匹配: wfctl confirm 传入 YAML edges 中不存在的 choice 值。"""
    stages = data.get("stages", [])
    # 找第一个 confirmation_point=true 的 stage
    cp_stages = [s for s in stages if isinstance(s, dict)
                 and s.get("confirmation_point")
                 and s.get("stage_id", "") not in (START_STAGE, END_STAGE)]
    if not cp_stages:
        return

    sid = cp_stages[0]["stage_id"]
    result = _run_wfctl(["create", "--workflow",
                         f"{data['workflow_id']}@{data['version']}"], sandbox)
    instance_id = result.get("instance_id", "")
    if not instance_id:
        return

    instance_dir = sandbox / ".agent" / "instances" / instance_id
    _drive_to_stage(sandbox, instance_id, instance_dir, sid)

    # 注入不存在的 choice
    fake_choice = "___NONEXISTENT_CHOICE___"
    _write_message(instance_dir, "AWAITING_CONFIRM", sid,
                   confirm_questions=[{"question": "ok?", "options": [fake_choice]}])
    _run_wfctl(["next", "--instance", instance_id], sandbox)

    cr = _run_wfctl(["confirm", "--instance", instance_id,
                     "--stage", sid, "--choice", fake_choice], sandbox)
    # 预期: wfctl 返回 error/instance_failed 而非静默接受
    if cr.get("status") not in ("error", "instance_failed"):
        _f("critical", "choice-mismatch", f"stage '{sid}' 不存在的 choice='{fake_choice}'",
           "wfctl confirm 应返回 error/instance_failed (choice 不存在于 YAML edges)",
           f"wfctl 返回: {json.dumps(cr, ensure_ascii=False)[:200]}",
           [sid])

    _run_wfctl(["terminate", "--instance", instance_id], sandbox)


def attack_if1_timeout(sandbox: Path, data: dict) -> None:
    """IF-1: 超时→retry→failure。不写任何消息，验证 wfctl 将 stage 置为 ERROR。"""
    # 找第一个有 retry > 0 且非确认点、非虚拟 stage
    stages = data.get("stages", [])
    candidates = [s for s in stages if isinstance(s, dict)
                  and s.get("stage_id", "") not in (START_STAGE, END_STAGE)
                  and not s.get("confirmation_point")
                  and s.get("retry", 0) > 0]
    if not candidates:
        return  # 没有可测的 stage

    sid = candidates[0]["stage_id"]

    # 修改 WORKFLOW.yaml 副本: 将 timeout_seconds 设为 1（使用结构化 YAML 读写）
    wf_dir = sandbox / ".claude" / "workflows" / f"{data['workflow_id']}@{data['version']}"
    yaml_path = wf_dir / "WORKFLOW.yaml"
    original_yaml = yaml_path.read_text(encoding="utf-8")

    # 读取 YAML，注入 timeout_seconds
    wf_data = yaml.safe_load(original_yaml)
    modified = False
    for stage in wf_data.get("stages", []):
        if isinstance(stage, dict) and stage.get("stage_id") == sid:
            stage["timeout_seconds"] = 1
            modified = True
            break

    if not modified:
        _f("info", "IF-1", f"stage '{sid}' 超时测试",
           "timeout_seconds 已注入", "无法修改 YAML（stage 未找到）", [sid])
        return

    yaml_path.write_text(yaml.dump(wf_data, allow_unicode=True, default_flow_style=False),
                         encoding="utf-8")

    result = _run_wfctl(["create", "--workflow",
                         f"{data['workflow_id']}@{data['version']}"], sandbox)
    instance_id = result.get("instance_id", "")
    if not instance_id:
        return

    instance_dir = sandbox / ".agent" / "instances" / instance_id
    _drive_to_stage(sandbox, instance_id, instance_dir, sid)

    # 不写任何消息，直接调 next → wfctl 应检测到超时
    time.sleep(2)  # 等待 timeout_seconds
    _run_wfctl(["next", "--instance", instance_id], sandbox)

    inst = _read_instance(instance_dir)
    if inst:
        for ss in inst.get("stages", []):
            if isinstance(ss, dict) and ss.get("stage_id") == sid:
                if ss.get("status") == "ERROR":
                    pass  # 符合预期
                else:
                    _f("warning", "IF-1", f"stage '{sid}' 超时→retry",
                       "超时后 stage 应进入 ERROR 状态",
                       f"实际状态: {ss.get('status')}", [sid])
                break

    _run_wfctl(["terminate", "--instance", instance_id], sandbox)
    # 恢复原始 YAML
    yaml_path.write_text(original_yaml, encoding="utf-8")


def attack_sw1_sub_workflow_failure(sandbox: Path, data: dict) -> None:
    """SW-1: 子工作流 FAILED → 父 stage ERROR。模拟子工作流失败，验证传播。"""
    stages = data.get("stages", [])
    wf_stages = [s for s in stages if isinstance(s, dict) and s.get("workflow")]
    if not wf_stages:
        return

    sid = wf_stages[0]["stage_id"]
    wf_ref = wf_stages[0]["workflow"]

    result = _run_wfctl(["create", "--workflow",
                         f"{data['workflow_id']}@{data['version']}"], sandbox)
    instance_id = result.get("instance_id", "")
    if not instance_id:
        return

    instance_dir = sandbox / ".agent" / "instances" / instance_id
    _drive_to_stage(sandbox, instance_id, instance_dir, sid)

    # 子工作流 spawn 后，模拟其 FAILED
    _run_wfctl(["next", "--instance", instance_id], sandbox)
    inst = _read_instance(instance_dir)
    if inst:
        for ss in inst.get("stages", []):
            if isinstance(ss, dict) and ss.get("stage_id") == sid:
                child_id = ss.get("child_instance_id")
                if child_id:
                    # 写子实例的 FAILED 状态到 .agent/instances/{child_id}/
                    # （与 _check_child_workflows 和 create_instance 的路径一致）
                    child_instance_dir = sandbox / ".agent" / "instances" / child_id
                    child_instance_dir.mkdir(parents=True, exist_ok=True)
                    child_json = {
                        "schema_version": "3.0.0",
                        "instance_id": child_id,
                        "workflow_id": wf_ref.split("@")[0],
                        "version": wf_ref.split("@")[1] if "@" in wf_ref else "1.0.0",
                        "goal": "mock",
                        "status": "FAILED",
                        "parent_instance_id": instance_id,
                        "consumed_message_ids": [],
                        "stages": [],
                    }
                    (child_instance_dir / "instance.json").write_text(
                        json.dumps(child_json, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    (child_instance_dir / "messages").mkdir(parents=True, exist_ok=True)

    _run_wfctl(["next", "--instance", instance_id], sandbox)
    inst = _read_instance(instance_dir)
    if inst:
        for ss in inst.get("stages", []):
            if isinstance(ss, dict) and ss.get("stage_id") == sid:
                if ss.get("status") != "ERROR":
                    _f("critical", "SW-1", f"子工作流 '{wf_ref}' FAILED",
                       "子工作流 FAILED 后父 stage 应进入 ERROR",
                       f"父 stage 状态: {ss.get('status')}", [sid])
                break

    _run_wfctl(["terminate", "--instance", instance_id], sandbox)


def attack_if2_merge_conflict(sandbox: Path, data: dict) -> None:
    """IF-2: 验证 conflict-resolver 可用性（仅存在性检查，不模拟实际冲突）。"""
    conflict_path = sandbox / ".claude" / "skills" / "conflict-resolver" / "SKILL.md"
    if not conflict_path.exists():
        has_parallel = any(isinstance(s, dict) and s.get("parallel")
                          for s in data.get("stages", []))
        if has_parallel:
            _f("warning", "IF-2", "合并冲突处理",
               "有并行 stage 的工作流应具备 conflict-resolver Skill",
               f"conflict-resolver SKILL.md 不存在: {conflict_path}",
               [])


# ─── driver ──────────────────────────────────────────────────


def _drive_to_stage(sandbox: Path, instance_id: str, instance_dir: Path,
                    target_stage_id: str, max_rounds: int = 30) -> bool:
    """驱动 wfctl 循环直到 target stage 就绪或被 spawn。

    返回值: True=到达目标 stage, False=未到达。
    """
    for _ in range(max_rounds):
        result = _run_wfctl(["next", "--instance", instance_id], sandbox)
        actions = result.get("actions", [])
        if not actions:
            inst = _read_instance(instance_dir)
            if inst and inst.get("status") in ("COMPLETED", "FAILED"):
                return False
            continue

        for action in actions:
            if not isinstance(action, dict):
                continue
            act_type = action.get("action", "")
            sid = action.get("stage_id", "")

            if act_type == "spawn":
                if sid == target_stage_id:
                    return True
                # 模拟上游 stage 的 DONE
                _write_message(instance_dir, "DONE", sid,
                               report=f"mock done for {sid}")
                break  # 重新调用 next

            elif act_type == "confirm":
                # 上游确认点，选第一个 confirmed choice 通过
                pending = action.get("pending", [])
                for p in pending:
                    psid = p.get("stage_id", "")
                    _write_message(instance_dir, "AWAITING_CONFIRM", psid,
                                   confirm_questions=p.get("questions", []))
                    _run_wfctl(["next", "--instance", instance_id], sandbox)
                    # 选"通过"或第一个 choice
                    pass_choice = "通过"
                    for q in p.get("questions", []):
                        if isinstance(q, dict):
                            for opt in q.get("options", []):
                                if "过" in str(opt) or "确认" in str(opt):
                                    pass_choice = str(opt)
                                    break
                    _run_wfctl(["confirm", "--instance", instance_id,
                                "--stage", psid, "--choice", pass_choice], sandbox)
                break  # 重新调用 next

            elif act_type in ("terminate",):
                return False

    return False


def run_attacks(sandbox: Path, data: dict,
                attacks: list[str] | None = None) -> list[dict]:
    """运行所有选择的攻击场景。"""
    global _findings
    _findings = []

    if attacks is None:
        attacks = ALL_ATTACKS

    attack_funcs = {
        "sm1": attack_sm1_loop_exhaustion,
        "sm2": attack_sm2_all_reject,
        "choice": attack_choice_mismatch,
        "timeout": attack_if1_timeout,
        "sw1": attack_sw1_sub_workflow_failure,
        "conflict": attack_if2_merge_conflict,
    }

    for atk in attacks:
        if atk in attack_funcs:
            try:
                attack_funcs[atk](sandbox, data)
            except Exception as e:
                _f("critical", atk, "攻击执行异常",
                   f"攻击 '{atk}' 正常完成", f"异常: {e}", [])
        _save_findings(sandbox)

    return _findings


# ─── CLI ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Workflow v3.0.0 实时审计引擎")
    parser.add_argument("--workflow-yaml", required=True, help="WORKFLOW.yaml 文件路径")
    parser.add_argument("--skills-dir", required=True, help="skills/ 目录路径（全局 + 局部）")
    parser.add_argument("--workflows-dir", help="workflows/ 目录路径（用于递归复制子工作流到沙箱）")
    parser.add_argument("--contracts-dir", help="contracts/ 目录路径")
    parser.add_argument("--attacks", default=",".join(ALL_ATTACKS),
                        help=f"要运行的攻击，逗号分隔。可用: {','.join(ALL_ATTACKS)}")
    parser.add_argument("--output", help="输出 JSON 文件路径（不指定则输出到 stdout）")
    parser.add_argument("--sandbox-dir", help="沙箱目录路径（不指定则自动创建临时目录）")
    parser.add_argument("--keep-sandbox", action="store_true", help="保留沙箱（调试用）")
    args = parser.parse_args()

    if yaml is None:
        print(json.dumps({"findings": [], "summary": {"error": "PyYAML not installed"}},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    yaml_path = Path(args.workflow_yaml).resolve()
    skills_dir = Path(args.skills_dir).resolve()
    contracts_dir = Path(args.contracts_dir).resolve() if args.contracts_dir else None

    if not yaml_path.exists():
        print(json.dumps({"findings": [], "summary": {"error": "WORKFLOW.yaml 不存在"}},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    attacks = [a.strip() for a in args.attacks.split(",") if a.strip()]

    # 检查 wfctl 可导入
    try:
        subprocess.run(
            [sys.executable, "-c", "import wfctl"],
            capture_output=True, timeout=5,
            env={**os.environ, "PYTHONPATH": str(WFCTL_PARENT)})
    except Exception:
        print(json.dumps({
            "findings": [],
            "summary": {"error": f"wfctl 模块不可导入——已尝试路径: {WFCTL_PARENT}"},
            "attacks_run": [],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    print("搭建沙箱...", file=sys.stderr)
    sandbox_dir = Path(args.sandbox_dir).resolve() if args.sandbox_dir else None
    workflows_dir = Path(args.workflows_dir).resolve() if args.workflows_dir else None
    sandbox = setup_sandbox(yaml_path, skills_dir, contracts_dir,
                            workflows_dir=workflows_dir, sandbox_dir=sandbox_dir)

    print("运行攻击场景...", file=sys.stderr)
    findings = run_attacks(sandbox, data, attacks)

    # 汇总
    critical = sum(1 for f in findings if f["severity"] == "critical")
    warning = sum(1 for f in findings if f["severity"] == "warning")
    info = sum(1 for f in findings if f["severity"] == "info")
    overall = "fail" if critical > 0 else ("conditional_pass" if warning > 0 else "pass")

    output = {
        "findings": findings,
        "summary": {
            "mode": "live",
            "critical_count": critical,
            "warning_count": warning,
            "info_count": info,
            "overall_result": overall,
        },
        "attacks_run": attacks,
        "sandbox": str(sandbox) if args.keep_sandbox else None,
    }

    if not args.keep_sandbox:
        try:
            shutil.rmtree(sandbox, ignore_errors=True)
        except Exception:
            pass

    output_json = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"审计结果已写入: {args.output}", file=sys.stderr)
    else:
        print(output_json)
    sys.exit(0 if overall != "fail" else 1)


if __name__ == "__main__":
    main()
