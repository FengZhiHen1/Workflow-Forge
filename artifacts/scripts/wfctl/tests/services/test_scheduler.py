"""测试 scheduler 服务。"""

import json
import subprocess
from pathlib import Path

import pytest

from core.schema.interface import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)
from services.scheduler import run_next, run_sync


def _make_workflow_spec() -> WorkflowSpec:
    return WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-flow",
        version="1.0.0",
        max_parallel_agents=4,
        anchor_prefix="wf",
        stages=[
            StageSpec(stage_id="s00-workflow-start", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, retry=1),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True, exclusive=True),
            StageSpec(stage_id="s99-workflow-end", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00-workflow-start", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s99-workflow-end", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s99-workflow-end", condition=EdgeCondition.CONFIRMED, choice="确认继续?"),
            EdgeSpec(from_stage="s03", to_stage="s99-workflow-end", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.FAILURE, max_loop=2),
        ],
    )


def _init_git_repo(path: Path):
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def _setup_instance(repo: Path, instance_id: str, stages_status: dict):
    inst_dir = repo / ".agent" / "instances" / instance_id
    inst_dir.mkdir(parents=True)
    stages = []
    for sid, status in stages_status.items():
        stages.append({
            "stage_id": sid,
            "stage_instance_id": sid,
            "status": status,
            "attempt_count": 0,
            "loop_counter": 0,
            "output_message_id": None,
            "child_instance_id": None,
            "fan_out_target": None,
        })
    data = {
        "schema_version": "3.0.0",
        "instance_id": instance_id,
        "workflow_id": "test-flow",
        "version": "1.0.0",
        "goal": "test",
        "status": "ACTIVE",
        "consumed_message_ids": [],
        "stages": stages,
    }
    (inst_dir / "instance.json").write_text(json.dumps(data), encoding="utf-8")
    return data


@pytest.fixture
def setup_repo(tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "A"\n    skill_id: skill-a\n    mandatory: true\n    confirmation_point: false\n    retry: 1\n'
        '  - stage_id: s02\n    name: "B"\n    skill_id: skill-b\n    mandatory: true\n    confirmation_point: false\n'
        '  - stage_id: s03\n    name: "C"\n    skill_id: skill-c\n    mandatory: true\n    confirmation_point: false\n    exclusive: true\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s02\n    condition: success\n'
        '  - from: s01\n    to: s03\n    condition: success\n'
        '  - from: s02\n    to: s99-workflow-end\n    condition: success\n'
        '  - from: s02\n    to: s99-workflow-end\n    condition: confirmed\n    choice: "确认继续?"\n'
        '  - from: s03\n    to: s99-workflow-end\n    condition: success\n'
        '  - from: s01\n    to: s02\n    condition: failure\n    max_loop: 2\n',
        encoding="utf-8",
    )
    _init_git_repo(repo)
    return repo


@pytest.fixture
def setup_instance_worktree(setup_repo: Path):
    repo = setup_repo
    wt = repo / ".tmp" / "worktrees" / "instance-sched-001"
    subprocess.run(
        ["git", "worktree", "add", str(wt), "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    _setup_instance(repo, "sched-001", {
        "s00-workflow-start": "DONE",
        "s01": "PENDING",
        "s02": "PENDING",
        "s03": "PENDING",
        "s99-workflow-end": "PENDING",
    })
    return repo, "sched-001"


def test_next_ready_single_stage(setup_instance_worktree, monkeypatch):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    result = run_next(inst_id)
    assert result["status"] == "ok"
    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    assert len(spawn_actions) == 1
    assert spawn_actions[0]["stage_id"] == "s01"


def test_next_multiple_ready(setup_instance_worktree, monkeypatch):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    # 将 s01 设为 DONE，触发 s02 和 s03 同时就绪
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    for s in data["stages"]:
        if s["stage_id"] == "s01":
            s["status"] = "DONE"
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"
    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    # s02 和 s03 并发就绪，应拆分为 stage 级 worktree
    assert len(spawn_actions) == 2
    stage_ids = {a["stage_id"] for a in spawn_actions}
    assert stage_ids == {"s02", "s03"}


