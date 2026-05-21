#!/usr/bin/env python3
"""
mineru_parse.py — MinerU Precision Extract API 完整封装

用法:
    python mineru_parse.py <pdf_path> [--scope <页码范围>] [--output <输出路径>]

示例:
    python mineru_parse.py ./textbook.pdf
    python mineru_parse.py ./textbook.pdf --scope "1-30"
    python mineru_parse.py ./textbook.pdf --output ./output.md

流程:
    1. 加载 .env 配置
    2. 预检：文件存在、大小 ≤200MB、页数 ≤200
    3. POST /api/v4/file-urls/batch  获取预签名上传 URL
    4. PUT 上传 PDF 到预签名 URL
    5. 轮询 GET /api/v4/extract-results/batch/{batch_id}
    6. 下载 full_zip_url → 解压 → 找到 JSON
    7. JSON 清洗 → 结构化 Markdown
    8. 返回/输出 Markdown
"""

import json
import os
import re
import sys
import time
import zipfile
import argparse
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests

# ── 错误码映射表（来源：MinerU API 文档 §1.7） ──────────────────────────

ERROR_CODE_MAP: Dict[str, str] = {
    # Token 相关
    "A0202": "Token 错误 — 请检查 Token 是否正确，确认是否包含 Bearer 前缀，或更换新 Token",
    "A0211": "Token 过期 — 请更换新 Token",
    # 通用错误
    "-500":  "传参错误 — 请确保参数类型及 Content-Type 正确",
    "-10001": "服务异常 — 请稍后再试",
    "-10002": "请求参数错误 — 请检查请求参数格式",
    # 上传相关
    "-60001": "生成上传 URL 失败 — 请稍后再试",
    "-60002": "获取匹配的文件格式失败 — 请确认文件后缀正确（pdf/doc/docx/ppt/pptx/xls/xlsx/png/jpg/jpeg）",
    "-60003": "文件读取失败 — 请检查文件是否损坏并重新上传",
    "-60004": "空文件 — 请上传有效文件",
    "-60005": "文件大小超出限制 — 最大支持 200MB",
    "-60006": "文件页数超过限制 — 请拆分文件后重试",
    "-60007": "模型服务暂时不可用 — 请稍后重试或联系技术支持",
    "-60008": "文件读取超时 — 请检查 URL 可访问性",
    "-60009": "任务提交队列已满 — 请稍后再试",
    "-60010": "解析失败 — 请稍后再试",
    "-60011": "获取有效文件失败 — 请确保文件已上传",
    "-60012": "找不到任务 — 请确保 task_id 有效且未被删除",
    "-60013": "没有权限访问该任务 — 只能访问自己提交的任务",
    "-60014": "删除运行中的任务 — 运行中的任务暂不支持删除",
    "-60015": "文件转换失败 — 可尝试手动转为 PDF 再上传",
    "-60016": "文件转换失败 — 文件转换为指定格式失败，可尝试其他格式导出或重试",
    "-60017": "重试次数达到上限 — 请等待后续模型升级后重试",
    "-60018": "每日解析任务数量已达上限 — 请明日再试",
    "-60019": "HTML 文件解析额度不足 — 请明日再试",
    "-60020": "文件拆分失败 — 请稍后重试",
    "-60021": "读取文件页数失败 — 请稍后重试",
    "-60022": "网页读取失败 — 可能因网络问题或限频导致，请稍后重试",
}

# ── 封面/噪声过滤关键词 ─────────────────────────────────────────────

COVER_NOISE_KEYWORDS = [
    "出版社", "主编", "副主编", "教材", "丛书", "高等学校",
    "普通高等教育", "国家级规划", "数字教材版", "编写组",
    "CIP", "图书在版编目", "编委会", "出版发行",
    "·北京·", "·上海·", "责任编辑", "封面设计",
    "版次", "印次", "字数", "定价", "ISBN", "中国版本图书馆",
]


# ── 配置加载 ───────────────────────────────────────────────────────

