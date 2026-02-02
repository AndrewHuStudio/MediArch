"""
P0 修复验证测试脚本

测试内容：
1. MongoDB citations 是否保留 positions/pdf_url/file_path
2. 跨章节引用是否正常工作
3. 引用索引是否与参考资料一一对应
4. 图片说明是否不包含引用索引
"""

import asyncio
import json
from typing import Dict, Any, List


def validate_mongodb_citations_preserved(response: Dict[str, Any]) -> Dict[str, Any]:
    """验证 MongoDB citations 是否保留了精确定位信息"""
    results = {
        "test_name": "MongoDB Citations Preserved",
        "passed": False,
        "details": {}
    }

    final_citations = response.get("final_citations", [])
    if not final_citations:
        results["details"]["error"] = "No final_citations found"
        return results

    # 检查是否有 positions
    citations_with_positions = [c for c in final_citations if c.get("positions")]
    results["details"]["citations_with_positions"] = len(citations_with_positions)
    results["details"]["total_citations"] = len(final_citations)

    # 检查是否有 pdf_url
    citations_with_pdf_url = [c for c in final_citations if c.get("pdf_url")]
    results["details"]["citations_with_pdf_url"] = len(citations_with_pdf_url)

    # 检查是否有 file_path
    citations_with_file_path = [c for c in final_citations if c.get("file_path")]
    results["details"]["citations_with_file_path"] = len(citations_with_file_path)

    # 通过条件：至少有一个 citation 包含 positions 和 pdf_url
    results["passed"] = (
        len(citations_with_positions) > 0 and
        len(citations_with_pdf_url) > 0
    )

    return results


def validate_cross_chapter_citations(response: Dict[str, Any]) -> Dict[str, Any]:
    """验证跨章节引用是否正常工作"""
    results = {
        "test_name": "Cross-Chapter Citations",
        "passed": False,
        "details": {}
    }

    final_citations = response.get("final_citations", [])
    if not final_citations:
        results["details"]["error"] = "No final_citations found"
        return results

    # 按 doc_id 分组，检查是否有多个章节
    doc_chapters = {}
    for citation in final_citations:
        doc_id = citation.get("doc_id") or citation.get("source")
        chapter = citation.get("chapter") or citation.get("section")

        if doc_id:
            if doc_id not in doc_chapters:
                doc_chapters[doc_id] = set()
            if chapter:
                doc_chapters[doc_id].add(chapter)

    # 找出有多个章节的文档
    cross_chapter_docs = {
        doc_id: chapters
        for doc_id, chapters in doc_chapters.items()
        if len(chapters) > 1
    }

    results["details"]["total_documents"] = len(doc_chapters)
    results["details"]["cross_chapter_documents"] = len(cross_chapter_docs)
    results["details"]["cross_chapter_docs_list"] = {
        doc_id: list(chapters)
        for doc_id, chapters in cross_chapter_docs.items()
    }

    # 通过条件：至少有一个文档有多个章节的引用
    results["passed"] = len(cross_chapter_docs) > 0

    return results


def validate_citation_index_mapping(response: Dict[str, Any]) -> Dict[str, Any]:
    """验证引用索引是否与参考资料一一对应"""
    results = {
        "test_name": "Citation Index Mapping",
        "passed": False,
        "details": {}
    }

    final_answer = response.get("final_answer", "")
    final_citations = response.get("final_citations", [])

    if not final_answer or not final_citations:
        results["details"]["error"] = "Missing final_answer or final_citations"
        return results

    # 提取答案中的所有引用索引 [n]
    import re
    citation_indices = set()
    for match in re.finditer(r'\[(\d+)\]', final_answer):
        idx = int(match.group(1))
        citation_indices.add(idx)

    # 检查索引是否在有效范围内
    max_valid_index = len(final_citations)
    invalid_indices = [idx for idx in citation_indices if idx < 1 or idx > max_valid_index]

    results["details"]["citation_indices_in_answer"] = sorted(list(citation_indices))
    results["details"]["max_valid_index"] = max_valid_index
    results["details"]["invalid_indices"] = invalid_indices

    # 通过条件：没有无效索引
    results["passed"] = len(invalid_indices) == 0

    return results


def validate_image_caption_format(response: Dict[str, Any]) -> Dict[str, Any]:
    """验证图片说明是否不包含引用索引"""
    results = {
        "test_name": "Image Caption Format",
        "passed": False,
        "details": {}
    }

    final_answer = response.get("final_answer", "")

    if not final_answer:
        results["details"]["error"] = "No final_answer found"
        return results

    # 查找所有 [image:i] 标记及其后续说明
    import re
    image_patterns = list(re.finditer(r'\[image:\d+\]([^\[]*?)(?=\[image:|\[image:|\n\n|$)', final_answer))

    results["details"]["total_images"] = len(image_patterns)

    # 检查图片说明中是否包含引用索引 [n]
    images_with_citation_index = []
    for match in image_patterns:
        caption = match.group(1)
        if re.search(r'\[\d+\]', caption):
            images_with_citation_index.append({
                "image_tag": match.group(0)[:20],
                "caption_snippet": caption[:100]
            })

    results["details"]["images_with_citation_index"] = len(images_with_citation_index)
    results["details"]["examples"] = images_with_citation_index[:3]

    # 通过条件：没有图片说明包含引用索引
    results["passed"] = len(images_with_citation_index) == 0

    return results


