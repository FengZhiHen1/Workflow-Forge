# module-spec-writer 输出契约

继承 `.claude/contracts/output.md`，扩展以下字段。

## 阶段专用输出

| Stage | 输出产物 | 说明 |
|-------|---------|------|
| s12-spec-prepare | 材料清单 + 同步检查结论 | `report` 中包含所有收集到的材料路径和 `_sync-issues.md` 写入状态 |
| s14-spec-contradiction | 用户确认请求 | `status: "PENDING_CONFIRM"`，`confirm_questions` 包含矛盾裁决问题 |
| s15-spec-design-doc | 设计文档（瘦身版） | 文件路径见下文，`report` 中包含路径和决策摘要 |
| s16-spec-contract-draft | 契约草案 JSON | `.tmp/contract-draft/{module_id}/` 目录，`report` 中包含类型清单 |
| s18-spec-contract-conflict | 用户确认请求 | `status: "PENDING_CONFIRM"`，`confirm_questions` 包含冲突裁决问题 |
| s19-spec-internal-design | 落地规范 + 契约文件 + 索引更新 | 文件路径见下文，`report` 中包含完整文件清单 |

## 文件输出路径

- **设计文档**：`docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-设计文档.md`
- **落地规范**：`docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-落地规范.md`
- **契约文件**：`docs/contracts/{module_id}/{TypeName}.json`
- **模块契约索引**：`docs/contracts/{module_id}/_module-index.json`
- **全局契约索引**：`docs/contracts/_index.json`
- **契约总索引**：`docs/功能设计/_contracts.md`
- **项目同步问题**：`docs/功能设计/_sync-issues.md`
- **契约草案**（临时）：`.tmp/contract-draft/{module_id}/`

## Message 状态约定

- **s12, s15, s16, s19**：完成后上报 `status: "DONE"`
- **s14, s18**：需用户裁决时上报 `status: "PENDING_CONFIRM"`，`confirm_questions` 长度 1-4
- **发现意图缺陷**：上报 `status: "ERROR"`，`report` 中包含回退路径说明
