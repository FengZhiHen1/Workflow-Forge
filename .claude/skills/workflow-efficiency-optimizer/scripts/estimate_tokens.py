#!/usr/bin/env python3
"""
Token 数量估算脚本

对 Markdown / YAML 文件进行字符级统计，按语言分流后用近似比率换算为 token 数。
输出 JSON，包含每个文件的估算值和各段（frontmatter / body / code blocks）的细分。

估算比率（基于 Claude BPE tokenizer 的经验值）：
  - CJK 字符：~1.5 chars/token
  - ASCII 文本（英文）：~4.0 chars/token
  - 代码块内容：~3.5 chars/token
  - 数字/标点：~1.0 chars/token

用法：
    python estimate_tokens.py --workflow-dir <工作流目录> [--output <输出JSON路径>]
    python estimate_tokens.py --files <file1> <file2> ... [--output <输出JSON路径>]
"""

import argparse
import json
import re
import sys
from pathlib import Path


# 字符分类正则
CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿　-〿＀-￯]")
ASCII_ALPHA_RE = re.compile(r"[a-zA-Z]")
DIGIT_PUNCT_RE = re.compile(r"[0-9!-/:-@[-`{-~]")

# token 换算比率（每 token 约含多少字符）
RATIO_CJK = 1.5
RATIO_ASCII = 4.0
RATIO_CODE = 3.5
RATIO_DIGIT_PUNCT = 1.0


def count_chars_by_type(text: str) -> dict:
    """统计文本中各类型字符数"""
    cjk = len(CJK_RE.findall(text))
    ascii_alpha = len(ASCII_ALPHA_RE.findall(text))
    digit_punct = len(DIGIT_PUNCT_RE.findall(text))
    # 剩余字符（空格、换行等）
    other = len(text) - cjk - ascii_alpha - digit_punct
    return {
        "cjk": cjk,
        "ascii_alpha": ascii_alpha,
        "digit_punct": digit_punct,
        "other": other,
        "total_chars": len(text),
    }


def estimate_tokens(chars: dict) -> int:
    """根据字符分类估算 token 数"""
    tokens = (
        chars["cjk"] / RATIO_CJK
        + chars["ascii_alpha"] / RATIO_ASCII
        + chars["digit_punct"] / RATIO_DIGIT_PUNCT
        + chars["other"] / RATIO_ASCII  # 空格等按英文比率
    )
    return round(tokens)


def extract_code_blocks(text: str) -> tuple:
    """提取代码块内容和剩余文本，返回 (code_text, remaining_text)"""
    code_parts = []
    remaining_parts = []
    # 匹配 ``` 围栏代码块
    fence_re = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    last_end = 0
    for match in fence_re.finditer(text):
        remaining_parts.append(text[last_end : match.start()])
        code_parts.append(match.group(1))
        last_end = match.end()
    remaining_parts.append(text[last_end:])
    return "\n".join(code_parts), "\n".join(remaining_parts)


def extract_frontmatter(text: str) -> tuple:
    """提取 YAML frontmatter (--- ... ---)，返回 (frontmatter, body)"""
    fm_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    match = fm_re.match(text)
    if match:
        return match.group(0), text[match.end() :]
    return "", text


def analyze_file(filepath: Path) -> dict:
    """分析单个文件的 token 估算"""
    try:
        raw = filepath.read_text(encoding="utf-8")
    except Exception:
        return {"file": str(filepath), "error": "Failed to read"}

    fm_text, body = extract_frontmatter(raw)
    code_text, prose_text = extract_code_blocks(body)

    fm_chars = count_chars_by_type(fm_text)
    body_chars = count_chars_by_type(prose_text)
    code_chars = count_chars_by_type(code_text)

    fm_tokens = estimate_tokens(fm_chars)
    body_tokens = estimate_tokens(body_chars)
    code_tokens_raw = estimate_tokens(code_chars)
    code_tokens = round(code_chars["total_chars"] / RATIO_CODE)

    return {
        "file": str(filepath),
        "total_chars": fm_chars["total_chars"] + body_chars["total_chars"] + code_chars["total_chars"],
        "total_estimated_tokens": fm_tokens + body_tokens + code_tokens,
        "sections": {
            "frontmatter": {"chars": fm_chars["total_chars"], "estimated_tokens": fm_tokens},
            "body_prose": {"chars": body_chars["total_chars"], "estimated_tokens": body_tokens},
            "code_blocks": {"chars": code_chars["total_chars"], "estimated_tokens": code_tokens},
        },
    }


def collect_files(workflow_dir: Path) -> list:
    """收集工作流目录下所有需分析的文件"""
    files = []
    patterns = ["*.md", "*.yaml", "*.yml"]
    for pattern in patterns:
        files.extend(workflow_dir.rglob(pattern))
    # 排除 .agent/, .tmp/, __pycache__/
    excluded = {".agent", ".tmp", "__pycache__", ".git", "node_modules"}
    files = [f for f in files if not any(e in f.parts for e in excluded)]
    return sorted(files, key=lambda p: str(p))


def main():
    parser = argparse.ArgumentParser(description="估算工作流文件的 token 数量")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workflow-dir", help="工作流目录路径")
    group.add_argument("--files", nargs="+", help="指定文件列表")
    parser.add_argument("--output", help="输出 JSON 文件路径（默认 stdout）")
    args = parser.parse_args()

    if args.workflow_dir:
        wf_dir = Path(args.workflow_dir)
        if not wf_dir.is_dir():
            print(json.dumps({"error": f"Not a directory: {args.workflow_dir}"}, ensure_ascii=False, indent=2))
            sys.exit(1)
        file_list = collect_files(wf_dir)
    else:
        file_list = [Path(f) for f in args.files]

    results = [analyze_file(f) for f in file_list]

    total_tokens = sum(r.get("total_estimated_tokens", 0) for r in results)
    total_chars = sum(r.get("total_chars", 0) for r in results)

    output = {
        "summary": {
            "files_scanned": len(results),
            "total_chars": total_chars,
            "total_estimated_tokens": total_tokens,
        },
        "files": results,
    }

    out_json = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out_json, encoding="utf-8")
        print(f"[INFO] Token report written to {args.output}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
