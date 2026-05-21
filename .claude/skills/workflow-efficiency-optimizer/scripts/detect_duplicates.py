#!/usr/bin/env python3
"""
跨 Skill 重复内容检测脚本

扫描 skills/ 目录下所有 SKILL.md，使用 n-gram 文本相似度检测
跨 Skill 的重复段落和指令块。

方法：
  1. 提取每个 SKILL.md 的正文（去除 frontmatter）
  2. 按段落切分
  3. 对每个段落计算标准化哈希（去空白、小写）
  4. 跨文件匹配相同哈希 → 字面重复
  5. 对长段落做 n-gram Jaccard 相似度 → 语义近似重复

用法：
    python detect_duplicates.py --skills-dir <skills目录> [--output <输出JSON路径>]
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def extract_body(filepath: Path) -> str:
    """提取 SKILL.md 正文（去除 YAML frontmatter）"""
    text = filepath.read_text(encoding="utf-8")
    fm_re = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
    match = fm_re.match(text)
    if match:
        return text[match.end():]
    return text


def normalize(text: str) -> str:
    """标准化文本：去多余空白、小写"""
    return re.sub(r"\s+", " ", text).strip().lower()


def paragraphize(text: str, min_chars: int = 50) -> list:
    """将文本按空行切分为段落，过滤太短的"""
    paras = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paras if len(p.strip()) >= min_chars]


def hash_text(text: str) -> str:
    """计算标准化文本的 MD5 哈希"""
    return hashlib.md5(normalize(text).encode("utf-8")).hexdigest()


def ngrams(text: str, n: int = 3) -> set:
    """生成字符 n-gram 集合"""
    clean = normalize(text)
    return {clean[i:i+n] for i in range(len(clean) - n + 1)}


def jaccard(a: set, b: set) -> float:
    """Jaccard 相似度"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_duplicates(skills_dir: Path) -> dict:
    """扫描 skills/ 目录，检测重复段落"""
    skill_files = list(skills_dir.glob("*/SKILL.md"))
    if not skill_files:
        return {"error": f"No SKILL.md files found under {skills_dir}"}

    # 按段落收集
    file_paras = {}   # {filepath: [(para_text, hash), ...]}
    hash_to_files = {}  # {hash: [filepath, ...]}

    for sf in skill_files:
        body = extract_body(sf)
        paras = paragraphize(body, min_chars=50)
        entries = []
        for p in paras:
            h = hash_text(p)
            entries.append((p, h))
            hash_to_files.setdefault(h, []).append(str(sf))
        file_paras[str(sf)] = entries

    # 1. 字面重复（同 hash 出现在 ≥2 个文件中）
    literal_dupes = []
    seen_hashes = set()
    for h, files in hash_to_files.items():
        unique_files = list(set(files))
        if len(unique_files) >= 2 and h not in seen_hashes:
            seen_hashes.add(h)
            # 找到对应段落文本
            sample_text = ""
            for fp, entries in file_paras.items():
                for pt, ph in entries:
                    if ph == h:
                        sample_text = pt[:200]
                        break
                if sample_text:
                    break
            literal_dupes.append({
                "hash": h[:12],
                "files": unique_files,
                "preview": sample_text,
            })

    # 2. n-gram 近似重复（Jaccard ≥ 0.6）
    # 只对跨文件的段落对做比较
    approx_dupes = []
    all_paras = []  # [(file, para_text, ngram_set)]
    for sf in skill_files:
        body = extract_body(sf)
        for p in paragraphize(body, min_chars=100):  # 更长的阈值，减少噪音
            all_paras.append((str(sf), p, ngrams(p, n=4)))

    checked_pairs = set()
    for i in range(len(all_paras)):
        for j in range(i + 1, len(all_paras)):
            f1, p1, ng1 = all_paras[i]
            f2, p2, ng2 = all_paras[j]
            if f1 == f2:
                continue
            pair_key = tuple(sorted([(f1, hash_text(p1)), (f2, hash_text(p2))]))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            sim = jaccard(ng1, ng2)
            if sim >= 0.6:
                approx_dupes.append({
                    "similarity": round(sim, 3),
                    "file_a": f1,
                    "file_b": f2,
                    "preview_a": p1[:150],
                    "preview_b": p2[:150],
                })

    # 排序：字面重复优先，近似重复按相似度降序
    approx_dupes.sort(key=lambda x: x["similarity"], reverse=True)

    # 计算每个 skill 文件与其他文件的重复度
    per_file_overlap = {}
    for sf in skill_files:
        sf_str = str(sf)
        overlap_count = 0
        for d in literal_dupes:
            if sf_str in d["files"]:
                overlap_count += 1
        other_count = len(literal_dupes) - overlap_count if literal_dupes else 0
        per_file_overlap[sf_str] = {
            "literal_duplicate_blocks": overlap_count,
            "unique_blocks_ratio": round(1 - overlap_count / max(len(literal_dupes), 1), 3),
        }

    return {
        "summary": {
            "skills_scanned": len(skill_files),
            "literal_duplicates": len(literal_dupes),
            "approximate_duplicates": len(approx_dupes),
            "total_redundant_blocks": len(literal_dupes) + len(approx_dupes),
        },
        "per_file_overlap": per_file_overlap,
        "literal_duplicates": literal_dupes[:20],  # 截取前 20 条
        "approximate_duplicates": approx_dupes[:20],
    }


def main():
    parser = argparse.ArgumentParser(description="检测跨 Skill 的重复内容")
    parser.add_argument("--skills-dir", required=True, help="skills/ 目录路径")
    parser.add_argument("--output", help="输出 JSON 文件路径（默认 stdout）")
    args = parser.parse_args()

    sd = Path(args.skills_dir)
    if not sd.is_dir():
        print(json.dumps({"error": f"Not a directory: {args.skills_dir}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    results = detect_duplicates(sd)

    out_json = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out_json, encoding="utf-8")
        print(f"[INFO] Duplicate detection written to {args.output}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
