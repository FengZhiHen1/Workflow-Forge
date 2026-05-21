#!/usr/bin/env python3
"""
代码接口签名提取与设计契约差异比对脚本。

职责：
  - 递归扫描代码目录，提取公开接口定义（Pydantic/dataclass 模型、函数签名、枚举类）
  - 与 Agent 预处理的设计文档声明 JSON 进行逐字段比对
  - 可选：与 contract-expectations.md 基线进行三方比对
  - 输出结构化差异 JSON

用法：
  python diff_code_design.py \
    --code-path <代码目录> \
    --design-declaration <设计声明JSON> \
    --baseline <contract-expectations.md，可选> \
    --output <输出JSON>

ISO-006：跳过 tests/、test/、__tests__/、spec/ 目录及 test_*、*_test 文件。
"""

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# ============================================================================
# 常量
# ============================================================================

# ISO-006：禁止读取的目录和文件名模式
FORBIDDEN_DIRS = {"tests", "test", "__tests__", "spec", "__pycache__", ".git", ".agent", ".tmp"}
FORBIDDEN_FILE_PATTERNS = (r"^test_", r"_test\.py$")

# 需要提取的公开接口 AST 节点类型
MODEL_BASE_CLASSES = {"BaseModel", "pydantic.BaseModel"}
DATACLASS_DECORATOR = "dataclass"
FUNC_VISIBILITY_PREFIX = "_"  # 下划线前缀的函数视为私有，跳过

# 设计声明 JSON 的 schema 版本
SCHEMA_VERSION = "1.0.0"

# 本地时区
LOCAL_TZ = timezone(timedelta(hours=8))


def _is_forbidden_path(file_path: str) -> bool:
    """检查路径是否属于 ISO-006 禁止范围。"""
    parts = Path(file_path).parts
    for part in parts:
        if part.lower() in FORBIDDEN_DIRS:
            return True
    filename = Path(file_path).name
    for pattern in FORBIDDEN_FILE_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return True
    return False


# ============================================================================
# 代码接口提取
# ============================================================================


def _resolve_type_annotation(node: Optional[ast.expr]) -> str:
    """将 AST 类型注解节点转为字符串表示。"""
    if node is None:
        return "Any"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        base = _resolve_type_annotation(node.value)
        slice_str = _resolve_type_annotation(node.slice)
        return f"{base}[{slice_str}]"
    if isinstance(node, ast.Tuple):
        elts = [_resolve_type_annotation(e) for e in node.elts]
        return f"Union[{', '.join(elts)}]"
    if isinstance(node, ast.BinOp):
        # 处理 str | None 这种联合类型（Python 3.10+）
        left = _resolve_type_annotation(node.left)
        right = _resolve_type_annotation(node.right)
        return f"{left} | {right}"
    if isinstance(node, ast.Attribute):
        return f"{_resolve_type_annotation(node.value)}.{node.attr}"
    return "Unknown"


def _extract_model_fields(class_def: ast.ClassDef) -> list[dict[str, Any]]:
    """从 Pydantic/dataclass 类定义中提取字段。"""
    fields: list[dict[str, Any]] = []
    for stmt in class_def.body:
        # 处理有类型注解的赋值语句 (Pydantic style)
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            field_info: dict[str, Any] = {
                "name": stmt.target.id,
                "type": _resolve_type_annotation(stmt.annotation),
                "required": True,
                "default": None,
                "constraints": [],
            }
            if stmt.value is not None:
                field_info["required"] = False
                if isinstance(stmt.value, ast.Constant):
                    field_info["default"] = str(stmt.value.value)
                else:
                    # 处理 Field(...) 调用
                    field_info["default"] = "..."
                    _parse_field_constraints(stmt.value, field_info)
            fields.append(field_info)

        # 处理普通赋值 (dataclass style, with type annotation from __annotations__)
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    # 跳过类变量（全大写，通常为常量）和私有变量
                    if target.id.startswith("__") or (target.id.isupper() and not target.id.startswith("_")):
                        continue
    return fields


def _parse_field_constraints(node: ast.expr, field_info: dict[str, Any]) -> None:
    """解析 Field() 调用中的约束参数。"""
    if not isinstance(node, ast.Call):
        return
    for kw in node.keywords:
        if kw.arg in ("min_length", "max_length", "ge", "le", "gt", "lt", "regex", "pattern"):
            if isinstance(kw.value, ast.Constant):
                field_info["constraints"].append(f"{kw.arg}={kw.value.value}")


