"""
测试脚本：验证 PDF 路径转换修复
"""
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from backend.app.utils.citation_builder import normalize_citations

# 模拟从 MongoDB 获取的 citations（包含绝对路径）
mock_citations = [
    {
        "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
        "location": "第12页",
        "snippet": "抢救室面积标准为 20㎡/间...",
        "file_path": r"E:\MyPrograms\250804-MediArch System\backend\databases\documents\标准规范\GB 51039-2014 综合医院建筑设计规范.pdf",
        "page_number": 12,
        "chunk_id": "xxx-yyy-zzz"
    },
    {
        "source": "医院建筑设计指南.pdf",
        "location": "第59页",
        "snippet": "手术室净高不应小于3.0m...",
        "file_path": r"E:\MyPrograms\250804-MediArch System\backend\databases\documents\书籍报告\医院建筑设计指南.pdf",
        "page_number": 59,
    }
]

print("="*80)
print("测试 PDF 路径转换")
print("="*80)

print("\n[转换前]")
for i, cite in enumerate(mock_citations, 1):
    print(f"{i}. file_path: {cite['file_path']}")

# 调用 normalize_citations
normalized = normalize_citations(mock_citations)

print("\n[转换后]")
for i, cite in enumerate(normalized, 1):
    print(f"{i}. file_path: {cite['file_path']}")

print("\n" + "="*80)
print("前端应该如何使用:")
print("="*80)
for i, cite in enumerate(normalized, 1):
    print(f"{i}. 访问 URL: /api/v1/documents/pdf?path={cite['file_path']}")

print("\n[OK] 测试完成！")
