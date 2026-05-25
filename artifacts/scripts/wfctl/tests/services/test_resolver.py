"""测试 resolver 服务。"""

from pathlib import Path

import pytest

from infrastructure.errors import InputError
from services.resolver import resolve, resolve_workflow


def test_resolve_empty_when_no_workflows(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".claude" / "workflows").mkdir()
    monkeypatch.chdir(repo)
    result = resolve()
    assert result == []


def test_resolve_scans_workflows(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    wf_dir = repo / ".claude" / "workflows" / "wf1"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: wf1\nversion: "1.0.0"\nmax_parallel_agents: 1\nstages: []\nedges: []',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    result = resolve()
    assert len(result) == 1
    assert result[0]["workflow_id"] == "wf1"


def test_resolve_with_frontmatter(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    wf_dir = repo / ".claude" / "workflows" / "wf1"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.md").write_text(
        "---\ndescription: Test flow\ntags: [test, demo]\n---\n# WF\n",
        encoding="utf-8",
    )
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: wf1\nversion: "1.0.0"\nmax_parallel_agents: 1\nstages: []\nedges: []',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    result = resolve()
    assert result[0]["description"] == "Test flow"
    assert result[0]["tags"] == ["test", "demo"]


def test_resolve_workflow_success(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    wf_dir = repo / ".claude" / "workflows" / "wf1"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: wf1\nversion: "1.0.0"\nmax_parallel_agents: 2\nanchor_prefix: "wf"\nstages:\n  - stage_id: s01\n    name: "A"\n    skill_id: sk\n    mandatory: true\n\nedges: []',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    result = resolve_workflow("wf1")
    assert result["workflow_id"] == "wf1"
    assert result["max_parallel_agents"] == 2


def test_resolve_workflow_not_found(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    monkeypatch.chdir(repo)
    with pytest.raises(InputError) as exc_info:
        resolve_workflow("nonexistent")
    assert exc_info.value.code == "WORKFLOW_NOT_FOUND"
