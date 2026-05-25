"""dashboard 命令：手动生成 HTML Dashboard。"""

from __future__ import annotations

import argparse
from pathlib import Path

from services.dashboard_builder import generate_project_dashboard, generate_instance_dashboard


def register_dashboard(subparsers):
    p = subparsers.add_parser("dashboard", help="生成 HTML Dashboard（手动触发）")
    p.add_argument("--instance", help="实例 ID（不提供则生成项目全局首页）")
    p.add_argument("--output", type=Path, help="输出路径（默认 .tmp/dashboard/）")
    p.set_defaults(handler=_handle_dashboard)


def _handle_dashboard(args) -> dict:
    if args.instance:
        return generate_instance_dashboard(args.instance, args.output)
    return generate_project_dashboard(args.output)
