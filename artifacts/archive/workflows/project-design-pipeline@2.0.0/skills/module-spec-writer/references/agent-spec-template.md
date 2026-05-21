# 落地规范输出模板（面向 Code Agent）

> 本文件定义了 `module-spec-writer` skill 输出的**落地规范**的完整结构和各节填写规范。
> 落地规范面向 Code Agent（Claude Code 等），聚焦于**精确的编码规格**——
> 类型定义、接口契约、状态转换表、异常阈值、可复制粘贴的测试数据。
> 不了解项目代码的 Agent 仅凭此文档应能独立完成编码。
> 生成时严格按此模板组织内容，替换所有占位符和说明性文字。
> **版本记录格式约束**：版本记录整体位于 Markdown 引用块内，每行以 `> ` 开头。追加新版本行或修改表行时，新行也必须以 `> ` 开头（含表头分隔行），禁止在引用块中混入无 `>` 前缀的行。
>
> **流水线上下文**：本落地规范基于已冻结的 `[编号]-[名称]-意图文档.md` 编写。技术实现必须与意图文档中的业务定义保持一致。

---

## 1 功能点：[编号] [名称] — 落地规范

> **文档生成时间**：`{由脚本 get_timestamp.py 输出的准确时间}`（精确到秒，默认中国时间，Asia/Shanghai）
> **版本记录**：
> | 版本 | 时间 | 修改人 | 变更摘要 |
> |------|------|--------|----------|
> | v1.0 | `YYYY-MM-DD HH:MM:SS` | AI Assistant | 初始版本 |
> | v1.1 | `YYYY-MM-DD HH:MM:SS` | AI Assistant | 修正字段类型，补充异常场景 |

> **冲突核查指引**：若发现与已有规格文档冲突，优先以时间戳更新的版本为准，并在版本记录中追加冲突解决条目。
> **配套文档**：本模块的设计思路与决策依据见 `[编号]-[名称]-设计文档.md`。

### 1.1 技术栈绑定

> **必须参考项目技术栈设计文档**：列出本项目统一规定的必选/禁选技术、版本约束和中间件要求。若技术栈设计文档中已明确规定某项技术，此处必须与其完全一致，不得擅自引入文档中未列出的新技术，不得使用文档中明确禁止的技术。

- **必须使用**：
  - 库/框架名 + 版本（如 `pydantic>=2.0`、`crewai>=0.30.0`、`neo4j>=5.0`）
  - 特定模式或类（如 `langgraph.StateGraph`、`asyncio.TaskGroup`）
  - 项目结构设计文档中规定的目录组织规范（如模块应放置在 `src/services/` 下，遵循 Feature-Based 结构）
- **禁止使用**：
  - 实现方式（如 "禁止裸调 `asyncio.gather` 替代 CrewAI 的 `Process.parallel`"）
  - 特定 API（如 "禁止直接调用 OpenAI API，必须通过 `ModelRouter` 统一路由"）
  - 技术栈设计文档中明确禁止的库/框架/模式

### 1.2 文件归属

> 列出本模块直接产出的源文件和测试文件的路径。供 module-implementation-executor 和 module-test-writer 快速定位文件位置。
> 路径必须与项目结构设计文档保持一致。

| 文件类型 | 路径 | 说明 |
|---------|------|------|
| 模块入口 | `src/services/world_builder.py` | 世界观构建服务主模块，包含 `build_world` 接口 |
| 状态机 | `src/services/world_builder/state_machine.py` | 状态转换逻辑和持久化 |
| 输入模型 | `src/models/world_build.py` | `WorldBuildInput`、`WorldBuildOutput` Pydantic 模型 |
| 测试文件 | `tests/services/test_world_builder.py` | `build_world` 接口的单元/集成测试 |
| 状态测试 | `tests/services/test_world_builder_state.py` | 状态机转换测试 |

> **约束**：
> - 只列本模块**直接产出**的文件，不列依赖项或第三方库的文件
> - 若项目结构设计文档未规定具体目录，基于技术栈和项目规模推断最合理的目录，并标注"（推断）"
> - 文件命名使用语义化名称，**严禁使用模块编号作为文件名或目录名**

