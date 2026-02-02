#!/usr/bin/env python3
"""清除无效的KG缓存记录"""

import os
import sys
from pathlib import Path
from pymongo import MongoClient
from dotenv import load_dotenv

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()


def main():
    print("[INFO] 连接MongoDB...")
    client = MongoClient(os.getenv("MONGODB_URI"))
    db = client[os.getenv("MONGODB_DATABASE", "mediarch")]
    collection = db.kg_extractions

    # 获取所有版本
    versions = collection.distinct("version")
    print(f"[INFO] 发现 {len(versions)} 个缓存版本")

    for version in versions:
        print(f"\n[INFO] 处理版本: {version}")

        # 统计总数
        total = collection.count_documents({"version": version})
        print(f"  总缓存数: {total}")

        # 查找并删除空结果的缓存
        deleted_count = 0

        # 遍历所有缓存记录
        for doc in collection.find({"version": version}):
            chunk_id = doc.get("chunk_id")
            result = doc.get("result", {})
            entities = result.get("entities", {})

            # 如果entities为空，删除
            if not entities:
                collection.delete_one({"_id": doc["_id"]})
                deleted_count += 1
                print(f"  [DELETED] chunk_id={chunk_id} (空entities)")

        print(f"  [OK] 已删除 {deleted_count} 条无效缓存")
        print(f"  剩余缓存: {collection.count_documents({'version': version})}")

    print("\n[OK] 清理完成！")


if __name__ == "__main__":
    main()
