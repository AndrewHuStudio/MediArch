# script/tests/test_reindex_from_ocr.py
# -*- coding: utf-8 -*-
"""定向重灌脚本的测试: 复用 OCR 产物 -> 切分, 重点验证图片绝对路径的坑。

这些测试只读本地 OCR 产物 (data_process/documents_ocr/), 不连任何数据库。
标尺文档: GB 51039-2014 综合医院建筑设计规范 (已知 5 张图, 正文含「选址」)。
"""

from pathlib import Path

import pytest

from script import reindex_from_ocr


GB_CATEGORY = "标准规范"
GB_DOC_DIR = "GB 51039-2014 综合医院建筑设计规范"  # OCR 目录名 (无后缀)
GB_SOURCE = GB_DOC_DIR + ".pdf"  # source_document 必须与正常管线一致 (带 .pdf)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GB_FULL_DIR = PROJECT_ROOT / "data_process" / "documents_ocr" / GB_CATEGORY / GB_DOC_DIR / "full"


requires_gb = pytest.mark.skipif(
    not GB_FULL_DIR.is_dir(),
    reason=f"GB 51039 OCR 产物不存在: {GB_FULL_DIR}",
)


@requires_gb
def test_build_ocr_result_artifacts_dir_parent_is_full():
    """图片路径的坑: chunking 取 artifacts_dir.parent 作为 doc_dir,
    再找 doc_dir/images/<file>。OCR 产物图片在 full/images/ 下,
    所以 artifacts_dir 必须满足 .parent == full 目录。"""
    ocr_result = reindex_from_ocr.build_ocr_result(GB_FULL_DIR)

    artifacts_dir = Path(ocr_result["artifacts_dir"])
    assert artifacts_dir.parent == GB_FULL_DIR.resolve()


@requires_gb
def test_build_ocr_result_has_markdown_and_detail():
    ocr_result = reindex_from_ocr.build_ocr_result(GB_FULL_DIR)
    result = ocr_result["result"]

    assert len(result["markdown"]) > 1000, "正文 markdown 应有实质内容"
    assert len(result["detail"]) > 100, "detail 应有大量段落"


@requires_gb
def test_chunk_from_ocr_yields_text_with_xuanzhi():
    """切分应产出大量正文 chunk, 且含「选址」(GB 51039 的 gold section)。"""
    chunks = reindex_from_ocr.chunk_from_ocr(GB_FULL_DIR, GB_SOURCE, GB_CATEGORY)

    text_chunks = [c for c in chunks if c.get("content_type") == "text"]
    assert len(text_chunks) > 100, f"正文 chunk 偏少: {len(text_chunks)}"
    assert any("选址" in (c.get("content") or "") for c in text_chunks), "应含「选址」正文"


@requires_gb
def test_chunk_source_document_keeps_pdf_suffix():
    """source_document 必须与正常管线一致 (带 .pdf), 否则幂等删旧匹配不到旧残片。"""
    chunks = reindex_from_ocr.chunk_from_ocr(GB_FULL_DIR, GB_SOURCE, GB_CATEGORY)
    assert chunks, "应有 chunk"
    for c in chunks:
        assert c.get("source_document") == GB_SOURCE, (
            f"source_document 应为 {GB_SOURCE!r}, 实际 {c.get('source_document')!r}"
        )


@requires_gb
def test_chunk_from_ocr_image_abs_paths_resolve():
    """图片坑的最终验证: image chunk 的 image_url_abs 必须指向真实存在的图片文件。
    GB 51039 已知 5 张图。"""
    chunks = reindex_from_ocr.chunk_from_ocr(GB_FULL_DIR, GB_SOURCE, GB_CATEGORY)

    image_chunks = [c for c in chunks if c.get("content_type") == "image"]
    assert len(image_chunks) == 5, f"GB 51039 应有 5 张图, 实际 {len(image_chunks)}"

    for c in image_chunks:
        abs_path = c.get("image_url_abs")
        assert abs_path, f"image chunk 缺少 image_url_abs: {c.get('chunk_id')}"
        assert Path(abs_path).is_file(), f"image_url_abs 不存在: {abs_path}"


@requires_gb
def test_discover_documents_source_name_has_pdf_suffix():
    """discover_documents 返回的 source_document 必须带 .pdf, 与正常管线一致。
    否则幂等删旧用的键 (无后缀) 匹配不到旧残片 (带 .pdf), 导致新旧并存。"""
    docs = reindex_from_ocr.discover_documents()
    assert docs, "应发现已 OCR 文档"

    gb = [d for d in docs if d.full_dir.resolve() == GB_FULL_DIR.resolve()]
    assert len(gb) == 1, "应发现 GB 51039"
    assert gb[0].source_document == GB_SOURCE, (
        f"source_document 应为 {GB_SOURCE!r}, 实际 {gb[0].source_document!r}"
    )
    # 每个发现的文档名都应带 .pdf
    for d in docs:
        assert d.source_document.endswith(".pdf"), (
            f"source_document 应带 .pdf: {d.source_document!r}"
        )


# ---- delete_existing 幂等删旧: 必须能命中孤儿残片 (chunk 的 doc_id 已无对应 document) ----

class _FakeCollection:
    """记录 delete 调用的假 Mongo collection。"""

    def __init__(self, docs=None):
        self._docs = list(docs or [])
        self.delete_filters = []

    def find(self, query, projection=None):
        return iter([])  # documents 集合为空 -> 模拟孤儿残片场景

    def delete_many(self, query):
        self.delete_filters.append(query)

        class _R:
            deleted_count = 7

        return _R()


class _FakeMongoWriter:
    def __init__(self):
        self.documents = _FakeCollection()
        self.chunks = _FakeCollection()


class _FakeMilvusCollection:
    def delete(self, expr):
        class _R:
            delete_count = 6

        return _R()

    def flush(self):
        pass


class _FakeMilvusWriter:
    def __init__(self):
        self.collection = _FakeMilvusCollection()

    def _ensure_loaded(self):
        pass


def test_delete_existing_deletes_mongo_chunks_by_source_document():
    """孤儿残片场景: documents 集合里没有对应 doc, 但 chunks 集合有残片。
    删旧必须直接按 source_document 删 chunks, 否则旧残片删不掉, 重灌后翻倍。"""
    mongo = _FakeMongoWriter()
    milvus = _FakeMilvusWriter()

    reindex_from_ocr.delete_existing("某文档.pdf", milvus, mongo)

    # chunks 必须收到一个按 source_document 的删除 (而不是只按 doc_id)
    assert any(
        f.get("source_document") == "某文档.pdf" for f in mongo.chunks.delete_filters
    ), f"chunks 删除应按 source_document, 实际: {mongo.chunks.delete_filters}"