### 1.3 输入定义（精确类型 / 或契约引用）

**对于对外接口类型**：不在这里写完整字段定义，改为**契约引用**。完整字段定义写入 `docs/contracts/{module_id}/{TypeName}.json`（JSON Schema 格式）。

```markdown
**WorldBuildInput**
- 【契约引用】`docs/contracts/M02/WorldBuildInput.json`
- 本模块作为该契约的定义方
- 消费方：暂无
```

**对于内部类型**（不对外暴露）：仍给出完整字段定义。每个字段必须包含完整的约束和示例，禁止用 `# ... 其他字段` 省略：

```python
class InternalBuildContext(BaseModel):
    """内部构建上下文，不对外暴露"""
    retry_count: int = Field(default=0, description="当前重试次数")
    partial_result: str | None = Field(default=None, description="已生成的部分结果")
```

**如果输入为函数参数**，给出带类型注解的签名：

```python
def build_world(
    genre: str,
    style_tags: list[str],
    reference_docs: list[DocumentRef] | None = None,
) -> WorldBuildOutput:
    ...
```

> 总设计未定义的字段，推断最合理类型并标注 `（待确认）`。

### 1.4 输出定义（精确类型 / 或契约引用）

**对于对外接口类型**：使用契约引用格式：

```markdown
**WorldBuildOutput**
- 【契约引用】`docs/contracts/M02/WorldBuildOutput.json`
- 本模块作为该契约的定义方
- 消费方：M03（世界观渲染引擎）
```

**对于内部类型**：仍给出完整 Pydantic 模型定义。

> 输出也必须为精确类型。对外接口优先使用契约引用，内部类型保留完整定义。

### 1.5 核心逻辑步骤

按执行顺序列出可测试的逻辑步骤，每步是一个原子操作。每一步必须包含：操作对象、具体操作、输入来源、输出去向、失败行为。禁止用 `"..."` 或 `"执行主逻辑"` 等模糊表述省略：

1. **步骤 1：输入校验**
   - **操作对象**：`WorldBuildInput` 模型实例
   - **具体操作**：调用 `WorldBuildInput.model_validate(data)` 进行 Pydantic 校验
   - **输入来源**：HTTP 请求体或上游模块传入的字典
   - **输出去向**：校验通过的 `WorldBuildInput` 实例进入步骤 2
   - **失败行为**：校验失败立即抛出 `ValidationError`，返回 422 状态码，不进入后续逻辑

2. **步骤 2：加载已有世界观上下文**
   - **操作对象**：Neo4j 中 `World` 节点
   - **具体操作**：执行 Cypher 查询 `MATCH (n:World {id: $world_id}) RETURN n.properties AS world_props`
   - **输入来源**：步骤 1 校验通过的 `world_id` 字段
   - **输出去向**：查询结果字典注入到步骤 3 的上下文
   - **失败行为**：Neo4j 连接失败 → 重试 3 次（指数退避 1s/2s/4s），仍失败则抛出 `GraphConnectionError`

3. **步骤 3：调用 CrewAI 构建世界观**  ← 禁止写 "执行主逻辑" 或 "..."
   - **操作对象**：`WorldBuildingCrew` Agent 组
   - **具体操作**：实例化 `Crew(agents=[world_builder, lore_creator], tasks=[build_task], process=Process.sequential)` 并执行 `crew.kickoff(inputs={"genre": genre, "context": world_props})`
   - **输入来源**：步骤 1 的 `genre`/`style_tags` + 步骤 2 的 `world_props`
   - **输出去向**：Agent 返回的原始文本进入步骤 4
   - **失败行为**：LLM 调用超时（>30s）→ 重试 2 次，仍失败则降级返回部分结果并标记 `is_partial=True`

