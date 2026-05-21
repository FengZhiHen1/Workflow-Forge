#!/usr/bin/env python3
"""
跨平台时间戳获取脚本
兼容 Windows / Linux / macOS

用法：
    python get_timestamp.py           # 输出：2026-04-28 20:06:57
    python get_timestamp.py --iso     # 输出：2026-04-28T20:06:57+08:00
    python get_timestamp.py --format  # 输出：YYYY-MM-DD HH:MM:SS
"""

import datetime
import sys


def _get_shanghai_tz():
    """获取 Asia/Shanghai 时区对象，兼容各平台与 Python 版本。"""
    # 方案 1：Python 3.9+ 内置 zoneinfo（PEP 615）
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        pass

    # 方案 2：pytz（若已安装）
    try:
        import pytz
        return pytz.timezone("Asia/Shanghai")
    except Exception:
        pass

    # 方案 3：兜底——固定 UTC+8 偏移
    return datetime.timezone(datetime.timedelta(hours=8), name="CST")


def get_timestamp():
    """
    返回中国标准时间字符串，格式：YYYY-MM-DD HH:MM:SS
    示例：2026-04-28 20:06:57
    """
    tz = _get_shanghai_tz()
    now = datetime.datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def get_iso_timestamp():
    """
    返回 ISO 8601 格式时间字符串（带时区）。
    示例：2026-04-28T20:06:57+08:00
    """
    tz = _get_shanghai_tz()
    now = datetime.datetime.now(tz)
    return now.isoformat()


def get_format_hint():
    """返回占位符提示，用于模板文档。"""
    return "YYYY-MM-DD HH:MM:SS"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="获取跨平台、跨 Python 版本的中国标准时间（CST）时间戳。"
    )
    parser.add_argument(
        "--iso",
        action="store_true",
        help="输出 ISO 8601 格式时间（含时区偏移）",
    )
    parser.add_argument(
        "--format",
        action="store_true",
        dest="format_hint",
        help="仅输出格式占位符提示",
    )
    args = parser.parse_args()

    if args.format_hint:
        print(get_format_hint())
    elif args.iso:
        print(get_iso_timestamp())
    else:
        print(get_timestamp())
