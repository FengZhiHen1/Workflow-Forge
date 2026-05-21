#!/usr/bin/env python3
"""
缓存断点分析脚本

分析 SubAgent prompt 的五段拼接结构，识别每段的"稳定性"（stable / variable-values / variable），
计算缓存命中率，并给出最优排列建议。

缓存原理（Anthropic prompt caching）：
  - 系统对新 prompt 与缓存中已有 prompt 做前缀匹配
  - 第一个不同的字节处为"缓存断点"
  - 断点之前的所有内容命中缓存（5 分钟 TTL）
  - 断点之后全部 miss

优化策略：
  将所有 stable 段放在最前，variable-values 段居中，variable 段置于末尾。
  最大化缓存前缀长度。

用法：
    python analyze_cache.py --template <prompt模板路径> [--output <输出JSON路径>]
"""

import argparse
import json
import re
import sys
from pathlib import Path


# 五段 prompt 模板的段名与默认稳定性
# stability: "stable" | "variable-values" | "variable"
DEFAULT_SEGMENTS = [
    {"name": "Skill声明", "stability": "variable", "note": "切换 Skill 时变化"},
    {"name": "WORKFLOW_CONTEXT", "stability": "variable-values", "note": "结构稳定，但 agent_id/stage_id 等字段值变化"},
    {"name": "STAGE_DIRECTION", "stability": "variable", "note": "每个 stage 完全不同"},
    {"name": "CONTRACT_READING_DUTY", "stability": "stable", "note": "所有 stage 完全一致"},
    {"name": "WORKFLOW_INJECTED_BANS", "stability": "stable", "note": "所有 stage 完全一致"},
]

# 稳定性权重（用于计算有效缓存贡献）
# stable 段在跨 stage 调用时完全命中
# variable-values 段在不换 stage 的重试中可命中，但跨 stage 时大概率 miss
# variable 段永远 miss
STABILITY_WEIGHT = {"stable": 1.0, "variable-values": 0.3, "variable": 0.0}


def estimate_segment_tokens(template_text: str) -> list:
    """从模板 markdown 中提取各段的估算 token 数（行数 × 平均每行 token 估算）"""
    segments = []
    current_name = None
    current_lines = []

    for line in template_text.split("\n"):
        # 检测段标题
        for seg in DEFAULT_SEGMENTS:
            seg_name = seg["name"]
            if seg_name in line and ("##" in line or "###" in line or "第" in line):
                if current_name and current_lines:
                    segments.append({"name": current_name, "lines": len(current_lines),
                                     "estimated_tokens": _estimate_lines_tokens(current_lines)})
                current_name = seg_name
                current_lines = []
                break
        else:
            if current_name:
                current_lines.append(line)

    if current_name and current_lines:
        segments.append({"name": current_name, "lines": len(current_lines),
                         "estimated_tokens": _estimate_lines_tokens(current_lines)})

    # 合并回 DEFAULT_SEGMENTS 的稳定性信息
    for seg in segments:
        for ds in DEFAULT_SEGMENTS:
            if ds["name"] in seg["name"]:
                seg["stability"] = ds["stability"]
                seg["stability_note"] = ds["note"]
                break
        else:
            seg["stability"] = "variable"
            seg["stability_note"] = ""

    return segments


def _estimate_lines_tokens(lines: list) -> int:
    """粗略估算：中文 ~25 chars/line → ~17 tokens/line，英文 ~60 chars/line → ~15 tokens/line"""
    text = "\n".join(lines)
    cjk = len(re.findall(r"[一-鿿]", text))
    total = len(text)
    # 粗略分流
    if cjk > total * 0.3:
        return round(total / 1.8)  # 中文为主
    else:
        return round(total / 3.5)  # 英文/代码为主


def analyze_cache(segments: list) -> dict:
    """分析当前排列的缓存效率"""
    total_tokens = sum(s["estimated_tokens"] for s in segments)
    cacheable_tokens = 0
    breakpoint_index = None

    for i, seg in enumerate(segments):
        if seg["stability"] == "variable":
            breakpoint_index = i
            break
        weight = STABILITY_WEIGHT.get(seg["stability"], 0)
        cacheable_tokens += round(seg["estimated_tokens"] * weight)

    hit_rate = cacheable_tokens / total_tokens if total_tokens > 0 else 0

    return {
        "current_order": [s["name"] for s in segments],
        "total_estimated_tokens": total_tokens,
        "cacheable_tokens": cacheable_tokens,
        "cache_hit_rate": round(hit_rate, 3),
        "breakpoint_index": breakpoint_index,
        "breakpoint_segment": segments[breakpoint_index]["name"] if breakpoint_index is not None else None,
    }


def suggest_optimal_order(segments: list) -> dict:
    """给出最优排列建议：stable 在前，variable-values 居中，variable 在末尾"""
    stable = [s for s in segments if s["stability"] == "stable"]
    var_vals = [s for s in segments if s["stability"] == "variable-values"]
    variable = [s for s in segments if s["stability"] == "variable"]

    optimal = stable + var_vals + variable
    optimal_analysis = analyze_cache(optimal)

    return {
        "optimal_order": [s["name"] for s in optimal],
        "reorder_required": [s["name"] for s in segments] != [s["name"] for s in optimal],
        "estimated_gain_tokens": optimal_analysis["cacheable_tokens"] - analyze_cache(segments)["cacheable_tokens"],
        "optimal_hit_rate": optimal_analysis["cache_hit_rate"],
    }


def main():
    parser = argparse.ArgumentParser(description="分析 SubAgent prompt 的缓存断点")
    parser.add_argument("--template", required=True, help="subagent-prompt-template.md 路径")
    parser.add_argument("--output", help="输出 JSON 文件路径（默认 stdout）")
    args = parser.parse_args()

    tmpl_path = Path(args.template)
    if not tmpl_path.is_file():
        print(json.dumps({"error": f"Template not found: {args.template}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    template_text = tmpl_path.read_text(encoding="utf-8")

    segments = estimate_segment_tokens(template_text)
    current = analyze_cache(segments)
    suggestion = suggest_optimal_order(segments)

    output = {
        "segments": [{k: v for k, v in s.items() if k != "lines"} for s in segments],
        "current_efficiency": current,
        "optimization": suggestion,
    }

    out_json = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out_json, encoding="utf-8")
        print(f"[INFO] Cache analysis written to {args.output}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