4. **步骤 4：输出校验与持久化**
   - **操作对象**：步骤 3 返回的原始文本
   - **具体操作**：调用 `WorldBuildOutput.model_validate_json(raw_text)` 校验，通过后写入 Neo4j `CREATE (n:WorldBuildResult {id: $id, content: $content})`
   - **输入来源**：步骤 3 的 Agent 输出
   - **输出去向**：校验通过的 `WorldBuildOutput` 实例返回给调用方，同时持久化到图数据库
   - **失败行为**：Pydantic 校验失败 → 重试最多 3 次（要求 LLM 修正格式），仍失败则抛出 `OutputValidationError`

> 禁止用自然语言段落描述流程。每一步必须是原子、可测试的操作。禁止省略任何步骤或写 `"..."`。

### 1.6 接口契约（对外暴露的公共接口）

> **本章节定义本模块对外暴露的公共接口，是 Agent 生成测试文件的核心依据。**
> 每个接口必须使用**语义化命名**，清晰表达其业务职责。禁止直接使用模块名或模块编号作为接口名称。

**命名规范**：
- ❌ 禁止：`M02_WorldBuilder`、`M02_build`、`WorldviewModule`
- ✅ 推荐：`WorldBuilder`、`build_world`、`WorldBuildingService`

每个接口必须包含以下要素：

#### 1.6.1 接口 1：[语义化名称]

```python
async def build_world(
    input: WorldBuildInput,
    context: BuildContext | None = None,
) -> WorldBuildOutput:
    """
    根据用户输入的题材和风格标签，生成完整的世界观设定。

    Args:
        input: 构建输入，包含题材类型和风格标签
        context: 可选的构建上下文，包含已有世界观片段

    Returns:
        WorldBuildOutput: 构建结果，包含世界观文本和状态标记

    Raises:
        ValidationError: 输入校验失败
        ExecutionTimeoutError: 执行超时且无法降级
        DependencyCommunicationError: 与依赖服务通信失败

    Side Effects:
        - 将构建结果持久化到 Neo4j
        - 记录结构化日志

    Idempotency:
        同一 build_id 的重复调用，若状态为 BUILDING 则返回进行中状态；
        若状态为 COMPLETED 则返回缓存结果。

    Thread Safety:
        本函数内部不维护可变状态，线程安全。
    """
```

| 属性 | 说明 |
|------|------|
| **接口名称** | `build_world` —— 语义化，描述"构建世界观"的业务动作 |
| **输入类型** | `WorldBuildInput`（详见"输入定义"章节） |
| **输出类型** | `WorldBuildOutput`（详见"输出定义"章节） |
| **异常类型** | `ValidationError`、`ExecutionTimeoutError`、`DependencyCommunicationError`（详见"异常与边界条件"章节） |
| **副作用** | 写入 Neo4j、记录日志 |
| **幂等性** | 基于 build_id 的幂等，重复调用返回相同结果 |
| **并发安全** | 线程安全，内部无共享可变状态 |

#### 1.6.2 接口 2：[语义化名称]

> 如有多个对外接口，按上述格式逐一列出。每个接口必须有完整的类型签名、docstring、异常声明和副作用说明。

---

### 1.7 依赖与集成接口（本模块调用的外部接口）

**必须区分两类依赖**：

#### 1.7.1 关键基础设施依赖（硬性前提，不可 mock）

这些是代码运行的底层基础，必须在项目技术栈设计文档和项目结构设计文档中已定义。若缺失，module-implementation-executor 无法生成可运行代码。

| 依赖类型 | 依赖方 | 具体接口 | 用途 | 项目结构设计文档依据 |
|:---|:---|:---|:---|:---|
| 图数据库 | Neo4j | `graph.query(cypher, params)` | 查询世界观节点 | `项目结构设计.md §数据库层` |
| 向量数据库 | Milvus | `vector_store.similarity_search(query, k=5, ...)` | 检索相似文档 | `项目结构设计.md §数据库层` |
| 外部 API | 模型路由服务 | `model_router.chat(messages, model=...)` | LLM 统一调用 | `项目结构设计.md §外部服务层` |
| 日志系统 | structlog | `logger.info("event", key=value)` | 结构化日志 | `项目结构设计.md §基础设施` |
| 消息队列 | RabbitMQ | `publisher.publish(routing_key, body)` | 异步通知 | `项目结构设计.md §消息中间件` |