def test_next_exclusive_blocks(setup_instance_worktree, monkeypatch):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    # s03 为 exclusive 且 RUNNING，s01 DONE 后 s02 就绪但应被阻塞
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    for s in data["stages"]:
        if s["stage_id"] == "s01":
            s["status"] = "DONE"
        if s["stage_id"] == "s03":
            s["status"] = "RUNNING"
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_next(inst_id)
    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    # exclusive RUNNING 阻塞所有新 spawn
    assert len(spawn_actions) == 0


def test_next_error_retry(setup_instance_worktree, monkeypatch):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    for s in data["stages"]:
        if s["stage_id"] == "s01":
            s["status"] = "ERROR"
            s["attempt_count"] = 0
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"
    retry_actions = [a for a in result["actions"] if a["action"] == "retry"]
    assert len(retry_actions) == 1
    assert retry_actions[0]["attempt"] == 1


def test_next_error_failure_edge(setup_instance_worktree, monkeypatch):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    for s in data["stages"]:
        if s["stage_id"] == "s01":
            s["status"] = "ERROR"
            s["attempt_count"] = 1  # retry=1，已耗尽
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"
    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    assert len(spawn_actions) == 1
    assert spawn_actions[0]["reason"] == "failure-edge"


def test_next_confirm_aggregation(setup_instance_worktree, monkeypatch, tmp_path: Path):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    # 写入一个 AWAITING_CONFIRM 消息
    messages_dir = repo / ".agent" / "instances" / inst_id / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    msg = {
        "message_id": "msg-c001",
        "instance_id": inst_id,
        "stage_id": "s02",
        "status": "AWAITING_CONFIRM",
        "report": "need confirm",
        "confirm_questions": ["确认继续?"],
        "timestamp": "2026-05-17T10:00:00+0800",
    }
    (messages_dir / "msg-c001.json").write_text(json.dumps(msg), encoding="utf-8")

    # 将 s01 设为 DONE，s02 消费消息后变为 AWAITING_CONFIRM
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    for s in data["stages"]:
        if s["stage_id"] == "s01":
            s["status"] = "DONE"
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"
    confirm_actions = [a for a in result["actions"] if a["action"] == "confirm"]
    assert len(confirm_actions) == 1
    assert confirm_actions[0]["pending"][0]["stage_id"] == "s02"


def test_sync_only_consume(setup_instance_worktree, monkeypatch, tmp_path: Path):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    messages_dir = repo / ".agent" / "instances" / inst_id / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    msg = {
        "message_id": "msg-s001",
        "instance_id": inst_id,
        "stage_id": "s01",
        "status": "DONE",
        "report": "done",
        "timestamp": "2026-05-17T10:00:00+0800",
    }
    (messages_dir / "msg-s001.json").write_text(json.dumps(msg), encoding="utf-8")

    result = run_sync(inst_id)
    assert result["status"] == "ok"
    assert len(result["changes"]) == 1
    # sync 不返回 spawn 等 actions
    assert "actions" not in result


def test_next_instance_not_active(setup_instance_worktree, monkeypatch):
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["status"] = "FAILED"
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "error"


