"""
检查图片chunks的content字段（可能包含VLM描述）
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

def check_image_chunks():
    """检查图片chunks的详细内容"""

    mongo_uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DATABASE", "mediarch")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    chunks_collection = db["mediarch_chunks"]

    print("="*70)
    print("Image Chunks Detail Check")
    print("="*70)

    # 找一个有image_url的chunk
    image_chunk = chunks_collection.find_one({"image_url": {"$exists": True}})

    if image_chunk:
        print("\n[Sample Image Chunk - ALL Fields]")
        print("-" * 70)

        for key, value in sorted(image_chunk.items()):
            if key == '_id':
                print(f"  {key}: {str(value)}")
            elif key == 'content':
                content_str = str(value)
                print(f"  {key}: {content_str[:200]}...")
                print(f"         Length: {len(content_str)} chars")
            else:
                print(f"  {key}: {value}")

        # 检查图片文件
        img_url = image_chunk.get('image_url', '')
        print("\n[Image File Check]")
        print("-" * 70)
        if img_url:
            # 尝试几种可能的路径
            possible_paths = [
                Path(img_url),
                Path("backend/databases/documents") / image_chunk.get('source_category', '') / img_url,
                Path("backend/databases/documents") / image_chunk.get('source_directory', '') / img_url,
            ]

            found = False
            for p in possible_paths:
                if p.exists():
                    print(f"  Path: {p}")
                    print(f"  Size: {p.stat().st_size / 1024:.1f} KB")
                    print(f"  [OK] File exists")
                    found = True
                    break

            if not found:
                print(f"  [WARN] Image file not found")
                print(f"  Tried paths:")
                for p in possible_paths:
                    print(f"    - {p}")

    # 统计
    print("\n[Statistics]")
    print("-" * 70)

    total_image_chunks = chunks_collection.count_documents({"image_url": {"$exists": True}})
    images_with_long_content = chunks_collection.count_documents({
        "image_url": {"$exists": True},
        "$expr": {"$gt": [{"$strLenCP": {"$ifNull": ["$content", ""]}}, 50]}
    })

    print(f"Total image chunks: {total_image_chunks}")
    print(f"Images with content > 50 chars: {images_with_long_content}")
    if total_image_chunks > 0:
        print(f"Percentage with VLM: {images_with_long_content/total_image_chunks*100:.1f}%")

    # 文本chunks统计
    print("\n[Text Chunks]")
    print("-" * 70)
    text_chunks = chunks_collection.count_documents({
        "content_type": "text",
        "content": {"$exists": True, "$ne": ""}
    })
    print(f"Text chunks: {text_chunks}")

    # 实际可用于检索的chunks
    print("\n[Retrieval Ready Chunks]")
    print("-" * 70)
    print(f"Text chunks (content_type=text): {text_chunks}")
    print(f"Image chunks (has image_url): {total_image_chunks}")
    print(f"  - With VLM (content > 50 chars): {images_with_long_content}")
    print(f"  - Without VLM: {total_image_chunks - images_with_long_content}")

    print("\n" + "="*70)

    client.close()

if __name__ == "__main__":
    try:
        check_image_chunks()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
