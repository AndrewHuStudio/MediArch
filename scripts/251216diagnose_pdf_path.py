"""
诊断脚本：检查 PDF 路径问题
"""
import os
from pymongo import MongoClient
from pathlib import Path

# 连接 MongoDB
client = MongoClient('mongodb://admin:mediarch2024@localhost:27017/')
db = client['mediarch']

# 获取一个样本 chunk
chunk = db['mediarch_chunks'].find_one({'content_type': 'text'})

if chunk:
    print("="*80)
    print("当前 chunk 中的路径字段:")
    print("="*80)
    print(f"file_path: {chunk.get('file_path')}")
    print(f"source_document: {chunk.get('source_document')}")
    print(f"doc_category: {chunk.get('doc_category')}")
    print(f"doc_title: {chunk.get('doc_title')}")

    # 分析路径
    file_path = chunk.get('file_path')
    if file_path:
        print(f"\n路径类型: {'绝对路径' if Path(file_path).is_absolute() else '相对路径'}")

        # 尝试转换为相对路径
        if Path(file_path).is_absolute():
            # 提取相对于 documents 目录的路径
            documents_dir = r"E:\MyPrograms\250804-MediArch System\backend\databases\documents"
            try:
                relative_path = Path(file_path).relative_to(documents_dir)
                print(f"转换后的相对路径: {relative_path}")
                print(f"前端应该访问: /api/v1/documents/pdf?path={relative_path}")
            except ValueError:
                print(f"[ERROR] 无法转换为相对路径，file_path 不在 documents 目录下")

    print("\n" + "="*80)
    print("问题诊断:")
    print("="*80)

    if file_path and Path(file_path).is_absolute():
        print("[问题] file_path 存储的是绝对路径")
        print("[影响] 前端无法直接使用")
        print("[解决] 需要在返回给前端前转换为相对路径")
    else:
        print("[OK] file_path 格式正常")

# 检查几个 chunk
print("\n" + "="*80)
print("抽样检查 10 个 chunks:")
print("="*80)
for i, chunk in enumerate(db['mediarch_chunks'].find({'content_type': 'text'}).limit(10), 1):
    fp = chunk.get('file_path', 'N/A')
    if fp != 'N/A' and Path(fp).is_absolute():
        print(f"[{i}] 绝对路径: ...{fp[-50:]}")
    else:
        print(f"[{i}] {fp[:50] if fp != 'N/A' else 'N/A'}")
