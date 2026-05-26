#!/usr/bin/env python3
"""
存量制品检测脚本 — 纯确定性文件系统扫描。

扫描指定模块目录，检测四类存量制品：
  1. 设计文档（意图文档、设计文档、落地规范）
  2. 实现代码（源文件）
  3. 测试代码（测试文件）
  4. 契约文件（contract-expectations.md）

输出结构化 JSON 报告到 stdout。
任何异常均输出底线 JSON（全部 missing），退出码非 0。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 实现代码的源码文件扩展名
SOURCE_EXTENSIONS: set[str] = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".kts",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".cs", ".rb", ".swift", ".scala", ".php", ".ex", ".exs",
}

# 扫描时排除的目录名
EXCLUDED_DIRS: set[str] = {
    "__pycache__", "node_modules", ".git", "venv", ".venv",
    ".tox", "dist", "build", ".next", ".nuxt", ".idea", ".vscode",
}

# 扫描时排除的文件模式（后缀匹配）
EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".pyc", ".pyo", ".so", ".dll", ".wasm",
)

# 跳过 SHA256 计算的文件大小阈值（100MB）
SHA256_SKIP_SIZE_BYTES: int = 100 * 1024 * 1024

# 设计文档子类型
DESIGN_DOC_TYPES: tuple[str, ...] = ("intent_doc", "design_doc", "landing_spec")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _timestamp_iso() -> str:
    """返回当前时间的 ISO 8601 字符串（带时区）。"""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256_file(filepath: str) -> Optional[str]:
    """计算文件的 SHA256 哈希。文件过大时返回跳过标记。读取失败返回 None。"""
    try:
        file_size = os.path.getsize(filepath)
        if file_size > SHA256_SKIP_SIZE_BYTES:
            return "skipped: file too large"
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def _file_mtime_iso(filepath: str) -> Optional[str]:
    """获取文件的修改时间（ISO 8601）。失败返回 None。"""
    try:
        ts = os.path.getmtime(filepath)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        return dt.isoformat()
    except (OSError, PermissionError):
        return None


def _relative_path(absolute: str, root: str) -> str:
    """将绝对路径转为相对于 root 的路径，统一使用正斜杠。"""
    try:
        rel = os.path.relpath(absolute, root)
    except ValueError:
        rel = absolute
    return rel.replace("\\", "/")


def _is_source_file(name: str) -> bool:
    """判断文件名是否为源码文件（按扩展名）。"""
    _, ext = os.path.splitext(name)
    return ext.lower() in SOURCE_EXTENSIONS


def _is_excluded(name: str, is_dir: bool = False) -> bool:
    """判断文件/目录是否应被排除。"""
    if is_dir and name in EXCLUDED_DIRS:
        return True
    if not is_dir and name.lower().endswith(EXCLUDED_SUFFIXES):
        return True
    # 排除压缩/打包文件
    if not is_dir and (".min." in name.lower() or ".bundle." in name.lower()):
        return True
    return False


def _make_item(path: str, root: str, item_type: Optional[str] = None) -> Optional[dict]:
    """为单个文件构造报告条目。读取失败返回 None。"""
    abs_path = os.path.join(root, path) if not os.path.isabs(path) else path
    sha = _sha256_file(abs_path)
    if sha is None:
        return None  # 文件不可读
    mtime = _file_mtime_iso(abs_path)
    size = os.path.getsize(abs_path) if os.path.exists(abs_path) else 0
    item: dict = {
        "path": _relative_path(abs_path, root),
        "modified_at": mtime,
        "sha256": sha,
        "size_bytes": size,
    }
    if item_type:
        item["type"] = item_type
    return item


def _module_in_path(path: str, module_id: str) -> bool:
    """判断路径是否包含模块编号（用于归属判定）。"""
    return module_id in os.path.basename(path) or module_id in path


# ---------------------------------------------------------------------------
# 四类制品扫描
# ---------------------------------------------------------------------------

def scan_design_docs(project_root: str, module_id: str) -> dict:
    """扫描设计文档：意图文档、设计文档、落地规范。"""
    docs_dir = os.path.join(project_root, "docs")
    if not os.path.isdir(docs_dir):
        return {
            "completeness": "missing",
            "completeness_reason": "docs/ 目录不存在",
            "items": [],
        }

    found: dict[str, list[str]] = {
        "intent_doc": [],
        "design_doc": [],
        "landing_spec": [],
    }
    failed_count = 0

    for dirpath, dirnames, filenames in os.walk(docs_dir):
        # 跳过排除目录
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for fname in filenames:
            if not fname.lower().endswith(".md"):
                continue

            rel_path = _relative_path(os.path.join(dirpath, fname), project_root)

            # 归属判定：路径或文件名必须包含 module_id
            if not _module_in_path(rel_path, module_id):
                continue

            fname_lower = fname.lower()
            abs_path = os.path.join(dirpath, fname)

            if "意图文档" in fname or "intent" in fname_lower:
                found["intent_doc"].append(abs_path)
            elif "落地规范" in fname or "landing-spec" in fname_lower or "implementation-spec" in fname_lower:
                found["landing_spec"].append(abs_path)
            elif "设计文档" in fname or "design-doc" in fname_lower or "design_doc" in fname_lower:
                found["design_doc"].append(abs_path)
            # 不匹配以上规则的 .md 文件忽略（可能是其他文档）

    # 构造 items
    items: list[dict] = []
    for dtype in DESIGN_DOC_TYPES:
        for fp in found.get(dtype, []):
            item = _make_item(fp, project_root, item_type=dtype)
            if item:
                items.append(item)
            else:
                failed_count += 1

    # 评级
    type_count = sum(1 for v in found.values() if v)
    if type_count == 3:
        completeness = "complete"
    elif 1 <= type_count <= 2:
        completeness = "partial"
    else:
        completeness = "missing"

    reason = f"发现 {type_count}/3 类设计文档，共 {len(items)} 件"
    if failed_count:
        reason += f"，{failed_count} 件读取失败"

    return {
        "completeness": completeness,
        "completeness_reason": reason,
        "items": items,
    }


def _is_test_path(rel_path: str, fname: str) -> bool:
    """判断是否为测试代码路径或文件名。"""
    path_lower = rel_path.lower().replace("\\", "/")
    # 路径中包含 test/tests/__tests__/spec 目录
    parts = path_lower.split("/")
    for part in parts:
        if part in ("test", "tests", "__tests__", "spec", "specs"):
            return True
    # 文件名匹配测试模式
    fname_lower = fname.lower()
    if fname_lower.startswith("test_") or fname_lower.endswith("_test.py"):
        return True
    if ".test." in fname_lower or ".spec." in fname_lower:
        return True
    return False


def scan_implementation_code(project_root: str, module_dir: str) -> dict:
    """扫描实现代码：module_dir 下的所有源文件（排除测试文件和排除目录）。"""
    target = os.path.join(project_root, module_dir)
    if not os.path.isdir(target):
        return {
            "completeness": "missing",
            "completeness_reason": f"模块目录 {module_dir} 不存在",
            "items": [],
        }

    items: list[dict] = []
    failed_count = 0

    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for fname in filenames:
            if not _is_source_file(fname):
                continue
            if _is_excluded(fname):
                continue

            abs_path = os.path.join(dirpath, fname)
            rel_path = _relative_path(abs_path, project_root)

            # 排除测试文件（这些归入测试代码）
            if _is_test_path(rel_path, fname):
                continue

            item = _make_item(abs_path, project_root)
            if item:
                items.append(item)
            else:
                failed_count += 1

    if items:
        reason = f"发现 {len(items)} 个源文件"
    else:
        reason = "未发现任何源文件"

    if failed_count:
        reason += f"（{failed_count} 件读取失败）"

    return {
        "completeness": "complete" if items else "missing",
        "completeness_reason": reason,
        "items": items,
    }


def scan_test_code(project_root: str, module_dir: str) -> dict:
    """扫描测试代码：module_dir 下的测试文件 + 项目 test(s) 目录下属于本模块的文件。"""
    items: list[dict] = []
    failed_count = 0
    scanned = set()  # 避免重复

    # 扫描范围 1：module_dir 内的测试文件
    target = os.path.join(project_root, module_dir)
    if os.path.isdir(target):
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            for fname in filenames:
                if not _is_source_file(fname):
                    continue
                if _is_excluded(fname):
                    continue
                rel_path = _relative_path(os.path.join(dirpath, fname), project_root)
                if not _is_test_path(rel_path, fname):
                    continue
                abs_path = os.path.join(dirpath, fname)
                if abs_path in scanned:
                    continue
                scanned.add(abs_path)
                item = _make_item(abs_path, project_root)
                if item:
                    items.append(item)
                else:
                    failed_count += 1

    # 扫描范围 2：项目根目录下的 tests/ / test/ 目录中属于本模块的文件
    for test_dir_name in ("tests", "test"):
        test_dir = os.path.join(project_root, test_dir_name)
        if not os.path.isdir(test_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(test_dir):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            for fname in filenames:
                if not _is_source_file(fname):
                    continue
                if _is_excluded(fname):
                    continue
                rel_path = _relative_path(os.path.join(dirpath, fname), project_root)
                # 归属判定：路径或文件名包含 module_id
                if not _module_in_path(rel_path, module_dir.split("/")[-1]):
                    continue
                if not _is_test_path(rel_path, fname):
                    continue
                abs_path = os.path.join(dirpath, fname)
                if abs_path in scanned:
                    continue
                scanned.add(abs_path)
                item = _make_item(abs_path, project_root)
                if item:
                    items.append(item)
                else:
                    failed_count += 1

    if items:
        reason = f"发现 {len(items)} 个测试文件"
    else:
        reason = "未发现任何测试文件"

    if failed_count:
        reason += f"（{failed_count} 件读取失败）"

    return {
        "completeness": "complete" if items else "missing",
        "completeness_reason": reason,
        "items": items,
    }


def scan_contract_files(project_root: str, module_id: str, module_dir: str) -> dict:
    """扫描契约文件：contract-expectations.md。"""
    items: list[dict] = []
    # 搜索范围扩展，例如 'M01' 作为目录名
    search_dirs: list[str] = []

    # 范围 1：contracts/ 目录
    contracts_dir = os.path.join(project_root, "contracts")
    if os.path.isdir(contracts_dir):
        search_dirs.append(contracts_dir)

    # 范围 2：module_dir 所在层级
    mod_abs = os.path.join(project_root, module_dir)
    if os.path.isdir(mod_abs):
        search_dirs.append(os.path.dirname(mod_abs))

    for search_root in search_dirs:
        for dirpath, dirnames, filenames in os.walk(search_root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            for fname in filenames:
                if fname.lower() != "contract-expectations.md":
                    continue
                rel_path = _relative_path(os.path.join(dirpath, fname), project_root)
                # 归属判定：路径包含 module_id
                if not _module_in_path(rel_path, module_id):
                    continue
                item = _make_item(os.path.join(dirpath, fname), project_root)
                if item:
                    items.append(item)

    if items:
        reason = f"发现 {len(items)} 个契约文件"
    else:
        reason = "未发现 contract-expectations.md"

    return {
        "completeness": "complete" if items else "missing",
        "completeness_reason": reason,
        "items": items,
    }


# ---------------------------------------------------------------------------
# 降级 JSON（全部 missing）
# ---------------------------------------------------------------------------

def fallback_report(module_id: str, module_code_dir: str, error: str) -> dict:
    """生成底线 JSON，所有类别标记为 missing。"""
    missing_block = {
        "completeness": "missing",
        "completeness_reason": "扫描未执行",
        "items": [],
    }
    return {
        "module_id": module_id or "unknown",
        "module_code_dir": module_code_dir or "unknown",
        "scan_timestamp": _timestamp_iso(),
        "error": error,
        "design_docs": dict(missing_block),
        "implementation_code": dict(missing_block),
        "test_code": dict(missing_block),
        "contract_files": dict(missing_block),
    }


# ---------------------------------------------------------------------------
# 主报告生成
# ---------------------------------------------------------------------------

def generate_report(module_id: str, module_code_dir: str, project_root: str) -> dict:
    """执行完整扫描并生成报告。"""
    report: dict = {
        "module_id": module_id,
        "module_code_dir": module_code_dir,
        "scan_timestamp": _timestamp_iso(),
    }

    # 校验参数
    if not module_id:
        raise ValueError("module_id 为空")
    if not module_code_dir:
        raise ValueError("module_code_dir 为空")
    if not os.path.isdir(os.path.join(project_root, module_code_dir)):
        # 目录不存在不是致命错误，但代码/测试必然是 missing
        pass

    report["design_docs"] = scan_design_docs(project_root, module_id)
    report["implementation_code"] = scan_implementation_code(project_root, module_code_dir)
    report["test_code"] = scan_test_code(project_root, module_code_dir)
    report["contract_files"] = scan_contract_files(project_root, module_id, module_code_dir)

    return report


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="存量制品检测器 — 扫描模块目录，输出结构化 JSON 报告"
    )
    parser.add_argument(
        "--module-id",
        required=True,
        help="目标模块编号，如 M01",
    )
    parser.add_argument(
        "--module-dir",
        required=True,
        help="模块代码目录路径（相对于项目根目录），如 src/modules/user-auth",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="项目根目录路径（默认当前工作目录）",
    )

    args = parser.parse_args()
    module_id: str = args.module_id
    module_dir: str = args.module_dir
    project_root: str = args.project_root or os.getcwd()

    try:
        report = generate_report(module_id, module_dir, project_root)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    except Exception as exc:
        # 任何异常 → 降级输出
        fallback = fallback_report(module_id, module_dir, str(exc))
        json.dump(fallback, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
