"""
Milvus Collection 初始化脚本

用于创建存储实体属性详细信息的向量集合
"""

import os
from dotenv import load_dotenv

load_dotenv()

def check_entity_attributes_collection(collection_name: str = "entity_attributes"):
    """非破坏性自检：连接 Milvus，列出集合与字段、索引、向量维度、实体数。

    仅读取，不会 drop 或修改任何结构；用于 Docker 环境快速核对。
    """
    try:
        from pymilvus import connections, utility, Collection

        # 连接 Milvus（读取环境变量或使用默认端口映射）
        host = os.getenv("MILVUS_HOST", "localhost")
        port = os.getenv("MILVUS_PORT", "19530")
        connections.connect(alias="default", host=host, port=port)
        print(f"[OK] Connected to Milvus @ {host}:{port}")

        # 列出所有集合
        try:
            all_collections = utility.list_collections()
        except Exception:
            # 低版本兼容处理
            all_collections = []
        print("Collections:")
        if all_collections:
            for name in all_collections:
                print(" -", name)
        else:
            print(" (no collections or list unavailable)")

        # 检查目标集合是否存在
        if not utility.has_collection(collection_name):
            print(f"\n[WARN] Collection '{collection_name}' not found.")
            print("提示：首次部署需先创建集合，或确认环境变量 MILVUS_HOST/MILVUS_PORT 是否指向 Docker 暴露端口。")
            return None

        # 加载并打印结构信息（安全）
        c = Collection(collection_name)
        c.load()
        print(f"\n[OK] Inspecting collection: {collection_name}")

        # 字段信息
        print("\nFields:")
        for f in c.schema.fields:
            params = getattr(f, "params", {}) or {}
            print(f" - {f.name} | dtype={getattr(f.dtype, 'name', f.dtype)} | params={params}")

        # 向量维度
        vec_field = next((f for f in c.schema.fields if f.name == "vector"), None)
        vec_dim = (getattr(vec_field, "params", {}) or {}).get("dim") if vec_field else None
        print(f"\nVector field: {'vector' if vec_field else '(missing)'} | dim={vec_dim}")

        # 索引信息
        try:
            idx_info = [getattr(idx, "to_dict", lambda: str(idx))() for idx in (c.indexes or [])]
        except Exception:
            idx_info = []
        print("\nIndexes:")
        if idx_info:
            for i, idx in enumerate(idx_info, 1):
                print(f" [{i}] {idx}")
        else:
            print(" (no indexes)")

        # 实体数量
        try:
            print(f"\nEntities (rows): {c.num_entities}")
        except Exception:
            pass

        print("\n✅ Milvus collection check complete (non-destructive).")
        return c

    except ImportError:
        print("[ERROR] pymilvus not installed. Please install: pip install pymilvus")
        return None
    except Exception as e:
        print(f"[ERROR] Milvus check failed: {e}")
        return None
    finally:
        try:
            from pymilvus import connections
            connections.disconnect("default")
        except Exception:
            pass

