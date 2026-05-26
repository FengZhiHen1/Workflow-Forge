#!/usr/bin/env python3
"""
SKILL.md 边界校验脚本。
扫描 SKILL.md 中的工作流结构泄漏、路径违规、协议块等。

用法：
    python validate_skill_boundary.py --skill-md <path/to/SKILL.md>

退出码：
    0 - 无 critical 违规
    1 - 发现 critical 违规
"""

import argparse
import re
import sys
from pathlib import Path

VIOLATIONS = {
    "stage_reference": {
        "patterns": [
            r"Stage\s+[a-zA-Z0-9_-]+",
            r"\bp\d+[a-zA-Z0-9_-]*\b",
            r"\bstage_id\b",
            r"\bedges?\b",
        ],
        "severity": "critical",
        "message": "发现 Stage/edges 引用。SKILL.md 必须对工作流结构完全无知。",
    },
    "workflow_protocol": {
        "patterns": [
            r"\[WORKFLOW_CONFIG\]",
            r"\[WORKFLOW_MESSAGE\]",
            r"\bworkflow_id\b",
        ],
        "severity": "critical",
        "message": "发现工作流协议块。Skill 是执行层，不处理编排协议。",
    },
    "artifact_path": {
        "patterns": [
            r"artifacts\/",
            r"workshop\/",
        ],
        "severity": "critical",
        "message": "发现生产车间路径引用。SKILL.md 中应使用消费者项目规范路径。",
    },
    "subagent_scheduling": {
        "patterns": [
            r"调用\s+\w+\s+SubAgent",
            r"调度\s+\w+\s+SubAgent",
            r"启动\s+\w+\s+SubAgent",
        ],
        "severity": "critical",
        "message": "发现 SubAgent 调度。编排由工作流层处理，Skill 不调度 SubAgent。",
    },
    "downstream_trigger": {
        "patterns": [
            r"触发\s+\w+",
            r"进入\s+(下一|后续)\s*阶段?",
            r"通知编排器",
        ],
        "severity": "warning",
        "message": "疑似描述下游触发行为。Skill 只应上报 DONE，不描述后续。",
    },
    "missing_askuserquestion": {
        "severity": "critical",
        "message": "Skill 需要用户确认但 SKILL.md 未包含 AskUserQuestion。需要交互的 Skill 必须显式调用 AskUserQuestion 请求用户决策。仅靠描述性表格不够——SubAgent 不会把选项表自动理解为交互点。",
    },
}

# 行级排除规则：如果行中包含这些标记，则跳过（通常是示例中的错误写法或规范引用）
SKIP_MARKERS = [
    "✗",
    "错误",
    "不要",
    "禁止",
    "Incorrect",
    "WRONG",
    "BAD",
    "违规",
    "反例",
    "违规写法",
    "详见",
    "见 `",
    "来自",
    "来源",
    "不应该",
    "不应",
    "不能",
    "不可",
]

# 优先级/等级上下文标记：P0/P1/P2 在此类上下文中是领域优先级，非 Stage ID
PRIORITY_CONTEXT_MARKERS = [
    "模型崩塌",
    "显著偏差",
    "可接受偏差",
    "优先级",
    "级假设",
    "假设",
    "遗漏项",
]

# 表格上下文标记：如果一行是 markdown 表格行且包含这些词，可能是示例表格
TABLE_CONTEXT_MARKERS = ["正确", "正确替代", "正确写法", "原因", "说明", "替代", "写法"]

# 教学/引用上下文标记：如果一行包含这些词，说明是在解释规范或引用文件
EDUCATIONAL_MARKERS = [
    "编排器",
    "框架",
    "规范",
    "prompt",
    "注入",
    "替换规则",
    "详见",
    "参见",
    "参考",
]