> **填写要求**："项目结构设计文档依据"列必须填写。若某项基础设施依赖在项目结构设计文档中未定义，必须在模块-test-writer 和 module-implementation-executor 执行前，通过 `AskUserQuestion` 确认基础设施方案。

#### 1.7.2 核心功能依赖（其他业务模块，可 mock）

这些是其他功能模块的接口调用。若依赖模块尚未落地，module-implementation-executor 可生成 mock/stub。

| 依赖模块 | 具体接口 | 用途 | 落地状态 |
|:---|:---|:---|:---|
| M03-用户管理 | `get_user_context(user_id)` | 获取用户偏好设置 | ⏭️ 待落地（已生成 mock） |
| M05-日志服务 | `structured_logger.bind(...)` | 统一日志上下文注入 | ✅ 已落地 |

> **填写要求**："落地状态"列必须标注。若标注为"待落地"，必须在落地规范的「注意事项」中说明 mock 策略和替换条件。

**通用要求**：
- 涉及 Neo4j 的，写出精确 Cypher 查询模板（带 `$param` 占位符）
- 涉及向量数据库的，写出嵌入模型名和维度
- 涉及外部 API 的，写出 HTTP 方法、路径模板、鉴权方式、超时配置
- 涉及 CrewAI/LangGraph 的，写出使用的具体类/装饰器/模式

### 1.8 状态机（如适用）

如涉及状态流转，必须按以下表格输出，禁止自然语言段落：

| 当前状态 | 触发事件 | 下一状态 | 前置条件 | 副作用 |
|----------|----------|----------|----------|--------|
| IDLE | `start_build` | BUILDING | 输入校验通过 | 创建任务记录，状态设为 BUILDING |
| BUILDING | `build_success` | COMPLETED | 所有 Agent 返回结果 | 保存结果到数据库，发送通知 |
| BUILDING | `build_fail` | FAILED | 重试次数超过上限 | 记录错误日志，发送告警 |
| FAILED | `retry` | BUILDING | 人工确认或自动触发 | 重试计数器 +1 |

> 不涉及状态机的模块，本节写 "本功能点不涉及状态流转，故无需状态机。"

### 1.9 异常与边界条件

至少 3 种异常/边界场景。每种异常必须写出精确的触发阈值、逐步处理策略、精确重试参数。禁止写 `"等等"`、`"其他异常"` 或省略处理细节：

#### 1.9.1 异常 1：输入无效或缺失
- **触发条件**：
  - 必填字段 `genre` 为 `None` 或空字符串 `""`
  - `style_tags` 包含非字符串元素（如 `["科幻", 123]`）
  - `genre` 值不在允许枚举列表 `ALLOWED_GENRES = {"科幻", "奇幻", "现实主义", "悬疑", "历史"}` 中
- **处理策略**：
  1. Pydantic 校验阶段捕获 `ValidationError`
  2. 提取第一个错误字段名和错误信息
  3. 返回 HTTP 422，响应体：`{"detail": {"field": "genre", "msg": "题材类型必须在允许列表中", "allowed": [...]}}`
  4. 记录结构化日志：`logger.warning("input_validation_failed", field="genre", received_value=...)`
  5. **不进入任何后续步骤**
- **重试参数**：不重试，直接失败。客户端修正输入后重新发起请求。

#### 1.9.2 异常 2：执行超时或资源不足
- **触发条件**：
  - CrewAI Agent 单轮调用超过 30 秒（`CREW_TIMEOUT = 30`）
  - LLM Token 消耗超过预算上限 `MAX_TOKENS = 8192`
  - Python 进程内存使用超过 `MAX_MEMORY_MB = 512`
- **处理策略**：
  1. 捕获 `TimeoutError` / `TokenLimitExceeded` / `MemoryError`
  2. 立即中断当前 Agent 任务（调用 `task.cancel()`）
  3. 如果已产生部分结果且长度 > 100 字符，包装为 `WorldBuildOutput(content=partial, is_partial=True)` 降级返回
  4. 如果无部分结果，抛出 `ExecutionTimeoutError`
  5. 记录错误日志：`logger.error("execution_timeout", step="crew_kickoff", duration_sec=..., retry_count=...)`
