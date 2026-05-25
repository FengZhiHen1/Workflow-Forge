#!/usr/bin/env python3
"""
审计报告生成脚本

汇总四个分析脚本的输出，按 5 个效率维度打分，生成带 ☑️ 勾选框的 Markdown 审计报告。

5 维度评分算法：
  - 缓存友好度：基于 cache_hit_rate（0-1 → 0-10 分）
  - Prompt 精简度：基于平均 token/file 和重复块数
  - 并发利用度：基于 parallelism_ratio
  - (已废弃) 确认点效率：confirmation_point 字段已移除
  - 上下文传递效率：基于上游 report 字段估算（需 LLM 补充）

用法：
    python generate_audit.py \
      --tokens-report <token JSON> \
      --cache-report <cache JSON> \
      --dag-report <dag JSON> \
      --duplicates-report <duplicates JSON> \
      [--baseline-report <优化前审计报告>] \
      [--output <输出Markdown路径>]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def score_cache(hit_rate: float) -> int:
    """缓存友好度：hit_rate 0-1 → 0-10"""
    return min(10, round(hit_rate * 12))


def score_prompt_leanness(tokens_report: dict, dupes_report: dict) -> int:
    """Prompt 精简度（近似）"""
    files = tokens_report.get("files", [])
    if not files:
        return 5
    avg_tokens = tokens_report["summary"]["total_estimated_tokens"] / len(files)
    dupes = dupes_report.get("summary", {}).get("total_redundant_blocks", 0)

    score = 10
    # 平均每文件超过 3000 token 扣分
    if avg_tokens > 3000:
        score -= min(3, round((avg_tokens - 3000) / 1000))
    # 重复块扣分
    score -= min(3, dupes)
    return max(1, score)


def score_concurrency(dag_report: dict) -> int:
    """并发利用度"""
    ratio = dag_report.get("summary", {}).get("parallelism_ratio", 1.0)
    max_para = dag_report.get("summary", {}).get("max_parallel_agents", 1)

    score = 5
    if ratio >= 2.0:
        score += 3
    elif ratio >= 1.5:
        score += 2
    elif ratio >= 1.2:
        score += 1

    if max_para >= 3:
        score += 2
    elif max_para >= 2:
        score += 1

    return min(10, score)


def score_confirmation_efficiency(dag_report: dict) -> int:
    """确认点效率（仅基于统计数据，语义分析由 LLM 补充）"""
    total = dag_report.get("summary", {}).get("total_real_stages", 1)
    conf = 0  # (已废弃)
    ratio = conf / max(total, 1)

    if ratio <= 0.2:
        return 9
    elif ratio <= 0.33:
        return 7
    elif ratio <= 0.5:
        return 5
    else:
        return 3


def score_context_efficiency(dag_report: dict) -> int:
    """上下文传递效率（近似，需 LLM 补充）"""
    # 基于 stage 数量和关键路径判断上下文链长度
    cp_len = dag_report.get("summary", {}).get("critical_path_length", 1)
    total = dag_report.get("summary", {}).get("total_real_stages", 1)

    # 关键路径越长，上下文传递链越长，效率越低
    if cp_len <= 5:
        return 8
    elif cp_len <= 10:
        return 6
    elif cp_len <= 15:
        return 4
    else:
        return 2


def generate_recommendations(tokens_report, cache_report, dag_report, dupes_report) -> list:
    """生成优化建议清单"""
    recs = []

    # 缓存优化建议
    if cache_report.get("optimization", {}).get("reorder_required"):
        gain = cache_report["optimization"]["estimated_gain_tokens"]
        recs.append({
            "id": "R01",
            "dimension": "缓存友好度",
            "title": "调整 SubAgent prompt 段顺序，将稳定段前置",
            "description": f"将 CONTRACT_READING_DUTY 和 WORKFLOW_INJECTED_BANS 移至 STAGE_DIRECTION 之前，"
                           f"预计提升缓存命中率至 {cache_report['optimization']['optimal_hit_rate']:.0%}",
            "estimated_token_saving": gain,
            "degradation_risk_percent": 0,
            "files": ["workflow-orchestrator/references/subagent-prompt-template.md"],
        })

    # DAG 并行优化
    bottleneck = dag_report.get("bottleneck_analysis", {})
    if bottleneck.get("max_parallel_low"):
        recs.append({
            "id": "R02",
            "dimension": "并发利用度",
            "title": "增大 max_parallel_agents 以释放并行能力",
            "description": bottleneck.get("message", ""),
            "estimated_token_saving": 0,
            "estimated_time_saving": "取决于并行 stage 数量",
            "degradation_risk_percent": 2,
            "files": ["WORKFLOW.yaml"],
        })

    parallel_opps = dag_report.get("parallel_opportunities", [])
    if parallel_opps:
        total_parallel = sum(g["count"] for g in parallel_opps)
        layer_info = ", ".join("L{}={}".format(g["level"], g["count"]) for g in parallel_opps[:5])
        recs.append({
            "id": "R03",
            "dimension": "并发利用度",
            "title": f"发现 {len(parallel_opps)} 个并行层级共 {total_parallel} 个可并行 stage",
            "description": f"各层并行数: {layer_info}",
            "estimated_token_saving": 0,
            "estimated_time_saving": f"释放并行可显著缩短总耗时",
            "degradation_risk_percent": 3,
            "files": ["WORKFLOW.yaml"],
        })

    # 重复内容优化
    dupes_summary = dupes_report.get("summary", {})
    if dupes_summary.get("literal_duplicates", 0) > 0:
        recs.append({
            "id": "R04",
            "dimension": "Prompt 精简度",
            "title": f"移除 {dupes_summary['literal_duplicates']} 处跨 Skill 字面重复段落",
            "description": "将重复指令提取为共享 reference 文件，各 Skill 改为引用而非复制",
            "estimated_token_saving": dupes_summary["literal_duplicates"] * 150,  # 粗略估算
            "degradation_risk_percent": 1,
            "files": [d["files"][0] for d in dupes_report.get("literal_duplicates", [])[:5]],
        })

    # 确认点优化
    conf_ratio = dag_report.get("summary", {}).get("confirmation_points", 0) / max(
        dag_report.get("summary", {}).get("total_real_stages", 1), 1
    )
    if conf_ratio > 0.33:
        recs.append({
            "id": "R05",
            "dimension": "(已废弃) 确认点效率",
            "title": f"确认点占比 {conf_ratio:.0%}，评估是否有可合并或异步化的确认点",
            "description": "需要 LLM 逐项审查每个 confirmation_point 的必要性",
            "estimated_time_saving": f"每移除一个确认点可节省约 1-2 分钟用户等待时间",
            "degradation_risk_percent": 4,
            "files": ["WORKFLOW.yaml"],
        })

    # Prompt 瘦身建议
    avg_tokens = tokens_report.get("summary", {}).get("total_estimated_tokens", 0) / max(
        tokens_report.get("summary", {}).get("files_scanned", 1), 1
    )
    if avg_tokens > 3000:
        recs.append({
            "id": "R06",
            "dimension": "Prompt 精简度",
            "title": f"平均每文件 {avg_tokens:.0f} token，检查是否有冗余指令",
            "description": "建议逐文件审查：移除保险条款、精简重复说明、将长 reference 改为按需加载",
            "estimated_token_saving": round(avg_tokens * 0.15 * tokens_report["summary"]["files_scanned"]),
            "degradation_risk_percent": 3,
            "files": [f["file"] for f in tokens_report.get("files", []) if f.get("total_estimated_tokens", 0) > 3000],
        })

    return sorted(recs, key=lambda r: r.get("estimated_token_saving", 0), reverse=True)


def build_report(scores: dict, recs: list, tokens_report, cache_report, dag_report, dupes_report,
                 baseline_scores: dict = None) -> str:
    """生成 Markdown 审计报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# 工作流效率审计报告",
        f"> 生成时间：{now}",
        "",
        "---",
        "",
        "## 执行摘要",
        "",
        "| 维度 | 评分 (0-10) |" + (" 优化前 | 变化 |" if baseline_scores else ""),
    ]

    if baseline_scores:
        lines.append("|------|------------|--------|------|")
        for dim, s in scores.items():
            before = baseline_scores.get(dim, s)
            delta = s - before
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            lines.append(f"| {dim} | {arrow} {s} | {before} | {'+' if delta >= 0 else ''}{delta} |")
    else:
        lines.append("|------|------------|")
        for dim, s in scores.items():
            lines.append(f"| {dim} | {s} |")

    avg_score = sum(scores.values()) / len(scores)
    lines.append("")
    lines.append(f"**综合评分：{avg_score:.1f} / 10**")
    if baseline_scores:
        avg_before = sum(baseline_scores.values()) / len(baseline_scores)
        lines.append(f"**优化前：{avg_before:.1f} / 10** → **优化后：{avg_score:.1f} / 10**")

    # 量化数据
    lines.extend([
        "",
        "### 量化指标",
        "",
        f"- 扫描文件数：{tokens_report.get('summary', {}).get('files_scanned', 0)}",
        f"- 总估算 Token：{tokens_report.get('summary', {}).get('total_estimated_tokens', 0):,}",
        f"- 当前缓存命中率：{cache_report.get('current_efficiency', {}).get('cache_hit_rate', 0):.1%}",
        f"- 关键路径长度：{dag_report.get('summary', {}).get('critical_path_length', 0)}",
        f"- 并行度比率：{dag_report.get('summary', {}).get('parallelism_ratio', 0)}",
        f"- 确认点数量：{dag_report.get('summary', {}).get('confirmation_points', 0)}",
        f"- 字面重复块：{dupes_report.get('summary', {}).get('literal_duplicates', 0)}",
        f"- 近似重复块：{dupes_report.get('summary', {}).get('approximate_duplicates', 0)}",
    ])

    # 优化建议
    lines.extend([
        "",
        "---",
        "",
        "## 优化建议清单",
        "",
        "> 勾选批准的条目后执行优化。劣化风险 >5% 的条目已标红。",
        "",
    ])

    total_saving = 0
    for r in recs:
        risk = r["degradation_risk_percent"]
        risk_label = f"🔴 {risk}%" if risk > 5 else f"🟡 {risk}%" if risk >= 3 else f"🟢 {risk}%"
        lines.append(f"### ☐ {r['id']}: {r['title']}")
        lines.append(f"- **维度**：{r['dimension']}")
        lines.append(f"- **描述**：{r['description']}")
        saving = r.get("estimated_token_saving", 0)
        total_saving += saving
        if saving:
            lines.append(f"- **预估 Token 节省**：{saving:,}")
        if r.get("estimated_time_saving"):
            lines.append(f"- **预估时间节省**：{r['estimated_time_saving']}")
        lines.append(f"- **劣化风险**：{risk_label}")
        if r.get("files"):
            lines.append(f"- **涉及文件**：{', '.join(r['files'][:5])}")
        lines.append("")

    lines.extend([
        "---",
        "",
        f"**预估总 Token 节省：{total_saving:,}**",
        "",
        "---",
        "",
        "## 附录：逐维度详析",
        "",
        "### 1. 缓存友好度",
        f"- 当前命中率：{cache_report.get('current_efficiency', {}).get('cache_hit_rate', 0):.1%}",
        f"- 缓存断点位：{cache_report.get('current_efficiency', {}).get('breakpoint_segment', 'N/A')}",
        f"- 最优命中率：{cache_report.get('optimization', {}).get('optimal_hit_rate', 0):.1%}",
        "",
        "### 2. Prompt 精简度",
        f"- 详见 `estimate_tokens.py` 和 `detect_duplicates.py` 输出",
        "",
        "### 3. 并发利用度",
        f"- 关键路径：{' → '.join(dag_report.get('critical_path', {}).get('path_stage_names', []))}",
        f"- 瓶颈分析：{dag_report.get('bottleneck_analysis', {}).get('message', 'N/A')}",
        "",
        "### 4. 确认点效率",
        f"- 确认点 Stage：{', '.join(dag_report.get('confirmation_point_stages', []))}",
        f"- *需要 LLM 逐项语义分析确认点必要性*",
        "",
        "### 5. 上下文传递效率",
        f"- *需要 LLM 审查 [STAGE_DIRECTION] 和上游 report 的实际内容*",
        "",
        "---",
        "",
        "*本报告由 workflow-efficiency-optimizer 自动生成。标注 \"需要 LLM\" 的维度需人工补充分析。*",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成效率审计报告")
    parser.add_argument("--tokens-report", required=True, help="estimate_tokens.py 的 JSON 输出")
    parser.add_argument("--cache-report", required=True, help="analyze_cache.py 的 JSON 输出")
    parser.add_argument("--dag-report", required=True, help="analyze_dag.py 的 JSON 输出")
    parser.add_argument("--duplicates-report", required=True, help="detect_duplicates.py 的 JSON 输出")
    parser.add_argument("--baseline-report", help="优化前审计报告（用于对比）")
    parser.add_argument("--output", required=True, help="输出 Markdown 报告路径")
    args = parser.parse_args()

    # 加载各报告
    def load_json(p):
        return json.loads(Path(p).read_text(encoding="utf-8"))

    tokens_report = load_json(args.tokens_report)
    cache_report = load_json(args.cache_report)
    dag_report = load_json(args.dag_report)
    dupes_report = load_json(args.duplicates_report)

    # 5 维度评分
    scores = {
        "缓存友好度": score_cache(cache_report.get("current_efficiency", {}).get("cache_hit_rate", 0)),
        "Prompt 精简度": score_prompt_leanness(tokens_report, dupes_report),
        "并发利用度": score_concurrency(dag_report),
        "确认点效率": score_confirmation_efficiency(dag_report),
        "上下文传递效率": score_context_efficiency(dag_report),
    }

    baseline_scores = None
    if args.baseline_report:
        # 从优化前报告中解析评分（简化：读 Markdown 中的表格行）
        baseline_text = Path(args.baseline_report).read_text(encoding="utf-8") if Path(args.baseline_report).exists() else ""
        baseline_scores = {}
        for dim in scores:
            for line in baseline_text.split("\n"):
                if dim in line and "|" in line:
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    for p in parts:
                        try:
                            baseline_scores[dim] = int(p)
                            break
                        except ValueError:
                            continue

    # 生成建议
    recs = generate_recommendations(tokens_report, cache_report, dag_report, dupes_report)

    # 生成报告
    report = build_report(scores, recs, tokens_report, cache_report, dag_report, dupes_report, baseline_scores)

    Path(args.output).write_text(report, encoding="utf-8")
    print(f"[INFO] Audit report written to {args.output}")


if __name__ == "__main__":
    main()