def load_env(script_dir: str) -> Dict[str, str]:
    """从脚本所在目录加载 .env 文件。若不存在则报错退出。"""
    env_path = os.path.join(script_dir, ".env")
    if not os.path.isfile(env_path):
        print(f"[错误] 未找到 .env 文件: {env_path}")
        print("请复制 .env.example 为 .env 并填入 MINERU_TOKEN")
        sys.exit(1)

    config: Dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if value:
                    config[key] = value

    # 校验必填项
    if not config.get("MINERU_TOKEN") or config["MINERU_TOKEN"] == "your_token_here":
        print("[错误] MINERU_TOKEN 未配置或仍为占位值 'your_token_here'")
        print("请编辑 .env 文件，填入从 https://mineru.net 获取的真实 Token")
        sys.exit(1)

    return config


# ── 预检 ────────────────────────────────────────────────────────────

def check_pdf(path: str) -> Tuple[int, float]:
    """检查 PDF 文件是否存在、大小是否超限、页数是否超限。
    返回 (页数, 文件大小_MB)。"""
    pdf_path = Path(path)
    if not pdf_path.is_file():
        print(f"[错误] PDF 文件不存在: {path}")
        sys.exit(1)

    file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 200:
        print(f"[错误] PDF 文件过大 ({file_size_mb:.1f}MB)，超过 200MB 限制")
        sys.exit(1)

    # 用 PyPDF2 快速数页（若未安装则跳过）
    page_count = 0
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
    except ImportError:
        print("[警告] PyPDF2 未安装，跳过页数检查。安装: pip install PyPDF2")
    except Exception as e:
        print(f"[警告] 无法读取 PDF 页数: {e}")

    if page_count > 200:
        print(f"[错误] PDF 页数 ({page_count}) 超过 200 页限制，请拆分后重试")
        sys.exit(1)

    return page_count, file_size_mb


# ── 获取 PageRange 上下文 ────────────────────────────────────────────


# ── 判定是否为封面噪声 ─────────────────────────────────────────────

def is_cover_noise(text: str) -> bool:
    """启发式判断：短文本 + 包含封面特征词 → 视为封面/噪声。"""
    text_stripped = text.strip()
    if len(text_stripped) >= 25:
        return False
    for kw in COVER_NOISE_KEYWORDS:
        if kw in text_stripped:
            return True
    return False


# ── 提取块的纯文本 ─────────────────────────────────────────────────

