#!/usr/bin/env python3
"""验证测试文件头部声明的场景数与场景清单实际数量是否一致。

用法：
    python validate_header_consistency.py <test_file.py> <scenario_file.md>

退出码：
    0 — 数量一致
    1 — 数量不一致或文件缺失
"""

import re
import sys
from pathlib import Path


def extract_declared_count(test_file_path: str) -> int | None:
    """从测试文件头部提取声明的场景数。"""
    content = Path(test_file_path).read_text(encoding="utf-8")
    # 匹配头部注释中的 "覆盖场景数：123"
    match = re.search(r'[#\s]*覆盖场景数：\s*(\d+)', content)
    if match:
        return int(match.group(1))
    return None


def count_actual_scenarios(scenario_file_path: str) -> int:
    """从场景清单文件统计实际场景数。

    按编号行匹配，格式如：
    | H01 | ...
    | B42 | ...
    | E15 | ...
    | S03 | ...
    """
    content = Path(scenario_file_path).read_text(encoding="utf-8")
    pattern = r'^\|\s*[A-Z]\d+\s*\|'
    matches = re.findall(pattern, content, re.MULTILINE)
    return len(matches)


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python validate_header_consistency.py <test_file.py> <scenario_file.md>")
        return 1

    test_file = sys.argv[1]
    scenario_file = sys.argv[2]

    if not Path(test_file).exists():
        print(f"❌ 测试文件不存在: {test_file}")
        return 1

    if not Path(scenario_file).exists():
        print(f"❌ 场景清单文件不存在: {scenario_file}")
        return 1

    declared = extract_declared_count(test_file)
    actual = count_actual_scenarios(scenario_file)

    if declared is None:
        print("❌ 未在测试文件头部找到 '覆盖场景数：{N}'")
        print(f"   请在文件头部添加：覆盖场景数：{actual}")
        return 1

    if declared != actual:
        print(f"❌ 数量不一致：")
        print(f"   测试文件头部声明: {declared}")
        print(f"   场景清单实际统计: {actual}")
        print(f"   请修正头部为: 覆盖场景数：{actual}")
        return 1

    print(f"✅ 数量一致: {actual} 个场景")
    return 0


if __name__ == "__main__":
    sys.exit(main())
