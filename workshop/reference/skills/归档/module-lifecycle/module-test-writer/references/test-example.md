# 测试编写样例

> 本样例基于落地规范中的「世界观构建」模块（`build_world` 接口），展示 module-test-writer 如何从契约和实现代码生成验收测试。
>
> 阅读本样例时，请对照落地规范中的对应章节，理解"推导来源 → 测试代码"的映射关系。

---

## 样例落地规范摘要

**接口契约**：
```python
async def build_world(
    input: WorldBuildInput,
    context: BuildContext | None = None,
) -> WorldBuildOutput
```

**输入模型约束**：
- `genre: str = Field(..., min_length=1, max_length=50)`
- `style_tags: list[str] = Field(default_factory=list)`
- `world_id: str = Field(..., pattern=r"^world-[a-z0-9]+$")`

**状态机**：
- `IDLE --start_build--> BUILDING`
- `BUILDING --build_success--> COMPLETED`
- `BUILDING --build_fail--> FAILED`
- `FAILED --retry--> BUILDING`

**异常条件**：
- `genre` 空字符串 → `ValidationError`
- LLM 调用 >30s → `ExecutionTimeoutError`，降级返回 `is_partial=True`
- Neo4j `ServiceUnavailable` → `DependencyCommunicationError`，重试 3 次

**假设实现代码关键分支**（module-test-writer 读取实现后理解）：
- `src/services/world_builder.py:32-48` — 输入校验（genre 非空、world_id pattern）
- `src/services/world_builder.py:52-78` — 主流程：调用 CrewAI → 解析结果 → 持久化到 Neo4j
- `src/services/world_builder.py:80-95` — 超时处理：catch asyncio.TimeoutError → 标记 is_partial
- `src/services/world_builder.py:97-112` — Neo4j 异常处理：catch ServiceUnavailable → 重试 3 次 → 抛 DependencyCommunicationError
- `src/services/world_builder.py:115-128` — 状态机校验：检查当前状态是否允许 start_build

---

## 测试文件完整代码

