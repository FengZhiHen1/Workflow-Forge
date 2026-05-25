"""pytest fixtures。"""

import json
from pathlib import Path

import pytest

from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)


@pytest.fixture
def temp_git_repo(tmp_path: Path):
    """创建临时 git 仓库（含 .claude/ 目录结构），返回项目根 Path。"""
    import subprocess

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    (repo / ".claude" / "workflows").mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, capture_output=True)

    # 初始提交
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    return repo


@pytest.fixture
def sample_workflow_yaml(temp_git_repo: Path):
    """在临时仓库中写入示例 WORKFLOW.yaml，返回路径。"""
    wf_dir = temp_git_repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True)
    yaml_content = """
schema_version: "3.0.0"
workflow_id: "test-flow"
version: "1.0.0"
max_parallel_agents: 4
anchor_prefix: "wf"

stages:
  - stage_id: s00-workflow-start
    name: "开始"

  - stage_id: s01
    name: "分析"
    skill_id: analyst
    mandatory: true

  - stage_id: s02
    name: "设计"
    skill_id: designer
    mandatory: true

  - stage_id: s99-workflow-end
    name: "结束"

edges:
  - from: s00-workflow-start
    to: s01
    condition: always

  - from: s01
    to: s02
    condition: success

  - from: s02
    to: s99-workflow-end
    condition: success
"""
    yaml_path = wf_dir / "WORKFLOW.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return yaml_path


@pytest.fixture
def sample_instance_json(temp_git_repo: Path):
    """写入示例 instance.json，返回路径。"""
    inst_dir = temp_git_repo / ".agent" / "instances" / "20260517-001"
    inst_dir.mkdir(parents=True)
    data = {
        "schema_version": "3.0.0",
        "instance_id": "20260517-001",
        "workflow_id": "test-flow",
        "version": "1.0.0",
        "goal": "test goal",
        "status": "ACTIVE",
        "consumed_message_ids": [],
        "stages": [
            {"stage_id": "s00-workflow-start", "status": "DONE"},
            {"stage_id": "s01", "status": "PENDING"},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s99-workflow-end", "status": "PENDING"},
        ],
    }
    path = inst_dir / "instance.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