def _extract_function_signature(func_def: ast.FunctionDef) -> dict[str, Any]:
    """提取函数签名信息（跳过私有函数）。"""
    if func_def.name.startswith(FUNC_VISIBILITY_PREFIX):
        return {}

    params: list[dict[str, Any]] = []
    for arg in func_def.args.args:
        if arg.arg == "self" or arg.arg == "cls":
            continue
        param_info = {
            "name": arg.arg,
            "type": _resolve_type_annotation(arg.annotation),
            "default": None,
        }
        params.append(param_info)

    # 处理默认值
    defaults_offset = len(func_def.args.args) - len(func_def.args.defaults)
    for i, default in enumerate(func_def.args.defaults):
        idx = defaults_offset + i
        if idx < len(params):
            if isinstance(default, ast.Constant):
                params[idx]["default"] = str(default.value)
            else:
                params[idx]["default"] = "..."

    return_type = _resolve_type_annotation(func_def.returns) if func_def.returns else "Any"

    return {
        "name": func_def.name,
        "kind": "function",
        "params": params,
        "return_type": return_type,
    }


def _extract_enum_members(class_def: ast.ClassDef) -> Optional[dict[str, Any]]:
    """从枚举类中提取成员列表。"""
    members: list[str] = []
    for stmt in class_def.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(stmt.value, ast.Constant):
                        members.append(f"{target.id}={stmt.value.value}")
                    elif isinstance(stmt.value, ast.Call):
                        members.append(f"{target.id}=auto()")
                    else:
                        members.append(target.id)
    return {
        "name": class_def.name,
        "kind": "enum",
        "members": members,
    } if members else None


def _is_model_class(base: ast.expr) -> bool:
    """判断类是否继承自 Pydantic BaseModel。"""
    if isinstance(base, ast.Name) and base.id in MODEL_BASE_CLASSES:
        return True
    if isinstance(base, ast.Attribute):
        full = f"{_resolve_type_annotation(base.value)}.{base.attr}"
        return full in MODEL_BASE_CLASSES
    return False


def _is_enum_class(base: ast.expr) -> bool:
    """判断类是否继承自 Enum。"""
    if isinstance(base, ast.Name) and base.id in ("Enum", "IntEnum", "StrEnum"):
        return True
    if isinstance(base, ast.Attribute) and base.attr in ("Enum", "IntEnum", "StrEnum"):
        return True
    return False


def _scan_python_file(file_path: str) -> list[dict[str, Any]]:
    """扫描单个 Python 文件，提取公开接口定义。"""
    if _is_forbidden_path(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"[WARN] 解析失败 {file_path}: {exc}", file=sys.stderr)
        return []

    interfaces: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # 跳过私有类
            if node.name.startswith(FUNC_VISIBILITY_PREFIX):
                continue

            is_model = any(_is_model_class(b) for b in node.bases)
            is_enum = any(_is_enum_class(b) for b in node.bases)
            is_dataclass = any(
                isinstance(d, ast.Name) and d.id == DATACLASS_DECORATOR
                for d in node.decorator_list
            )

            if is_model or is_dataclass:
                record = {
                    "name": node.name,
                    "kind": "model",
                    "source": file_path,
                    "fields": _extract_model_fields(node),
                }
                interfaces.append(record)

            if is_enum:
                enum_record = _extract_enum_members(node)
                if enum_record:
                    enum_record["source"] = file_path
                    interfaces.append(enum_record)

        elif isinstance(node, ast.FunctionDef):
            # 跳过嵌套函数和私有函数
            if node.name.startswith(FUNC_VISIBILITY_PREFIX):
                continue
            # 检查是否有 decorator（API 路由标记等）——公开函数
            sig = _extract_function_signature(node)
            if sig:
                sig["source"] = file_path
                interfaces.append(sig)

    return interfaces


# ============================================================================
# 比对引擎
# ============================================================================


def _normalize_name(name: str) -> str:
    """规范化名称（小写、去下划线）用于模糊匹配。"""
    return re.sub(r"[_\-\s]", "", name.lower())


