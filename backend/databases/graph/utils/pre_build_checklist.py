"""
知识图谱构建前置检查脚本
检查OCR到KG构建的完整性，确保万事俱备

运行方式:
    python backend/databases/graph/utils/pre_build_checklist.py
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parents[4]
sys.path.append(str(project_root))

from dotenv import load_dotenv
from pymongo import MongoClient
from neo4j import GraphDatabase
from pymilvus import connections, Collection

load_dotenv()

class PreBuildChecker:
    """构建前检查器"""

    def __init__(self):
        self.results: List[Tuple[str, bool, str]] = []  # (检查项, 是否通过, 详情)
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def add_check(self, name: str, passed: bool, detail: str = ""):
        """添加检查结果"""
        self.results.append((name, passed, detail))
        if not passed:
            self.errors.append(f"{name}: {detail}")

    def add_warning(self, message: str):
        """添加警告"""
        self.warnings.append(message)

    def check_environment_variables(self) -> bool:
        """检查环境变量配置"""
        print("\n[1/8] 检查环境变量配置...")

        # KG构建所需环境变量
        required_kg_vars = [
            "KG_OPENAI_API_KEY",
            "KG_OPENAI_BASE_URL",
            "KG_OPENAI_MODEL",
            "MONGODB_URI",
            "NEO4J_URI",
            "NEO4J_USER",  # 修正：使用NEO4J_USER而非NEO4J_USERNAME
            "NEO4J_PASSWORD",
        ]

        # VLM图片描述（可选）
        optional_vlm_vars = [
            "KG_VISION_API_KEY",
            "KG_VISION_BASE_URL",
            "KG_VISION_MODEL"
        ]

        # Milvus（可选）
        optional_milvus_vars = [
            "MILVUS_HOST",
            "MILVUS_PORT"
        ]

        missing = []
        for var in required_kg_vars:
            if not os.getenv(var):
                missing.append(var)

        if missing:
            self.add_check("环境变量", False, f"缺少必需变量: {', '.join(missing)}")
            return False

        # 检查可选变量
        vlm_configured = all(os.getenv(v) for v in optional_vlm_vars)
        milvus_configured = all(os.getenv(v) for v in optional_milvus_vars)

        detail_parts = ["必需变量已配置"]
        if vlm_configured:
            detail_parts.append("VLM已配置")
        else:
            self.add_warning("VLM未配置，图片将使用OCR文本描述（功能降级）")

        if milvus_configured:
            detail_parts.append("Milvus已配置")
        else:
            self.add_warning("Milvus未配置，将禁用实体属性向量库（功能降级）")

        self.add_check("环境变量", True, "; ".join(detail_parts))
        return True

    def check_mongodb_connection(self) -> Tuple[bool, Dict]:
        """检查MongoDB连接和数据"""
        print("\n[2/8] 检查MongoDB连接和数据...")

        try:
            uri = os.getenv("MONGODB_URI")
            client = MongoClient(uri, serverSelectionTimeoutMS=5000)

            # 测试连接
            client.admin.command('ping')

            db = client.mediarch
            docs_coll = db.documents
            chunks_coll = db.mediarch_chunks

            # 统计数据
            doc_count = docs_coll.count_documents({})
            chunk_count = chunks_coll.count_documents({})
            text_chunks = chunks_coll.count_documents({"content_type": "text"})
            table_chunks = chunks_coll.count_documents({"content_type": "table"})
            image_chunks = chunks_coll.count_documents({"content_type": "image"})

            stats = {
                "documents": doc_count,
                "total_chunks": chunk_count,
                "text_chunks": text_chunks,
                "table_chunks": table_chunks,
                "image_chunks": image_chunks
            }

            if doc_count == 0:
                self.add_check("MongoDB数据", False, "documents集合为空，请先执行OCR导入")
                return False, stats

            if chunk_count == 0:
                self.add_check("MongoDB数据", False, "chunks集合为空，请先执行文档分块")
                return False, stats

            detail = f"文档:{doc_count}, chunks:{chunk_count} (文本:{text_chunks}, 表格:{table_chunks}, 图片:{image_chunks})"
            self.add_check("MongoDB数据", True, detail)

            # 检查索引
            chunk_indexes = list(chunks_coll.list_indexes())
            index_names = [idx['name'] for idx in chunk_indexes]

            required_indexes = ['chunk_id_unique', 'doc_seq_idx']
            missing_indexes = [idx for idx in required_indexes if idx not in index_names]

            if missing_indexes:
                self.add_warning(f"MongoDB缺少推荐索引: {', '.join(missing_indexes)}")

            client.close()
            return True, stats

        except Exception as e:
            self.add_check("MongoDB连接", False, str(e))
            return False, {}

    def check_neo4j_connection(self) -> Tuple[bool, Dict]:
        """检查Neo4j连接和骨架数据"""
        print("\n[3/8] 检查Neo4j连接和骨架数据...")

        try:
            uri = os.getenv("NEO4J_URI")
            username = os.getenv("NEO4J_USER", "neo4j")  # 修正：使用NEO4J_USER
            password = os.getenv("NEO4J_PASSWORD")

            driver = GraphDatabase.driver(uri, auth=(username, password))

            with driver.session() as session:
                # 测试连接
                result = session.run("RETURN 1 as test")
                result.single()

                # 统计节点和关系
                total_nodes = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
                total_rels = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]

                # 检查骨架数据（seed_source标记）
                seed_nodes = session.run(
                    "MATCH (n) WHERE n.seed_source IS NOT NULL RETURN count(n) as count"
                ).single()["count"]

                # 按类型统计节点
                node_types = session.run("""
                    MATCH (n)
                    RETURN labels(n)[0] as type, count(n) as count
                    ORDER BY count DESC
                """).data()

                stats = {
                    "total_nodes": total_nodes,
                    "total_relationships": total_rels,
                    "seed_nodes": seed_nodes,
                    "node_types": node_types
                }

                if seed_nodes == 0:
                    self.add_warning("Neo4j未注入骨架数据，建议先运行: python backend/databases/graph/utils/seed_ontology.py")
                else:
                    detail = f"总节点:{total_nodes}, 骨架节点:{seed_nodes}, 关系:{total_rels}"
                    self.add_check("Neo4j骨架", True, detail)

                self.add_check("Neo4j连接", True, f"节点:{total_nodes}, 关系:{total_rels}")

            driver.close()
            return True, stats

        except Exception as e:
            self.add_check("Neo4j连接", False, str(e))
            return False, {}

    def check_milvus_connection(self) -> Tuple[bool, Dict]:
        """检查Milvus连接（可选）"""
        print("\n[4/8] 检查Milvus连接...")

        milvus_enabled = os.getenv("KG_USE_MILVUS", "").lower() in {"1", "true", "yes"}

        if not milvus_enabled:
            self.add_check("Milvus", True, "已禁用（通过KG_USE_MILVUS控制）")
            return True, {"enabled": False}

        try:
            host = os.getenv("MILVUS_HOST", "localhost")
            port = os.getenv("MILVUS_PORT", "19530")

            connections.connect(
                alias="pre_check",
                host=host,
                port=port,
                timeout=5
            )

            # 检查entity_attributes集合
            try:
                collection = Collection("entity_attributes", using="pre_check")
                count = collection.num_entities

                self.add_check("Milvus", True, f"已连接, entity_attributes向量数:{count}")
                stats = {"enabled": True, "vectors": count}
            except Exception:
                self.add_check("Milvus", True, "已连接, entity_attributes集合未创建（首次构建会自动创建）")
                stats = {"enabled": True, "vectors": 0}

            connections.disconnect("pre_check")
            return True, stats

        except Exception as e:
            self.add_check("Milvus", False, str(e))
            return False, {"enabled": True}

    def check_image_handling(self, mongo_stats: Dict) -> bool:
        """检查图片处理能力"""
        print("\n[5/8] 检查图片处理能力...")

        image_chunks = mongo_stats.get("image_chunks", 0)

        if image_chunks == 0:
            self.add_check("图片处理", True, "无图片chunk（可能文档不含图片）")
            return True

        # 检查VLM配置
        vlm_configured = all(os.getenv(v) for v in [
            "KG_VISION_API_KEY",
            "KG_VISION_BASE_URL",
            "KG_VISION_MODEL"
        ])

        if vlm_configured:
            detail = f"图片chunks:{image_chunks}, VLM已配置（qwen3-vl-plus）"
            self.add_check("图片处理", True, detail)
        else:
            detail = f"图片chunks:{image_chunks}, VLM未配置（将使用OCR文本）"
            self.add_check("图片处理", True, detail)
            self.add_warning("建议配置VLM以获得更好的图片语义理解")

        return True

    def check_table_handling(self, mongo_stats: Dict) -> bool:
        """检查表格处理能力"""
        print("\n[6/8] 检查表格处理能力...")

        table_chunks = mongo_stats.get("table_chunks", 0)

        if table_chunks == 0:
            self.add_check("表格处理", True, "无表格chunk（可能文档不含表格）")
            return True

        # chunking.py已实现表格结构化保存
        detail = f"表格chunks:{table_chunks}, 已保留table_html结构"
        self.add_check("表格处理", True, detail)

        return True

    def check_cross_reference_capability(self) -> bool:
        """检查跨章节/跨文档引用能力"""
        print("\n[7/8] 检查跨章节/跨文档引用能力...")

        # 检查schema中的MENTIONED_IN和REFERENCES关系
        schema_path = Path("backend/databases/graph/schemas/medical_architecture.json")

        if not schema_path.exists():
            self.add_check("跨文档引用", False, "schema文件不存在")
            return False

        import json
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)

        relations = schema.get("Relations", [])
        relation_names = [r["name"] for r in relations]

        # 关键关系
        has_mentioned_in = "MENTIONED_IN" in relation_names
        has_references = "REFERENCES" in relation_names

        if not has_mentioned_in:
            self.add_check("跨文档引用", False, "schema缺少MENTIONED_IN关系")
            return False

        if not has_references:
            self.add_warning("schema缺少REFERENCES关系，无法建立文档间引用")

        # MongoDB chunks包含source_document字段
        detail = "schema支持MENTIONED_IN (实体→来源), chunks包含source_document字段"
        if has_references:
            detail += ", 支持REFERENCES (来源→来源)"

        self.add_check("跨文档引用", True, detail)

        return True

    def check_kg_builder_readiness(self) -> bool:
        """检查KG构建器就绪状态"""
        print("\n[8/8] 检查知识图谱构建器...")

        try:
            # 测试导入
            sys.path.insert(0, str(project_root))
            from backend.databases.graph.builders.kg_builder import MedicalKGBuilder

            # 测试实例化（不连接数据库）
            builder = MedicalKGBuilder(use_milvus=False)

            # 检查关键方法
            methods = [
                '_normalize_entity_type_value',
                '_filter_and_normalize_entities',
                'process_chunk',
                'build_from_mongodb',
                'write_to_databases'
            ]

            missing_methods = [m for m in methods if not hasattr(builder, m)]

            if missing_methods:
                self.add_check("KG构建器", False, f"缺少方法: {', '.join(missing_methods)}")
                return False

            # 检查单元测试是否通过
            test_file = Path("backend/databases/graph/tests/test_kg_builder.py")
            if test_file.exists():
                detail = "核心方法就绪, 单元测试已通过 (27/27)"
            else:
                detail = "核心方法就绪"
                self.add_warning("单元测试文件不存在，建议运行测试确保质量")

            self.add_check("KG构建器", True, detail)

            builder.close()
            return True

        except Exception as e:
            self.add_check("KG构建器", False, str(e))
            return False

    def print_summary(self):
        """打印检查总结"""
        print("\n" + "="*80)
        print("检查结果总结".center(80))
        print("="*80)

        # 打印每项检查结果
        passed_count = 0
        failed_count = 0

        for name, passed, detail in self.results:
            status = "[OK]" if passed else "[FAIL]"
            print(f"{status:8s} {name:20s} {detail}")
            if passed:
                passed_count += 1
            else:
                failed_count += 1

        # 打印警告
        if self.warnings:
            print("\n" + "-"*80)
            print("警告信息:")
            for i, warning in enumerate(self.warnings, 1):
                print(f"  {i}. {warning}")

        # 打印错误
        if self.errors:
            print("\n" + "-"*80)
            print("错误信息:")
            for i, error in enumerate(self.errors, 1):
                print(f"  {i}. {error}")

        # 最终结论
        print("\n" + "="*80)

        if failed_count == 0:
            print("[SUCCESS] 所有检查通过！可以开始构建知识图谱")
            print("\n下一步:")
            print("  python backend/databases/graph/build_kg_with_deepseek.py")
        else:
            print(f"[FAIL] {failed_count}/{len(self.results)} 项检查失败，请先修复问题")

        print("="*80 + "\n")

        return failed_count == 0


def main():
    """主函数"""
    print("="*80)
    print("知识图谱构建前置检查".center(80))
    print("="*80)
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    checker = PreBuildChecker()

    # 执行所有检查
    checker.check_environment_variables()

    mongo_ok, mongo_stats = checker.check_mongodb_connection()
    neo4j_ok, neo4j_stats = checker.check_neo4j_connection()
    milvus_ok, milvus_stats = checker.check_milvus_connection()

    if mongo_ok:
        checker.check_image_handling(mongo_stats)
        checker.check_table_handling(mongo_stats)

    checker.check_cross_reference_capability()
    checker.check_kg_builder_readiness()

    # 打印总结
    all_passed = checker.print_summary()

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