def validate_citation_deduplication(response: Dict[str, Any]) -> Dict[str, Any]:
    """验证同一段落中同一来源不重复标注"""
    results = {
        "test_name": "Citation Deduplication",
        "passed": False,
        "details": {}
    }

    final_answer = response.get("final_answer", "")

    if not final_answer:
        results["details"]["error"] = "No final_answer found"
        return results

    # 按段落分割
    paragraphs = final_answer.split('\n\n')

    # 检查每个段落中是否有重复的引用索引
    import re
    paragraphs_with_duplicate_citations = []

    for i, paragraph in enumerate(paragraphs):
        # 提取段落中的所有引用索引
        citation_indices = [int(m.group(1)) for m in re.finditer(r'\[(\d+)\]', paragraph)]

        # 检查是否有重复
        if len(citation_indices) != len(set(citation_indices)):
            # 统计每个索引出现的次数
            from collections import Counter
            counter = Counter(citation_indices)
            duplicates = {idx: count for idx, count in counter.items() if count > 1}

            paragraphs_with_duplicate_citations.append({
                "paragraph_index": i,
                "paragraph_snippet": paragraph[:100],
                "duplicates": duplicates
            })

    results["details"]["total_paragraphs"] = len(paragraphs)
    results["details"]["paragraphs_with_duplicates"] = len(paragraphs_with_duplicate_citations)
    results["details"]["examples"] = paragraphs_with_duplicate_citations[:3]

    # 通过条件：没有段落包含重复的引用索引
    results["passed"] = len(paragraphs_with_duplicate_citations) == 0

    return results


def run_all_tests(response: Dict[str, Any]) -> Dict[str, Any]:
    """运行所有测试"""
    test_results = {
        "summary": {
            "total_tests": 5,
            "passed_tests": 0,
            "failed_tests": 0
        },
        "tests": []
    }

    # 运行所有测试
    tests = [
        validate_mongodb_citations_preserved,
        validate_cross_chapter_citations,
        validate_citation_index_mapping,
        validate_image_caption_format,
        validate_citation_deduplication
    ]

    for test_func in tests:
        result = test_func(response)
        test_results["tests"].append(result)

        if result["passed"]:
            test_results["summary"]["passed_tests"] += 1
        else:
            test_results["summary"]["failed_tests"] += 1

    return test_results


def print_test_results(test_results: Dict[str, Any]):
    """打印测试结果"""
    print("\n" + "="*80)
    print("P0 [FIX] [OK] [FAIL]")
    print("="*80)

    summary = test_results["summary"]
    print(f"\n[OK]: {summary['passed_tests']}/{summary['total_tests']}")
    print(f"[FAIL]: {summary['failed_tests']}/{summary['total_tests']}")

    print("\n" + "-"*80)
    print("[OK]/[FAIL]")
    print("-"*80)

    for test in test_results["tests"]:
        status = "[OK]" if test["passed"] else "[FAIL]"
        print(f"\n{status} {test['test_name']}")

        if test["details"]:
            for key, value in test["details"].items():
                if isinstance(value, (list, dict)) and len(str(value)) > 200:
                    print(f"  - {key}: {str(value)[:200]}...")
                else:
                    print(f"  - {key}: {value}")

    print("\n" + "="*80)


if __name__ == "__main__":
    # 示例：从文件加载响应数据
    import sys

    def _print_usage() -> None:
        print("Usage:")
        print("  python backend/tests/test_p0_fixes.py response.json")
        print("  type response.json | python backend/tests/test_p0_fixes.py -")
        print("  cat response.json | python backend/tests/test_p0_fixes.py -")
        print("  Get-Content response.json | python backend/tests/test_p0_fixes.py -")

    def _load_json_from_bytes(raw: bytes) -> Dict[str, Any]:
        raw = raw.strip(b"\x00 \t\r\n")
        if not raw:
            raise json.JSONDecodeError("Empty JSON input", "", 0)

        decode_errors: List[Exception] = []
        for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError as e:
                decode_errors.append(e)
                continue

            text = text.strip()
            if not text:
                continue

            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                decode_errors.append(e)
                continue

        raise json.JSONDecodeError(
            "Unable to decode input as JSON (tried utf-8/utf-16 variants)",
            raw[:200].decode("utf-8", errors="replace"),
            0,
        )

    def _load_json_from_file(path: str) -> Dict[str, Any]:
        with open(path, "rb") as f:
            return _load_json_from_bytes(f.read())

    def _load_json_from_stdin() -> Dict[str, Any]:
        if hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read()
        else:
            raw = sys.stdin.read().encode("utf-8", errors="replace")
        return _load_json_from_bytes(raw)

    try:
        if len(sys.argv) > 1:
            response_arg = sys.argv[1]
            if response_arg == "-":
                response = _load_json_from_stdin()
            else:
                response = _load_json_from_file(response_arg)
            test_results = run_all_tests(response)
            print_test_results(test_results)
        else:
            if sys.stdin is not None and not sys.stdin.isatty():
                response = _load_json_from_stdin()
                test_results = run_all_tests(response)
                print_test_results(test_results)
            else:
                print("[FAIL]")
                _print_usage()
                sys.exit(1)
    except FileNotFoundError as e:
        print("[FAIL]")
        print(f"File not found: {e.filename}")
        _print_usage()
        sys.exit(1)
    except json.JSONDecodeError as e:
        print("[FAIL]")
        print(f"Invalid JSON: {e}")
        _print_usage()
        sys.exit(1)
