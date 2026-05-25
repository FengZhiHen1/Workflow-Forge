"""测试 domain.dag.topology — Tarjan SCC 拓扑分析。"""

import pytest

from domain.dag.graph import AdjacencyList, build_adjacency
from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)
from domain.dag.topology import TopologyResult, analyze_topology


def _make_linear_spec() -> WorkflowSpec:
    return WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s0", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s1", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
            StageSpec(stage_id="s2", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
            StageSpec(stage_id="s3", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s0", to_stage="s1", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s1", to_stage="s2", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s2", to_stage="s3", condition=EdgeCondition.SUCCESS),
        ],
    )


class TestTopologyResult:
    def test_default_construction(self):
        r = TopologyResult()
        assert r.order == []
        assert r.cycles == []
        assert r.back_edges == []


class TestAnalyzeTopology:
    def test_acyclic_linear_dag(self):
        """无环线性 DAG：s0→s1→s2→s3。验证拓扑序正确、无环、无回边。"""
        spec = _make_linear_spec()
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert result.order == ["s0", "s1", "s2", "s3"]
        assert result.cycles == []
        assert result.back_edges == []

    def test_acyclic_diamond(self):
        """菱形 DAG：s0→A→B→D + s0→A→C→D。验证无环。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="diamond",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="s0", name="start", target_type=StageTargetType.VIRTUAL),
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
                StageSpec(stage_id="C", name="C", target_type=StageTargetType.SKILL, target="c"),
                StageSpec(stage_id="D", name="D", target_type=StageTargetType.SKILL, target="d"),
            ],
            edges=[
                EdgeSpec(from_stage="s0", to_stage="A", condition=EdgeCondition.ALWAYS),
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="A", to_stage="C", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="B", to_stage="D", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="C", to_stage="D", condition=EdgeCondition.SUCCESS),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert result.cycles == []
        assert set(result.order) == {"s0", "A", "B", "C", "D"}
        assert result.order[0] == "s0"
        assert result.order[-1] == "D"
        a_pos = result.order.index("A")
        b_pos = result.order.index("B")
        c_pos = result.order.index("C")
        d_pos = result.order.index("D")
        assert a_pos < b_pos < d_pos
        assert a_pos < c_pos < d_pos

    def test_self_loop(self):
        """单个节点自环：检测为 cycle，back_edges 含自环边。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="self-loop",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
            ],
            edges=[
                EdgeSpec(from_stage="A", to_stage="A", condition=EdgeCondition.FAILURE, max_loop=3),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert result.cycles == [["A"]]
        assert len(result.back_edges) == 1
        assert result.back_edges[0].from_stage == "A"
        assert result.back_edges[0].to_stage == "A"

    def test_multi_node_cycle(self):
        """多节点环 A→B→C→A。验证 SCC 检测。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="multi-cycle",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="s0", name="start", target_type=StageTargetType.VIRTUAL),
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
                StageSpec(stage_id="C", name="C", target_type=StageTargetType.SKILL, target="c"),
            ],
            edges=[
                EdgeSpec(from_stage="s0", to_stage="A", condition=EdgeCondition.ALWAYS),
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="B", to_stage="C", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="C", to_stage="A", condition=EdgeCondition.FAILURE, max_loop=2),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        # 环应包含 A/B/C
        assert len(result.cycles) == 1
        assert set(result.cycles[0]) == {"A", "B", "C"}
        # 回边 C→A
        assert any(e.from_stage == "C" and e.to_stage == "A" for e in result.back_edges)
        assert result.order[0] == "s0"

    def test_cross_scc_back_edge(self):
        """跨 SCC 回边：两个 SCC 之间，from 在拓扑序中排在 to 之后的边。

        构造: SCC1={A,B} (A→B), SCC2={C} (孤立), 加回边 B→C...
        实际用更简单的场景: A→B, B→C, C→B（形成 SCC），A→D→B（跨SCC回边）。
        简化：用已知的 DFS back edge 场景验证。
        """
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="cross-scc",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
                StageSpec(stage_id="C", name="C", target_type=StageTargetType.SKILL, target="c"),
            ],
            edges=[
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="B", to_stage="C", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="C", to_stage="B", condition=EdgeCondition.FAILURE, max_loop=2),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        # B/C form a cycle, back edge should be C→B (DFS back edge)
        assert len(result.cycles) == 1
        assert set(result.cycles[0]) == {"B", "C"}
        assert any(e.from_stage == "C" and e.to_stage == "B" for e in result.back_edges)

    def test_disconnected_components(self):
        """两个独立 DAG 子图：验证所有节点都在 order 中。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="disconnected",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="A1", name="A1", target_type=StageTargetType.SKILL, target="a1"),
                StageSpec(stage_id="A2", name="A2", target_type=StageTargetType.SKILL, target="a2"),
                StageSpec(stage_id="B1", name="B1", target_type=StageTargetType.SKILL, target="b1"),
                StageSpec(stage_id="B2", name="B2", target_type=StageTargetType.SKILL, target="b2"),
            ],
            edges=[
                EdgeSpec(from_stage="A1", to_stage="A2", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="B1", to_stage="B2", condition=EdgeCondition.SUCCESS),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert set(result.order) == {"A1", "A2", "B1", "B2"}
        assert result.cycles == []
        assert result.back_edges == []

    def test_single_node_no_edges(self):
        """单节点无出边。验证 order=[node]。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="isolated",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="X", name="X", target_type=StageTargetType.SKILL, target="x"),
            ],
            edges=[],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert result.order == ["X"]
        assert result.cycles == []
        assert result.back_edges == []

    def test_complex_graph_with_cycle_and_diamond(self):
        """混合图：包含环和菱形依赖。验证正确检测环。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="complex",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="s0", name="start", target_type=StageTargetType.VIRTUAL),
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
                StageSpec(stage_id="C", name="C", target_type=StageTargetType.SKILL, target="c"),
                StageSpec(stage_id="D", name="D", target_type=StageTargetType.SKILL, target="d"),
                StageSpec(stage_id="E", name="E", target_type=StageTargetType.SKILL, target="e"),
            ],
            edges=[
                EdgeSpec(from_stage="s0", to_stage="A", condition=EdgeCondition.ALWAYS),
                # Diamond: A→B→D, A→C→D
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="A", to_stage="C", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="B", to_stage="D", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="C", to_stage="D", condition=EdgeCondition.SUCCESS),
                # Self-loop on D
                EdgeSpec(from_stage="D", to_stage="D", condition=EdgeCondition.FAILURE, max_loop=2),
                EdgeSpec(from_stage="D", to_stage="E", condition=EdgeCondition.SUCCESS),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert len(result.cycles) == 1
        assert result.cycles[0] == ["D"]
        assert result.order[-1] == "E"

    def test_back_edge_ordering(self):
        """验证回边从拓扑序后面的节点指向前面的节点。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="back-edge-ordering",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="s0", name="start", target_type=StageTargetType.VIRTUAL),
                StageSpec(stage_id="s1", name="s1", target_type=StageTargetType.SKILL, target="s1"),
                StageSpec(stage_id="s2", name="s2", target_type=StageTargetType.SKILL, target="s2"),
                StageSpec(stage_id="s3", name="s3", target_type=StageTargetType.SKILL, target="s3"),
            ],
            edges=[
                EdgeSpec(from_stage="s0", to_stage="s1", condition=EdgeCondition.ALWAYS),
                EdgeSpec(from_stage="s1", to_stage="s2", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s2", to_stage="s3", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s3", to_stage="s1", condition=EdgeCondition.FAILURE, max_loop=2),
            ],
        )
        adj = build_adjacency(spec)
        result = analyze_topology(adj)

        assert len(result.cycles) == 1
        assert set(result.cycles[0]) == {"s1", "s2", "s3"}
        assert any(e.from_stage == "s3" and e.to_stage == "s1" for e in result.back_edges)
