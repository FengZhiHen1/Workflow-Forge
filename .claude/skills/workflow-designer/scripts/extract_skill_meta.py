#!/usr/bin/env python3
"""
提取 skills/ 目录下所有 Skill 的 name + description。

输出 JSON:
    {"skills": [{"skill_id": "...", "name": "...", "description": "..."}, ...]}

调用方式:
    python extract_skill_meta.py --skills-dir <path/to/skills/>
"""

import argparse
import json
import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def extract_frontmatter(content: str) -> dict:
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}
    raw = m.group(1)
    result = {}
    for line in raw.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description="提取 skills/ 下所有 Skill 的 name 和 description")
    parser.add_argument("--skills-dir", required=True, help="skills/ 目录路径")
    args = parser.parse_args()

    skills_dir = Path(args.skills_dir)
    if not skills_dir.exists():
        print(json.dumps({"skills": [], "error": f"目录不存在: {skills_dir}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    skills = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_text(encoding="utf-8")
        fm = extract_frontmatter(content)
        skills.append({
            "skill_id": skill_dir.name,
            "name": fm.get("name", ""),
            "description": fm.get("description", ""),
        })

    print(json.dumps({"skills": skills}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