def test_next_continue_action_same_skill(setup_instance_worktree, monkeypatch):
    """同 skill_id 的下游 stage 应生成 continue action 而非 spawn。"""
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)

    # 将 s01 和 s02 设为相同 skill_id 的 stage
    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "需求收集"\n    skill_id: design-tech-stack\n    mandatory: true\n    confirmation_point: true\n'
        '  - stage_id: s02\n    name: "架构选型"\n    skill_id: design-tech-stack\n    mandatory: true\n    confirmation_point: true\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s02\n    condition: confirmed\n    choice: "通过"\n'
        '  - from: s02\n    to: s99-workflow-end\n    condition: confirmed\n    choice: "通过"\n',
        encoding="utf-8",
    )

    # 设 s01 已完成且携带 system_agent_id
    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    # 重建 stages 以匹配新 YAML
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01", "stage_instance_id": "s01", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None,
         "system_agent_id": "agent-001"},
        {"stage_id": "s02", "stage_instance_id": "s02", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    # 写入 running_agents.json
    agents_path = repo / ".agent" / "running_agents.json"
    agents_path.parent.mkdir(parents=True, exist_ok=True)
    agents_path.write_text(json.dumps([
        {"skill_id": "design-tech-stack", "system_agent_id": "agent-001", "stage_id": "s01", "instance_id": inst_id},
    ]), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"

    actions = result["actions"]
    continue_actions = [a for a in actions if a["action"] == "continue"]
    spawn_actions = [a for a in actions if a["action"] == "spawn"]
    assert len(continue_actions) == 1, f"Expected 1 continue, got {continue_actions}"
    assert len(spawn_actions) == 0, f"Expected 0 spawn, got {spawn_actions}"
    assert continue_actions[0]["stage_id"] == "s02"
    assert continue_actions[0]["skill_id"] == "design-tech-stack"
    assert continue_actions[0]["system_agent_id"] == "agent-001"


def test_next_spawn_action_different_skill(setup_instance_worktree, monkeypatch):
    """不同 skill_id 的下游 stage 仍应生成 spawn action。"""
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)

    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "需求收集"\n    skill_id: design-tech-stack\n    mandatory: true\n    confirmation_point: true\n'
        '  - stage_id: s02\n    name: "审查"\n    skill_id: compliance-reviewer\n    mandatory: true\n    confirmation_point: false\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s02\n    condition: confirmed\n    choice: "通过"\n'
        '  - from: s02\n    to: s99-workflow-end\n    condition: success\n',
        encoding="utf-8",
    )

    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01", "stage_instance_id": "s01", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None,
         "system_agent_id": "agent-001"},
        {"stage_id": "s02", "stage_instance_id": "s02", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    # 写入 running_agents.json（同 skill 在 s01）
    agents_path = repo / ".agent" / "running_agents.json"
    agents_path.parent.mkdir(parents=True, exist_ok=True)
    agents_path.write_text(json.dumps([
        {"skill_id": "design-tech-stack", "system_agent_id": "agent-001", "stage_id": "s01", "instance_id": inst_id},
    ]), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"

    actions = result["actions"]
    continue_actions = [a for a in actions if a["action"] == "continue"]
    spawn_actions = [a for a in actions if a["action"] == "spawn"]
    assert len(continue_actions) == 0, f"Expected 0 continue, got {continue_actions}"
    assert len(spawn_actions) == 1, f"Expected 1 spawn, got {spawn_actions}"
    assert spawn_actions[0]["stage_id"] == "s02"
    assert spawn_actions[0]["skill_id"] == "compliance-reviewer"


def test_next_continue_writes_continued_to(setup_instance_worktree, monkeypatch):
    """continue action 应在上一 stage 写入 continued_to 字段。"""
    repo, inst_id = setup_instance_worktree
    monkeypatch.chdir(repo)

    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "WORKFLOW.yaml").write_text(
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "A"\n    skill_id: shared-skill\n    mandatory: true\n    confirmation_point: true\n'
        '  - stage_id: s02\n    name: "B"\n    skill_id: shared-skill\n    mandatory: true\n    confirmation_point: true\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s02\n    condition: confirmed\n    choice: "通过"\n'
        '  - from: s02\n    to: s99-workflow-end\n    condition: confirmed\n    choice: "通过"\n',
        encoding="utf-8",
    )

    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01", "stage_instance_id": "s01", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None,
         "system_agent_id": "agent-002"},
        {"stage_id": "s02", "stage_instance_id": "s02", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    agents_path = repo / ".agent" / "running_agents.json"
    agents_path.parent.mkdir(parents=True, exist_ok=True)
    agents_path.write_text(json.dumps([
        {"skill_id": "shared-skill", "system_agent_id": "agent-002", "stage_id": "s01", "instance_id": inst_id},
    ]), encoding="utf-8")

    result = run_next(inst_id)
    assert result["status"] == "ok"

    # 重新加载实例，验证 s01.continued_to 和 s02.system_agent_id
    loaded = json.loads(inst_path.read_text(encoding="utf-8"))
    s01 = next(s for s in loaded["stages"] if s["stage_id"] == "s01")
    s02 = next(s for s in loaded["stages"] if s["stage_id"] == "s02")
    assert s01.get("continued_to") == "s02"
    assert s02.get("system_agent_id") == "agent-002"


# ─── loop_exceeded 收敛测试 ───────────────────────────────────────────


def _write_wf_yaml(repo: Path, yaml_text: str):
    wf_dir = repo / ".claude" / "workflows" / "test-flow"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "WORKFLOW.yaml").write_text(yaml_text, encoding="utf-8")


