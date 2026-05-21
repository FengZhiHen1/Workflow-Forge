# 契约读取指引

> 本文件为工作流级共享规范。所有 `project-design-pipeline@2.1.0` 的 Skill 在任务开始时必须依次读取以下文件。

执行任务前，必须依次读取：

1. `references/agent-protocol.md` — Agent 协议样板（契约读取、消息上报、降级熔断、确认点规范）
2. 编排器注入的 `workflow_refs` 中列出的文件（如有）
3. `references/directory-convention.md` — 全局目录结构约定