def should_skip_line(line: str, match_text: str = None) -> bool:
    """判断一行是否属于示例中的错误写法、规范引用或教学材料，应跳过检查。"""
    # 直接包含跳过标记的行
    if any(marker in line for marker in SKIP_MARKERS):
        return True

    # markdown 表格行，且同时包含上下文标记——通常是正反例表格或规范映射表
    if line.strip().startswith("|") and line.strip().endswith("|"):
        if any(marker in line for marker in TABLE_CONTEXT_MARKERS):
            return True

    # 教学/解释上下文：如果一行在解释"为什么不应该"或引用规范，且包含违规词，跳过
    if any(marker in line for marker in EDUCATIONAL_MARKERS):
        return True

    # 优先级/等级上下文：P0/P1/P2 在此类上下文中是领域优先级，非 Stage ID
    if match_text and re.match(r'^[Pp]\d$', match_text):
        if any(marker in line for marker in PRIORITY_CONTEXT_MARKERS):
            return True

    return False


def validate_skill_boundary(skill_md_path: str) -> list[dict]:
    """扫描 SKILL.md，返回违规列表。"""
    content = Path(skill_md_path).read_text(encoding="utf-8")
    results = []
    lines = content.splitlines()

    for category, config in VIOLATIONS.items():
        # 跳过无 patterns 的检查项（由独立函数处理）
        if "patterns" not in config:
            continue
        for pattern in config["patterns"]:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                # 定位所在行
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                line = content[line_start:line_end].strip()
                line_num = content[:match.start()].count("\n") + 1

                # 跳过示例中的错误写法、规范引用、表格上下文
                if should_skip_line(line, match.group()):
                    continue

                # 额外启发式：如果是 frontmatter 中的 name 字段（skill 自身名称），不视为 stage_id
                if category == "stage_reference" and re.match(r"^name:\s*" + re.escape(match.group()), line):
                    continue

                # 额外启发式：如果是代码块中的示例（用反引号包裹的），通常是文件路径或模板值
                if line.count("`") >= 2:
                    backtick_contents = line.split("`")[1::2]
                    if any(match.group() in s for s in backtick_contents):
                        continue

                # 额外启发式：跳过 P0/P1/P2/P3 优先级标记（常见于文档/测试优先级，非 stage 引用）
                if category == "stage_reference" and re.match(r"^P[0-3]$", match.group()):
                    continue

                # 额外启发式：如果是 YAML/Markdown 模板的键名或值（如 phase: P0, p4_adversarial_history: []）
                if category == "stage_reference":
                    # 匹配 YAML 键值对：键名中包含匹配文本
                    if re.match(r'^\s*[\w_-]+\s*:\s*', line):
                        key_part = line.split(":", 1)[0].strip()
                        if match.group() in key_part:
                            continue
                    # 跳过纯模板数据行（如 "- P0: <ISO8601> — 初始化完成"）
                    if re.match(r'^\s*-\s+\w+\s*:', line):
                        continue

                results.append(
                    {
                        "category": category,
                        "severity": config["severity"],
                        "message": config["message"],
                        "line": line,
                        "line_num": line_num,
                        "match": match.group(),
                    }
                )

    # 去重：同一行同一类别只保留一条
    seen = set()
    unique_results = []
    for r in results:
        key = (r["category"], r["line_num"], r["match"])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    return unique_results


def check_askuserquestion_present(skill_md_path: str) -> list[dict]:
    """检查 SKILL.md 是否包含至少一处 AskUserQuestion 调用。"""
    content = Path(skill_md_path).read_text(encoding="utf-8")
    results = []

    if "AskUserQuestion" not in content:
        results.append(
            {
                "category": "missing_askuserquestion",
                "severity": VIOLATIONS["missing_askuserquestion"]["severity"],
                "message": VIOLATIONS["missing_askuserquestion"]["message"],
                "line": "N/A（全文搜索）",
                "line_num": 0,
                "match": "AskUserQuestion 未找到",
            }
        )

    return results


