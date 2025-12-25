"""
知识关联增强模块

功能：
1. 自动补充隐含的设计知识关联
2. 增强多跳推理能力
3. 建立空间-设计方法-规范标准的完整链路

使用场景：
- 知识图谱构建完成后运行
- 自动发现并补充缺失的关联关系

运行：
python backend/databases/graph/optimization/enrich_knowledge_links.py
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

from neo4j import GraphDatabase
from rich.console import Console
from rich.progress import track
from rich.panel import Panel

console = Console()


class KnowledgeLinkEnricher:
    """知识关联增强器"""
    
    def __init__(self):
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
        )
        self.added_links = 0
    
    def run_all_enrichments(self):
        """运行所有增强策略"""
        console.print(Panel.fit(
            "[bold cyan]🔗 知识关联增强[/bold cyan]\n"
            "自动补充设计知识关联，增强多跳推理能力",
            border_style="cyan"
        ))
        console.print()
        
        with self.driver.session() as session:
            # 1. 传递规范引用（空间→设计方法→规范）
            console.print("[cyan]1️⃣  传递规范引用关系...[/cyan]")
            count1 = self._propagate_compliance_via_design_methods(session)
            console.print(f"   ✓ 新增 {count1} 条间接符合关系\n")
            
            # 2. 补充设计方法指导关系
            console.print("[cyan]2️⃣  补充设计方法指导关系...[/cyan]")
            count2 = self._add_design_method_guides(session)
            console.print(f"   ✓ 新增 {count2} 条指导关系\n")
            
            # 3. 关联同层级实体的共同规范
            console.print("[cyan]3️⃣  关联共同规范的实体...[/cyan]")
            count3 = self._link_entities_with_common_standards(session)
            console.print(f"   ✓ 新增 {count3} 条关联关系\n")
            
            # 4. 传递文献引用链
            console.print("[cyan]4️⃣  传递文献引用链...[/cyan]")
            count4 = self._propagate_reference_chains(session)
            console.print(f"   ✓ 新增 {count4} 条传递引用\n")
            
            self.added_links = count1 + count2 + count3 + count4
            
            console.print(Panel.fit(
                f"[bold green]✅ 增强完成！[/bold green]\n"
                f"总计新增 {self.added_links} 条知识关联",
                border_style="green"
            ))
    
    def _propagate_compliance_via_design_methods(self, session) -> int:
        """
        传递规范引用：
        如果 空间A -[COMPLIES_WITH]-> 规范X
        且 设计方法B -[GUIDES]-> 空间A
        且 设计方法B -[DERIVED_FROM]-> 规范X
        则这是一个有效的知识链，标记为已验证
        
        如果设计方法B没有DERIVED_FROM关系，则自动补充
        """
        result = session.run("""
            MATCH (space)-[:COMPLIES_WITH]->(standard:Source)
            MATCH (method:DesignMethod)-[:GUIDES]->(space)
            WHERE NOT (method)-[:DERIVED_FROM]->(standard)
            MERGE (method)-[r:DERIVED_FROM {inferred: true}]->(standard)
            RETURN count(r) AS added
        """)
        return result.single()["added"]
    
    def _add_design_method_guides(self, session) -> int:
        """
        补充设计方法指导关系：
        如果 功能分区A 包含 空间B
        且 空间B 符合 规范X
        且 设计方法C 源自 规范X
        但 设计方法C 不指导 功能分区A
        则补充 设计方法C -[GUIDES]-> 功能分区A
        """
        result = session.run("""
            MATCH (zone:FunctionalZone)-[:CONTAINS]->(space:Space)
            MATCH (space)-[:COMPLIES_WITH]->(standard:Source)
            MATCH (method:DesignMethod)-[:DERIVED_FROM]->(standard)
            WHERE NOT (method)-[:GUIDES]->(zone)
            MERGE (method)-[r:GUIDES {inferred: true}]->(zone)
            RETURN count(r) AS added
        """)
        return result.single()["added"]
    
    def _link_entities_with_common_standards(self, session) -> int:
        """
        关联遵循相同规范的实体：
        如果 空间A 和 空间B 都符合 规范X
        且它们属于同一个功能分区
        则创建一个间接关联（通过共同规范）
        """
        result = session.run("""
            MATCH (space1:Space)-[:COMPLIES_WITH]->(standard:Source)<-[:COMPLIES_WITH]-(space2:Space)
            MATCH (zone:FunctionalZone)-[:CONTAINS]->(space1)
            MATCH (zone)-[:CONTAINS]->(space2)
            WHERE elementId(space1) < elementId(space2)
            AND NOT (space1)-[:RELATED_TO]-(space2)
            MERGE (space1)-[r:RELATED_TO {
                via: 'common_standard',
                standard_name: standard.title,
                inferred: true
            }]->(space2)
            RETURN count(r) AS added
        """)
        return result.single()["added"]
    
    def _propagate_reference_chains(self, session) -> int:
        """
        传递文献引用链：
        如果 论文A -[REFERENCES]-> 论文B
        且 论文B -[REFERENCES]-> 论文C
        创建 论文A -[REFERENCES_INDIRECTLY]-> 论文C (二级引用)
        """
        result = session.run("""
            MATCH (a:Source)-[:REFERENCES]->(b:Source)-[:REFERENCES]->(c:Source)
            WHERE NOT (a)-[:REFERENCES]->(c)
            AND elementId(a) <> elementId(c)
            MERGE (a)-[r:REFERENCES {
                indirect: true,
                via: b.title,
                depth: 2
            }]->(c)
            RETURN count(r) AS added
        """)
        return result.single()["added"]
    
    def analyze_knowledge_paths(self):
        """分析知识路径的连通性"""
        console.print("\n[bold]📊 知识路径分析[/bold]\n")
        
        with self.driver.session() as session:
            # 1. 统计多跳路径
            paths_2hop = session.run("""
                MATCH path = (space:Space)-[:COMPLIES_WITH|GUIDES*2]-(standard:Source)
                RETURN count(DISTINCT path) AS count
            """).single()["count"]
            
            paths_3hop = session.run("""
                MATCH path = (space:Space)-[*3]-(source:Source)
                WHERE all(r in relationships(path) WHERE type(r) IN ['COMPLIES_WITH', 'GUIDES', 'DERIVED_FROM', 'REFERENCES'])
                RETURN count(DISTINCT path) AS count
            """).single()["count"]
            
            # 2. 设计方法覆盖率
            spaces_with_methods = session.run("""
                MATCH (space:Space)<-[:GUIDES]-(method:DesignMethod)
                RETURN count(DISTINCT space) AS count
            """).single()["count"]
            
            total_spaces = session.run("""
                MATCH (space:Space)
                RETURN count(space) AS count
            """).single()["count"]
            
            # 3. 规范标准覆盖率
            entities_with_standards = session.run("""
                MATCH (entity)-[:COMPLIES_WITH]->(standard:Source)
                RETURN count(DISTINCT entity) AS count
            """).single()["count"]
            
            total_entities = session.run("""
                MATCH (entity)
                WHERE entity:Space OR entity:FunctionalZone
                RETURN count(entity) AS count
            """).single()["count"]
            
            console.print(f"[cyan]2-hop 知识路径:[/cyan] {paths_2hop:,}")
            console.print(f"[cyan]3-hop 知识路径:[/cyan] {paths_3hop:,}")
            console.print(f"[cyan]设计方法覆盖:[/cyan] {spaces_with_methods}/{total_spaces} ({spaces_with_methods/total_spaces*100:.1f}%)" if total_spaces > 0 else "[cyan]设计方法覆盖:[/cyan] 0%")
            console.print(f"[cyan]规范标准覆盖:[/cyan] {entities_with_standards}/{total_entities} ({entities_with_standards/total_entities*100:.1f}%)" if total_entities > 0 else "[cyan]规范标准覆盖:[/cyan] 0%")
    
    def close(self):
        """关闭连接"""
        self.driver.close()


def main():
    enricher = KnowledgeLinkEnricher()
    
    try:
        # 1. 运行增强
        enricher.run_all_enrichments()
        
        # 2. 分析结果
        enricher.analyze_knowledge_paths()
        
        console.print("\n[bold green]✅ 知识关联增强完成！[/bold green]")
        console.print("\n[cyan]💡 提示：[/cyan]")
        console.print("  在Neo4j Browser中运行以下查询验证多跳路径：")
        console.print("  ```")
        console.print("  // 查看空间→设计方法→规范的完整路径")
        console.print("  MATCH path = (s:Space)-[:GUIDES|DERIVED_FROM*]-(source:Source)")
        console.print("  RETURN path LIMIT 25")
        console.print("  ```\n")
        
    finally:
        enricher.close()


if __name__ == "__main__":
    main()

