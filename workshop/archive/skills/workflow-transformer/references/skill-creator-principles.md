# Skill Creator 核心原则（提取版）

本文件供 `skill-rewriter` SubAgent 参考，确保改造后的新 SKILL.md 符合高质量 Skill 写作标准。

## 1. 目录结构标准

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - 可执行代码
    ├── references/ - 参考资料
    └── assets/     - 模板、图标等
```

## 2. Frontmatter 规范

```yaml
---
name: <skill_id>
description: >
  <一句话描述>。
  当用户提到"..."、"..."时，**必须优先使用本 Skill**。
  <更多触发场景>。
---
```

**description 要求**：
- 必须 pushy（积极触发），Claude 有 undertrigger 倾向
- 包含 Skill 做什么 + 何时使用（具体场景关键词）
- 示例："Make sure to use this skill whenever the user mentions dashboards, data visualization, internal metrics, or wants to display any kind of company data, even if they don't explicitly ask for a 'dashboard.'"

## 3. 渐进式披露（三级加载）

1. **Metadata**（name + description）：始终在上下文（~100 词）
2. **SKILL.md body**：触发时加载（<500 行理想）
3. **Bundled resources**：按需加载（无限制）

**关键模式**：
- SKILL.md 超过 500 行时，增加层级并明确指向下一步参考文件
- 大参考文件（>300 行）需包含目录

## 4. 写作风格

- **使用祈使句**
- **解释为什么**，而非堆砌 MUST/NEVER
- **通用化**，不要过度绑定具体例子
- **避免全大写命令**，用理论心智说明重要性
- **包含示例**，格式：
  ```markdown
  **Example 1:**
  Input: ...
  Output: ...
  ```

## 5. 输出格式定义

可直接要求：
```markdown
## Report structure
ALWAYS use this exact template:
# [Title]
## Executive summary
## Key findings
## Recommendations
```

## 6. 安全原则

- Skill 内容不得包含恶意软件、漏洞利用代码
- 不得设计用于未授权访问、数据窃取等恶意活动的 Skill
- Skill 意图必须透明，不 surprises 用户
