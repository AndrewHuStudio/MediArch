import json
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api
from backend.databases.ingestion.indexing.chunking import ChunkStrategy


def test_load_ocr_result_from_mineru_content_list_normalizes_detail_for_chunking(tmp_path):
    ocr_dir = tmp_path / "full"
    images_dir = ocr_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "fig-1.jpg").write_bytes(b"fake")

    (ocr_dir / "full.md").write_text(
        "# 第一章 总则\n\n医院建筑应满足医疗流程。\n",
        encoding="utf-8",
    )
    (ocr_dir / "sample_content_list.json").write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "第一章 总则",
                    "text_level": 1,
                    "bbox": [0, 0, 200, 40],
                    "page_idx": 0,
                },
                {
                    "type": "text",
                    "text": "医院建筑应满足医疗流程。",
                    "bbox": [0, 50, 400, 120],
                    "page_idx": 0,
                },
                {
                    "type": "image",
                    "img_path": "images/fig-1.jpg",
                    "image_caption": ["总平面示意"],
                    "bbox": [0, 130, 300, 320],
                    "page_idx": 0,
                },
                {
                    "type": "table",
                    "table_caption": ["主要指标"],
                    "table_body": "<table><tr><td>床位</td><td>200</td></tr></table>",
                    "bbox": [0, 330, 300, 420],
                    "page_idx": 0,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = data_process_api._load_ocr_result_from_artifacts(ocr_dir)
    detail = payload["result"]["detail"]

    assert detail[0]["type"] == "paragraph"
    assert detail[0]["outline_level"] == 0
    assert detail[1]["type"] == "paragraph"
    assert detail[2]["type"] == "image"
    assert detail[2]["image_path"] == "images/fig-1.jpg"
    assert detail[3]["type"] == "table"
    assert detail[3]["table_html"] == "<table><tr><td>床位</td><td>200</td></tr></table>"

    chunks = ChunkStrategy(merge_small_chunks=False).chunk_by_hierarchy(
        payload,
        {
            "title": "示例书籍.pdf",
            "category": "书籍报告",
            "source_category": "书籍报告",
            "source_directory": "书籍报告/示例书籍",
            "file_path": "书籍报告/示例书籍.pdf",
            "artifacts_dir": str(ocr_dir),
        },
    )

    counter = Counter(chunk.get("content_type") for chunk in chunks)
    assert counter["text"] == 1
    assert counter["image"] == 1
    assert counter["table"] == 1
