"""CLI create 冒烟测试。"""

import subprocess
from pathlib import Path

import pytest

from cli.workflow.create import _handle_create


class FakeArgs:
    def __init__(self, workflow="test-flow", goal=""):
        self.workflow = workflow
        self.goal = goal


def test_create_cmd(monkeypatch, tmp_path: Path):
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
        '  - stage_id: s01\n    name: "A"\n    skill_id: sk\n    mandatory: true\n\n'
        'edges:\n  - from: s00-workflow-start\n    to: s01\n    condition: always\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    monkeypatch.chdir(repo)
    result = _handle_create(FakeArgs(workflow="test-flow", goal="test goal"))
    assert result["status"] == "ok"
    assert result["workflow_id"] == "test-flow"
    assert "instance_id" in result


def test_create_cmd_with_version(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    wf_dir = repo / ".claude" / "workflows" / "wf"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: wf\nversion: "2.0.0"\nmax_parallel_agents: 1\nstages: []\nedges: []',
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    monkeypatch.chdir(repo)
    result = _handle_create(FakeArgs(workflow="wf@2.0.0"))
    assert result["status"] == "ok"