```python
# ============================================================
# 验收测试
# 来源模块：M01-世界观构建
# 来源文档：M01-世界观构建-落地规范.md
# 文档版本：v1.2
# 生成时间：2026-05-04 10:52:27
# 生成者：module-test-writer
# 覆盖场景数：14（正常 3 + 边界 5 + 错误 4 + 集成 2）
# 测试场景清单：docs/testing-design/M01/test-scenarios.md
# ============================================================

import pytest
from unittest.mock import Mock, patch, AsyncMock

# 项目类型（来自文件归属板块）
from src.models.world_build import WorldBuildInput, WorldBuildOutput, BuildContext

# 被测接口（来自文件归属板块）
from src.services.world_builder import build_world

# 外部依赖 MOCK（必须在注释中标注）
# TODO: 依赖模块 M03-用户管理 尚未落地，以下为 MOCK
from src.services.user_manager import get_user_context  # MOCKED

# TODO: 依赖模块 M05-日志服务 尚未落地，以下为 MOCK
from src.utils.logger import structured_logger  # MOCKED


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_neo4j():
    """MOCK：Neo4j 连接。依赖模块 M02-图数据库 已落地，但测试环境无真实数据库。"""
    with patch("src.services.world_builder.graph") as mock:
        mock.query = Mock(return_value=[{"world_props": {"name": "测试世界"}}])
        yield mock


@pytest.fixture
def mock_crewai():
    """MOCK：CrewAI LLM 调用。外部服务，测试中使用可控返回值替代。"""
    with patch("src.services.world_builder.Crew") as mock_crew_class:
        mock_crew = Mock()
        mock_crew.kickoff = Mock(return_value="""
            基础设定：星际联邦统治着银河系的 12 个星区。
            文化细节：联邦公民崇尚理性与效率，艺术被视作多余的情感宣泄。
            地理环境：主星首都位于一个双星系统的宜居带上。
        """)
        mock_crew_class.return_value = mock_crew
        yield mock_crew_class


@pytest.fixture
def mock_logger():
    """MOCK：依赖模块 M05-日志服务 尚未落地，以下为 MOCK。
    当 M05 落地后，应替换为真实依赖并移除本 fixture。
    """
    with patch("src.services.world_builder.structured_logger") as mock:
        yield mock


@pytest.fixture
def valid_input():
    """有效输入数据（来自落地规范 §验收测试-正向1）。"""
    return WorldBuildInput(
        genre="科幻",
        style_tags=["赛博朋克", "反乌托邦"],
        world_id="world-001",
    )


# ============================================================================
# 正常路径测试（Happy Path）
# ============================================================================

class TestHappyPath:
    """正常路径：落地规范 §验收测试 中明确要求覆盖的有效输入场景。"""

    async def test_build_world_full_input_success(
        self, valid_input, mock_neo4j, mock_crewai, mock_logger
    ):
        """验证完整输入成功生成世界观。

        场景编号：H01
        契约依据：落地规范 §验收测试-正向1
        实现分支：src/services/world_builder.py:52-78（主流程）
        """
        result = await build_world(input=valid_input)

        # 值断言（强）：具体状态值
        assert result.status == "COMPLETED"
        assert result.is_partial is False

        # 结构断言（强）：content 必须包含三个维度关键词
        assert "基础设定" in result.content
        assert "文化细节" in result.content
        assert "地理环境" in result.content

        # 行为断言（强）：副作用验证——结果已持久化到 Neo4j
        assert mock_neo4j.query.call_count >= 1
        cypher_calls = [call[0][0] for call in mock_neo4j.query.call_args_list]
        assert any("CREATE" in cql and "WorldBuildResult" in cql for cql in cypher_calls)

        # 行为断言（强）：日志已记录
        mock_logger.info.assert_called()

    async def test_build_world_empty_style_tags_success(
        self, mock_neo4j, mock_crewai, mock_logger
    ):
        """验证空风格标签成功生成。

        场景编号：H02
        契约依据：落地规范 §验收测试-正向2
        实现分支：src/services/world_builder.py:52-78（主流程）
        """
        input_data = WorldBuildInput(genre="奇幻", style_tags=[], world_id="world-002")
        result = await build_world(input=input_data)

        assert result.is_partial is False
        assert result.status == "COMPLETED"
        # 内容存在性检查增强强度，但不单独使用
        assert len(result.content) > 50

    async def test_build_world_omitted_context_success(
        self, valid_input, mock_neo4j, mock_crewai
    ):
        """验证不传入可选参数 context 时正常执行。

        场景编号：H03
        契约依据：落地规范 §输入定义 — context: BuildContext | None = None
        实现分支：src/services/world_builder.py:52-78（主流程，context 默认 None）
        """
        result = await build_world(input=valid_input)  # 不传 context

        assert result.status == "COMPLETED"
        assert result.is_partial is False


# ============================================================================
# 边界路径测试
# ============================================================================

class TestBoundaryConditions:
    """边界路径：从输入模型的字段约束推导边界值。
    来源：落地规范 §输入定义 中的 Field 约束条件。
    """

    async def test_genre_min_length_boundary_empty_string(self, mock_neo4j, mock_crewai):
        """验证 genre 空字符串时抛出 ValidationError（刚好越界下界）。

        场景编号：B01
        契约依据：genre: str = Field(..., min_length=1)
        实现分支：src/services/world_builder.py:32-48（输入校验）
        """
        input_data = WorldBuildInput(genre="", style_tags=[], world_id="world-empty")

        with pytest.raises(ValidationError):
            await build_world(input=input_data)

    async def test_genre_min_length_boundary_single_char(self, mock_neo4j, mock_crewai):
        """验证 genre 单字符时正常通过（刚好合法下界）。

        场景编号：B02
        契约依据：genre: str = Field(..., min_length=1)
        实现分支：src/services/world_builder.py:32-48（输入校验）
        """
        input_data = WorldBuildInput(genre="科", style_tags=[], world_id="world-single")
        result = await build_world(input=input_data)

        assert result.status == "COMPLETED"

    async def test_genre_max_length_boundary_50_chars(self, mock_neo4j, mock_crewai):
        """验证 genre 刚好 50 字符时正常通过（刚好合法上界）。

        场景编号：B03
        契约依据：genre: str = Field(..., max_length=50)
        实现分支：src/services/world_builder.py:32-48（输入校验）
        """
        long_genre = "科" * 50
        input_data = WorldBuildInput(genre=long_genre, style_tags=[], world_id="world-max")
        result = await build_world(input=input_data)

        assert result.status == "COMPLETED"

    async def test_genre_max_length_boundary_51_chars(self, mock_neo4j, mock_crewai):
        """验证 genre 51 字符时抛出 ValidationError（刚好越界上界）。

        场景编号：B04
        契约依据：genre: str = Field(..., max_length=50)
        实现分支：src/services/world_builder.py:32-48（输入校验）
        """
        too_long = "科" * 51
        input_data = WorldBuildInput(genre=too_long, style_tags=[], world_id="world-over")

        with pytest.raises(ValidationError):
            await build_world(input=input_data)

    async def test_world_id_pattern_invalid_format(self, mock_neo4j, mock_crewai):
        """验证 world_id 不符合 pattern 时抛出 ValidationError。

        场景编号：B05
        契约依据：world_id: str = Field(..., pattern=r"^world-[a-z0-9]+$")
        实现分支：src/services/world_builder.py:32-48（输入校验）
        """
        input_data = WorldBuildInput(genre="科幻", style_tags=[], world_id="invalid-id")

        with pytest.raises(ValidationError):
            await build_world(input=input_data)


# ============================================================================
# 错误路径测试
# ============================================================================

class TestErrorPaths:
    """错误路径：验证异常条件和错误处理分支。
    来源：落地规范 §异常与边界条件。
    """

    async def test_llm_timeout_returns_partial(self, valid_input, mock_neo4j, mock_crewai):
        """验证 LLM 超时时抛出 ExecutionTimeoutError 并标记 is_partial。

        场景编号：E01
        契约依据：落地规范 §异常条件 — LLM 调用 >30s → ExecutionTimeoutError，is_partial=True
        实现分支：src/services/world_builder.py:80-95（超时处理）
        """
        mock_crewai.return_value.kickoff = AsyncMock(side_effect=asyncio.TimeoutError)

        with pytest.raises(ExecutionTimeoutError):
            result = await build_world(input=valid_input)
            assert result.is_partial is True

    async def test_neo4j_failure_retries_then_raises(self, valid_input, mock_neo4j):
        """验证 Neo4j 不可用时重试 3 次后抛出 DependencyCommunicationError。

        场景编号：E02
        契约依据：落地规范 §异常条件 — Neo4j ServiceUnavailable → 重试 3 次 → DependencyCommunicationError
        实现分支：src/services/world_builder.py:97-112（Neo4j 异常处理）
        """
        mock_neo4j.query.side_effect = ServiceUnavailable("连接失败")

        with pytest.raises(DependencyCommunicationError):
            await build_world(input=valid_input)

        # 行为断言：重试了 3 次
        assert mock_neo4j.query.call_count == 3

    async def test_invalid_genre_raises_validation_error(self, mock_neo4j, mock_crewai):
        """验证无效题材类型时抛出 ValidationError。

        场景编号：E03
        契约依据：落地规范 §验收测试-异常1
        实现分支：src/services/world_builder.py:32-48（输入校验）
        """
        input_data = WorldBuildInput(
            genre="不存在的题材",
            style_tags=[],
            world_id="world-003",
        )

        with pytest.raises(ValidationError, match="题材类型必须在允许列表中"):
            await build_world(input=input_data)

    async def test_illegal_state_transition_raises_error(self, valid_input, mock_neo4j):
        """验证从 COMPLETED 状态触发构建时抛出 StateTransitionError。

        场景编号：E04
        契约依据：落地规范 §状态机 — 未定义 COMPLETED --start_build--> 任何状态
        实现分支：src/services/world_builder.py:115-128（状态机校验）
        """
        with patch("src.services.world_builder.get_task_state", return_value="COMPLETED"):
            with pytest.raises(StateTransitionError, match="非法状态转移"):
                await build_world(input=valid_input)


# ============================================================================
# 集成路径测试
# ============================================================================

class TestIntegrationPaths:
    """集成路径：验证多接口联动和状态传递。
    来源：落地规范 §编排逻辑。
    """

    async def test_full_pipeline_state_progression(
        self, valid_input, mock_neo4j, mock_crewai
    ):
        """验证完整工作流中状态从 IDLE → BUILDING → COMPLETED 的正确传递。

        场景编号：I01
        契约依据：落地规范 §状态机 — IDLE --start_build--> BUILDING --build_success--> COMPLETED
        实现分支：src/services/world_builder.py:115-128（状态机）+ :52-78（主流程）
        """
        # 初始状态为 IDLE
        with patch("src.services.world_builder.get_task_state", return_value="IDLE"):
            with patch("src.services.world_builder.update_task_state") as mock_update:
                result = await build_world(input=valid_input)

                assert result.status == "COMPLETED"
                # 验证状态更新被调用
                mock_update.assert_called()
                # 验证最终状态为 COMPLETED
                final_call = mock_update.call_args_list[-1]
                assert final_call[1].get("state") == "COMPLETED"

    async def test_retry_from_failed_state(self, valid_input, mock_neo4j, mock_crewai):
        """验证从 FAILED 状态重试时状态回到 BUILDING。

        场景编号：I02
        契约依据：落地规范 §状态机 — FAILED --retry--> BUILDING
        实现分支：src/services/world_builder.py:115-128（状态机）
        """
        with patch("src.services.world_builder.get_task_state", return_value="FAILED"):
            with patch("src.services.world_builder.update_task_state") as mock_update:
                result = await build_world(input=valid_input)

                assert result.status == "COMPLETED"
                # 验证重试时状态被更新为 BUILDING
                build_calls = [c for c in mock_update.call_args_list
                               if c[1].get("state") == "BUILDING"]
                assert len(build_calls) >= 1


# ============================================================================
# 断言强度对比示例（给 Skill 使用者的参考）
# ============================================================================

class TestAssertionStrengthExamples:
    """以下测试故意展示【弱断言】和【强断言】的区别。
    module-test-writer 生成的所有测试都应达到"强断言"级别。
    """

    async def test_weak_assertion_example(self, valid_input, mock_neo4j, mock_crewai):
        """❌ 弱断言示例（禁止单独使用此类断言）：
        以下断言太弱，一个返回 "stub" 的桩实现就能通过。
        """
        result = await build_world(input=valid_input)

        # ❌ 太弱：只检查返回值存在
        assert result is not None

        # ❌ 太弱：只检查 content 非空
        assert len(result.content) > 0

        # ❌ 太弱：纯 mock 调用验证，不结合业务结果
        mock_neo4j.query.assert_called_once()

    async def test_strong_assertion_example(self, valid_input, mock_neo4j, mock_crewai):
        """✅ 强断言示例（推荐）：
        以下断言组合足以让错误的桩实现失败。
        """
        result = await build_world(input=valid_input)

        # ✅ 值断言：具体字段值
        assert result.status == "COMPLETED"
        assert result.is_partial is False

        # ✅ 结构断言：内容包含业务语义关键词
        assert "基础设定" in result.content
        assert "文化细节" in result.content
        assert "地理环境" in result.content

        # ✅ 行为断言：副作用验证——具体 Cypher 语句包含预期内容
        assert mock_neo4j.query.call_count >= 1
        last_cypher = mock_neo4j.query.call_args[0][0]
        assert "CREATE" in last_cypher
        assert "WorldBuildResult" in last_cypher
        assert "$id" in last_cypher  # 参数化查询验证
```

