"""测试 status_builder — 项目与实例状态聚合视图构建。"""

import json
from pathlib import Path

import pytest

from services.status_builder import build_instance_status, build_project_status


def _init_repo_root(root: Path):
    """在指定目录下创建 .claude/ 和 .agent/ 基础设施。"""
    (root / ".claude").mkdir(parents=True)
    (root / ".agent").mkdir(parents=True)
    (root / ".claude" / "workflows").mkdir(parents=True)
    (root / ".tmp" / "worktrees").mkdir(parents=True)


def _write_instance(root: Path, instance_id: str, status: str, stages: list[dict],
                    workflow_id: str = "test-flow", version: str = "1.0.0"):
    inst_dir = root / ".agent" / "instances" / instance_id
    inst_dir.mkdir(parents=True)
    data = {
        "schema_version": "3.0.0",
        "instance_id": instance_id,
        "workflow_id": workflow_id,
        "version": version,
        "goal": "test goal",
        "status": status,
        "consumed_message_ids": [],
        "stages": stages,
    }
    (inst_dir / "instance.json").write_text(json.dumps(data), encoding="utf-8")


def _write_workflow_yaml(root: Path, workflow_id: str):
    wf_dir = root / ".claude" / "workflows" / workflow_id
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        f'schema_version: "3.0.0"\nworkflow_id: {workflow_id}\nversion: "1.0.0"\n'
        f'max_parallel_agents: 4\nanchor_prefix: "wf"\n'
        f'stages:\n'
        f'  - stage_id: s00-workflow-start\n    name: "开始"\n'
        f'  - stage_id: s01\n    name: "A"\n    skill_id: skill-a\n    mandatory: true\n\n'
        f'  - stage_id: s02\n    name: "B"\n    skill_id: skill-b\n    mandatory: true\n\n'
        f'  - stage_id: s99-workflow-end\n    name: "结束"\n'
        f'edges:\n'
        f'  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        f'  - from: s01\n    to: s02\n    condition: success\n'
        f'  - from: s02\n    to: s99-workflow-end\n    condition: success\n',
        encoding="utf-8",
    )


# ─── build_project_status ─────────────────────────────────────────────


def test_empty_project(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    monkeypatch.chdir(root)
    result = build_project_status()
    assert result == {
        "active_instances": [],
        "paused_instances": [],
        "recent_completed": [],
        "recent_failed": [],
    }


def test_active_instance(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-001", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s99-workflow-end", "status": "PENDING"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    assert len(result["active_instances"]) == 1
    inst = result["active_instances"][0]
    assert inst["workflow_id"] == "test-flow"
    assert inst["stages_done"] == 2
    assert inst["stages_total"] == 4
    assert len(result["paused_instances"]) == 0


def test_paused_instance(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-002", "PAUSED", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "RUNNING"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    assert len(result["paused_instances"]) == 1
    inst = result["paused_instances"][0]
    assert inst["status"] == "PAUSED"
    assert inst["stages_done"] == 1
    assert inst["stages_total"] == 2


def test_completed_instance(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-003", "COMPLETED", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s99-workflow-end", "status": "DONE"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    assert len(result["recent_completed"]) == 1
    assert result["recent_completed"][0] == "20260524-003"


def test_failed_instance(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-004", "FAILED", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "ERROR"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    assert len(result["recent_failed"]) == 1
    assert result["recent_failed"][0] == "20260524-004"


def test_active_instance_blocked_by_awaiting_confirm(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-005", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s02", "status": "AWAITING_CONFIRM", "output_message_id": "msg-001"},
        {"stage_id": "s99-workflow-end", "status": "PENDING"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    inst = result["active_instances"][0]
    blocked = inst["blocked_by"]
    assert len(blocked) == 1
    assert blocked[0]["stage_id"] == "s02"
    assert blocked[0]["status"] == "AWAITING_CONFIRM"


def test_active_instance_blocked_by_error(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-006", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "ERROR", "output_message_id": "msg-err"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    inst = result["active_instances"][0]
    blocked = inst["blocked_by"]
    assert len(blocked) == 1
    assert blocked[0]["status"] == "ERROR"


def test_multiple_instances(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-010", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "RUNNING"},
    ])
    _write_instance(root, "20260524-011", "COMPLETED", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s99-workflow-end", "status": "DONE"},
    ])
    _write_instance(root, "20260524-012", "FAILED", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "ERROR"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    assert len(result["active_instances"]) == 1
    assert len(result["recent_completed"]) == 1
    assert len(result["recent_failed"]) == 1


def test_corrupted_instance_skipped(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    inst_dir = root / ".agent" / "instances" / "20260524-bad"
    inst_dir.mkdir(parents=True)
    (inst_dir / "instance.json").write_text("not valid json", encoding="utf-8")
    _write_instance(root, "20260524-020", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
    ])
    monkeypatch.chdir(root)

    result = build_project_status()
    assert len(result["active_instances"]) == 1


# ─── build_instance_status ────────────────────────────────────────────


def test_instance_status_not_found(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    monkeypatch.chdir(root)
    result = build_instance_status("nonexistent")
    assert result["status"] == "error"
    assert "not found" in result["reason"]


def test_instance_status_summary(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-030", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "RUNNING"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99-workflow-end", "status": "PENDING"},
    ])
    monkeypatch.chdir(root)

    result = build_instance_status("20260524-030")
    assert result["instance_id"] == "20260524-030"
    assert result["status"] == "ACTIVE"
    summary = result["stages_summary"]
    assert summary["total"] == 5
    assert summary["done"] == 1
    assert summary["running"] == 1
    assert summary["pending"] == 3


def test_instance_status_awaiting_confirm_stage(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-031", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s02", "status": "AWAITING_CONFIRM",
         "output_message_id": "msg-c001", "confirm_questions": ["确认继续?"]},
    ])
    monkeypatch.chdir(root)

    result = build_instance_status("20260524-031")
    awaiting = [s for s in result["stages"] if s["stage_id"] == "s02"]
    assert len(awaiting) == 1
    assert awaiting[0]["status"] == "AWAITING_CONFIRM"
    assert awaiting[0]["confirm_questions"] == ["确认继续?"]


def test_instance_status_error_stage_with_attempts(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-032", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "ERROR", "attempt_count": 2, "output_message_id": "msg-e01"},
    ])
    monkeypatch.chdir(root)

    result = build_instance_status("20260524-032")
    error_stages = [s for s in result["stages"] if s["stage_id"] == "s01"]
    assert len(error_stages) == 1
    assert error_stages[0]["status"] == "ERROR"
    assert error_stages[0]["attempt_count"] == 2


def test_done_stages_filtered_from_details(monkeypatch, tmp_path: Path):
    root = tmp_path / "project"
    _init_repo_root(root)
    _write_instance(root, "20260524-033", "ACTIVE", [
        {"stage_id": "s00-workflow-start", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s02", "status": "PENDING"},
    ])
    monkeypatch.chdir(root)

    result = build_instance_status("20260524-033")
    stage_ids = [s["stage_id"] for s in result["stages"]]
    assert "s00-workflow-start" not in stage_ids
    assert "s01" not in stage_ids
    assert "s02" in stage_ids
