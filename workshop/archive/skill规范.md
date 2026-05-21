> 本文档仅供参考，绝对不要以这个为真正的标准。
> 需要根据实际情况灵活调整。
>
> 本文档已经过时。新的设计中 Skill 保持独立。工作流相关规范由提示词注入。


**Skill 定义规范 v1.0.0**

---

## 1. 文件体系

Skill 是**自包含的能力单元**，可被原生平台直接调用，也可被工作流编排器调度。

```
.claude/
├── contracts/
│   └── common.md                    # 通用契约（所有 Skill 共享，纳入版本控制）
└── skills/
    └── <skill_id>/
        ├── skill.md                 # Skill 主文件（标准 Frontmatter + 正文）
        └── references/
            ├── contract-input.md    # 专用输入契约
            ├── contract-output.md   # 专用输出契约
            └── ...                  # 其他参考资料
```

**关键约定**：
- `.claude/contracts/common.md` 纳入版本控制，作为工作流基础设施。
- Skill 的 `references/` 目录纳入版本控制，由 Skill 作者维护。
- `skill_id` 与目录名严格一致，kebab-case。

---

## 2. 通用契约规范（`.claude/contracts/common.md`）

所有被工作流编排器调度的 SubAgent 必须读取并遵守此契约。编排器在启动 SubAgent 时，从本文件提取**硬禁令段落**，追加到 system prompt 末尾。

完整模板见 `reference/templates/common-contract.template.md`。

---

## 3. 专用契约规范

### 3.1 专用输入契约（`references/contract-input.md`）

定义该 Skill 的输入参数、任务模式、上游依赖规则。

完整模板见 `reference/templates/contract-input.template.md`。

### 3.2 专用输出契约（`references/contract-output.md`）

定义该 Skill 的产出规范、Message 必填字段、产物路径。

完整模板见 `reference/templates/contract-output.template.md`。

---

## 4. Skill 主文件规范（`skill.md`）

采用**标准 Frontmatter + 双段正文**结构：前半段为**外部对接协议**（与编排器交互），后半段为**内部执行规范**（自主任务流程）。两段之间用 `---` 分隔。

完整模板见 `reference/templates/skill.template.md`。其中 `[WORKFLOW_CONFIG]` 配置块详见 `reference/templates/workflow-config.template.json`。

---

## 5. 编排器注入规范

编排器启动 SubAgent 时，在 Skill 的 system prompt 后追加以下内容：

### 5.1 工作流上下文注入（精简版）

完整模板见 `reference/templates/workflow-context.template.md`。

### 5.2 硬禁令注入（从通用契约提取）

完整模板见 `reference/templates/workflow-injected-bans.template.md`。

---

## 6. 降级熔断机制

### 6.1 方案级降级（强制上报）

| 触发条件 | Skill 行为 | 上报内容 |
|---------|-----------|---------|
| 原定算法无法运行（如 ILP 求解器缺失） | 停止执行，不降级 | `status: PENDING_CONFIRM`，`confirm_questions: ["原定算法 X 无法运行，建议降级为 Y。是否同意？"]` |
| 用户要求跳过 mandatory 阶段 | 停止执行 | `status: PENDING_CONFIRM`，说明冲突 |
| 精度要求与资源约束矛盾 | 停止执行 | `status: PENDING_CONFIRM`，提供选项 |

### 6.2 资源级降级（自主执行 + 报告）

| 触发条件 | Skill 行为 | 报告要求 |
|---------|-----------|---------|
| OOM | 分批计算 / 降采样 / 换稀疏矩阵 | `report` 中说明："因内存限制，采用分批计算，批次大小调整为 X" |
| 超时 | 减少迭代次数 / 简化启发式 | `report` 中说明："因超时，迭代次数从 1000 降至 500" |
| 依赖安装失败 | 降级到标准库等价实现 | `report` 中说明："依赖 X 缺失，改用标准库实现 Y" |

---

## 7. 与 Workflow 规范的衔接

- Workflow Reference 的 `stages[].skill_id` 匹配 `.claude/skills/` 下的目录名。
- 编排器启动 SubAgent 时，读取 `skill.md` 的 Markdown Body 作为 system prompt，追加 `[WORKFLOW_CONTEXT]` 和 `[WORKFLOW_INJECTED_BANS]`。
- Skill 的 `[WORKFLOW_CONFIG]` 代码块供编排器快速提取契约路径和任务模式，无需解析整个 Markdown。
- Skill 执行完毕后，通过 `write_message.py` 写入 Message，编排器读取 Message 推进 Workflow Instance 状态机。

---

## 8. 版本升级

- Skill 的 `version` 字段语义化升级。
- 通用契约 `.claude/contracts/common.md` 升级时，所有 Skill 自动适用新版本（因 Skill 每次启动都重新读取）。
- 专用契约升级时，Skill 的 `version` 同步升级。