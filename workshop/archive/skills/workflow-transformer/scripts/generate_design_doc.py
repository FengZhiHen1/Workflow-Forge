#!/usr/bin/env python3
"""
Workflow Transformer 设计文档生成脚本。

生成临时设计文档，方便用户审阅改造方案。
输出包含：决策摘要 + 产物预览（选项 C）。
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_doc(analysis_path: str, decisions_path: str, workflow_yaml_path: str,
                 workflow_md_path: str, skill_md_path: str, output_path: str,
                 additional_skill_mds: list = None):
    """生成设计文档。
    
    Args:
        additional_skill_mds: 多 Skill 模式下额外的 SKILL.md 路径列表
    """

    analysis = load_json(analysis_path)
    decisions = load_text(decisions_path)
    workflow_yaml = load_text(workflow_yaml_path)
    workflow_md = load_text(workflow_md_path)
    skill_md = load_text(skill_md_path)
    
    # --- 格式适配器：兼容旧 Skill 改造 与 Workflow v2 升级 两种报告格式 ---
    # 1. source_skills 适配
    if not analysis.get("source_skills") and analysis.get("source_workflow"):
        sw = analysis["source_workflow"]
        analysis["source_skills"] = [{
            "path": sw.get("path", ""),
            "name": sw.get("workflow_id", sw.get("path", "")),
            "line_count": sw.get("stage_count", 0)
        }]
    
    # 2. proposed_stages / proposed_edges 适配
    pv2 = analysis.get("proposed_v2_structure", {})
    if not analysis.get("proposed_stages") and pv2.get("stages"):
        analysis["proposed_stages"] = pv2["stages"]
    if not analysis.get("proposed_edges") and pv2.get("edges"):
        analysis["proposed_edges"] = pv2["edges"]
    # --- 格式适配结束 ---
    
    mode = analysis.get("mode", "single")
    source_skills = analysis.get("source_skills", [])
    additional_skills = []
    if additional_skill_mds:
        for path in additional_skill_mds:
            additional_skills.append(load_text(path))

    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 标题
    lines.append("# Workflow 改造设计文档")
    lines.append("")
    lines.append(f"> 生成时间：{now}")
    lines.append("> 本文档为临时审阅文档，用户确认后产物将转正")
    lines.append("")

    # 1. 改造决策摘要
    lines.append("## 一、改造决策摘要")
    lines.append("")
    lines.append("### 1.1 改造范围")
    lines.append("")
    
    if mode == "multi" and len(source_skills) > 1:
        lines.append(f"- **改造模式**：多 Skill 合并改造（共 {len(source_skills)} 个 Skill）")
        lines.append("")
        for i, source in enumerate(source_skills):
            lines.append(f"- **Skill {i+1}**：`{source.get('path', 'N/A')}`")
            lines.append(f"  - 名称：{source.get('name', 'N/A')}")
            lines.append(f"  - 行数：{source.get('line_count', 'N/A')}")
    else:
        source = source_skills[0] if source_skills else {}
        lines.append(f"- **源 Skill**：`{source.get('path', 'N/A')}`")
        lines.append(f"- **Skill 名称**：{source.get('name', 'N/A')}")
        lines.append(f"- **原文件行数**：{source.get('line_count', 'N/A')}")
    lines.append("")

    lines.append("### 1.2 核心改造策略")
    lines.append("")
    lines.append("| 改造项 | 策略 | 理由 |")
    lines.append("|--------|------|------|")

    aq_count = len(analysis.get("askuserquestion_points", []))
    sub_count = len(analysis.get("subagent_calls", []))
    stage_count = len(analysis.get("proposed_stages", []))
    rel_count = len(analysis.get("skill_relationships", []))

    lines.append(f"| AskUserQuestion 映射 | 全部提升为 Stage 级 `confirmation_point` | 共识别 {aq_count} 个确认点，符合 v2 规范 |")
    lines.append(f"| SubAgent 调用 | 全部外提为 Workflow Stage | 共识别 {sub_count} 个内部 SubAgent，因 SubAgent 不能调度 SubAgent |")
    lines.append(f"| Stage 拆分 | 细粒度拆分，共 {stage_count} 个 Stage | 每个确认点独立 Stage，回退粒度最细 |")
    if mode == "multi" and rel_count > 0:
        lines.append(f"| Skill 间关系 | 显式化为 Workflow edges | 共识别 {rel_count} 个跨 Skill 依赖 |")
    lines.append("")

    lines.append("### 1.3 用户确认的关键决策")
    lines.append("")
    lines.append(decisions if decisions.strip() else "*（无额外用户决策记录）*")
    lines.append("")

    # 2. Stage 清单表
    lines.append("## 二、Stage 清单")
    lines.append("")
    lines.append("| Stage ID | 名称 | Skill ID | Mandatory | Confirmation | 来源旧 Skill 步骤 |")
    lines.append("|----------|------|----------|-----------|--------------|------------------|")

    for stage in analysis.get("proposed_stages", []):
        sid = stage.get("stage_id", "")
        name = stage.get("name", "")
        skill_id = stage.get("skill_id", "")
        mandatory = "是" if stage.get("mandatory", True) else "否"
        confirm = "是" if stage.get("confirmation_point", False) else "否"
        derived = stage.get("derived_from", "")
        lines.append(f"| `{sid}` | {name} | `{skill_id}` | {mandatory} | {confirm} | {derived} |")

    lines.append("")

    # 3. Edges 流转说明
    lines.append("## 三、Edges 流转说明")
    lines.append("")

    edges = analysis.get("proposed_edges", [])
    if edges:
        lines.append("```")
        for edge in edges:
            fr = edge.get("from", "")
            to = edge.get("to", "")
            cond = edge.get("condition", "")
            reason = edge.get("reason", "")
            loop_info = ""
            if edge.get("max_loop"):
                loop_info = f" [max_loop={edge['max_loop']}]"
            lines.append(f"{fr} --{cond}--> {to}{loop_info}")
            if reason:
                lines.append(f"  理由：{reason}")
        lines.append("```")
    else:
        lines.append("*（线性执行，无特殊 edges）*")
    lines.append("")

    # 4. 新 Skill 结构摘要
    lines.append("## 四、新 Skill 结构摘要")
    lines.append("")

    # 从 skill_md 提取 frontmatter
    skill_lines = skill_md.splitlines()
    in_frontmatter = False
    frontmatter_lines = []
    for sl in skill_lines:
        if sl.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                break
        if in_frontmatter:
            frontmatter_lines.append(sl)

    lines.append("### 4.1 Frontmatter")
    lines.append("")
    if frontmatter_lines:
        lines.append("```yaml")
        lines.extend(frontmatter_lines)
        lines.append("```")
    else:
        lines.append("*（未能提取 frontmatter）*")
    lines.append("")

    # 提取 SKILL.md 的主要章节
    lines.append("### 4.2 主要章节")
    lines.append("")
    sections = []
    for sl in skill_lines:
        if sl.startswith("# ") and not sl.startswith("# "):
            continue
        if sl.startswith("## "):
            sections.append(sl[3:].strip())
        elif sl.startswith("### "):
            sections.append("  - " + sl[4:].strip())

    if sections:
        for sec in sections[:20]:  # 限制数量
            lines.append(f"- {sec}")
        if len(sections) > 20:
            lines.append(f"- ... 等共 {len(sections)} 个章节")
    else:
        lines.append("*（未能提取章节结构）*")
    lines.append("")

    # 与旧 Skill 的差异
    lines.append("### 4.3 与旧 Skill 的关键差异")
    lines.append("")
    lines.append("| 维度 | 旧 Skill | 新 Skill |")
    lines.append("|------|---------|---------|")
    lines.append("| 用户确认 | 直接调用 `AskUserQuestion` | 上报 `PENDING_CONFIRM`，由编排器处理 |")
    lines.append("| SubAgent 调度 | Skill 内部嵌套调用 | **禁止**——已外提为 Workflow Stage |")
    lines.append("| 协议层 | 无 | 新增外部对接协议（契约读取、输入校验、输出上报） |")
    lines.append("| 配置块 | 无 | 新增 `[WORKFLOW_CONFIG]` JSON 块 |")
    lines.append("| 产物目录 | 可能分散 | 统一在 `results/workflows/<id>@<ver>/skills/<skill_id>/` |")
    lines.append("")

    # 5. 待用户审阅事项
    lines.append("## 五、待用户重点审阅")
    lines.append("")

    risks = analysis.get("risk_notes", [])
    if risks:
        lines.append("### 5.1 风险提示")
        lines.append("")
        for i, risk in enumerate(risks, 1):
            lines.append(f"{i}. {risk}")
        lines.append("")

    lines.append("### 5.2 建议审阅重点")
    lines.append("")
    lines.append("1. **Stage 拆分粒度**：确认每个 confirmation_point 的位置是否符合业务直觉")
    lines.append("2. **Edges 流转**：特别是 `failure` 回退路径和 `max_loop` 设置")
    lines.append("3. **Skill ID 命名**：新提取的 Skill 名称是否清晰、不冲突")
    lines.append("4. **并发规则**：`allowed_parallel_stages` 是否合理")
    lines.append("5. **新 SKILL.md 的业务逻辑**：确认核心业务能力未被误删或曲解")
    if mode == "multi":
        lines.append("6. **跨 Skill 衔接**：确认 Skill A 到 Skill B 的流转条件（`always` vs `confirmed`）是否符合业务直觉")
        lines.append("7. **数据流完整性**：确认 Skill A 的输出产物路径与 Skill B 的输入期望一致")
    lines.append("")

    # 6. 产物文件清单
    lines.append("## 六、产物文件清单")
    lines.append("")
    lines.append("| 文件 | 路径 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| WORKFLOW.yaml | `{workflow_yaml_path}` | 机器规范 |")
    lines.append(f"| WORKFLOW.md | `{workflow_md_path}` | 人类可读文档 |")
    if mode == "multi" and additional_skill_mds:
        lines.append(f"| 新 SKILL.md (Skill 1) | `{skill_md_path}` | 改造后的 Skill |")
        for i, path in enumerate(additional_skill_mds, 2):
            lines.append(f"| 新 SKILL.md (Skill {i}) | `{path}` | 改造后的 Skill |")
    else:
        lines.append(f"| 新 SKILL.md | `{skill_md_path}` | 改造后的 Skill |")
    lines.append("")
    
    # 多 Skill 模式下额外章节
    if mode == "multi":
        lines.append("## 七、Skill 间关系映射")
        lines.append("")
        lines.append("### 7.1 关系图谱")
        lines.append("")
        
        relationships = analysis.get("skill_relationships", [])
        if relationships:
            lines.append("| 关系 | 从 Skill | 到 Skill | 类型 | 描述 | Edge 建议 |")
            lines.append("|------|---------|---------|------|------|----------|")
            for rel in relationships:
                from_idx = rel.get("from_skill_index", 0)
                to_idx = rel.get("to_skill_index", 0)
                from_name = source_skills[from_idx].get("name", f"Skill {from_idx}") if from_idx < len(source_skills) else f"Skill {from_idx}"
                to_name = source_skills[to_idx].get("name", f"Skill {to_idx}") if to_idx < len(source_skills) else f"Skill {to_idx}"
                rel_type = rel.get("relation_type", "")
                desc = rel.get("description", "")
                edge = rel.get("suggested_edge", {})
                edge_str = f"`{edge.get('condition', 'always')}`" if edge else ""
                lines.append(f"| {from_name} → {to_name} | {from_name} | {to_name} | {rel_type} | {desc} | {edge_str} |")
        else:
            lines.append("*（未识别显式关系，默认为线性执行）*")
        lines.append("")
        
        lines.append("### 7.2 Stage 来源映射")
        lines.append("")
        lines.append("| Stage ID | 名称 | 来源 Skill |")
        lines.append("|----------|------|-----------|")
        for stage in analysis.get("proposed_stages", []):
            sid = stage.get("stage_id", "")
            name = stage.get("name", "")
            skill_idx = stage.get("derived_from_skill_index", 0)
            skill_name = source_skills[skill_idx].get("name", f"Skill {skill_idx}") if skill_idx < len(source_skills) else f"Skill {skill_idx}"
            lines.append(f"| `{sid}` | {name} | {skill_name} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**确认后操作**：主 Agent 将把上述产物从 `.tmp/` 移动到 `results/workflows/<id>@<ver>/` 下。")
    lines.append("")

    # 写入文件
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"设计文档已生成：{output}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="生成 Workflow 改造设计文档")
    parser.add_argument("--analysis", required=True, help="analyzer 报告 JSON 路径")
    parser.add_argument("--decisions", required=True, help="设计决策 Markdown 路径")
    parser.add_argument("--workflow-yaml", required=True, help="WORKFLOW.yaml 路径")
    parser.add_argument("--workflow-md", required=True, help="WORKFLOW.md 路径")
    parser.add_argument("--skill-md", required=True, help="新 SKILL.md 路径")
    parser.add_argument("--additional-skill-mds", nargs="*", default=[], help="多 Skill 模式下额外的 SKILL.md 路径列表")
    parser.add_argument("--output", required=True, help="输出设计文档路径")

    args = parser.parse_args()

    try:
        sys.exit(generate_doc(
            args.analysis,
            args.decisions,
            args.workflow_yaml,
            args.workflow_md,
            args.skill_md,
            args.output,
            args.additional_skill_mds
        ))
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
