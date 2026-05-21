"""CLI next 冒烟测试。"""

import json
import subprocess
from pathlib import Path

import pytest

from cli.next_cmd import _handle_next


class FakeArgs:
    def __init__(self, instance="test-001"):
        self.instance = instance


def test_next_cmd(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 1\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "A"\n    skill_id: sk\n    mandatory: true\n    confirmation_point: false\n'
        'edges:\n  - from: s00-workflow-start\n    to: s01\n    condition: always\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    # 创建 worktree 和 instance
    wt = repo / ".tmp" / "worktrees" / "instance-test-001"
    subprocess.run(["git", "worktree", "add", str(wt), "HEAD"], cwd=str(repo), check=True, capture_output=True)
    inst_dir = repo / ".agent" / "instances" / "test-001"
    inst_dir.mkdir(parents=True)
    data = {
        "schema_version": "3.0.0",
        "instance_id": "test-001",
        "workflow_id": "test-flow",
        "version": "1.0.0",
        "status": "ACTIVE",
        "consumed_message_ids": [],
        "stages": [
            {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE"},
            {"stage_id": "s01", "stage_instance_id": "s01", "status": "PENDING", "attempt_count": 0, "loop_counter": 0},
        ],
    }
    (inst_dir / "instance.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.chdir(repo)
    result = _handle_next(FakeArgs(instance="test-001"))
    assert result["status"] == "ok"
    assert "actions" in result
    assert any(a["action"] == "spawn" and a["stage_id"] == "s01" for a in result["actions"])