def extract_block_text(block: Dict) -> str:
    """从 para_block 的 lines/spans 中提取所有文本内容，inline_equation 包裹为 $...$。"""
    parts: List[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            stype = span.get("type", "text")
            content = span.get("content", "")
            if stype == "inline_equation":
                parts.append(f"${content}$")
            elif stype == "text":
                parts.append(content)
    return "".join(parts)


# ── JSON → Markdown 转换 ────────────────────────────────────────────

def json_to_markdown(json_data: Dict, scope: Optional[str]) -> str:
    """将 MinerU 返回的 JSON 清洗并转换为结构化 Markdown。"""
    lines: List[str] = []
    pdf_info = json_data.get("pdf_info", [])

    if not pdf_info:
        return "[结果] MinerU 未返回任何页面内容。"

    # 计算整体页面宽度中位数，用于判断标题层级
    page_widths = [p.get("page_size", [555, 754])[0] for p in pdf_info]
    median_width = sorted(page_widths)[len(page_widths) // 2] if page_widths else 555

    for page in pdf_info:
        page_idx = page.get("page_idx", 0)
        page_width = page.get("page_size", [555, 754])[0]
        para_blocks = page.get("para_blocks", [])

        # 页面标记（可选注释）
        # lines.append(f"\n<!-- page {page_idx + 1} -->\n")

        for block in para_blocks:
            btype = block.get("type", "text")
            bbox = block.get("bbox", [0, 0, 0, 0])

            # ── text 块 ──
            if btype == "text":
                text_content = extract_block_text(block)

                # 跳过空内容
                if not text_content.strip():
                    continue

                # 第一页封面/噪声过滤
                if page_idx == 0 and is_cover_noise(text_content):
                    continue

                lines.append(f"\n{text_content}\n")

            # ── title 块 ──
            elif btype == "title":
                text_content = extract_block_text(block)

                if not text_content.strip():
                    continue

                if page_idx == 0 and is_cover_noise(text_content):
                    continue

                # 根据 bbox 宽度判断标题层级
                bbox_width = bbox[2] - bbox[0]
                if bbox_width > median_width * 0.7:
                    lines.append(f"\n## {text_content}\n")
                else:
                    lines.append(f"\n### {text_content}\n")

            # ── formula / interline_equation 块（行间公式）──
            elif btype in ("formula", "interline_equation"):
                all_spans: List[str] = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        all_spans.append(span.get("content", ""))
                formula_text = "".join(all_spans).strip()
                if formula_text:
                    lines.append(f"\n$$\n{formula_text}\n$$\n")

            # ── image 块 ──
            elif btype == "image":
                # 提取 image_caption 和 image_body
                caption = ""
                for sub in block.get("blocks", []):
                    if sub.get("type") == "image_caption":
                        for line in sub.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("type") == "text":
                                    caption = span.get("content", "").strip()
                if caption:
                    lines.append(f"\n> [图像: {caption}]\n")
                else:
                    lines.append("\n> [图像]\n")

            # ── table 块 ──
            elif btype == "table":
                table_md = _convert_table_block(block)
                if table_md:
                    lines.append(f"\n{table_md}\n")

            # ── list 块 ──
            elif btype == "list":
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        stype = span.get("type", "text")
                        content = span.get("content", "").strip()
                        if content:
                            if stype == "inline_equation":
                                lines.append(f"- ${content}$")
                            else:
                                lines.append(f"- {content}")

            # ── 未识别类型，尝试作为文本处理 ──
            else:
                text_content = extract_block_text(block)
                if text_content.strip():
                    lines.append(f"\n{text_content}\n")

    return "\n".join(lines)


def _convert_table_block(block: Dict) -> str:
    """将 table block（含 html）转换为 GFM 表格。若解析失败则降级为图片占位。"""
    caption = ""
    html_content = ""
    image_path = ""

    for sub in block.get("blocks", []):
        stype = sub.get("type", "")
        if stype == "table_caption":
            for line in sub.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("type") == "text":
                        caption = span.get("content", "").strip()
        elif stype == "table_body":
            for line in sub.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("type") == "table":
                        html_content = span.get("html", "")
                    if span.get("image_path"):
                        image_path = span.get("image_path", "")

    # 尝试从 HTML 解析为 GFM 表格
    if html_content:
        try:
            gfm = _html_table_to_gfm(html_content)
            if gfm:
                header = f"**{caption}**\n\n" if caption else ""
                return header + gfm
        except Exception as exc:
            print(f"[警告] HTML 表格转 GFM 失败: {exc}")
            pass

    # 降级：输出为图片占位
    if image_path:
        cap = f": {caption}" if caption else ""
        return f"> [表格{cap}]({image_path})"
    cap = f": {caption}" if caption else ""
    return f"> [表格{cap}]"


def _html_table_to_gfm(html: str) -> str:
    """将简单 HTML <table> 转换为 GFM Markdown 表格。"""
    # 提取所有行
    rows: List[List[str]] = []
    # 匹配所有 <tr>...</tr>
    tr_pattern = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
    td_pattern = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL)

    for tr_match in tr_pattern.finditer(html):
        cells = []
        for td_match in td_pattern.finditer(tr_match.group(1)):
            cell_text = re.sub(r"<[^>]+>", "", td_match.group(1)).strip()
            cells.append(cell_text)
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    # 处理 rowspan/colspan（简化处理：合并相同内容的连续行）
    # 这里做简化处理：使用原始 HTML 中提取的单元格内容
    gfm_lines: List[str] = []

    # 表头行
    gfm_lines.append("| " + " | ".join(rows[0]) + " |")
    gfm_lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")

    # 数据行
    for row in rows[1:]:
        # 确保列数对齐
        while len(row) < len(rows[0]):
            row.append("")
        gfm_lines.append("| " + " | ".join(row[:len(rows[0])]) + " |")

    return "\n".join(gfm_lines)


# ── 页码裁剪 ──────────────────────────────────────────────────────

def filter_pages(json_data: Dict, scope: Optional[str]) -> Dict:
    """如果指定了页码范围，裁剪 pdf_info 到指定范围。"""
    if not scope:
        return json_data

    # 解析 "1-30" 或 "5" 格式
    parts = scope.split("-")
    try:
        start = int(parts[0].strip()) - 1  # 转为 0-indexed
        end = int(parts[1].strip()) if len(parts) > 1 else start + 1
    except ValueError:
        print(f"[警告] 无法解析页码范围 '{scope}'，将处理全部页面")
        return json_data

    pdf_info = json_data.get("pdf_info", [])
    filtered = [p for p in pdf_info if start <= p.get("page_idx", 0) < end]
    if not filtered:
        print(f"[警告] 页码范围 {scope} 未匹配到任何页面，将处理全部页面")
        return json_data

    return {**json_data, "pdf_info": filtered}


# ── API 交互 ────────────────────────────────────────────────────────

BASE_URL = "https://mineru.net/api/v4"


def api_headers(token: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def parse_api_error(response_json: Dict) -> str:
    """从 API 响应中提取可读错误信息。"""
    code = str(response_json.get("code", ""))
    msg = response_json.get("msg", "")
    if code in ERROR_CODE_MAP:
        return ERROR_CODE_MAP[code]
    if code != "0":
        return f"API 返回错误 (code={code}): {msg}"
    return msg


def get_upload_url(token: str, file_name: str, model_version: str) -> Tuple[str, str]:
    """
    步骤 1: POST /api/v4/file-urls/batch 获取预签名上传 URL。
    返回 (batch_id, presigned_url)。
    """
    url = f"{BASE_URL}/file-urls/batch"
    payload = {
        "files": [{"name": file_name, "data_id": "pdf_parse_1"}],
        "model_version": model_version,
    }

    resp = requests.post(url, headers=api_headers(token), json=payload, timeout=30)
    result = resp.json()

    if resp.status_code != 200 or result.get("code") != 0:
        error_msg = parse_api_error(result)
        raise RuntimeError(f"获取上传 URL 失败: {error_msg}")

    batch_id = result["data"]["batch_id"]
    file_urls = result["data"]["file_urls"]
    if not file_urls:
        raise RuntimeError("获取上传 URL 成功但未返回任何 URL")

    return batch_id, file_urls[0]


def upload_file(presigned_url: str, file_path: str) -> None:
    """步骤 2: PUT 上传 PDF 到预签名 URL。"""
    with open(file_path, "rb") as f:
        resp = requests.put(presigned_url, data=f, timeout=300)

    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"文件上传失败 (HTTP {resp.status_code})。"
            f"预签名 URL 可能已过期（有效期 24 小时），请重试。"
        )


def poll_extract_result(
    token: str,
    batch_id: str,
    poll_interval: int,
    poll_timeout: int,
) -> Dict:
    """步骤 3: 轮询 GET /api/v4/extract-results/batch/{batch_id}。"""
    url = f"{BASE_URL}/extract-results/batch/{batch_id}"
    state_labels = {
        "pending": "排队中",
        "running": "提取中",
        "uploading": "文件上传中",
        "waiting-file": "等待文件就绪",
    }

    start = time.time()
    while time.time() - start < poll_timeout:
        resp = requests.get(url, headers=api_headers(token), timeout=30)
        result = resp.json()

        if result.get("code") != 0:
            error_msg = parse_api_error(result)
            raise RuntimeError(f"查询任务结果失败: {error_msg}")

        extract_results = result["data"].get("extract_result", [])
        if not extract_results:
            raise RuntimeError("查询结果中未包含 extract_result")

        first = extract_results[0]
        state = first.get("state", "unknown")
        elapsed = int(time.time() - start)

        if state == "done":
            full_zip_url = first.get("full_zip_url", "")
            if not full_zip_url:
                raise RuntimeError("任务完成但未返回 full_zip_url")
            return first

        if state == "failed":
            err_msg = first.get("err_msg", "未知错误")
            err_code = first.get("err_code", "")
            detail = ERROR_CODE_MAP.get(str(err_code), "")
            raise RuntimeError(
                f"提取任务失败: {err_msg}"
                + (f"\n错误码 {err_code}: {detail}" if detail else "")
            )

        progress_info = first.get("extract_progress", {})
        if progress_info:
            current = progress_info.get("extracted_pages", "?")
            total = progress_info.get("total_pages", "?")
            print(f"  [{elapsed}s] {state_labels.get(state, state)} ({current}/{total} 页)")
        else:
            print(f"  [{elapsed}s] {state_labels.get(state, state)}")

        time.sleep(poll_interval)

    raise TimeoutError(
        f"轮询超时 ({poll_timeout}s)，任务可能仍在处理中。"
        f"请稍后使用 batch_id 手动查询: {batch_id}"
    )


def download_and_extract_json(full_zip_url: str, token: str) -> Dict:
    """步骤 4-5: 下载 ZIP 并提取 JSON。"""
    resp = requests.get(full_zip_url, headers=api_headers(token), timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"下载 ZIP 失败 (HTTP {resp.status_code})")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(resp.content)
        zip_path = tmp.name

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            all_names = zf.namelist()
            json_names = [n for n in all_names if n.endswith(".json") and not n.startswith("__MACOSX")]

            if not json_names:
                file_list = "\n".join(all_names)
                raise RuntimeError(
                    f"ZIP 中未找到 JSON 文件。\nZIP 内容:\n{file_list}"
                )

            # MinerU API 返回多个 JSON，优先选 layout.json（含 pdf_info 结构），
            # 其次选第一个 .json 作为降级
            json_name = None
            for name in json_names:
                if name.endswith("layout.json") or name == "layout.json":
                    json_name = name
                    break
            if json_name is None:
                json_name = json_names[0]

            # 读取 JSON
            with zf.open(json_name) as jf:
                raw_bytes = jf.read()
                # 尝试 UTF-8，失败则尝试 GBK
                try:
                    raw_text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    raw_text = raw_bytes.decode("gbk", errors="replace")
                return json.loads(raw_text)
    finally:
        os.unlink(zip_path)


# ── 主流程 ─────────────────────────────────────────────────────────

def process_pdf(
    pdf_path: str,
    token: str,
    model_version: str = "vlm",
    poll_interval: int = 3,
    poll_timeout: int = 300,
    scope: Optional[str] = None,
) -> str:
    """完整 PDF 解析流程：上传 → 轮询 → 下载 → 清洗 → 返回 Markdown。"""
    file_name = os.path.basename(pdf_path)

    # 步骤 1: 获取预签名上传 URL
    print(f"[1/5] 获取上传 URL ...")
    batch_id, presigned_url = get_upload_url(token, file_name, model_version)
    print(f"  batch_id: {batch_id}")

    # 步骤 2: 上传 PDF
    print(f"[2/5] 上传 PDF ({file_name}) ...")
    upload_file(presigned_url, pdf_path)
    print("  上传完成")

    # 步骤 3: 轮询提取结果
    print(f"[3/5] 等待提取完成（轮询间隔 {poll_interval}s，超时 {poll_timeout}s）...")
    extract_result = poll_extract_result(token, batch_id, poll_interval, poll_timeout)
    full_zip_url = extract_result.get("full_zip_url", "")
    print(f"  提取完成")

    # 步骤 4: 下载 ZIP 并提取 JSON
    print(f"[4/5] 下载结果并提取 JSON ...")
    json_data = download_and_extract_json(full_zip_url, token)
    total_pages = len(json_data.get("pdf_info", []))
    print(f"  共 {total_pages} 页")

    # 步骤 5: JSON → Markdown
    print(f"[5/5] 结构化清洗 → Markdown ...")
    if scope:
        json_data = filter_pages(json_data, scope)
    markdown = json_to_markdown(json_data, scope)
    print(f"  完成")
    return markdown


# ── CLI 入口 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MinerU Precision Extract API — PDF 解析为结构化 Markdown",
    )
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument(
        "--scope", "-s",
        default=None,
        help='页码范围，如 "1-30" 或 "5"（仅处理第 5 页）',
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出 Markdown 文件路径（非 --check 模式必填）",
    )
    parser.add_argument(
        "--env", "-e",
        default=None,
        help=".env 文件路径（默认从脚本所在目录加载）",
    )
    parser.add_argument(
        "--check", "-c",
        action="store_true",
        help="仅做环境预检（.env / Token / PDF），不执行实际提取",
    )
    args = parser.parse_args()

    # 确定脚本目录
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # ── --check 模式：仅预检环境，不执行提取 ──
    if args.check:
        env_dir = os.path.dirname(args.env) if args.env else script_dir
        env_path = os.path.join(env_dir, ".env") if os.path.isdir(env_dir) else env_dir

        if not os.path.isfile(env_path):
            print(json.dumps({
                "status": "env_missing",
                "message": f"未找到 .env 文件（预期路径: {env_path}）",
                "fix": f"请将 skills/pdf-parser/.env.example 复制到 {env_path} 并填入 MINERU_TOKEN"
            }))
            sys.exit(1)

        config = load_env(env_dir)
        token = config.get("MINERU_TOKEN", "").strip()

        if not token or token == "your_token_here":
            print(json.dumps({
                "status": "token_missing",
                "message": "MINERU_TOKEN 未配置或仍为占位值",
                "fix": "请编辑 .env 文件，填入从 https://mineru.net 获取的真实 Token"
            }))
            sys.exit(1)

        if not os.path.isfile(args.pdf_path):
            print(json.dumps({
                "status": "pdf_missing",
                "message": f"PDF 文件不存在: {args.pdf_path}",
                "fix": "请检查 PDF 文件路径是否正确"
            }))
            sys.exit(2)

        file_size_mb = os.path.getsize(args.pdf_path) / (1024 * 1024)
        if file_size_mb > 200:
            print(json.dumps({
                "status": "pdf_too_large",
                "message": f"PDF 文件 {file_size_mb:.1f}MB 超过 200MB 限制",
                "fix": "请压缩 PDF 或拆分为多个小文件"
            }))
            sys.exit(2)

        print(json.dumps({"status": "ok", "message": "环境预检通过"}))
        sys.exit(0)

    # ── 正常提取模式 ──

    if not args.output:
        print("[错误] 非 --check 模式下 --output/-o 为必填参数", file=sys.stderr)
        sys.exit(1)

    # 加载配置
    env_dir = os.path.dirname(args.env) if args.env else script_dir
    config = load_env(env_dir)
    token = config["MINERU_TOKEN"]
    model_version = config.get("MINERU_MODEL_VERSION", "vlm")
    poll_interval = int(config.get("MINERU_POLL_INTERVAL", "3"))
    poll_timeout = int(config.get("MINERU_POLL_TIMEOUT", "300"))

    # 预检 PDF
    page_count, file_size_mb = check_pdf(args.pdf_path)
    print(f"PDF: {args.pdf_path}")
    if page_count > 0:
        print(f"  页数: {page_count}")
    print(f"  大小: {file_size_mb:.1f} MB")
    print()

    # 执行解析
    try:
        markdown = process_pdf(
            pdf_path=args.pdf_path,
            token=token,
            model_version=model_version,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            scope=args.scope,
        )
    except Exception as e:
        print(f"\n[失败] {e}", file=sys.stderr)
        sys.exit(1)

    # 输出（始终写入文件，避免上下文 token 消耗）
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"\n[DONE] Markdown 已写入: {args.output}")

    sys.exit(0)


if __name__ == "__main__":
    main()
