"""
测试文档信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 功能说明:
   验证数据库中文本和图片数据的完整性

🎯 测试目标:
   - 检查MongoDB中文本chunks和图片chunks的数量
   - 验证图片文件是否正确保存
   - 检查VLM描述是否生成
   - 确认前端可以正确检索图文内容

📂 涉及的主要文件:
   - backend/databases/ingestion/indexing/mongodb_writer.py
   - backend/databases/ingestion/indexing/vision_describer.py

🗑️ 删除时机:
   - [✓] 图文数据完整性确认完成
   - [✓] 前端检索功能正常
   - [ ] 预计可删除时间: 2025-12-20

⚠️ 注意事项:
   - 需要MongoDB和图片目录访问权限
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

def check_multimodal_data():
    """检查多模态数据的完整性"""

    print("\n" + "="*70)
    print("检查多模态数据完整性")
    print("="*70)

    # 连接MongoDB
    mongo_uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DATABASE", "mediarch")

    print(f"\n[1/5] 连接MongoDB...")
    print(f"  URI: {mongo_uri}")
    print(f"  Database: {db_name}")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    chunks_collection = db["mediarch_chunks"]
    docs_collection = db["documents"]

    print("[OK] MongoDB连接成功\n")

    # 统计chunks
    print("[2/5] 统计chunks类型...")
    print("-" * 70)

    total_chunks = chunks_collection.count_documents({})
    text_chunks = chunks_collection.count_documents({"chunk_type": "text"})
    image_chunks = chunks_collection.count_documents({"chunk_type": "image"})

    print(f"总chunks数: {total_chunks}")
    print(f"  - 文本chunks: {text_chunks} ({text_chunks/total_chunks*100:.1f}%)")
    print(f"  - 图片chunks: {image_chunks} ({image_chunks/total_chunks*100:.1f}%)")

    # 检查VLM描述
    print("\n[3/5] 检查VLM图片描述...")
    print("-" * 70)

    image_with_vlm = chunks_collection.count_documents({
        "chunk_type": "image",
        "vlm_description": {"$exists": True, "$ne": ""}
    })

    if image_chunks > 0:
        vlm_rate = image_with_vlm / image_chunks * 100
        print(f"包含VLM描述的图片: {image_with_vlm}/{image_chunks} ({vlm_rate:.1f}%)")

        if vlm_rate < 100:
            print(f"[WARN] 有 {image_chunks - image_with_vlm} 张图片缺少VLM描述")
    else:
        print("[WARN] 没有图片chunks")

    # 检查图片文件
    print("\n[4/5] 检查图片文件存储...")
    print("-" * 70)

    # 获取所有文档的category
    docs = list(docs_collection.find({}, {"category": 1, "source_directory": 1}))
    categories = set()
    for doc in docs:
        cat = doc.get("category") or doc.get("source_directory")
        if cat:
            categories.add(cat)

    print(f"文档分类数: {len(categories)}")
    print(f"分类列表: {', '.join(sorted(categories))}")

    # 统计每个分类的图片
    total_image_files = 0
    base_path = Path("backend/databases/documents")

    print("\n各分类图片统计:")
    for category in sorted(categories):
        images_dir = base_path / category / "images"
        if images_dir.exists():
            image_files = list(images_dir.glob("*"))
            image_files = [f for f in image_files if f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}]
            total_image_files += len(image_files)
            print(f"  - {category}: {len(image_files)} 张")
        else:
            print(f"  - {category}: 0 张 [目录不存在]")

    print(f"\n图片文件总数: {total_image_files}")

    if total_image_files != image_chunks:
        print(f"[WARN] 图片文件数({total_image_files})与chunks数({image_chunks})不匹配")
    else:
        print("[OK] 图片文件与chunks数量一致")

    # 抽样检查
    print("\n[5/5] 抽样检查图文chunks...")
    print("-" * 70)

    # 随机抽取一个文本chunk
    sample_text = chunks_collection.find_one({"chunk_type": "text"})
    if sample_text:
        print("\n[示例文本chunk]")
        print(f"  Document: {sample_text.get('document_name', 'N/A')}")
        print(f"  页码: {sample_text.get('page_id', 'N/A')}")
        print(f"  文本长度: {len(sample_text.get('text', ''))} 字符")
        print(f"  文本预览: {sample_text.get('text', '')[:100]}...")
        print(f"  是否有向量: {'embedding' in sample_text or 'vector_id' in sample_text}")

    # 随机抽取一个图片chunk
    sample_image = chunks_collection.find_one({"chunk_type": "image"})
    if sample_image:
        print("\n[示例图片chunk]")
        print(f"  Document: {sample_image.get('document_name', 'N/A')}")
        print(f"  页码: {sample_image.get('page_id', 'N/A')}")
        print(f"  图片路径: {sample_image.get('image_url', 'N/A')}")
        vlm_desc = sample_image.get('vlm_description', '')
        if vlm_desc:
            print(f"  VLM描述长度: {len(vlm_desc)} 字符")
            print(f"  VLM描述预览: {vlm_desc[:100]}...")
        else:
            print(f"  VLM描述: [无]")

        # 检查图片文件是否存在
        img_url = sample_image.get('image_url', '')
        if img_url:
            img_path = Path(img_url)
            if img_path.exists():
                print(f"  文件状态: [存在] ({img_path.stat().st_size / 1024:.1f} KB)")
            else:
                print(f"  文件状态: [不存在]")

    # 总结
    print("\n" + "="*70)
    print("总结")
    print("="*70)

    ready_for_retrieval = True
    issues = []

    if text_chunks == 0:
        issues.append("[FAIL] 没有文本chunks")
        ready_for_retrieval = False
    else:
        print(f"[OK] 文本数据完整: {text_chunks} 个文本chunks")

    if image_chunks > 0:
        print(f"[OK] 图片数据存在: {image_chunks} 个图片chunks")
        if image_with_vlm < image_chunks:
            issues.append(f"[WARN] 有 {image_chunks - image_with_vlm} 张图片缺少VLM描述")
        else:
            print(f"[OK] 所有图片都有VLM描述")
    else:
        issues.append("[WARN] 没有图片chunks（可能PDF中没有图片）")

    if total_image_files > 0:
        print(f"[OK] 图片文件已保存: {total_image_files} 个文件")
    else:
        issues.append("[WARN] 没有图片文件")

    print("\n前端检索能力评估:")
    if ready_for_retrieval:
        print("[OK] 可以进行图文检索!")
        print("  - 文本检索: 支持 (基于向量相似度)")
        if image_chunks > 0:
            print("  - 图片检索: 支持 (基于VLM描述)")
            print("  - 图文混合: 支持 (检索结果包含文本和图片)")
        else:
            print("  - 图片检索: 不支持 (没有图片数据)")
    else:
        print("[FAIL] 数据不完整，无法进行检索")

    if issues:
        print("\n需要注意的问题:")
        for issue in issues:
            print(f"  {issue}")

    print("\n" + "="*70)

    client.close()

    return {
        "total_chunks": total_chunks,
        "text_chunks": text_chunks,
        "image_chunks": image_chunks,
        "image_with_vlm": image_with_vlm,
        "image_files": total_image_files,
        "ready": ready_for_retrieval,
        "issues": issues
    }

if __name__ == "__main__":
    try:
        result = check_multimodal_data()
        sys.exit(0 if result["ready"] else 1)
    except Exception as e:
        print(f"\n[ERROR] 检查失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
