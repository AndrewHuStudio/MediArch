# -*- coding: utf-8 -*-
"""检查数据库中的多模态数据（图片和表格）"""

import os
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

def check_multimodal_data():
    """检查MongoDB中的多模态数据"""
    print("\n" + "=" * 80)
    print("检查数据库中的多模态数据（图片和表格）")
    print("=" * 80 + "\n")

    # 连接MongoDB
    uri = os.getenv("MONGODB_URI")
    client = MongoClient(uri)
    db = client["mediarch"]
    chunks = db["mediarch_chunks"]

    # 统计数据
    total_count = chunks.count_documents({})
    image_count = chunks.count_documents({"content_type": "image"})
    table_count = chunks.count_documents({"content_type": "table"})
    text_count = chunks.count_documents({"content_type": "text"})

    print(f"[1] 总 chunk 数量: {total_count}")
    print(f"[2] 图片 chunk 数量: {image_count} ({image_count/total_count*100:.1f}%)")
    print(f"[3] 表格 chunk 数量: {table_count} ({table_count/total_count*100:.1f}%)")
    print(f"[4] 文本 chunk 数量: {text_count} ({text_count/total_count*100:.1f}%)")
    print()

    # 图片示例
    print("[5] 图片 chunk 示例:")
    sample_img = chunks.find_one({"content_type": "image"})
    if sample_img:
        print(f"  - chunk_id: {sample_img.get('chunk_id')}")
        print(f"  - image_url: {sample_img.get('image_url', '')[:80]}...")
        print(f"  - content (前100字): {sample_img.get('content', '')[:100]}")
        print(f"  - parent_chunk_id: {sample_img.get('parent_chunk_id')}")
        print(f"  - section: {sample_img.get('section')}")
        print(f"  - page: {sample_img.get('metadata', {}).get('page')}")
    else:
        print("  [WARN] 无图片 chunk")
    print()

    # 表格示例
    print("[6] 表格 chunk 示例:")
    sample_table = chunks.find_one({"content_type": "table"})
    if sample_table:
        print(f"  - chunk_id: {sample_table.get('chunk_id')}")
        print(f"  - content (前100字): {sample_table.get('content', '')[:100]}")
        has_html = "Yes" if sample_table.get('table_html') else "No"
        print(f"  - table_html: {has_html}")
        print(f"  - section: {sample_table.get('section')}")
        print(f"  - page: {sample_table.get('metadata', {}).get('page')}")
    else:
        print("  [WARN] 无表格 chunk")
    print()

    # 检查VLM描述
    if sample_img:
        content = sample_img.get('content', '')
        if '[图片:' in content or '[图片]' in content:
            print("[7] VLM 视觉描述检查:")
            if len(content) > 20:
                print(f"  [OK] 图片有 VLM 描述 ({len(content)} 字符)")
                print(f"  描述预览: {content[:200]}...")
            else:
                print(f"  [WARN] 图片描述过短，可能未启用 VLM")
                print(f"  内容: {content}")

    print("\n" + "=" * 80)
    print("检查完成！")
    print("=" * 80)


if __name__ == "__main__":
    check_multimodal_data()
