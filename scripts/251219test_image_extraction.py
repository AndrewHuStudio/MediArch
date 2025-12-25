"""
测试图片提取功能
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
功能说明:
   测试修复后的 chunking.py 是否能正确从 Markdown 中提取图片

测试目标:
   - 验证图片 chunks 是否被创建
   - 验证 image_url 和 image_url_abs 是否正确
   - 统计图片数量
"""

import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BACKEND_ROOT = PROJECT_ROOT / "backend"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline
from dotenv import load_dotenv
import os

# 加载环境变量
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)

print("[OK] 开始测试图片提取功能")
print("=" * 80)

# 设置强制重新索引
os.environ["FORCE_REINGEST"] = "1"

# 初始化 Pipeline
pipeline = DocumentIngestionPipeline(engine="mineru")

# 选择一个测试文档（包含图片的 PDF）
test_pdf = "backend/databases/documents/标准规范/GB51039-2014综合医院建筑设计标准.pdf"
test_pdf_path = str(PROJECT_ROOT / test_pdf)

if not Path(test_pdf_path).exists():
    print(f"[FAIL] 测试文件不存在: {test_pdf_path}")
    sys.exit(1)

print(f"[OK] 测试文件: {Path(test_pdf_path).name}")
print()

try:
    # 处理文档
    result = pipeline.process_document(
        pdf_path=test_pdf_path,
        category="标准规范",
    )

    print("[OK] 处理完成")
    print("=" * 80)
    print(f"总 chunks 数: {result.get('total_chunks', 0)}")
    print(f"文本 chunks: {result.get('text_chunks', 0)}")
    print(f"图片 chunks: {result.get('image_chunks', 0)}")
    print(f"VLM 处理成功: {result.get('vlm_processed', 0)}")
    print(f"VLM 处理失败: {result.get('vlm_failed', 0)}")
    print()

    # 从 MongoDB 验证
    from pymongo import MongoClient
    client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://admin:mediarch2024@localhost:27017/'))
    db = client['mediarch']

    # 统计这个文档的图片 chunks
    doc_id = result.get('mongo_doc_id')
    if doc_id:
        from bson import ObjectId
        image_chunks = list(db.mediarch_chunks.find(
            {'doc_id': ObjectId(doc_id), 'content_type': 'image'}
        ).limit(3))

        print(f"[OK] MongoDB 中该文档的图片 chunks 数量: {len(image_chunks)}")

        if image_chunks:
            print("\n[OK] 图片 chunk 示例:")
            sample = image_chunks[0]
            print(f"  - chunk_id: {sample['chunk_id']}")
            print(f"  - content: {sample['content'][:100]}")
            print(f"  - image_url: {sample.get('image_url', 'N/A')}")
            print(f"  - image_url_abs 存在: {bool(sample.get('image_url_abs'))}")
            print(f"  - vlm_processed: {sample.get('metadata', {}).get('vlm_processed', 'N/A')}")
        else:
            print("[WARN] 未找到图片 chunks！")

    print()
    print("=" * 80)
    print("[OK] 测试完成")

except Exception as e:
    print(f"\n[FAIL] 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
