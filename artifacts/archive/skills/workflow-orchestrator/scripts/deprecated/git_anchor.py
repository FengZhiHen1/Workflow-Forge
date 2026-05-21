#!/usr/bin/env python3
"""
Git Anchor Manager

管理工作流 stage 的 Git 锚点 tag 创建与回退。

用法:
    python git_anchor.py create \
        --instance wf-refactor-pipeline-20260509-001-a7f3 \
        --stage s2_refactor \
        --message-id 20260509-003-a7f3

    python git_anchor.py rollback \
        --tag wf-refactor-pipeline-20260509-001-a7f3-s2_refactor-20260509-003-a7f3-pre

    python git_anchor.py list --instance wf-...
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run_git(args_list: list, cwd: Path = None) -> tuple:
    """运行 git 命令，返回 (returncode, stdout, stderr)。"""
    try:
        result = subprocess.run(
            ["git"] + args_list,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            encoding="utf-8",
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git command not found"


def find_git_root() -> Path:
    """查找 git 仓库根目录。"""
    cwd = Path.cwd()
    code, stdout, _ = run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if code == 0 and stdout:
        return Path(stdout)
    return cwd


def build_tag_name(instance_id: str, stage_id: str, message_id: str) -> str:
    """构建标准化的 tag 名称。"""
    return f"{instance_id}-{stage_id}-{message_id}-pre"


def cmd_create(args):
    git_root = find_git_root()
    tag_name = build_tag_name(args.instance, args.stage, args.message_id)

    # 检查是否已存在
    code, _, _ = run_git(["rev-parse", tag_name], cwd=git_root)
    if code == 0:
        print(json.dumps({
            "success": True,
            "tag": tag_name,
            "already_existed": True,
            "git_root": str(git_root).replace("\\", "/"),
        }, ensure_ascii=False, indent=2))
        return

    code, stdout, stderr = run_git(
        ["tag", "-a", tag_name, "-m", f"Anchor before stage {args.stage} of {args.instance}"],
        cwd=git_root,
    )

    if code != 0:
        print(json.dumps({
            "error": f"Failed to create git tag: {stderr}",
            "tag": tag_name,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(json.dumps({
        "success": True,
        "tag": tag_name,
        "already_existed": False,
        "git_root": str(git_root).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


def cmd_rollback(args):
    git_root = find_git_root()

    # 验证 tag 存在
    code, _, _ = run_git(["rev-parse", args.tag], cwd=git_root)
    if code != 0:
        print(json.dumps({
            "error": f"Git tag not found: {args.tag}",
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 执行 checkout（保留 .agent/ 由调用方处理）
    code, stdout, stderr = run_git(
        ["checkout", args.tag, "--", "."],
        cwd=git_root,
    )

    if code != 0:
        print(json.dumps({
            "error": f"Git checkout failed: {stderr}",
            "tag": args.tag,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(json.dumps({
        "success": True,
        "tag": args.tag,
        "git_root": str(git_root).replace("\\", "/"),
        "message": f"Checked out {args.tag} into working tree",
    }, ensure_ascii=False, indent=2))


def cmd_list(args):
    git_root = find_git_root()
    prefix = args.instance + "-" if args.instance else ""

    code, stdout, stderr = run_git(
        ["tag", "-l", f"{prefix}*"],
        cwd=git_root,
    )

    if code != 0:
        print(json.dumps({
            "error": f"Failed to list tags: {stderr}",
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    tags = [t.strip() for t in stdout.split("\n") if t.strip()]
    print(json.dumps({
        "instance": args.instance or "all",
        "tags": tags,
        "count": len(tags),
        "git_root": str(git_root).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Git Anchor Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a git anchor tag")
    p_create.add_argument("--instance", required=True)
    p_create.add_argument("--stage", required=True)
    p_create.add_argument("--message-id", required=True)

    p_roll = sub.add_parser("rollback", help="Checkout a git anchor tag")
    p_roll.add_argument("--tag", required=True)

    p_list = sub.add_parser("list", help="List git anchor tags")
    p_list.add_argument("--instance", default="", help="Filter by instance prefix")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "rollback":
        cmd_rollback(args)
    elif args.command == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
