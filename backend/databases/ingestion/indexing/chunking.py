"""
智能 Chunk 分块策略（优化版 v2 - 2025-01-22）

核心改进：
1. 归一化坐标：position 从 OCR 像素坐标转为 PDF 页面比例坐标 [0, 1]
2. 增大 chunk：max_chunk_size 从 1000 → 1200（可配置），保持条款完整性
3. 表格结构化：保留表格 HTML/JSON，新增 content_type="table"
4. 图文关联：记录图片所属段落的 parent_chunk_id
5. 多位置支持：跨页 chunk 保存 positions 数组而非单个 position
6. 增强元数据：year（提取年份）、table_structure（表格数据）

性能优化：
- 内容使用列表累积，最后再 join，避免 O(n^2) 级字符串拼接
- 小块合并同样用列表累积
- 更少的 dict 复制和 update
"""

import uuid
from typing import List, Dict, Optional, Any


class ChunkStrategy:
    """Chunk分块策略"""

    def __init__(
        self,
        max_chunk_size: int = 1200,  # 提升到1200以保持条款完整
        min_chunk_size: int = 100,
        merge_small_chunks: bool = True,
        chunk_overlap: int = 100,
        normalize_positions: bool = True,  # 是否归一化坐标
    ):
        """
        初始化分块策略

        Args:
            max_chunk_size: 最大chunk字符数（默认1200，适合医疗规范长条款）
            min_chunk_size: 最小chunk字符数
            merge_small_chunks: 是否合并过小的chunk
            chunk_overlap: chunk 重叠字符数
            normalize_positions: 是否将 position 归一化为 [0,1] 比例坐标
        """
        self.max_chunk_size = int(max_chunk_size)
        self.min_chunk_size = int(min_chunk_size)
        self.merge_small_chunks = bool(merge_small_chunks)
        self.chunk_overlap = max(int(chunk_overlap), 0)
        self.normalize_positions = bool(normalize_positions)
        self._page_sizes_cache: Dict[int, tuple] = {}  # 缓存页面尺寸 {page: (width, height)}

    # ---------- 公共 API ----------
    def chunk_by_hierarchy(self, textin_result: Dict, doc_metadata: Dict) -> List[Dict]:
        """
        基于层级结构进行分块

        策略：
        1. 按照 outline_level（标题层级）分块
        2. 同一标题下的内容合并为一个 chunk
        3. 保留位置信息和元数据（坐标归一化为 [0,1] 比例）
        4. 表格单独处理，保留结构化数据
        5. 图片记录所属段落关联
        """
        result = textin_result.get("result")
        if not result:
            return []

        import logging
        logger = logging.getLogger(__name__)

        details = result.get("detail") or []
        markdown = result.get("markdown") or ""

        if not details:
            return self._simple_chunk(markdown, doc_metadata)

        source_document = (
            doc_metadata.get("source_document")
            or doc_metadata.get("title")
            or doc_metadata.get("doc_title")
            or doc_metadata.get("category")
            or doc_metadata.get("file_path")
            or "未知来源"
        )
        source_category = (
            doc_metadata.get("source_category")
            or doc_metadata.get("category")
            or "未分组"
        )
        source_directory = (
            doc_metadata.get("source_directory")
            or doc_metadata.get("file_path")
            or ""
        )

        # [FIX 2025-12-27] 获取 PDF 路径用于提取真实页面尺寸
        pdf_path = (
            doc_metadata.get("source_path")
            or doc_metadata.get("file_path")
            or doc_metadata.get("document_path")
        )

        # 尝试从标题提取年份（用于后续检索过滤）
        year = self._extract_year(source_document)

        chunks: List[Dict[str, Any]] = []
        current_chunk: Optional[Dict[str, Any]] = None
        current_section: Optional[str] = None
        last_text_chunk_id: Optional[str] = None  # 用于图片关联

        # 为当前 chunk 维护内容列表与长度，避免重复拼接与重复计算
        content_parts: List[str] = []
        content_len: int = 0

        # 用于累积同一 chunk 的多个位置（跨页时）
        accumulated_positions: List[Dict[str, Any]] = []

        def flush_current_chunk(force_overlap: bool = False):
            """将当前 chunk（若存在）落盘到 chunks"""
            nonlocal current_chunk, content_parts, content_len, accumulated_positions, last_text_chunk_id
            if current_chunk is None:
                return
            overlap_seed = None
            overlap_text = ""
            if content_parts:
                content_str = ("".join(content_parts)).strip()
                current_chunk["content"] = content_str

                # 保存多位置信息（跨页chunk）
                if accumulated_positions:
                    current_chunk["positions"] = accumulated_positions

                if (
                    force_overlap
                    and self.chunk_overlap > 0
                    and content_str
                ):
                    overlap_text = content_str[-self.chunk_overlap :]
                    page_hint = (
                        current_chunk.get("metadata", {}).get("page_number")
                        or current_chunk.get("page_range", [1, 1])[1]
                    )
                    overlap_seed = {
                        "section": current_chunk.get("section"),
                        "outline_level": current_chunk.get("outline_level", -1),
                        "position": current_chunk.get("position"),
                        "page_id": page_hint,
                    }
            if current_chunk.get("content"):
                # 记录为最后一个文本chunk（供图片关联）
                if current_chunk.get("content_type") == "text":
                    last_text_chunk_id = current_chunk["chunk_id"]
                chunks.append(current_chunk)
            current_chunk = None
            content_parts = []
            content_len = 0
            accumulated_positions = []

            if overlap_seed and overlap_text:
                new_ck = new_chunk(
                    section=overlap_seed["section"],
                    page_id=overlap_seed["page_id"],
                    outline_level=overlap_seed["outline_level"],  # 修复：overlap_level -> outline_level
                    position=overlap_seed["position"],
                    has_title=False,
                    paragraph_id=None,
                    content_type="text",
                )
                current_chunk = new_ck
                content_parts = [overlap_text]
                content_len = len(overlap_text)

        def new_chunk(
            section: Optional[str],
            page_id: int,
            outline_level: int,
            position: List = None,
            has_title: bool = False,
            paragraph_id: Optional[Any] = None,
            content_type: str = "text",
        ) -> Dict[str, Any]:
            """创建一个新的 chunk dict

            [FIX 2025-01-22] 增强元数据：
            - 归一化坐标：position 转为 [0,1] 比例
            - 年份提取：便于时间过滤
            - 图文关联：parent_chunk_id
            - 多位置：positions 数组
            """
            # 归一化坐标
            normalized_pos = self._normalize_position(position, page_id, pdf_path) if position else []

            return {
                "chunk_id": str(uuid.uuid4()),
                "sequence": len(chunks) + 1,  # 暂时顺序，最终会重编号
                "section": section or ("正文" if content_type == "text" else "图片"),
                "page_range": [page_id, page_id],
                "content": "",  # 最终由 content_parts 写回
                "content_type": content_type,
                "outline_level": int(outline_level),
                "position": normalized_pos,  # 归一化后的坐标
                "source_document": source_document,
                "source_category": doc_metadata.get("source_category"),
                "source_directory": doc_metadata.get("source_directory"),
                "metadata": {
                    "paragraph_ids": ([] if paragraph_id is None else [paragraph_id]),
                    "has_title": bool(has_title),
                    "page_number": page_id,
                    "page": page_id,
                    "section": section or ("正文" if content_type == "text" else "图片"),
                    "heading": section if has_title else None,
                    "year": year,  # 文档年份
                },
                # 文档元数据
                "doc_type": doc_metadata.get("type"),
                "doc_title": doc_metadata.get("title"),
                "doc_category": doc_metadata.get("category"),
                "doc_source_category": doc_metadata.get("source_category"),
                # [FIX 2025-12-09] 添加 file_path 和 document_path 字段，用于 PDF 预览
                "file_path": doc_metadata.get("file_path"),
                "document_path": doc_metadata.get("document_path") or doc_metadata.get("file_path"),
            }

        # ✨ [FIX 2025-12-19] 从 Markdown 中提取所有图片引用（兜底）
        # 说明：优先使用 detail 里的 image item（能拿到 page_id/position/父段落关联）；
        # 若 detail 不提供图片，再回退到 Markdown 的 ![]() 链接提取。
        artifacts_dir = doc_metadata.get("artifacts_dir")

        def _detail_has_images() -> bool:
            for it in details:
                if not isinstance(it, dict):
                    continue
                if str(it.get("type") or "").lower() != "image":
                    continue
                if it.get("image_url") or it.get("image_path") or it.get("path"):
                    return True
            return False

        detail_has_images = _detail_has_images()

        # 从 Markdown 中一次性提取所有图片（仅当 detail 不提供图片时）
        if artifacts_dir and not detail_has_images:
            markdown = result.get("markdown") or ""
            if markdown:
                import re
                from pathlib import Path

                image_pattern = re.compile(r"!\[([^\]]*)\]\(images/([^)]+)\)")
                heading_pattern = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")

                lines = markdown.splitlines()
                current_md_section = ""
                extracted: List[tuple[str, str, str]] = []  # (caption, filename, section)

                def _is_bad_caption_line(line: str) -> bool:
                    s = (line or "").strip()
                    if not s:
                        return True
                    if s.startswith("![") or s.startswith("![]"):
                        return True
                    if s.startswith("|"):
                        return True
                    if s.startswith("<table") or s.startswith("</table") or s.startswith("<tr") or s.startswith("<td"):
                        return True
                    return False

                for idx, line in enumerate(lines):
                    h = heading_pattern.match(line)
                    if h:
                        current_md_section = h.group(1).strip()
                        continue

                    for m in image_pattern.finditer(line):
                        alt = (m.group(1) or "").strip()
                        image_filename_raw = (m.group(2) or "").strip()
                        if not image_filename_raw:
                            continue

                        # 防止 "xxx.jpg \"title\"" 这类情况
                        image_filename = image_filename_raw.split()[0].strip().strip('"').strip("'")

                        caption = alt
                        if not caption:
                            # 兜底：向上找最近的可用文本行/标题
                            for j in range(idx - 1, max(-1, idx - 9), -1):
                                prev = (lines[j] or "").strip()
                                if _is_bad_caption_line(prev):
                                    continue
                                hh = heading_pattern.match(prev)
                                if hh:
                                    caption = hh.group(1).strip()
                                    break
                                caption = prev if len(prev) <= 80 else prev[:80]
                                break

                        caption = caption or current_md_section or "无标题"
                        extracted.append((caption, image_filename, current_md_section or "图片"))

                if extracted:
                    logger.info(f"Found {len(extracted)} images in Markdown (fallback mode)")

                    # 为每张图片创建独立的 chunk
                    for caption, image_filename, section in extracted:
                        artifacts_path = Path(artifacts_dir)
                        doc_dir = artifacts_path.parent  # documents_ocr/<category>/<doc>
                        category_name = doc_dir.parent.name
                        doc_folder = doc_dir.name
                        image_rel_path = f"{category_name}/{doc_folder}/images/{image_filename}"
                        image_abs_path = (doc_dir / "images" / image_filename).resolve()  # 解析为绝对路径

                        # 创建图片 chunk（页码未知，先用 1 占位；caption 至少包含章节/上下文）
                        img_chunk = {
                            "chunk_id": str(uuid.uuid4()),
                            "sequence": len(chunks) + 1,
                            "section": section or "图片",
                            "page_range": [1, 1],
                            "content": f"[图片: {caption}]",
                            "content_type": "image",
                            "image_url": image_rel_path,
                            "image_url_abs": str(image_abs_path) if image_abs_path.exists() else None,
                            "position": [],
                            "source_document": source_document,
                            "source_category": source_category,
                            "source_directory": source_directory,
                            "metadata": {
                                "paragraph_ids": [],
                                "page_number": 1,
                                "page": 1,
                                "section": section or "图片",
                                "caption": caption,
                                "caption_source": "markdown_context",
                                "year": year,
                            },
                            "doc_type": doc_metadata.get("type"),
                            "doc_title": doc_metadata.get("title"),
                            "doc_category": doc_metadata.get("category"),
                            "doc_source_category": source_category,
                            "file_path": doc_metadata.get("file_path"),
                            "document_path": doc_metadata.get("document_path") or doc_metadata.get("file_path"),
                            "outline_level": -1,
                        }
                        chunks.append(img_chunk)

        for item in details:
            outline_level = item.get("outline_level", -1)
            text = (item.get("text") or "").strip()
            page_id = int(item.get("page_id", 1))
            paragraph_id = item.get("paragraph_id")
            item_type = str(item.get("type") or "").lower()
            position = item.get("position") or []

            # 空文本直接跳过
            if not text and item_type not in {"table", "image"}:
                continue

            # 标题（outline_level >= 0）
            if outline_level >= 0:
                flush_current_chunk()
                current_section = text
                current_chunk = new_chunk(
                    section=current_section,
                    page_id=page_id,
                    outline_level=outline_level,
                    position=position,
                    has_title=True,
                    paragraph_id=paragraph_id,
                    content_type="text",
                )
                content_parts.append(text)
                content_parts.append("\n\n")
                content_len += len(text) + 2
                continue

            # 正文段落
            if item_type == "paragraph":
                if current_chunk is None:
                    current_chunk = new_chunk(
                        section=current_section,
                        page_id=page_id,
                        outline_level=-1,
                        position=position,
                        has_title=False,
                        paragraph_id=None,
                        content_type="text",
                    )

                # 累积正文
                content_parts.append(text)
                content_parts.append("\n\n")
                content_len += len(text) + 2

                # [FIX 2025-01-22] 累积位置信息（修复缺失）
                if position:
                    normalized_pos = self._normalize_position(position, page_id, pdf_path)
                    if normalized_pos and len(normalized_pos) >= 5:
                        # normalized_pos 格式: [page, x0_ratio, y0_ratio, x1_ratio, y1_ratio]
                        accumulated_positions.append({
                            "page": page_id,
                            "bbox": normalized_pos[1:5]  # [x0, y0, x1, y1] ratios
                        })

                # [FIX 2025-01-17] 更新分页与段落元数据（增强页码跟踪）
                pr = current_chunk["page_range"]
                # 更新页码区间（起始和结束页）
                if page_id < pr[0]:
                    pr[0] = page_id
                if page_id > pr[1]:
                    pr[1] = page_id

                # 更新metadata中的page_number（如果跨页，记录结束页）
                if page_id > current_chunk["metadata"].get("page_number", page_id):
                    current_chunk["metadata"]["page_number"] = page_id
                    current_chunk["metadata"]["page"] = page_id

                # 追加段落ID
                if paragraph_id is not None:
                    current_chunk["metadata"]["paragraph_ids"].append(paragraph_id)

                # 超过上限就立即落盘
                if content_len > self.max_chunk_size:
                    flush_current_chunk(force_overlap=True)
                continue

            # 表格处理分支（结构化保存）
            if item_type == "table":
                table_html = item.get("table_html") or ""
                table_caption = item.get("table_caption") or []
                caption_text = " ".join(table_caption) if isinstance(table_caption, list) else str(table_caption)

                # 提取纯文本（去除HTML标签）
                import re
                table_text = re.sub(r"<[^>]+>", " ", table_html)
                table_text = " ".join(table_text.split())  # 压缩空白

                full_content = f"[表格] {caption_text}\n{table_text}".strip()

                if full_content:
                    flush_current_chunk()
                    normalized_pos = self._normalize_position(position, page_id, pdf_path) if position else []
                    chunks.append({
                        "chunk_id": str(uuid.uuid4()),
                        "sequence": len(chunks) + 1,
                        "section": current_section or "表格",
                        "page_range": [page_id, page_id],
                        "content": full_content,
                        "content_type": "table",
                        "table_html": table_html,  # 保留原始HTML
                        "position": normalized_pos,
                        "source_document": source_document,
                        "source_category": source_category,
                        "source_directory": source_directory,
                        "metadata": {
                            "paragraph_ids": [paragraph_id] if paragraph_id else [],
                            "page_number": page_id,
                            "page": page_id,
                            "section": current_section or "表格",
                            "caption": caption_text,
                            "year": year,
                        },
                        "doc_type": doc_metadata.get("type"),
                        "doc_title": doc_metadata.get("title"),
                        "doc_category": doc_metadata.get("category"),
                        "doc_source_category": source_category,
                        "outline_level": -1,
                    })
                continue

            # 图片：允许 text 为空（很多 OCR 会把图片当作无文本元素）
            if item_type == "image":
                image_url = item.get("image_url") or item.get("image_path") or item.get("path")
                if image_url:
                    # Guard: some OCR outputs may provide a directory-like placeholder (e.g. "mineru_outputs")
                    # instead of a concrete image file path; skip such entries to avoid broken image chunks.
                    try:
                        from pathlib import Path

                        raw_image_url = str(image_url).strip()
                        filename = Path(raw_image_url.split("?", 1)[0].split("#", 1)[0]).name
                        if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                            continue
                    except Exception:
                        pass
                    caption_text = (
                        text
                        or (item.get("caption") if isinstance(item.get("caption"), str) else "")
                        or (item.get("title") if isinstance(item.get("title"), str) else "")
                    ).strip()
                    section_hint = (current_section or "").strip()
                    caption_display = caption_text or section_hint or "无标题"
                    caption_for_meta = caption_text or section_hint

                    image_url_norm = image_url
                    image_url_abs = None
                    if artifacts_dir:
                        try:
                            from pathlib import Path
                            artifacts_path = Path(artifacts_dir)
                            doc_dir = artifacts_path.parent
                            category_name = doc_dir.parent.name
                            doc_folder = doc_dir.name
                            filename = Path(str(image_url)).name
                            image_url_norm = f"{category_name}/{doc_folder}/images/{filename}"
                            abs_path = (doc_dir / "images" / filename).resolve()
                            if abs_path.exists():
                                image_url_abs = str(abs_path)
                        except Exception:
                            image_url_norm = image_url
                    flush_current_chunk()
                    # [FIX 2025-01-17] 增强图片chunk的元数据
                    # [FIX 2025-12-27] 同时添加 positions 数组字段（与 text chunk 保持一致）
                    normalized_pos = self._normalize_position(position, page_id, pdf_path) if position else []
                    img_chunk = {
                        "chunk_id": str(uuid.uuid4()),
                        "sequence": len(chunks) + 1,
                        "section": current_section or "图片",
                        "page_range": [page_id, page_id],
                        "content": f"[图片: {caption_display}]",
                        "content_type": "image",
                        "image_url": image_url_norm,
                        "image_url_abs": image_url_abs,
                        "position": normalized_pos,
                        "parent_chunk_id": last_text_chunk_id,  # [FIX 2025-01-22] 添加图文关联
                        "source_document": source_document,
                        "source_category": source_category,
                        "source_directory": source_directory,
                        "metadata": {
                            "paragraph_ids": (
                                [paragraph_id] if paragraph_id is not None else []
                            ),
                            # [NEW] 图片也需要页码信息
                            "page_number": page_id,
                            "page": page_id,
                            # [NEW] 图片的章节信息
                            "section": current_section or "图片",
                            # [NEW] 图片说明文字
                            "caption": caption_for_meta if caption_for_meta else None,
                            # [FIX 2025-01-22] 冗余字段，便于查询
                            "parent_text_chunk": last_text_chunk_id,
                        },
                        "doc_type": doc_metadata.get("type"),
                        "doc_title": doc_metadata.get("title"),
                        "doc_category": doc_metadata.get("category"),
                        "doc_source_category": source_category,
                        "outline_level": -1,
                    }
                    # [FIX 2025-12-27] 添加 positions 数组（与 text chunk 保持一致）
                    if normalized_pos and len(normalized_pos) >= 5:
                        img_chunk["positions"] = [{
                            "page": page_id,
                            "bbox": normalized_pos[1:5]  # [x0, y0, x1, y1] ratios
                        }]
                    chunks.append(img_chunk)
                continue

        # 收尾
        flush_current_chunk()

        # 合并过小 chunk（可选）
        if self.merge_small_chunks and chunks:
            chunks = self._merge_small_chunks(chunks)

        # 重新编号，保证 sequence 连续
        for i, ck in enumerate(chunks, start=1):
            ck["sequence"] = i

        return chunks

    # ---------- 无 detail 的简单分块 ----------
    def _simple_chunk(self, text: str, doc_metadata: Dict) -> List[Dict]:
        chunks: List[Dict] = []
        if not text:
            return chunks

        lines = text.split("\n\n")
        buf_parts: List[str] = []
        buf_len = 0

        def flush(use_overlap: bool = False):
            nonlocal buf_parts, buf_len
            if not buf_parts:
                return
            content = ("".join(buf_parts)).strip()
            if not content:
                buf_parts = []
                buf_len = 0
                return
            chunks.append(
                {
                    "chunk_id": str(uuid.uuid4()),
                    "sequence": len(chunks) + 1,
                    "content": content,
                    "content_type": "text",
                    "doc_type": doc_metadata.get("type"),
                    "doc_title": doc_metadata.get("title"),
                    "doc_category": doc_metadata.get("category"),
                }
            )
            overlap_text = ""
            if use_overlap and self.chunk_overlap > 0:
                overlap_text = content[-self.chunk_overlap :]
            buf_parts = []
            buf_len = 0
            if overlap_text:
                buf_parts = [overlap_text]
                buf_len = len(overlap_text)

        for line in lines:
            seg = line + "\n\n"
            seg_len = len(seg)
            if buf_len and (buf_len + seg_len > self.max_chunk_size):
                flush(use_overlap=True)
            buf_parts.append(seg)
            buf_len += seg_len

        flush()
        return chunks

    # ---------- 合并小块 ----------
    def _merge_small_chunks(self, chunks: List[Dict]) -> List[Dict]:
        if not chunks:
            return []

        merged: List[Dict] = []
        buffer: Optional[Dict] = None
        parts: List[str] = []
        total_len = 0

        def flush_buffer():
            nonlocal buffer, parts, total_len
            if buffer is None:
                return
            buffer["content"] = ("".join(parts)).strip()
            merged.append(buffer)
            buffer = None
            parts = []
            total_len = 0

        for ck in chunks:
            # 图片 chunk 直接落盘；如果前面在合并文本，需要先冲刷
            if ck.get("content_type") == "image":
                flush_buffer()
                merged.append(ck)
                continue

            content = ck.get("content") or ""
            clen = len(content)

            if clen < self.min_chunk_size:
                # 小块：合并到 buffer
                if buffer is None:
                    buffer = {
                        **ck,
                        "chunk_id": str(uuid.uuid4()),
                        "metadata": {
                            **ck.get("metadata", {}),
                            "paragraph_ids": list(ck.get("metadata", {}).get("paragraph_ids", [])),
                        },
                    }
                    parts = [content]
                    total_len = clen
                else:
                    parts.append("\n\n")
                    parts.append(content)
                    total_len += 2 + clen
                    # 合并页码区间
                    pr = buffer["page_range"]
                    br = ck.get("page_range") or pr
                    if br[0] < pr[0]:
                        pr[0] = br[0]
                    if br[1] > pr[1]:
                        pr[1] = br[1]
                    # 合并段落 id
                    if "paragraph_ids" in ck.get("metadata", {}):
                        buffer["metadata"]["paragraph_ids"].extend(
                            ck["metadata"]["paragraph_ids"]
                        )
                # 这里保持原策略：只按 min 判定，不强制切分
            else:
                flush_buffer()
                merged.append(ck)

        flush_buffer()

        # 重新编号
        for i, ck in enumerate(merged, start=1):
            ck["sequence"] = i

        return merged

    # ---------- 辅助方法 ----------
    def _get_page_size(self, page_id: int, pdf_path: Optional[str] = None) -> tuple:
        """获取 PDF 页面的真实尺寸（宽度, 高度）

        Args:
            page_id: 页码（从1开始）
            pdf_path: PDF 文件路径

        Returns:
            (width, height) 或 (595.0, 842.0) 默认 A4 尺寸
        """
        # 先从缓存获取
        if page_id in self._page_sizes_cache:
            return self._page_sizes_cache[page_id]

        # 默认 A4 尺寸
        default_size = (595.0, 842.0)

        if not pdf_path:
            return default_size

        try:
            # 尝试使用 PyMuPDF (fitz)
            try:
                import fitz
                doc = fitz.open(pdf_path)
                if 0 <= page_id - 1 < len(doc):
                    page = doc[page_id - 1]
                    rect = page.rect
                    size = (float(rect.width), float(rect.height))
                    self._page_sizes_cache[page_id] = size
                    doc.close()
                    return size
                doc.close()
            except ImportError:
                pass
            except Exception:
                pass

            # 尝试使用 pypdf
            try:
                from pypdf import PdfReader
                reader = PdfReader(pdf_path)
                if 0 <= page_id - 1 < len(reader.pages):
                    page = reader.pages[page_id - 1]
                    box = page.mediabox
                    size = (float(box.width), float(box.height))
                    self._page_sizes_cache[page_id] = size
                    return size
            except ImportError:
                pass
            except Exception:
                pass

            # 尝试使用 PyPDF2
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(pdf_path)
                if 0 <= page_id - 1 < len(reader.pages):
                    page = reader.pages[page_id - 1]
                    box = page.mediabox
                    size = (float(box.width), float(box.height))
                    self._page_sizes_cache[page_id] = size
                    return size
            except ImportError:
                pass
            except Exception:
                pass

        except Exception:
            pass

        return default_size

    def _normalize_position(self, position: List, page_id: int, pdf_path: Optional[str] = None) -> List:
        """归一化 bbox 坐标为 [0, 1] 比例坐标

        输入: [x0, y0, x1, y1] (OCR 像素坐标)
        输出: [page, x0_ratio, y0_ratio, x1_ratio, y1_ratio]

        [FIX 2025-12-27] 基于真实 PDF 页面尺寸归一化，添加越界保护和诊断日志
        """
        import logging
        logger = logging.getLogger(__name__)

        if not self.normalize_positions or not position or len(position) < 4:
            return position or []

        # 获取真实页面尺寸
        page_width, page_height = self._get_page_size(page_id, pdf_path)

        try:
            x0, y0, x1, y1 = float(position[0]), float(position[1]), float(position[2]), float(position[3])

            # 归一化
            x0_ratio = x0 / page_width
            y0_ratio = y0 / page_height
            x1_ratio = x1 / page_width
            y1_ratio = y1 / page_height

            # 检查越界并记录日志
            out_of_bounds = False
            if not (0 <= x0_ratio <= 1 and 0 <= y0_ratio <= 1 and 0 <= x1_ratio <= 1 and 0 <= y1_ratio <= 1):
                out_of_bounds = True
                logger.warning(
                    "[bbox_out_of_bounds] page=%d raw=[%.1f,%.1f,%.1f,%.1f] page_size=(%.1f,%.1f) "
                    "normalized=[%.4f,%.4f,%.4f,%.4f]",
                    page_id, x0, y0, x1, y1, page_width, page_height,
                    x0_ratio, y0_ratio, x1_ratio, y1_ratio
                )

            # Clamp 到 [0, 1] 范围
            x0_ratio = max(0.0, min(1.0, x0_ratio))
            y0_ratio = max(0.0, min(1.0, y0_ratio))
            x1_ratio = max(0.0, min(1.0, x1_ratio))
            y1_ratio = max(0.0, min(1.0, y1_ratio))

            # 确保 x0 < x1, y0 < y1
            if x0_ratio >= x1_ratio:
                logger.warning("[bbox_invalid_x] page=%d x0=%.4f >= x1=%.4f, swapping", page_id, x0_ratio, x1_ratio)
                x0_ratio, x1_ratio = min(x0_ratio, x1_ratio), max(x0_ratio, x1_ratio)
            if y0_ratio >= y1_ratio:
                logger.warning("[bbox_invalid_y] page=%d y0=%.4f >= y1=%.4f, swapping", page_id, y0_ratio, y1_ratio)
                y0_ratio, y1_ratio = min(y0_ratio, y1_ratio), max(y0_ratio, y1_ratio)

            return [
                page_id,
                round(x0_ratio, 4),
                round(y0_ratio, 4),
                round(x1_ratio, 4),
                round(y1_ratio, 4)
            ]
        except Exception as e:
            logger.warning("[bbox_normalize_failed] page=%d position=%s error=%s", page_id, position, e)
            return position or []

    def _extract_year(self, text: str) -> Optional[int]:
        """从文本中提取年份（如 "GB 51039-2014" -> 2014）"""
        import re
        if not text:
            return None

        # 匹配常见年份格式
        patterns = [
            r'(\d{4})年',  # 2014年
            r'-(\d{4})',    # GB 51039-2014
            r'_(\d{4})',    # xxx_2014
            r'\((\d{4})\)', # (2014)
            r'\b(19\d{2}|20\d{2})\b'  # 独立的4位年份
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                year = int(match.group(1))
                if 1990 <= year <= 2030:  # 合理范围
                    return year

        return None
