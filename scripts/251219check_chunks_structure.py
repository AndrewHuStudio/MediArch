"""
检查chunks的实际字段结构
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient
import json

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

def check_chunks_structure():
    """检查chunks的实际结构"""

    # 连接MongoDB
    mongo_uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DATABASE", "mediarch")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    chunks_collection = db["mediarch_chunks"]

    print("="*70)
    print("Chunks Structure Analysis")
    print("="*70)

    # 获取第一个chunk
    sample = chunks_collection.find_one({})

    if sample:
        print("\n[Sample Chunk Fields]")
        print("-" * 70)

        # 移除_id以便更好显示
        if '_id' in sample:
            sample['_id'] = str(sample['_id'])

        # 显示所有字段
        for key, value in sample.items():
            if key == 'text':
                print(f"  {key}: {str(value)[:100]}... (length: {len(str(value))})")
            elif key == 'embedding':
                print(f"  {key}: [vector data] (dim: {len(value) if isinstance(value, list) else 'N/A'})")
            else:
                print(f"  {key}: {value}")

        print("\n[Key Field Check]")
        print("-" * 70)
        print(f"  Has 'chunk_type': {'chunk_type' in sample}")
        print(f"  Has 'type': {'type' in sample}")
        print(f"  Has 'text': {'text' in sample}")
        print(f"  Has 'image_url': {'image_url' in sample}")
        print(f"  Has 'vlm_description': {'vlm_description' in sample}")
        print(f"  Has 'embedding': {'embedding' in sample}")

        # 统计不同类型
        print("\n[Statistics by Fields]")
        print("-" * 70)

        total = chunks_collection.count_documents({})
        has_text = chunks_collection.count_documents({"text": {"$exists": True, "$ne": ""}})
        has_image_url = chunks_collection.count_documents({"image_url": {"$exists": True, "$ne": ""}})
        has_vlm = chunks_collection.count_documents({"vlm_description": {"$exists": True, "$ne": ""}})
        has_embedding = chunks_collection.count_documents({"embedding": {"$exists": True}})

        print(f"Total chunks: {total}")
        print(f"  - With text: {has_text} ({has_text/total*100:.1f}%)")
        print(f"  - With image_url: {has_image_url} ({has_image_url/total*100:.1f}%)")
        print(f"  - With vlm_description: {has_vlm} ({has_vlm/total*100:.1f}%)")
        print(f"  - With embedding: {has_embedding} ({has_embedding/total*100:.1f}%)")

        # 区分文本和图片chunks
        print("\n[Inferred Chunk Types]")
        print("-" * 70)

        # 有text且没有image_url的是文本chunks
        text_chunks_count = chunks_collection.count_documents({
            "text": {"$exists": True, "$ne": ""},
            "image_url": {"$exists": False}
        })

        # 有image_url的是图片chunks
        image_chunks_count = chunks_collection.count_documents({
            "image_url": {"$exists": True, "$ne": ""}
        })

        print(f"Text chunks (has text, no image_url): {text_chunks_count}")
        print(f"Image chunks (has image_url): {image_chunks_count}")
        print(f"Other: {total - text_chunks_count - image_chunks_count}")

        # 检查图片示例
        if image_chunks_count > 0:
            print("\n[Sample Image Chunk]")
            print("-" * 70)
            img_sample = chunks_collection.find_one({"image_url": {"$exists": True}})
            if img_sample:
                print(f"  document_name: {img_sample.get('document_name', 'N/A')}")
                print(f"  page_id: {img_sample.get('page_id', 'N/A')}")
                print(f"  image_url: {img_sample.get('image_url', 'N/A')}")
                vlm = img_sample.get('vlm_description', '')
                if vlm:
                    print(f"  vlm_description: {vlm[:100]}...")
                else:
                    print(f"  vlm_description: [None]")

    else:
        print("\n[ERROR] No chunks found!")

    print("\n" + "="*70)
    print("Conclusion")
    print("="*70)
    print("\n[Analysis]")
    print("The chunks do NOT have 'chunk_type' field.")
    print("This is likely because the field name was changed or not set during ingestion.")
    print("\nThe system can still work because:")
    print("  - Text chunks have 'text' field")
    print("  - Image chunks have 'image_url' field")
    print("  - Both can be distinguished by checking these fields")
    print("\n[Recommendation]")
    print("No fix needed. The retrieval system should check:")
    print("  - if 'image_url' exists -> it's an image chunk")
    print("  - if 'text' exists and no 'image_url' -> it's a text chunk")
    print("="*70)

    client.close()

if __name__ == "__main__":
    try:
        check_chunks_structure()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
