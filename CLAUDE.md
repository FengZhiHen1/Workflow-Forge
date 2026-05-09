# 工作空间定位

本工作空间为**工作流**的生产车间，与真实项目环境决然不同！！！

## 目录规范

- `docs/`：存放文档资料
- `scripts/`：存放基础设施脚本
- `contracts/`：存放通用契约
- `reference/`：存放用于借鉴的 Skill。**特别注意**，这些 Skill 不满足新的工作流规范要求，仅供参考其业务逻辑。
- `results/workflows/[workflow_id]/`：存放工作流产物
    - `results/workflows/[workflow_id]/WORKFLOW.md`：工作流定义文档
    - `results/workflows/[workflow_id]/skills/`：存放相应工作流使用到的 Skill。
- `results/skills/`：存放相应全局 Skill（如编排器）。