- **重试参数**：最大 3 次，指数退避（1s, 2s, 4s）。第 3 次失败后不再重试，执行上述降级/抛错策略。

#### 1.9.3 异常 3：与依赖模块通信失败
- **触发条件**：
  - Neo4j 连接池返回 `ServiceUnavailable` 或连接超时（>5s）
  - 向量数据库查询返回 HTTP 5xx 或响应体不包含预期字段 `embeddings`
  - 外部 API（如模型路由服务）返回 503/504 或 JSON 解析失败
- **处理策略**：
  1. 捕获具体异常类型（`neo4j.exceptions.ServiceUnavailable`、`httpx.HTTPStatusError`、`json.JSONDecodeError`）
  2. 关闭当前失效连接，从连接池获取新连接
  3. 重试同一操作
  4. 第 3 次仍失败：抛出 `DependencyCommunicationError`，向上层返回 503，触发告警通知（Webhook 到运维群）
  5. 记录日志：`logger.critical("dependency_failure", service="neo4j|vector_db|model_router", error_type=...)`
- **重试参数**：最大 3 次，固定间隔 2s。每次重试前必须重新建立连接，禁止在失效连接上重试。

> 根据模块特性补充更多异常场景（如并发冲突、数据不一致、幂等性失效等）。每种新增异常都必须遵循上述详细度标准。

### 1.10 验收测试场景

> 本章节定义**需要覆盖的核心测试场景**，聚焦"测什么"而非"怎么测"。
> 具体的测试代码实现（fixtures、mock、测试框架选型）由专门的测试 Skill 负责。

**原则**：
- 每个核心业务流程至少 1 个正向场景
- 每个异常类型（见"异常与边界条件"章节）至少 1 个异常场景
- 只定义场景和关键断言点，不定义测试代码细节

---

#### 1.10.1 正向测试 1：完整输入成功生成世界观

- **场景**：用户提供了有效的题材和风格标签，系统成功生成完整世界观
- **Given**: 有效的 `WorldBuildInput`（genre="科幻", style_tags=["赛博朋克", "反乌托邦"], world_id="world-001"）
- **When**: 调用 `build_world()`
- **Then**:
  - 返回 `WorldBuildOutput`，`status="COMPLETED"`，`is_partial=False`
  - `content` 为非空字符串，包含三个必需维度（基础设定、文化细节、地理环境）
  - 结果已持久化到 Neo4j

#### 1.10.2 正向测试 2：空风格标签成功生成

- **场景**：用户未提供风格标签，系统仍成功生成世界观
- **Given**: `WorldBuildInput`（genre="奇幻", style_tags=[]）
- **When**: 调用 `build_world()`
- **Then**:
  - 返回 `WorldBuildOutput`，`is_partial=False`
  - 不抛出异常

#### 1.10.3 正向测试 3：幂等重复调用

- **场景**：使用相同参数重复调用，第二次直接返回缓存结果
- **Given**: 首次调用已完成且状态为 COMPLETED
- **When**: 使用相同参数再次调用
- **Then**:
  - 返回结果与首次一致
  - 不重新触发 LLM 调用

#### 1.10.4 异常测试 1：无效题材类型

- **场景**：用户提供了不在允许列表中的题材类型
- **Given**: genre="不存在的题材"
- **When**: 调用 `build_world()`
- **Then**:
  - 抛出 `ValidationError`
  - 不进入后续处理步骤

#### 1.10.5 异常测试 2：LLM 超时降级

- **场景**：LLM 调用超时，但已生成部分内容
- **Given**: LLM 响应时间超过超时阈值（30s），但已返回部分内容
- **When**: 调用 `build_world()`
- **Then**:
  - 抛出 `ExecutionTimeoutError`
  - 返回的部分结果中 `is_partial=True`

#### 1.10.6 异常测试 3：Neo4j 连接失败

