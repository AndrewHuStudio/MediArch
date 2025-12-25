"""
知识图谱分析工具（合并版）

功能：
- 统计分析：MongoDB和Neo4j的数据规模分析
- 探索功能：实体/关系类型分布、三元组示例
- 效率评估：提取效率、正常性检查
- 关键词搜索：快速查找实体
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from pymongo import MongoClient

load_dotenv()


class KGAnalyzer:
    """知识图谱分析器"""
    
    def __init__(self):
        # Neo4j连接
        self.neo4j_driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
        )
        
        # MongoDB连接
        self.mongo_client = MongoClient(os.getenv("MONGODB_URI"))
        self.mongo_db = self.mongo_client[os.getenv("MONGODB_DATABASE", "mediarch")]
    
    # ==================== MongoDB分析 ====================
    
    def analyze_mongodb(self):
        """分析MongoDB中的数据规模"""
        print("\n" + "="*80)
        print("[MongoDB] 数据分析")
        print("="*80)
        
        # 统计文档数量
        doc_count = self.mongo_db.documents.count_documents({})
        print(f"\n1. 文档总数: {doc_count}")
        
        # 统计每个文档的chunks数量
        total_chunks = 0
        total_content_length = 0
        
        print("\n2. 各文档chunks分布:")
        print("-" * 80)
        print(f"{'文档名':<50} {'Chunks数':<15} {'平均Chunk长度'}")
        print("-" * 80)
        
        for doc in self.mongo_db.documents.find({}):
            chunks = doc.get("chunks", [])
            doc_title = doc.get("title", "未知文档")
            chunk_count = len(chunks)
            total_chunks += chunk_count
            
            # 计算平均chunk长度
            chunk_lengths = [len(c.get("content", "")) for c in chunks]
            avg_length = sum(chunk_lengths) / len(chunk_lengths) if chunk_lengths else 0
            total_content_length += sum(chunk_lengths)
            
            print(f"{doc_title:<50} {chunk_count:<15} {avg_length:.0f}字")
        
        print("-" * 80)
        print(f"{'总计':<50} {total_chunks:<15} {total_content_length/total_chunks:.0f}字")
        
        return {
            "doc_count": doc_count,
            "total_chunks": total_chunks,
            "avg_chunk_length": total_content_length / total_chunks if total_chunks > 0 else 0
        }
    
    # ==================== Neo4j分析 ====================
    
    def analyze_neo4j(self):
        """分析Neo4j中的知识图谱数据"""
        print("\n" + "="*80)
        print("[Neo4j] 知识图谱分析")
        print("="*80)
        
        stats = {}
        
        with self.neo4j_driver.session() as session:
            # 1. 总节点数
            result = session.run("MATCH (n) RETURN count(n) as total")
            stats["total_nodes"] = result.single()["total"]
            
            # 2. 实体节点数（Level 2）
            result = session.run("MATCH (n:Entity {level: 2}) RETURN count(n) as total")
            stats["entity_nodes"] = result.single()["total"]
            
            # 3. 属性节点数（Level 1）
            result = session.run("MATCH (n:Attribute) RETURN count(n) as total")
            stats["attribute_nodes"] = result.single()["total"]
            
            # 4. 关系总数
            result = session.run("MATCH ()-[r]->() RETURN count(r) as total")
            stats["total_relationships"] = result.single()["total"]

            # 重复边与悬空边检测（轻量）
            print("\n[Checks] 数据质量检查：")
            dup = session.run(
                """
                MATCH (a:Entity)-[r]->(b:Entity)
                WITH a.id as s, type(r) as t, b.id as o, count(*) as c
                WHERE c > 1
                RETURN count(*) as dup_triplets
                """
            ).single()["dup_triplets"]
            print(f"  - 重复三元组数: {dup}")

            dangling = session.run(
                """
                MATCH (a:Entity)-[r]->(b)
                WHERE NOT b:Entity
                RETURN count(r) as dangling
                """
            ).single()["dangling"]
            print(f"  - 悬空边数: {dangling}")
            
            # 5. 节点统计
            print("\n1. 节点统计:")
            print(f"   - 总节点数: {stats['total_nodes']}")
            print(f"   - 实体节点 (Level 2): {stats['entity_nodes']}")
            print(f"   - 属性节点 (Level 1): {stats['attribute_nodes']}")
            print(f"   - 关系总数: {stats['total_relationships']}")
            
            # 6. 实体类型分布（Top 10）
            print("\n2. 实体类型分布 (Top 10):")
            print("-" * 60)
            print(f"{'实体类型':<30} {'数量':<15} {'占比'}")
            print("-" * 60)
            
            result = session.run("""
                MATCH (n:Entity {level: 2})
                WHERE n.schema_type IS NOT NULL
                RETURN n.schema_type as type, count(*) as count
                ORDER BY count DESC
                LIMIT 10
            """)
            
            for record in result:
                entity_type = record["type"]
                count = record["count"]
                percentage = (count / stats["entity_nodes"] * 100) if stats["entity_nodes"] > 0 else 0
                print(f"{entity_type:<30} {count:<15} {percentage:.1f}%")
            
            # 7. 关系类型分布（Top 10）
            print("\n3. 关系类型分布 (Top 10):")
            print("-" * 60)
            print(f"{'关系类型':<30} {'数量':<15} {'占比'}")
            print("-" * 60)
            
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(*) as count
                ORDER BY count DESC
                LIMIT 10
            """)
            
            for record in result:
                rel_type = record["rel_type"]
                count = record["count"]
                percentage = (count / stats["total_relationships"] * 100) if stats["total_relationships"] > 0 else 0
                print(f"{rel_type:<30} {count:<15} {percentage:.1f}%")
            
            # 8. 实体连接度统计
            print("\n4. 实体连接度分析:")
            print("-" * 60)
            result = session.run("""
                MATCH (n:Entity {level: 2})
                OPTIONAL MATCH (n)-[r]-()
                WITH n, count(r) as degree
                RETURN 
                    min(degree) as min_degree,
                    avg(degree) as avg_degree,
                    max(degree) as max_degree
            """)
            
            record = result.single()
            print(f"   - 最小连接度: {record['min_degree']}")
            print(f"   - 平均连接度: {record['avg_degree']:.2f}")
            print(f"   - 最大连接度: {record['max_degree']}")
            
            # 9. 高连接度实体（Top 10）
            print("\n5. 核心实体 - 高连接度 (Top 10):")
            print("-" * 80)
            print(f"{'实体名称':<40} {'类型':<20} {'连接度'}")
            print("-" * 80)
            
            result = session.run("""
                MATCH (n:Entity {level: 2})
                OPTIONAL MATCH (n)-[r]-()
                WITH n, count(r) as degree
                ORDER BY degree DESC
                LIMIT 10
                RETURN n.name as name, n.schema_type as type, degree
            """)
            
            for record in result:
                name = record["name"] or "未命名"
                entity_type = record["type"] or "未分类"
                degree = record["degree"]
                print(f"{name:<40} {entity_type:<20} {degree}")
        
        return stats
    
    # ==================== 效率分析 ====================
    
    def analyze_efficiency(self, mongodb_stats, neo4j_stats):
        """分析提取效率"""
        print("\n" + "="*80)
        print("[Efficiency] 提取效率分析")
        print("="*80)
        
        chunks = mongodb_stats["total_chunks"]
        entities = neo4j_stats["entity_nodes"]
        attributes = neo4j_stats["attribute_nodes"]
        relationships = neo4j_stats["total_relationships"]
        
        print(f"\n1. 平均每个Chunk提取:")
        if chunks > 0:
            print(f"   - 实体数: {entities/chunks:.2f} 个")
            print(f"   - 属性数: {attributes/chunks:.2f} 个")
            print(f"   - 关系数: {relationships/chunks:.2f} 个")
        
        print(f"\n2. 平均每个实体:")
        if entities > 0:
            print(f"   - 属性数: {attributes/entities:.2f} 个")
            print(f"   - 关系数: {relationships/entities:.2f} 个")
        
        print(f"\n3. 节点分布:")
        total_nodes = entities + attributes
        if total_nodes > 0:
            print(f"   - 实体节点占比: {entities/total_nodes*100:.1f}%")
            print(f"   - 属性节点占比: {attributes/total_nodes*100:.1f}%")
    
    def evaluate_normalcy(self, mongodb_stats, neo4j_stats):
        """评估数据规模是否正常"""
        print("\n" + "="*80)
        print("[Quality] 正常性评估")
        print("="*80)
        
        chunks = mongodb_stats["total_chunks"]
        entities = neo4j_stats["entity_nodes"]
        attributes = neo4j_stats["attribute_nodes"]
        total_nodes = neo4j_stats["total_nodes"]
        
        warnings = []
        
        # 检查1: 每个chunk的实体数
        entities_per_chunk = entities / chunks if chunks > 0 else 0
        if entities_per_chunk > 10:
            warnings.append(f"[Warning] 平均每个chunk提取了{entities_per_chunk:.1f}个实体，可能偏多")
        elif entities_per_chunk < 2:
            warnings.append(f"[Warning] 平均每个chunk只提取了{entities_per_chunk:.1f}个实体，可能偏少")
        else:
            print(f"[OK] 实体提取数量正常 (平均{entities_per_chunk:.1f}个/chunk)")
        
        # 检查2: 每个实体的属性数
        attrs_per_entity = attributes / entities if entities > 0 else 0
        if attrs_per_entity > 5:
            warnings.append(f"[Warning] 平均每个实体有{attrs_per_entity:.1f}个属性，可能偏多")
        elif attrs_per_entity < 0.5:
            warnings.append(f"[Warning] 平均每个实体只有{attrs_per_entity:.1f}个属性，可能偏少")
        else:
            print(f"[OK] 属性提取数量正常 (平均{attrs_per_entity:.1f}个/实体)")
        
        # 检查3: 总节点数
        expected_nodes_min = chunks * 3  # 保守估计
        expected_nodes_max = chunks * 15  # 激进估计
        
        if total_nodes < expected_nodes_min:
            warnings.append(f"[Warning] 总节点数({total_nodes})低于预期范围")
        elif total_nodes > expected_nodes_max:
            warnings.append(f"[Warning] 总节点数({total_nodes})高于预期范围，可能提取了过多细节")
        else:
            print(f"[OK] 总节点数在合理范围内 ({expected_nodes_min}-{expected_nodes_max})")
        
        # 输出警告
        if warnings:
            print("\n" + "-"*80)
            for warning in warnings:
                print(warning)
        
        # 最终结论
        print("\n" + "="*80)
        print("[Summary] 结论:")
        print("="*80)
        
        if 500 <= total_nodes <= 5000:
            print(f"""
 你的知识图谱规模是正常的！

具体分析：
  - 使用了 {mongodb_stats['doc_count']} 个PDF文档
  - 生成了 {chunks} 个chunks（文本块）
  - 提取了 {entities} 个实体节点
  - 提取了 {attributes} 个属性节点
  - 总计 {total_nodes} 个节点
""")
        else:
            print(f"\n 图谱规模({total_nodes}节点)可能需要检查")
    
    # ==================== 探索功能 ====================
    
    def show_sample_triples(self, limit=30):
        """查看示例三元组"""
        with self.neo4j_driver.session() as session:
            result = session.run("""
                MATCH (a:Entity {level: 2})-[r:RELATION]->(b:Entity {level: 2})
                RETURN a.name as subject, 
                       r.type as relation, 
                       b.name as object
                LIMIT $limit
            """, limit=limit)
            
            print("\n" + "="*80)
            print(f"[Triples] 示例三元组（前{limit}条）")
            print("="*80)
            
            for i, record in enumerate(result, 1):
                subj = record['subject']
                rel = record['relation']
                obj = record['object']
                print(f"  {i}. ({subj}) --[{rel}]--> ({obj})")
    
    def search_entity(self, keyword: str):
        """搜索包含关键词的实体"""
        with self.neo4j_driver.session() as session:
            result = session.run("""
                MATCH (n:Entity)
                WHERE n.name CONTAINS $keyword
                RETURN n.name as name, 
                       n.level as level,
                       n.schema_type as type
                LIMIT 20
            """, keyword=keyword)
            
            print(f"\n[Search] 搜索结果：'{keyword}'")
            print("="*80)
            
            count = 0
            for record in result:
                count += 1
                name = record['name']
                level = record['level']
                entity_type = record['type'] or '未分类'
                level_name = {1: "属性", 2: "实体"}.get(level, f"Level {level}")
                print(f"  {count}. {name} [{level_name}] ({entity_type})")
            
            if count == 0:
                print("  未找到匹配结果")
    
    # ==================== 主分析流程 ====================
    
    def full_analysis(self):
        """完整分析流程"""
        print("\n" + "="*80)
        print("[Analysis] 知识图谱完整分析")
        print("="*80)
        
        # 1. MongoDB分析
        mongodb_stats = self.analyze_mongodb()
        
        # 2. Neo4j分析
        neo4j_stats = self.analyze_neo4j()
        
        # 3. 效率分析
        self.analyze_efficiency(mongodb_stats, neo4j_stats)
        
        # 4. 正常性评估
        self.evaluate_normalcy(mongodb_stats, neo4j_stats)
        
        # 5. 三元组示例
        self.show_sample_triples(limit=20)
        
        # 6. 关键词搜索示例
        print("\n" + "="*80)
        print("[Search] 关键词搜索示例")
        print("="*80)
        
        keywords = ["手术室", "医院", "规范", "面积"]
        for keyword in keywords:
            self.search_entity(keyword)
        
        print("\n" + "="*80)
        print("[Success] 分析完成！")
        print("="*80)
        print("\n[Info] 提示：访问 http://localhost:7474 使用Neo4j Browser进行可视化探索\n")
    
    def close(self):
        """关闭所有连接"""
        self.neo4j_driver.close()
        self.mongo_client.close()


def main():
    """主函数"""
    try:
        analyzer = KGAnalyzer()
        
        # 运行完整分析
        analyzer.full_analysis()
        
        analyzer.close()
        
    except Exception as e:
        print(f"\n[ERROR] 分析失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    from .graph_tools import cmd_analyze
    cmd_analyze()

