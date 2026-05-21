#!/usr/bin/env python3
"""
YAML 转 JSON 工具。

供 SubAgent 使用：SubAgent 输出 YAML 格式的分析报告，
调用本脚本转换为标准 JSON，供下游脚本消费。
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("错误：需要 PyYAML。请安装：pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="将 YAML 转换为标准 JSON")
    parser.add_argument("--input", required=True, help="输入 YAML 文件路径")
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：输入文件不存在：{args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"YAML 解析错误：{e}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"转换完成：{args.output}")
    sys.exit(0)


if __name__ == "__main__":
    main()
