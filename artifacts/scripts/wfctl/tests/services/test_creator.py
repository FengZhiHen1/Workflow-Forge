"""测试 creator 服务。"""

import json
from pathlib import Path

import pytest

from core.errors import InputError
from services.creator import create_instance


def test_create_instance_success(monkeypatch, tmp_path: Path):
    import subprocess

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 2\nanchor_prefix: "wf"\nstages:\n  - stage_id: s00-workflow-start\n    name: "开始"\n  - stage_id: s01\n    name: "分析"\n    skill_id: analyst\n    mandatory: true\n    confirmation_point: false\nedges:\n  - from: s00-workflow-start\n    to: s01\n    condition: always',
        encoding="utf-8",
    )

    # 初始化 git 仓库
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    monkeypatch.chdir(repo)
    result = create_instance("test-flow", goal="test goal")

    assert result["status"] == "ok"
    assert result["workflow_id"] == "test-flow"
    inst_id = result["instance_id"]

    # 验证 instance.json
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    assert inst_path.exists()
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    assert data["goal"] == "test goal"
    assert data["status"] == "ACTIVE"
    assert len(data["stages"]) == 2

    # 验证 identity 文件
    identity_file = repo / ".tmp" / "worktrees" / f"instance-{inst_id}" / ".wfctl_identity.json"
    assert identity_file.exists()


def test_create_instance_workflow_not_found(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    monkeypatch.chdir(repo)
    with pytest.raises(InputError) as exc_info:
        create_instance("missing")
    assert exc_info.value.code == "WORKFLOW_NOT_FOUND"