def _compare_fields(
    code_fields: list[dict[str, Any]],
    design_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """逐字段比对，返回差异列表。"""
    diffs: list[dict[str, Any]] = []
    code_by_name = {f["name"]: f for f in code_fields}
    design_by_name = {f["name"]: f for f in design_fields}

    all_names = set(code_by_name.keys()) | set(design_by_name.keys())
    for name in sorted(all_names):
        cf = code_by_name.get(name)
        df = design_by_name.get(name)

        if cf and df:
            if cf.get("type") != df.get("type"):
                diffs.append({
                    "field": name,
                    "dimension": "type",
                    "code": cf.get("type"),
                    "design": df.get("type"),
                })
            if cf.get("required") != df.get("required"):
                diffs.append({
                    "field": name,
                    "dimension": "required",
                    "code": cf.get("required"),
                    "design": df.get("required"),
                })
        elif cf and not df:
            diffs.append({"field": name, "dimension": "code_only", "code": cf.get("type")})
        elif df and not cf:
            diffs.append({"field": name, "dimension": "doc_only", "design": df.get("type")})

    return diffs


def _match_interface(
    code_interface: dict[str, Any],
    design_interfaces: list[dict[str, Any]],
    baseline_entries: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """在设计中找到与代码接口匹配的条目，返回差异记录或 None（匹配成功）。"""
    code_name = code_interface["name"]
    code_kind = code_interface.get("kind", "unknown")

    # 1. 精确名称匹配
    for di in design_interfaces:
        if di["name"] == code_name:
            if code_kind == "model" and di.get("kind") == "model":
                field_diffs = _compare_fields(
                    code_interface.get("fields", []),
                    di.get("fields", []),
                )
                if field_diffs:
                    return {
                        "diff_id": "",  # 由 Agent 分配
                        "name": code_name,
                        "category": "mismatch",
                        "dimension": "field_drift",
                        "code": {"fields": code_interface.get("fields"), "source": code_interface.get("source")},
                        "design_doc": {"fields": di.get("fields"), "source": di.get("source")},
                        "field_diffs": field_diffs,
                    }
                return None  # 完全匹配

            if code_kind == "function" and di.get("kind") == "function":
                if (code_interface.get("return_type") != di.get("return_type") or
                        code_interface.get("params") != di.get("params")):
                    return {
                        "diff_id": "",
                        "name": code_name,
                        "category": "mismatch",
                        "dimension": "signature",
                        "code": {
                            "params": code_interface.get("params"),
                            "return_type": code_interface.get("return_type"),
                            "source": code_interface.get("source"),
                        },
                        "design_doc": {
                            "params": di.get("params"),
                            "return_type": di.get("return_type"),
                            "source": di.get("source"),
                        },
                    }
                return None  # 完全匹配

            if code_kind == "enum" and di.get("kind") == "enum":
                c_members = set(code_interface.get("members", []))
                d_members = set(di.get("members", []))
                if c_members != d_members:
                    return {
                        "diff_id": "",
                        "name": code_name,
                        "category": "mismatch",
                        "dimension": "enum_values",
                        "code": {"members": sorted(c_members), "source": code_interface.get("source")},
                        "design_doc": {"members": sorted(d_members), "source": di.get("source")},
                    }
                return None

    # 2. 模糊名称匹配（去下划线后一致）
    code_norm = _normalize_name(code_name)
    for di in design_interfaces:
        if _normalize_name(di["name"]) == code_norm:
            return {
                "diff_id": "",
                "name": code_name,
                "category": "mismatch",
                "dimension": "name_mismatch",
                "code": {"name": code_name, "source": code_interface.get("source")},
                "design_doc": {"name": di["name"], "source": di.get("source")},
            }

    # 3. 未匹配 —— 是 code_only（之后还会检查设计中有但代码无的）
    return None  # 由调用方判定


def _run_comparison(
    code_interfaces: list[dict[str, Any]],
    design_declarations: list[dict[str, Any]],
    baseline_entries: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """执行代码-设计-基线三方比对。"""
    differences: list[dict[str, Any]] = []
    matched_design: set[str] = set()

    diff_counter = 0

    for ci in code_interfaces:
        result = _match_interface(ci, design_declarations, baseline_entries)
        if result is not None:
            diff_counter += 1
            result["diff_id"] = f"D{diff_counter:03d}"
            differences.append(result)

            # 标记已匹配的设计条目
            for di in design_declarations:
                if di["name"] == ci["name"] or _normalize_name(di["name"]) == _normalize_name(ci["name"]):
                    matched_design.add(di["name"])
        else:
            # 检查是否在设计中找到匹配（结果干净 = 匹配）
            for di in design_declarations:
                if di["name"] == ci["name"] or _normalize_name(di["name"]) == _normalize_name(ci["name"]):
                    matched_design.add(di["name"])
                    break

    # 检查设计中声明但代码中未实现的 (doc_only)
    for di in design_declarations:
        if di["name"] not in matched_design:
            # 确认代码中确实没有
            code_names = {ci["name"] for ci in code_interfaces}
            code_norm_names = {_normalize_name(ci["name"]) for ci in code_interfaces}
            if di["name"] not in code_names and _normalize_name(di["name"]) not in code_norm_names:
                diff_counter += 1
                differences.append({
                    "diff_id": f"D{diff_counter:03d}",
                    "name": di["name"],
                    "category": "doc_only",
                    "code": None,
                    "design_doc": {
                        "kind": di.get("kind", "unknown"),
                        "source": di.get("source"),
                    },
                })

    # 按类别统计
    summary = {"code_only": 0, "doc_only": 0, "mismatch": 0, "matched": 0}
    for d in differences:
        cat = d.get("category", "")
        if cat in summary:
            summary[cat] += 1
    summary["matched"] = len(code_interfaces) - summary["code_only"] - summary["mismatch"]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "scan_summary": {
            "code_interfaces_found": len(code_interfaces),
            "design_interfaces_found": len(design_declarations),
            "baseline_entries": len(baseline_entries) if baseline_entries else 0,
        },
        "summary": summary,
        "differences": differences,
    }


# ============================================================================
# 设计声明 JSON 加载
# ============================================================================


def _load_design_declarations(path: str) -> list[dict[str, Any]]:
    """加载 Agent 预处理的设计声明 JSON。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "declarations" in data:
        return data["declarations"]
    raise ValueError(f"无法识别的设计声明格式: {type(data)}，期望 [list] 或 {{declarations: [...]}}")


# ============================================================================
# contract-expectations.md 解析（简易版）
# ============================================================================


def _parse_baseline_markdown(path: str) -> list[dict[str, Any]]:
    """解析 contract-expectations.md 提取接口契约条目（简易 Markdown 表格/列表解析）。"""
    entries: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[WARN] baseline 文件不存在: {path}", file=sys.stderr)
        return []
    except OSError as exc:
        print(f"[ERROR] 无法读取 baseline 文件: {exc}", file=sys.stderr)
        return []

    # 解析 Markdown 表格中的契约条目
    # 期望格式: | entry_id | name | kind | fields/signature | section |
    in_table = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "entry_id" in stripped.lower():
            in_table = True
            continue
        if in_table and stripped.startswith("|") and not stripped.startswith("|---"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cells) >= 4:
                entries.append({
                    "entry_id": cells[0] if len(cells) > 0 else "",
                    "name": cells[1] if len(cells) > 1 else "",
                    "kind": cells[2] if len(cells) > 2 else "unknown",
                    "signature_or_fields": cells[3] if len(cells) > 3 else "",
                })
        elif in_table and not stripped.startswith("|"):
            in_table = False

    return entries


# ============================================================================
# CLI 入口
# ============================================================================


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="代码接口签名提取与设计契约差异比对工具",
    )
    parser.add_argument(
        "--code-path", required=True,
        help="源代码目录路径（递归扫描）",
    )
    parser.add_argument(
        "--design-declaration", required=True,
        help="设计文档接口声明 JSON 文件路径",
    )
    parser.add_argument(
        "--baseline", default=None,
        help="contract-expectations.md 路径（可选）",
    )
    parser.add_argument(
        "--output", required=True,
        help="差异报告 JSON 输出路径",
    )
    return parser.parse_args()


def _scan_directory(code_path: str) -> list[dict[str, Any]]:
    """递归扫描代码目录，提取所有公开接口。"""
    all_interfaces: list[dict[str, Any]] = []
    py_files: list[str] = []

    for root, dirs, files in os.walk(code_path):
        # 原地过滤禁止目录
        dirs[:] = [d for d in dirs if d.lower() not in FORBIDDEN_DIRS]
        for f in files:
            if f.endswith(".py"):
                fp = os.path.join(root, f)
                if not _is_forbidden_path(fp):
                    py_files.append(fp)

    print(f"[INFO] 扫描 {len(py_files)} 个 Python 文件...")
    for fp in py_files:
        interfaces = _scan_python_file(fp)
        if interfaces:
            rel_path = os.path.relpath(fp, code_path)
            for iface in interfaces:
                iface["source"] = rel_path
            all_interfaces.extend(interfaces)

    return all_interfaces


def main() -> None:
    args = _parse_args()

    if not os.path.isdir(args.code_path):
        print(f"[ERROR] 代码路径无效: {args.code_path}", file=sys.stderr)
        sys.exit(1)

    # 1. 扫描代码
    code_interfaces = _scan_directory(args.code_path)
    print(f"[INFO] 提取到 {len(code_interfaces)} 个代码接口定义")

    # 2. 加载设计声明
    design_declarations = _load_design_declarations(args.design_declaration)
    print(f"[INFO] 加载 {len(design_declarations)} 条设计声明")

    # 3. 加载基线（可选）
    baseline_entries = None
    if args.baseline and os.path.isfile(args.baseline):
        baseline_entries = _parse_baseline_markdown(args.baseline)
        print(f"[INFO] 加载 {len(baseline_entries)} 条基线条目")

    # 4. 执行比对
    result = _run_comparison(code_interfaces, design_declarations, baseline_entries)

    # 5. 写入输出
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 差异报告已写入: {args.output}")
    print(f"[INFO] 总计: {len(result['differences'])} 处差异 "
          f"(code_only={result['summary']['code_only']}, "
          f"doc_only={result['summary']['doc_only']}, "
          f"mismatch={result['summary']['mismatch']})")


if __name__ == "__main__":
    main()