def setup_entity_attributes_collection(
    collection_name: str = "entity_attributes",
    drop_existing: bool = False,
    vector_dim: int = None,
    load_immediately: bool = False,
):
    
    try:
        from pymilvus import (
            connections,
            Collection,
            CollectionSchema,
            FieldSchema,
            DataType,
            utility
        )
        
        # 连接Milvus
        connections.connect(
            alias="default",
            host=os.getenv("MILVUS_HOST", "localhost"),
            port=os.getenv("MILVUS_PORT", "19530")
        )
        print("[OK] Connected to Milvus")
        
        # 维度配置
       
        vector_dim = int(os.getenv("EMBEDDING_DIM"))
            

        # 已存在：默认不删除，直接返回
        if utility.has_collection(collection_name):
            if not drop_existing:
                print(f"[INFO] Collection '{collection_name}' already exists. Skipping creation.")
                c = Collection(collection_name)
                try:
                    c.load()
                except Exception:
                    pass
                return c
            else:
                print(f"[WARN] Dropping existing collection '{collection_name}' ...")
                utility.drop_collection(collection_name)

        # 定义字段
        fields = [
            # 主键
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
            
            # 关联信息
            FieldSchema(name="entity_name", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="entity_id", dtype=DataType.VARCHAR, max_length=64),
            
            # 属性内容
            FieldSchema(name="attribute_text", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="attribute_type", dtype=DataType.VARCHAR, max_length=32),
            
            # 富媒体标记
            FieldSchema(name="has_image", dtype=DataType.BOOL),
            FieldSchema(name="has_table", dtype=DataType.BOOL),
            FieldSchema(name="image_refs", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="table_refs", dtype=DataType.VARCHAR, max_length=1024),
            
            # 向量字段
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=vector_dim),
            
            # 溯源信息
            FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="source_document", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="page_number", dtype=DataType.INT64),
        ]
        
        # 创建Schema
        schema = CollectionSchema(
            fields=fields,
            description="实体属性详细信息存储（富媒体+向量）"
        )
        
        # 创建Collection
        collection = Collection(
            name=collection_name,
            schema=schema,
            using='default'
        )
        print(f"[OK] Created collection: {collection_name}")
        
        # 创建向量索引
        index_params = {
            "metric_type": "COSINE",  # 余弦相似度
            "index_type": "IVF_FLAT",  # 倒排文件索引
            "params": {"nlist": 128}
        }
        
        collection.create_index(
            field_name="vector",
            index_params=index_params
        )
        print(f"[OK] Created vector index (COSINE similarity)")
        
        # 创建标量索引（加速过滤）
        collection.create_index(
            field_name="entity_name",
            index_name="entity_name_idx"
        )
        collection.create_index(
            field_name="chunk_id",
            index_name="chunk_id_idx"
        )
        print(f"[OK] Created scalar indexes")
        
        # 可选：加载 Collection 到内存（默认跳过，避免在某些环境卡住）
        if load_immediately:
            try:
                collection.load()
                print(f"[OK] Collection loaded into memory")
            except Exception as e:
                print(f"[WARN] Load skipped due to error: {e}")
        
        # 显示Collection信息
        print(f"\n{'='*60}")
        print("Collection Information:")
        print(f"  Name: {collection.name}")
        print(f"  Schema: {len(fields)} fields")
        print(f"  Vector dim: {vector_dim}")
        print(f"  Metric: COSINE")
        print(f"  Index: IVF_FLAT")
        print(f"{'='*60}\n")
        
        print("✅ Milvus collection setup complete! (load_immediately=", load_immediately, ")", sep="")
        
        return collection
        
    except ImportError:
        print("[ERROR] pymilvus not installed. Please install:")
        print("  pip install pymilvus")
        return None
    except Exception as e:
        print(f"[ERROR] Failed to setup Milvus collection: {e}")
        return None
    finally:
        connections.disconnect("default")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Milvus entity_attributes 工具")
    sub = parser.add_subparsers(dest="cmd", required=False)

    # 非破坏性自检（默认）
    p_check = sub.add_parser("check", help="非破坏性检查集合与索引（默认）")
    p_check.add_argument("--name", default="entity_attributes", help="集合名，默认 entity_attributes")

    # 创建集合（默认安全：已存在不删除）；可用 --drop-existing 强制重建；--dim 指定维度
    p_setup = sub.add_parser("setup", help="创建集合（默认不删除已存在）")
    p_setup.add_argument("--name", default="entity_attributes", help="集合名，默认 entity_attributes")
    p_setup.add_argument("--drop-existing", action="store_true", help="如已存在则删除重建（危险）")
    p_setup.add_argument("--dim", type=int, help="向量维度")
    p_setup.add_argument("--load", action="store_true", help="创建完成后立即加载集合到内存（默认否）")

    args = parser.parse_args()

    if args.cmd == "setup":
        print("="*60)
        print("Milvus Collection Setup")
        print("="*60)
        setup_entity_attributes_collection(
            collection_name=getattr(args, "name", "entity_attributes"),
            drop_existing=bool(getattr(args, "drop_existing", False)),
            vector_dim=getattr(args, "dim", None),
            load_immediately=bool(getattr(args, "load", False)),
        )
    else:
        # 默认执行 check
        print("="*60)
        print("Milvus Collection Check")
        print("="*60)
        check_entity_attributes_collection(getattr(args, "name", "entity_attributes"))

