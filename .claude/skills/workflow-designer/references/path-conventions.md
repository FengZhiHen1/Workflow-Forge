# 产出物路径规范（v3.0.0）

> 本文档定义 workflow-designer 所有产出物的路径映射规则。
> SKILL.md 中引用本文档，不重复内嵌表格。

## 双重视角核心规则

> **产出物按生产车间规范落盘，产出物内容按消费者项目规范定位。**

| 场景 | 使用规范 |
|------|---------|
| 工作流产物的草稿路径（`$WD/`）和转正路径（`artifacts/workflows/`） | 生产车间规范 |
| SKILL.md 中说"读取 `xxx` 文件" | **消费者项目规范** |
| WORKFLOW.md 中描述工作流行为、引用脚本路径 | **消费者项目规范** |
| Skill 的 references/ 中的模板、示例路径 | **消费者项目规范** |

## 关键路径映射

| 生产车间路径 | 消费者项目路径 | 何时使用消费者路径 |
|-------------|--------------|------------------|
| `artifacts/workflows/<id>@<ver>/` | `.claude/workflows/<id>/` | WORKFLOW 文件内部引用工作流路径时 |
| `artifacts/skills/<id>/` | `.claude/skills/<id>/` | Skill 之间互相引用时 |
| `artifacts/contracts/` | `.claude/contracts/` | Skill 中引用通用契约时 |
| `artifacts/scripts/wfctl/` | `.claude/scripts/wfctl/` | Skill 中调用 wfctl 命令时 |
| — | `.agent/instances/<id>/` | 工作流/Skill 引用运行时状态时 |
| — | `.tmp/worktrees/` | 引用临时产物时 |

> **记住**：你产出的 Skill 运行在消费者项目中，它看到的文件系统是消费者的，不是生产车间的。SKILL.md 里绝对不能出现 `artifacts/` 路径——那是车间内部路径，消费者项目里不存在。

## 项目根相对路径规则

Skill 中引用工作流级共享资源（如 `directory-convention.md`、`agent-protocol.md`、工作流级 `scripts/`）时，**必须使用相对于项目根目录的绝对路径**，如：

- ✅ `.claude/workflows/<id>/references/directory-convention.md`
- ✅ `.claude/workflows/<id>/scripts/write_message.py`

**禁止**使用相对路径：

- ❌ `../references/directory-convention.md`
- ❌ `./references/directory-convention.md`
- ❌ `../../scripts/write_message.py`

原因：Skill 运行在消费者项目中，它不知道自己在 `.claude/skills/<id>/` 下。相对路径会因 Skill 目录深度不同而失效。项目根相对路径（以 `.claude/`、`.agent/`、`.tmp/` 开头）在所有上下文都唯一确定。

## 完整路径表

| 产物 | 草稿路径（`$WD` 下） | 转正路径（生产车间） | 消费者项目路径 |
|------|---------------------|---------------------|--------------|
| WORKFLOW.yaml | `$WD/WORKFLOW.yaml` | `artifacts/workflows/<id>@<ver>/WORKFLOW.yaml` | `.claude/workflows/<id>/WORKFLOW.yaml` |
| WORKFLOW.md | `$WD/WORKFLOW.md` | `artifacts/workflows/<id>@<ver>/WORKFLOW.md` | `.claude/workflows/<id>/WORKFLOW.md` |
| directory-convention.md | `$WD/references/directory-convention.md` | `artifacts/workflows/<id>@<ver>/references/directory-convention.md` | `.claude/workflows/<id>/references/directory-convention.md` |
| agent-protocol.md | `$WD/references/agent-protocol.md` | `artifacts/workflows/<id>@<ver>/references/agent-protocol.md` | `.claude/workflows/<id>/references/agent-protocol.md` |
| 工作流级 references/ | `$WD/references/` | `artifacts/workflows/<id>@<ver>/references/` | `.claude/workflows/<id>/references/` |
| 工作流级 scripts/ | `$WD/scripts/` | `artifacts/workflows/<id>@<ver>/scripts/` | `.claude/workflows/<id>/scripts/` |
| 工作流级 resources/ | `$WD/resources/` | `artifacts/workflows/<id>@<ver>/resources/` | `.claude/workflows/<id>/resources/` |
| 工作流归档 | — | `artifacts/archive/workflows/<id>@<ver>/` | `.claude/archive/workflows/<id>@<ver>/` |
| SKILL.md | `$WD/skills/<skill_id>/SKILL.md` | `artifacts/workflows/<id>@<ver>/skills/<skill_id>/SKILL.md` | `.claude/skills/<skill_id>/SKILL.md` |
| Skill references/ | `$WD/skills/<skill_id>/references/` | `artifacts/workflows/<id>@<ver>/skills/<skill_id>/references/` | `.claude/skills/<skill_id>/references/` |
| Skill scripts/ | `$WD/skills/<skill_id>/scripts/` | `artifacts/workflows/<id>@<ver>/skills/<skill_id>/scripts/` | `.claude/skills/<skill_id>/scripts/` |
| 决策文档 | `$WD/decision.md` | ❌ 不转正 | — |

> `$WD` = `.tmp/workflow-designer-<YYYYMMDD-HHMMSS>/`
>
> **草稿路径和转正路径**用生产车间规范（你的操作空间），**消费者项目路径**用于产出的文件内容中（SKILL.md 的引用、WORKFLOW.md 的描述）。绝不能在产出的 SKILL.md 中出现 `artifacts/` 路径。
