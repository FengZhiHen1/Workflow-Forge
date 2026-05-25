"""CLI resolve 冒烟测试。"""

from pathlib import Path

import pytest

from cli.workflow.resolve import _handle_resolve


class FakeArgs:
    def __init__(self, workflow=None):
        self.workflow = workflow


def test_resolve_no_args(monkeypatch, tmp_path: Path):
    """无参数时扫描工作流列表。"""
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".claude" / "workflows" / "wf1").mkdir(parents=True)
    (repo / ".claude" / "workflows" / "wf1" / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: wf1\nversion: "1.0.0"\nmax_parallel_agents: 1\nstages: []\nedges: []',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    result = _handle_resolve(FakeArgs())
    assert "workflows" in result
    assert len(result["workflows"]) == 1
    assert result["workflows"][0]["workflow_id"] == "wf1"
