# backend/app/services/rebuild_service.py
# -*- coding: utf-8 -*-

"""
数据库全量重建服务

功能：
1. 清空 MongoDB (mediarch_documents, mediarch_chunks)
2. 清空 Milvus (mediarch_chunks collection)
3. 清空 Neo4j (仅删除非概念节点，保留预注入的空间知识)
4. 重新处理所有 PDF (OCR + 图片提取 + VLM 描述)
5. 重建向量索引 (Milvus)
6. 重建知识图谱 (Neo4j)
7. 验证数据完整性

作者: Claude Code
日期: 2025-12-19
"""

import os
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime, timezone

from pymongo import MongoClient
from pymilvus import connections, utility, Collection
from neo4j import GraphDatabase
from dotenv import load_dotenv

# 导入现有模块
from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline

logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


class DatabaseRebuildService:
    """数据库全量重建服务"""

    def __init__(self):
        """初始化服务"""
        load_dotenv()

        # MongoDB 配置
        self.mongo_uri = os.getenv("MONGODB_URI")
        self.mongo_db = os.getenv("MONGODB_DATABASE", "mediarch")

        # Milvus 配置
        self.milvus_host = os.getenv("MILVUS_HOST", "localhost")
        self.milvus_port = os.getenv("MILVUS_PORT", "19530")

        # Neo4j 配置
        self.neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        self.neo4j_password = os.getenv("NEO4J_PASSWORD", "mediarch2024")
        self.neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")

        # 文档目录
        project_root = Path(__file__).resolve().parents[3]
        self.documents_dir = project_root / "backend" / "databases" / "documents"

        # 文档分类
        self.categories = {
            "标准规范": "标准规范",
            "参考论文": "参考论文",
            "书籍报告": "书籍报告",
            "政策文件": "政策文件",
        }

        # 统计信息
        self.stats = {
            "mongodb_docs_deleted": 0,
            "mongodb_chunks_deleted": 0,
            "milvus_vectors_deleted": 0,
            "neo4j_nodes_deleted": 0,
            "neo4j_rels_deleted": 0,
            "pdf_total": 0,
            "pdf_success": 0,
            "pdf_failed": 0,
            "failed_files": []
        }

    def get_mongodb_stats(self) -> Dict[str, int]:
        """获取 MongoDB 统计信息"""
        try:
            client = MongoClient(self.mongo_uri)
            db = client[self.mongo_db]

            stats = {
                "documents": db['documents'].count_documents({}),
                "chunks": db['mediarch_chunks'].count_documents({})
            }

            client.close()
            return stats
        except Exception as e:
            logger.error(f"获取 MongoDB 统计失败: {e}")
            return {"documents": 0, "chunks": 0}

    def get_milvus_stats(self) -> Dict[str, int]:
        """获取 Milvus 统计信息"""
        try:
            connections.connect("default", host=self.milvus_host, port=self.milvus_port)

            stats = {"vectors": 0}
            if utility.has_collection("mediarch_chunks"):
                collection = Collection("mediarch_chunks")
                collection.load()
                stats["vectors"] = collection.num_entities
                collection.release()

            connections.disconnect("default")
            return stats
        except Exception as e:
            logger.error(f"获取 Milvus 统计失败: {e}")
            return {"vectors": 0}

    def get_neo4j_stats(self) -> Dict[str, int]:
        """获取 Neo4j 统计信息"""
        try:
            driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password)
            )

            stats = {"nodes": 0, "relationships": 0, "concept_nodes": 0}

            with driver.session(database=self.neo4j_database) as session:
                # 总节点数
                result = session.run("MATCH (n) RETURN count(n) as count")
                stats["nodes"] = result.single()["count"]

                # 概念节点数（预注入的空间知识）
                result = session.run("MATCH (n) WHERE n.is_concept = true RETURN count(n) as count")
                stats["concept_nodes"] = result.single()["count"]

                # 关系数
                result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
                stats["relationships"] = result.single()["count"]

            driver.close()
            return stats
        except Exception as e:
            logger.error(f"获取 Neo4j 统计失败: {e}")
            return {"nodes": 0, "relationships": 0, "concept_nodes": 0}

    def clear_mongodb(self) -> bool:
        """清空 MongoDB 数据"""
        logger.info("[MongoDB] 开始清空数据...")

        try:
            client = MongoClient(self.mongo_uri)
            db = client[self.mongo_db]

            # 统计现有数据
            doc_count = db['documents'].count_documents({})
            chunk_count = db['mediarch_chunks'].count_documents({})

            logger.info(f"[MongoDB] 当前数据: documents={doc_count}, chunks={chunk_count}")

            if doc_count == 0 and chunk_count == 0:
                logger.info("[MongoDB] 数据已经是空的，跳过清空")
                client.close()
                return True

            # 删除所有文档
            logger.info("[MongoDB] 删除 documents...")
            result = db['documents'].delete_many({})
            self.stats["mongodb_docs_deleted"] = result.deleted_count
            logger.info(f"[MongoDB] 已删除 {result.deleted_count} 个 documents")

            # 删除所有 chunks
            logger.info("[MongoDB] 删除 mediarch_chunks...")
            result = db['mediarch_chunks'].delete_many({})
            self.stats["mongodb_chunks_deleted"] = result.deleted_count
            logger.info(f"[MongoDB] 已删除 {result.deleted_count} 个 chunks")

            client.close()
            logger.info("[MongoDB] 清空完成")
            return True

        except Exception as e:
            logger.error(f"[MongoDB] 清空失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def clear_milvus(self) -> bool:
        """清空 Milvus 向量数据"""
        logger.info("[Milvus] 开始清空向量数据...")

        try:
            connections.connect("default", host=self.milvus_host, port=self.milvus_port)

            # 统计现有向量
            if utility.has_collection("mediarch_chunks"):
                collection = Collection("mediarch_chunks")
                collection.load()
                vector_count = collection.num_entities
                logger.info(f"[Milvus] 当前向量数: {vector_count}")
                collection.release()

                if vector_count == 0:
                    logger.info("[Milvus] 向量库已经是空的，跳过清空")
                    connections.disconnect("default")
                    return True

                # 删除 collection
                logger.info("[Milvus] 删除 collection: mediarch_chunks")
                collection.drop()
                self.stats["milvus_vectors_deleted"] = vector_count
                logger.info(f"[Milvus] 已删除 {vector_count} 个向量")
            else:
                logger.info("[Milvus] Collection 不存在，跳过清空")

            connections.disconnect("default")
            logger.info("[Milvus] 清空完成")
            return True

        except Exception as e:
            logger.error(f"[Milvus] 清空失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def clear_neo4j(self, preserve_concepts: bool = True) -> bool:
        """
        清空 Neo4j 知识图谱

        Args:
            preserve_concepts: 是否保留概念节点（预注入的空间知识）
        """
        logger.info("[Neo4j] 开始清空知识图谱...")

        try:
            driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password)
            )

            with driver.session(database=self.neo4j_database) as session:
                # 统计现有数据
                result = session.run("MATCH (n) RETURN count(n) as count")
                node_count = result.single()["count"]

                result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
                rel_count = result.single()["count"]

                if preserve_concepts:
                    result = session.run("MATCH (n) WHERE n.is_concept = true RETURN count(n) as count")
                    concept_count = result.single()["count"]
                    logger.info(f"[Neo4j] 当前数据: nodes={node_count}, relationships={rel_count}, concept_nodes={concept_count}")
                else:
                    logger.info(f"[Neo4j] 当前数据: nodes={node_count}, relationships={rel_count}")

                if node_count == 0:
                    logger.info("[Neo4j] 图谱已经是空的，跳过清空")
                    driver.close()
                    return True

                # 删除节点和关系
                if preserve_concepts:
                    # 只删除非概念节点
                    logger.info("[Neo4j] 删除非概念节点和相关关系...")
                    result = session.run("""
                        MATCH (n)
                        WHERE n.is_concept IS NULL OR n.is_concept = false
                        DETACH DELETE n
                        RETURN count(n) as deleted
                    """)
                    deleted = result.single()["deleted"]
                    self.stats["neo4j_nodes_deleted"] = deleted
                    logger.info(f"[Neo4j] 已删除 {deleted} 个非概念节点及其关系")
                    logger.info(f"[Neo4j] 保留了 {concept_count} 个概念节点")
                else:
                    # 删除所有节点
                    logger.info("[Neo4j] 删除所有节点和关系...")
                    result = session.run("""
                        MATCH (n)
                        DETACH DELETE n
                        RETURN count(n) as deleted
                    """)
                    deleted = result.single()["deleted"]
                    self.stats["neo4j_nodes_deleted"] = deleted
                    logger.info(f"[Neo4j] 已删除 {deleted} 个节点及其关系")

            driver.close()
            logger.info("[Neo4j] 清空完成")
            return True

        except Exception as e:
            logger.error(f"[Neo4j] 清空失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def reindex_documents(self) -> bool:
        """重新处理和索引所有文档"""
        logger.info("[索引] 开始重新处理文档...")

        try:
            # 初始化 pipeline
            pipeline = DocumentIngestionPipeline(engine="mineru")

            # 设置强制重新索引
            os.environ["FORCE_REINGEST"] = "1"

            # 统计
            total_files = 0
            success_files = 0
            failed_files = []

            # 遍历所有分类
            for category_name, category_dir in self.categories.items():
                category_path = self.documents_dir / category_dir

                if not category_path.exists():
                    logger.warning(f"[索引] 目录不存在: {category_path}")
                    continue

                logger.info(f"[索引] 处理分类: {category_name}")
                logger.info(f"[索引] 目录: {category_path}")

                # 查找所有 PDF
                pdf_files = list(category_path.glob("*.pdf"))
                logger.info(f"[索引] 找到 {len(pdf_files)} 个 PDF 文件")

                # 处理每个 PDF
                for pdf_file in pdf_files:
                    total_files += 1
                    logger.info(f"\n[{total_files}] 处理: {pdf_file.name}")

                    try:
                        result = pipeline.process_document(
                            pdf_path=str(pdf_file),
                            category=category_name
                        )

                        if result.get("status") == "success":
                            success_files += 1
                            logger.info(f"[OK] 成功: {pdf_file.name}")
                            logger.info(f"    - MongoDB Doc ID: {result.get('mongo_doc_id')}")
                            logger.info(f"    - Chunks: {result.get('total_chunks', 0)}")
                        else:
                            logger.warning(f"[WARN] 状态: {result.get('status')}")
                            logger.warning(f"    - 原因: {result.get('reason', 'unknown')}")
                            failed_files.append((pdf_file.name, result.get('reason', 'unknown')))

                    except Exception as e:
                        logger.error(f"[FAIL] 失败: {pdf_file.name}")
                        logger.error(f"    - 错误: {e}")
                        failed_files.append((pdf_file.name, str(e)))

            # 更新统计
            self.stats["pdf_total"] = total_files
            self.stats["pdf_success"] = success_files
            self.stats["pdf_failed"] = len(failed_files)
            self.stats["failed_files"] = failed_files

            # 打印总结
            logger.info("\n" + "="*80)
            logger.info("索引完成总结")
            logger.info("="*80)
            logger.info(f"总文件数: {total_files}")
            logger.info(f"成功: {success_files}")
            logger.info(f"失败: {len(failed_files)}")

            if failed_files:
                logger.warning("\n失败的文件:")
                for filename, error in failed_files:
                    logger.warning(f"  - {filename}: {error}")

            logger.info("[索引] 重新索引完成")
            return True

        except Exception as e:
            logger.error(f"[索引] 重新索引失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def verify_rebuild(self) -> Dict[str, any]:
        """验证重建结果"""
        logger.info("[验证] 开始验证重建结果...")

        verification = {
            "mongodb": self.get_mongodb_stats(),
            "milvus": self.get_milvus_stats(),
            "neo4j": self.get_neo4j_stats(),
            "success": True,
            "warnings": []
        }

        # 检查数据一致性
        mongo_chunks = verification["mongodb"]["chunks"]
        milvus_vectors = verification["milvus"]["vectors"]

        if mongo_chunks > 0 and milvus_vectors > 0:
            ratio = milvus_vectors / mongo_chunks * 100
            logger.info(f"[验证] 向量化覆盖率: {ratio:.1f}%")

            if ratio < 80:
                verification["warnings"].append(f"向量化覆盖率偏低: {ratio:.1f}%")
                logger.warning(f"[验证] 警告: 向量化覆盖率偏低 ({ratio:.1f}%)")
            else:
                logger.info("[验证] 向量化覆盖率良好")

        # 检查文档数量
        if verification["mongodb"]["documents"] == 0:
            verification["warnings"].append("MongoDB 中没有文档")
            logger.warning("[验证] 警告: MongoDB 中没有文档")

        if verification["milvus"]["vectors"] == 0:
            verification["warnings"].append("Milvus 中没有向量")
            logger.warning("[验证] 警告: Milvus 中没有向量")

        logger.info("[验证] 验证完成")
        return verification

    def execute_full_rebuild(
        self,
        clear_mongodb: bool = True,
        clear_milvus: bool = True,
        clear_neo4j: bool = True,
        preserve_concepts: bool = True,
        reindex: bool = True,
        verify: bool = True
    ) -> Dict[str, any]:
        """
        执行完整的数据库重建流程

        Args:
            clear_mongodb: 是否清空 MongoDB
            clear_milvus: 是否清空 Milvus
            clear_neo4j: 是否清空 Neo4j
            preserve_concepts: 是否保留 Neo4j 概念节点
            reindex: 是否重新索引文档
            verify: 是否验证结果

        Returns:
            Dict: 重建结果和统计信息
        """
        logger.info("="*80)
        logger.info("开始执行数据库全量重建")
        logger.info("="*80)

        start_time = datetime.now(timezone.utc)

        # 1. 清空数据库
        if clear_mongodb:
            if not self.clear_mongodb():
                return {"status": "failed", "step": "clear_mongodb", "stats": self.stats}

        if clear_milvus:
            if not self.clear_milvus():
                return {"status": "failed", "step": "clear_milvus", "stats": self.stats}

        if clear_neo4j:
            if not self.clear_neo4j(preserve_concepts=preserve_concepts):
                return {"status": "failed", "step": "clear_neo4j", "stats": self.stats}

        # 2. 重新索引文档
        if reindex:
            if not self.reindex_documents():
                return {"status": "failed", "step": "reindex_documents", "stats": self.stats}

        # 3. 验证结果
        verification = None
        if verify:
            verification = self.verify_rebuild()

        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()

        logger.info("\n" + "="*80)
        logger.info("数据库全量重建完成")
        logger.info("="*80)
        logger.info(f"总耗时: {duration:.2f} 秒")

        return {
            "status": "success",
            "stats": self.stats,
            "verification": verification,
            "duration_seconds": duration,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }
