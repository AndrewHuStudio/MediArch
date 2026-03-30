"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
知识图谱构建器（CLI 版本）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
功能说明:
   从 MongoDB chunks 读取文本，使用 DeepSeek V3 抽取实体和关系，
   写入 Neo4j 图数据库

涉及的主要文件:
   - backend/databases/graph/builders/kg_builder.py (核心构建器)
   - backend/databases/graph/schemas/medical_architecture.json (图谱 Schema)

使用方法:
   # 基础用法
   python -m backend.cli.build_kg

   # 先在环境变量中配置 Schema
   set KG_SCHEMA_PATH=backend/databases/graph/schemas/medical_architecture.json
   python -m backend.cli.build_kg

   # 跳过磁盘检查
   python -m backend.cli.build_kg --skip-disk-check

注意:
   当前版本默认只构建 Neo4j 图谱，不包含 Milvus 向量库
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("PYTHONPATH", str(PROJECT_ROOT))

def main() -> None:
    from backend.databases.graph.build_kg_with_deepseek import main as build_kg_main

    build_kg_main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n[FAIL] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
