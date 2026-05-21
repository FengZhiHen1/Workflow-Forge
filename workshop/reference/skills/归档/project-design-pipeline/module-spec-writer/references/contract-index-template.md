# 模块接口契约索引模板

## 格式规范

每个模块一个条目（约 9 行），仅提取接口表面信息：

```markdown
# 模块接口契约索引

## M01 - 用户输入解析器
- **输入**: `UserInput {raw_text: str, source: Literal["web","api"]}`
- **输出**: `ParsedInput {intent: str, entities: list[Entity], confidence: float}`
- **状态机**: 无
- **模块依赖**: 无
- **外部依赖**: OpenAI API (via ModelRouter)
- **技术栈**: pydantic>=2.0, openai>=1.0
- **契约文件**: `docs/contracts/M01/UserInput.json`, `docs/contracts/M01/ParsedInput.json`
- **更新时间**: `2026-04-28 20:06:57`

## M02 - 世界观构建引擎
- **输入**: `WorldBuildInput {genre: str, style_tags: list[str], world_id: str}`
- **输出**: `WorldBuildOutput {world_id: str, content: str, is_partial: bool}`
- **状态机**: IDLE→BUILDING→COMPLETED|FAILED, FAILED→BUILDING
- **模块依赖**: M01 (接收 ParsedInput 的 genre/style_tags)
- **外部依赖**: Neo4j (World节点), CrewAI (WorldBuildingCrew), text-embedding-3-large (dim=3072)
- **技术栈**: pydantic>=2.0, crewai>=0.30.0, neo4j>=5.0
- **契约文件**: `docs/contracts/M02/WorldBuildInput.json`, `docs/contracts/M02/WorldBuildOutput.json`
- **更新时间**: `2026-04-28 20:06:57`
```

## 操作规则

- 若 `_contracts.md` 不存在，先创建文件头 `# 模块接口契约索引`，再追加所有已有完整规格的契约条目（此时需先扫描已有规格提取契约），最后追加本次模块的条目
- 若 `_contracts.md` 已存在，按模块编号顺序插入/更新本次模块的条目（已存在同编号条目则替换）
- 更新时间必须通过 `get_timestamp.py` 脚本获取

## 约束

契约索引条目只提取接口表面，禁止写入完整字段定义、逻辑步骤、异常处理、验收测试等详细内容。完整信息保留在各模块的独立规格文档和契约 JSON 文件中。

## 契约文件引用规则

- 每个模块条目必须列出其定义的契约文件路径（`docs/contracts/{module_id}/*.json`）
- 若模块复用了其他模块的契约（未新建），在条目中标注：`复用契约: M01/ParsedInput.json`
- 契约索引不重复描述契约内容，只提供导航和路径引用
