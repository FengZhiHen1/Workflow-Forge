# module-spec-writer 输入契约

继承 `.claude/contracts/input.md`，扩展以下字段。

## 阶段专用输入

本 Skill 根据 `stage_id` 消费不同的上游产物：

| Stage | 期望 `upstream_files` | 说明 |
|-------|----------------------|------|
| s12-spec-prepare | 已冻结的意图文档路径 | 模块意图文档（`[编号]-[名称]-意图文档.md`） |
| s14-spec-contradiction | spec-researcher 报告路径 | 《技术决策完整报告》含业务矛盾标记清单 |
| s15-spec-design-doc | spec-researcher 报告路径 + 用户裁决（如有） | 技术决策报告及 `upstream_message_ids` 中的确认回复 |
| s16-spec-contract-draft | 设计文档路径 | s15 生成的设计文档 |
| s18-spec-contract-conflict | contract-harmonizer 报告路径 | 《契约协调报告》JSON |
| s19-spec-internal-design | 契约草案 + 用户裁决（如有） | s16 草案目录 + s18 确认回复 |

## 特殊指令

- `stage_direction`：由编排器设置为当前 Stage 的 `stage_id`，决定执行哪个分支。
- `special_instructions`：可能包含模块编号、模块名称、所属分组等上下文信息。

## 路径约定

- 意图文档标准路径：`docs/功能设计/[所属分组]/[编号]-[名称]/[编号]-[名称]-意图文档.md`
- 若标准路径不存在，允许扫描 `docs/功能设计/` 及其子目录按模块编号和名称匹配查找。