- **场景**：Neo4j 服务不可用，重试后仍失败
- **Given**: Neo4j 返回 `ServiceUnavailable`
- **When**: 调用 `build_world()`
- **Then**:
  - 抛出 `DependencyCommunicationError`
  - 已执行重试逻辑（最大 3 次）

> **测试职责边界**：
> - **本 Skill（module-spec-writer）**：定义"测什么"——核心场景和关键断言点
> - **测试 Skill（如 module-test-writer）**：定义"怎么测"——测试代码、fixtures、mock、测试框架

### 1.11 注意事项与禁止行为（编码层面）

1. **[约束 1]** 摘录总设计中对本功能点的明确编码约束（如 "温度参数对思考模式无效，启用 thinking 后不要设置 temperature"）
2. **[易错点 1]** 标注编码时容易出错的地方（如 "Neo4j 查询时务必使用参数化查询，禁止字符串拼接 Cypher"）
3. **[禁止行为]** 禁止使用的实现方式或调用模式
4. **[偷懒红线]** 绝对禁止以"这个很简单"、"显而易见"、"和某某模块类似"为由省略任何细节

### 1.12 文档详细度自检清单

输出文档前，强制自检以下项目。如有任何一项不满足，必须补充完善：

- [ ] 文档自包含：一位不了解本项目代码的 Agent，仅凭此文档即可完成编码
- [ ] 无偷懒表述：全文搜索并消除 `"等等"`、`"..."`、`"其他字段"`、`"类似"`、`"同上"`、`"参考其他模块"`、`"请根据实际情况补充"`、`"开发者自行决定"`
- [ ] 类型定义完整：每个 Pydantic 字段都有 `description` + `examples` + 约束（`min_length`/`max_length`/`ge`/`le`/`pattern` 等）
- [ ] 逻辑步骤完整：每个步骤都有操作对象、具体操作、输入来源、输出去向、失败行为
- [ ] 异常处理完整：每种异常都有精确的触发阈值、逐步处理策略、精确重试参数
- [ ] 无隐藏假设：所有默认值来源、条件分支、业务规则都已显式写出
- [ ] 技术栈绑定明确：必须使用和禁止使用的项均已列出，且与项目技术栈设计文档保持一致
- [ ] 意图一致性：已确认技术实现与已冻结的意图文档一致

### 1.14 外部接口契约清单

> 本章节列出本模块所有对外接口的契约文件路径，是契约协调的最终输出。

| 契约名称 | 文件路径 | 契约类型 | 成熟度 | 定义方 | 消费方 |
|:---------|:---------|:---------|:-------|:-------|:-------|
| WorldBuildInput | `docs/contracts/M02/WorldBuildInput.json` | input | draft | M02 | — |
| WorldBuildOutput | `docs/contracts/M02/WorldBuildOutput.json` | output | stable | M02 | M03 |
| BuildStatus | `docs/contracts/M02/BuildStatus.json` | shared-enum | stable | M02 | M03, M05 |

> 复用其他模块的契约也在这里列出，标注定义方和消费方。

### 1.15 意图一致性声明

> 本章节是落地规范的固定结尾，用于建立与意图文档的追溯关系。

- **配套意图文档**：`[编号]-[名称]-意图文档.md`
- **冻结时间**：`YYYY-MM-DD HH:MM:SS`
- **一致性确认**：
  - [ ] 本落地规范中的输入/输出类型定义与意图文档中的业务字段定义一致
  - [ ] 本落地规范中的状态机实现与意图文档中的状态业务定义一致
  - [ ] 本落地规范中的异常处理策略与意图文档中的异常业务策略一致
  - [ ] 本落地规范中的验收测试场景覆盖意图文档中的所有验收标准
  - [ ] 本落地规范中的技术实现未超出意图文档中"留给规范阶段的技术决策"的范围
- **偏差说明**（如有）：若技术实现与意图文档存在偏差（经用户确认的技术优化或调整），在此列出偏差项和原因。无偏差则写"无偏差，技术实现与意图文档完全一致"。