def _make_simple_repo(tmp_path_factory, monkeypatch_factory):
    """创建一个不含 worktree 的简单仓库，绕过 git worktree 锁干扰。"""
    import subprocess

    repo = tmp_path_factory.mktemp("repo")
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)
    return repo


def test_next_loop_exceeded_converges_to_report(tmp_path, monkeypatch):
    """loop_exceeded 边应收敛到 s13-report 而非终止。"""
    import subprocess

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    inst_id = "loop-001"
    _setup_instance(repo, inst_id, {
        "s00-workflow-start": "DONE",
        "s01": "ERROR",
        "s02": "PENDING",
        "s13-report": "PENDING",
        "s99-workflow-end": "PENDING",
    })

    _write_wf_yaml(repo,
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "验证"\n    skill_id: validator\n    mandatory: true\n    confirmation_point: false\n    retry: 3\n'
        '  - stage_id: s02\n    name: "下一阶段"\n    skill_id: next-step\n    mandatory: true\n    confirmation_point: false\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        '  - stage_id: s13-report\n    name: "报告"\n    skill_id: reporter\n    mandatory: true\n    confirmation_point: true\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s02\n    condition: success\n'
        '  - from: s01\n    to: s02\n    condition: failure\n    max_loop: 3\n'
        '  - from: s01\n    to: s13-report\n    condition: loop_exceeded\n'
        '  - from: s02\n    to: s99-workflow-end\n    condition: success\n'
        '  - from: s13-report\n    to: s99-workflow-end\n    condition: confirmed\n',
    )

    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01", "stage_instance_id": "s01", "status": "ERROR",
         "attempt_count": 3, "loop_counter": 3, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s02", "stage_instance_id": "s02", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s13-report", "stage_instance_id": "s13-report", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.chdir(repo)
    result = run_next(inst_id)
    assert result["status"] == "ok"

    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    terminate_actions = [a for a in result["actions"] if a["action"] == "terminate"]

    assert len(spawn_actions) >= 1
    spawned_stages = {a["stage_id"] for a in spawn_actions}
    assert "s13-report" in spawned_stages
    assert len(terminate_actions) == 0


def test_next_loop_exceeded_converges_to_end(tmp_path, monkeypatch):
    """某些 stage 的 loop_exceeded 边应终止到 s99（如 init/contract）。"""
    import subprocess

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    inst_id = "loop-002"
    _setup_instance(repo, inst_id, {
        "s00-workflow-start": "DONE",
        "s01-init": "ERROR",
        "s99-workflow-end": "PENDING",
    })

    _write_wf_yaml(repo,
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01-init\n    name: "初始化"\n    skill_id: init\n    mandatory: true\n    confirmation_point: true\n    retry: 1\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01-init\n    condition: always\n'
        '  - from: s01-init\n    to: s99-workflow-end\n    condition: confirmed\n    choice: "确认"\n'
        '  - from: s01-init\n    to: s99-workflow-end\n    condition: rejected\n    choice: "放弃"\n'
        '  - from: s01-init\n    to: s99-workflow-end\n    condition: loop_exceeded\n',
    )

    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01-init", "stage_instance_id": "s01-init", "status": "ERROR",
         "attempt_count": 1, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.chdir(repo)
    result = run_next(inst_id)
    assert result["status"] == "ok"

    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    assert len(spawn_actions) >= 1
    assert any(a["stage_id"] == "s99-workflow-end" for a in spawn_actions)