def check_choices_alignment(skill_md_path: str, expected_choices: list[str]) -> list[dict]:
    """检查 SKILL.md 中是否至少有一处 AskUserQuestion 的选项包含所有 expected_choices。

    策略：在每个 AskUserQuestion 出现位置的上下文（前后 500 字符）中提取引号字符串，
    检查 expected_choices 是否全部被覆盖。若 Skill 有多个 AskUserQuestion（如内部澄清 +
    路由选择），只要路由选择类的选项覆盖了 choices 即可通过。
    """
    content = Path(skill_md_path).read_text(encoding="utf-8")
    results = []

    if not expected_choices:
        return results

    auq_positions = [m.start() for m in re.finditer(r"AskUserQuestion", content)]
    if not auq_positions:
        results.append(
            {
                "category": "choices_mismatch",
                "severity": "critical",
                "message": "未找到 AskUserQuestion，无法校验 choices 对齐。",
                "line": "N/A",
                "line_num": 0,
                "match": f"expected choices: {expected_choices}",
            }
        )
        return results

    # 收集每个 AskUserQuestion 上下文中的引号字符串
    found_choices = set()
    for pos in auq_positions:
        context = content[max(0, pos - 300) : min(len(content), pos + 500)]
        # 提取双引号字符串（选项文本常见写法）
        quoted = set(re.findall(r'"([^"]+)"', context))
        for choice in expected_choices:
            if choice in quoted:
                found_choices.add(choice)

    missing = set(expected_choices) - found_choices
    if missing:
        results.append(
            {
                "category": "choices_mismatch",
                "severity": "critical",
                "message": (
                    f"AskUserQuestion 选项与 edges choices 不匹配。"
                    f"缺失的选项: {sorted(missing)}。"
                    f"expected: {expected_choices}"
                ),
                "line": "N/A（多上下文搜索）",
                "line_num": 0,
                "match": f"missing choices: {sorted(missing)}",
            }
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验 SKILL.md 是否遵守 Skill-工作流边界"
    )
    parser.add_argument("--skill-md", required=True, help="SKILL.md 文件路径")
    parser.add_argument(
        "--expect-askuserquestion",
        action="store_true",
        help="要求 SKILL.md 必须包含 AskUserQuestion（用于需要用户交互的 Stage）",
    )
    parser.add_argument("--choices", nargs="+", default=None, help="预期的 AskUserQuestion 选项值列表")
    args = parser.parse_args()

    if not Path(args.skill_md).exists():
        print(f"ERROR: 文件不存在: {args.skill_md}", file=sys.stderr)
        return 2

    results = validate_skill_boundary(args.skill_md)

    # 可选检查：需要用户交互的 Stage 要求 AskUserQuestion 存在
    if args.expect_askuserquestion:
        results.extend(check_askuserquestion_present(args.skill_md))

    # 可选检查：路由选择类的 AskUserQuestion 选项须与 edges choices 对齐
    if args.choices is not None:
        results.extend(check_choices_alignment(args.skill_md, args.choices))

    critical = [r for r in results if r["severity"] == "critical"]
    warnings = [r for r in results if r["severity"] == "warning"]

    if critical:
        print(f"CRITICAL: 发现 {len(critical)} 处违规（必须修正）\n")
        for r in critical:
            print(f"  [{r['category']}] 第 {r['line_num']} 行")
            print(f"    内容: {r['line'][:100]}")
            print(f"    匹配: '{r['match']}'")
            print(f"    说明: {r['message']}\n")

    if warnings:
        print(f"WARNING: 发现 {len(warnings)} 处潜在违规（建议检查）\n")
        for r in warnings:
            print(f"  [{r['category']}] 第 {r['line_num']} 行")
            print(f"    内容: {r['line'][:100]}")
            print(f"    匹配: '{r['match']}'")
            print(f"    说明: {r['message']}\n")

    if not critical and not warnings:
        status_parts = ["PASS: 无违规"]
        checks = ["边界"]
        if args.expect_askuserquestion:
            checks.append("AskUserQuestion 存在性")
        if args.choices is not None:
            checks.append("choices 对齐")
        status_parts[0] = f"PASS: 无违规（{' + '.join(checks)}检查通过）"
        print(status_parts[0])
        return 0

    if critical:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