---

## 推导映射速查表

| 落地规范章节 | 推导出的测试类型 | 本样例中的对应测试 |
|:---|:---|:---|
| §输入定义 `min_length=1` | 边界值：空字符串（越界）、单字符（合法） | `test_genre_min_length_boundary_*` |
| §输入定义 `max_length=50` | 边界值：50 字符（合法）、51 字符（越界） | `test_genre_max_length_boundary_*` |
| §输入定义 `pattern=r"^world-[a-z0-9]+$"` | 格式：不匹配（越界） | `test_world_id_pattern_invalid_format` |
| §验收测试-正向1 | 正常路径：完整输入成功 | `test_build_world_full_input_success` |
| §验收测试-正向2 | 正常路径：空风格标签 | `test_build_world_empty_style_tags_success` |
| §异常条件 `>30s` | 错误路径：超时处理 | `test_llm_timeout_returns_partial` |
| §异常条件 Neo4j | 错误路径：外部依赖失败 | `test_neo4j_failure_retries_then_raises` |
| §状态机 | 错误路径：非法状态转移 | `test_illegal_state_transition_raises_error` |
| §状态机 | 集成路径：状态传递 | `test_full_pipeline_state_progression` |
| §状态机 | 集成路径：重试流程 | `test_retry_from_failed_state` |