def test_next_max_loop_zero_boundary(tmp_path, monkeypatch):
    """max_loop=0 时，首次失败即走 loop_exceeded 边。"""
    import subprocess

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    inst_id = "loop-003"
    _setup_instance(repo, inst_id, {
        "s00-workflow-start": "DONE",
        "s01": "ERROR",
        "s13-report": "PENDING",
        "s99-workflow-end": "PENDING",
    })

    _write_wf_yaml(repo,
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "A"\n    skill_id: skill-a\n    mandatory: true\n    confirmation_point: false\n    retry: 0\n'
        '  - stage_id: s13-report\n    name: "报告"\n    skill_id: reporter\n    mandatory: true\n    confirmation_point: true\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s99-workflow-end\n    condition: success\n'
        '  - from: s01\n    to: s99-workflow-end\n    condition: failure\n    max_loop: 0\n'
        '  - from: s01\n    to: s13-report\n    condition: loop_exceeded\n'
        '  - from: s13-report\n    to: s99-workflow-end\n    condition: confirmed\n',
    )

    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01", "stage_instance_id": "s01", "status": "ERROR",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s13-report", "stage_instance_id": "s13-report", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.chdir(repo)
    result = run_next(inst_id)
    assert result["status"] == "ok"

    spawn_actions = [a for a in result["actions"] if a["action"] == "spawn"]
    loop_exceeded = [a for a in spawn_actions if a["reason"] == "loop-exceeded"]
    assert len(loop_exceeded) >= 1
    assert any(a["stage_id"] == "s13-report" for a in loop_exceeded)


def test_next_error_without_retry_or_loop_edges(tmp_path, monkeypatch):
    """无 retry、无 failure edge、无 loop_exceeded edge 时应终止实例。"""
    import subprocess

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".claude").mkdir()
    (repo / ".agent").mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    inst_id = "loop-004"
    _setup_instance(repo, inst_id, {
        "s00-workflow-start": "DONE",
        "s01": "ERROR",
        "s99-workflow-end": "PENDING",
    })

    _write_wf_yaml(repo,
        'schema_version: "3.0.0"\nworkflow_id: test-flow\nversion: "1.0.0"\nmax_parallel_agents: 4\nanchor_prefix: "wf"\nstages:\n'
        '  - stage_id: s00-workflow-start\n    name: "开始"\n'
        '  - stage_id: s01\n    name: "A"\n    skill_id: skill-a\n    mandatory: true\n    confirmation_point: false\n    retry: 0\n'
        '  - stage_id: s99-workflow-end\n    name: "结束"\n'
        'edges:\n'
        '  - from: s00-workflow-start\n    to: s01\n    condition: always\n'
        '  - from: s01\n    to: s99-workflow-end\n    condition: success\n',
    )

    inst_path = repo / ".agent" / "instances" / inst_id / "instance.json"
    data = json.loads(inst_path.read_text(encoding="utf-8"))
    data["stages"] = [
        {"stage_id": "s00-workflow-start", "stage_instance_id": "s00-workflow-start", "status": "DONE",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s01", "stage_instance_id": "s01", "status": "ERROR",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
        {"stage_id": "s99-workflow-end", "stage_instance_id": "s99-workflow-end", "status": "PENDING",
         "attempt_count": 0, "loop_counter": 0, "output_message_id": None,
         "child_instance_id": None, "fan_out_target": None},
    ]
    inst_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.chdir(repo)
    result = run_next(inst_id)
    assert result["status"] == "ok"

    terminate_actions = [a for a in result["actions"] if a["action"] == "terminate"]
    assert len(terminate_actions) == 1
    assert terminate_actions[0]["status"] == "FAILED"
