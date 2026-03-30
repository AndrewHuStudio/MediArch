"""
预注入骨架脚本 v2.0 - 基于JSON数据

使用 ontology_seed_data.json 创建概念节点骨架，包括：
1. Hospital（综合医院）
2. DepartmentGroup（4个部门）
3. FunctionalZone（~20个功能分区）
4. Space（~150个标准空间）
5. DesignMethodCategory（5个设计方法分类）
6. DesignMethod（5个种子设计方法）
"""

from __future__ import annotations

from pathlib import Path
import sys
project_root = Path(__file__).resolve().parents[4]
sys.path.append(str(project_root))

import argparse
import hashlib
import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional

from backend.env_loader import load_dotenv
from neo4j import GraphDatabase


SEED_DATA_ENV_VAR = "KG_SEED_DATA_PATH"


def resolve_seed_data_path() -> Path:
    seed_path = (os.getenv(SEED_DATA_ENV_VAR) or "").strip()
    if not seed_path:
        raise RuntimeError(f"{SEED_DATA_ENV_VAR} environment variable is required.")
    json_path = Path(seed_path).expanduser().resolve()
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")
    return json_path


class OntologySeederV2:
    """基于JSON的预注入骨架创建器"""

    def __init__(
        self,
        dry_run: bool = False,
        clear_existing: bool = False
    ) -> None:
        load_dotenv()
        self.json_path = resolve_seed_data_path()
        self.dry_run = dry_run
        self.clear_existing = clear_existing

        # 加载JSON数据
        with open(self.json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

        self.version = self.data.get("_version", "2.0")
        self.source = self.data.get("_source", str(self.json_path))

        # 连接Neo4j
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
        )

        # 统计
        self.stats = {
            "hospital": 0,
            "departments": 0,
            "zones": 0,
            "spaces": 0,
            "method_categories": 0,
            "methods": 0,
            "relationships": 0
        }

    def clear_concept_nodes(self):
        """清空所有概念节点"""
        if self.dry_run:
            print("[DRY-RUN] Would clear all concept nodes (is_concept=true)")
            return

        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (n) WHERE n.is_concept = true
                DETACH DELETE n
                RETURN count(n) as deleted
            """)
            record = result.single()
            deleted = record["deleted"] if record else 0
            print(f"[OK] Cleared {deleted} concept nodes")

    def _generate_stable_id(self, label: str, name: str) -> str:
        """生成稳定的节点ID"""
        unique_key = f"{label}:{name}".lower().strip()
        digest = hashlib.sha256(unique_key.encode("utf-8")).hexdigest()[:16]
        return f"entity_{digest}"

    def create_hospital_node(self) -> str:
        """创建医院根节点"""
        hospital = self.data.get("hospital", {})
        name = hospital.get("name", "综合医院")
        node_id = self._generate_stable_id("Hospital", name)

        if self.dry_run:
            print(f"[DRY-RUN] Would create Hospital: {name} (id={node_id})")
            return node_id

        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (h:Hospital {id: $id})
                ON CREATE SET
                    h.name = $name,
                    h.is_concept = true,
                    h.seed_version = $seed_version,
                    h.seed_source = $seed_source,
                    h.schema_type = '医院',
                    h.level = $level,
                    h.description = $description,
                    h.created_at = datetime($created_at)
                ON MATCH SET
                    h.seed_version = $seed_version,
                    h.updated_at = datetime($created_at)
            """,
                id=node_id,
                name=name,
                seed_version=hospital.get("seed_version", self.version),
                seed_source=hospital.get("seed_source", self.source),
                level=hospital.get("level", "通用"),
                description=hospital.get("description", ""),
                created_at=datetime.utcnow().isoformat()
            )

        self.stats["hospital"] += 1
        print(f"[OK] Created Hospital: {name}")
        return node_id

    def create_department_nodes(self, hospital_id: str) -> Dict[str, str]:
        """创建部门节点"""
        departments = self.data.get("departments", [])
        dept_ids = {}

        for dept in departments:
            name = dept.get("name")
            if not name:
                continue

            node_id = self._generate_stable_id("DepartmentGroup", name)
            dept_ids[name] = node_id

            if self.dry_run:
                print(f"[DRY-RUN] Would create Department: {name} (id={node_id})")
                continue

            with self.driver.session(database=self.database) as session:
                # 创建部门节点
                session.run("""
                    MERGE (d:DepartmentGroup {id: $id})
                    ON CREATE SET
                        d.name = $name,
                        d.is_concept = true,
                        d.seed_version = $seed_version,
                        d.seed_source = $seed_source,
                        d.schema_type = '部门',
                        d.description = $description,
                        d.created_at = datetime($created_at)
                    ON MATCH SET
                        d.seed_version = $seed_version,
                        d.updated_at = datetime($created_at)
                """,
                    id=node_id,
                    name=name,
                    seed_version=dept.get("seed_version", self.version),
                    seed_source=dept.get("seed_source", self.source),
                    description=dept.get("description", ""),
                    created_at=datetime.utcnow().isoformat()
                )

                # 连接到医院
                session.run("""
                    MATCH (h:Hospital {id: $hospital_id})
                    MATCH (d:DepartmentGroup {id: $dept_id})
                    MERGE (h)-[r:CONTAINS]->(d)
                    ON CREATE SET
                        r.seed_source = $seed_source,
                        r.seed_version = $seed_version
                """,
                    hospital_id=hospital_id,
                    dept_id=node_id,
                    seed_source=self.source,
                    seed_version=self.version
                )
                self.stats["relationships"] += 1

            self.stats["departments"] += 1
            print(f"  [OK] Created Department: {name}")

        return dept_ids

    def create_zone_nodes(self, dept_ids: Dict[str, str]) -> Dict[str, str]:
        """创建功能分区节点"""
        departments = self.data.get("departments", [])
        zone_ids = {}

        for dept in departments:
            dept_name = dept.get("name")
            dept_id = dept_ids.get(dept_name)
            if not dept_id:
                continue

            zones = dept.get("functional_zones", [])
            for zone in zones:
                zone_name = zone.get("name")
                if not zone_name:
                    continue

                node_id = self._generate_stable_id("FunctionalZone", zone_name)
                zone_ids[zone_name] = node_id

                if self.dry_run:
                    print(f"[DRY-RUN] Would create Zone: {zone_name} under {dept_name}")
                    continue

                with self.driver.session(database=self.database) as session:
                    # 创建功能分区节点
                    session.run("""
                        MERGE (z:FunctionalZone {id: $id})
                        ON CREATE SET
                            z.name = $name,
                            z.is_concept = true,
                            z.is_physical = $is_physical,
                            z.zone_type = $zone_type,
                            z.seed_version = $seed_version,
                            z.seed_source = $seed_source,
                            z.schema_type = '功能分区',
                            z.description = $description,
                            z.created_at = datetime($created_at)
                        ON MATCH SET
                            z.is_physical = $is_physical,
                            z.zone_type = $zone_type,
                            z.seed_version = $seed_version,
                            z.updated_at = datetime($created_at)
                    """,
                        id=node_id,
                        name=zone_name,
                        is_physical=zone.get("is_physical", None),
                        zone_type=zone.get("zone_type", ""),
                        seed_version=zone.get("seed_version", self.version),
                        seed_source=zone.get("seed_source", self.source),
                        description=zone.get("description", ""),
                        created_at=datetime.utcnow().isoformat()
                    )

                    # 连接到部门
                    session.run("""
                        MATCH (d:DepartmentGroup {id: $dept_id})
                        MATCH (z:FunctionalZone {id: $zone_id})
                        MERGE (d)-[r:CONTAINS]->(z)
                        ON CREATE SET
                            r.seed_source = $seed_source,
                            r.seed_version = $seed_version
                    """,
                        dept_id=dept_id,
                        zone_id=node_id,
                        seed_source=self.source,
                        seed_version=self.version
                    )
                    self.stats["relationships"] += 1

                self.stats["zones"] += 1
                print(f"    [OK] Created Zone: {zone_name}")

        return zone_ids

    def create_space_nodes(self, zone_ids: Dict[str, str]):
        """创建空间节点"""
        departments = self.data.get("departments", [])

        for dept in departments:
            zones = dept.get("functional_zones", [])
            for zone in zones:
                zone_name = zone.get("name")
                zone_id = zone_ids.get(zone_name)
                if not zone_id:
                    continue

                spaces = zone.get("spaces", [])
                for space in spaces:
                    space_name = space.get("name")
                    if not space_name:
                        continue

                    node_id = self._generate_stable_id("Space", space_name)

                    if self.dry_run:
                        print(f"[DRY-RUN] Would create Space: {space_name} under {zone_name}")
                        continue

                    with self.driver.session(database=self.database) as session:
                        # 创建空间节点
                        session.run("""
                            MERGE (s:Space {id: $id})
                            ON CREATE SET
                                s.name = $name,
                                s.is_concept = $is_concept,
                                s.seed_version = $seed_version,
                                s.seed_source = $seed_source,
                                s.schema_type = '空间',
                                s.description = $description,
                                s.created_at = datetime($created_at)
                            ON MATCH SET
                                s.seed_version = $seed_version,
                                s.updated_at = datetime($created_at)
                        """,
                            id=node_id,
                            name=space_name,
                            is_concept=space.get("is_concept", True),
                            seed_version=space.get("seed_version", self.version),
                            seed_source=space.get("seed_source", self.source),
                            description=space.get("description", ""),
                            created_at=datetime.utcnow().isoformat()
                        )

                        # 连接到功能分区
                        session.run("""
                            MATCH (z:FunctionalZone {id: $zone_id})
                            MATCH (s:Space {id: $space_id})
                            MERGE (z)-[r:CONTAINS]->(s)
                            ON CREATE SET
                                r.seed_source = $seed_source,
                                r.seed_version = $seed_version
                        """,
                            zone_id=zone_id,
                            space_id=node_id,
                            seed_source=self.source,
                            seed_version=self.version
                        )
                        self.stats["relationships"] += 1

                    self.stats["spaces"] += 1

        print(f"[OK] Created {self.stats['spaces']} spaces")

    def create_design_method_categories(self) -> Dict[str, str]:
        """创建设计方法分类节点"""
        categories = self.data.get("design_method_categories", [])
        category_ids = {}

        for category in categories:
            name = category.get("name")
            if not name:
                continue

            node_id = self._generate_stable_id("DesignMethodCategory", name)
            category_ids[name] = node_id

            if self.dry_run:
                print(f"[DRY-RUN] Would create DesignMethodCategory: {name}")
                continue

            with self.driver.session(database=self.database) as session:
                session.run("""
                    MERGE (c:DesignMethodCategory {id: $id})
                    ON CREATE SET
                        c.name = $name,
                        c.problem_domain = $problem_domain,
                        c.is_concept = true,
                        c.seed_version = $seed_version,
                        c.seed_source = $seed_source,
                        c.schema_type = '设计方法分类',
                        c.description = $description,
                        c.created_at = datetime($created_at)
                    ON MATCH SET
                        c.seed_version = $seed_version,
                        c.problem_domain = $problem_domain,
                        c.updated_at = datetime($created_at)
                """,
                    id=node_id,
                    name=name,
                    problem_domain=category.get("problem_domain", ""),
                    seed_version=category.get("seed_version", self.version),
                    seed_source=category.get("seed_source", self.source),
                    description=category.get("description", ""),
                    created_at=datetime.utcnow().isoformat()
                )

            self.stats["method_categories"] += 1
            print(f"[OK] Created DesignMethodCategory: {name}")

        return category_ids

    def create_design_methods(self, category_ids: Dict[str, str]):
        """创建种子设计方法节点"""
        methods = self.data.get("design_methods_seed", [])

        for method in methods:
            title = method.get("title")
            if not title:
                continue

            node_id = self._generate_stable_id("DesignMethod", title)
            category_name = method.get("category")
            category_id = category_ids.get(category_name)

            if self.dry_run:
                print(f"[DRY-RUN] Would create DesignMethod: {title} -> {category_name}")
                continue

            with self.driver.session(database=self.database) as session:
                # 创建设计方法节点
                session.run("""
                    MERGE (m:DesignMethod {id: $id})
                    ON CREATE SET
                        m.title = $title,
                        m.is_concept = true,
                        m.seed_version = $seed_version,
                        m.seed_source = $seed_source,
                        m.schema_type = '设计方法',
                        m.category = $category,
                        m.description = $description,
                        m.methodology_type = $methodology_type,
                        m.applicable_spaces = $applicable_spaces,
                        m.applicability = $applicability,
                        m.design_phase = $design_phase,
                        m.created_at = datetime($created_at)
                    ON MATCH SET
                        m.seed_version = $seed_version,
                        m.updated_at = datetime($created_at)
                """,
                    id=node_id,
                    title=title,
                    seed_version=method.get("seed_version", self.version),
                    seed_source=method.get("seed_source", self.source),
                    category=category_name,
                    description=method.get("description", ""),
                    methodology_type=method.get("methodology_type", ""),
                    applicable_spaces=method.get("applicable_spaces", []),
                    applicability=method.get("applicability", ""),
                    design_phase=method.get("design_phase", ""),
                    created_at=datetime.utcnow().isoformat()
                )

                # 连接到分类
                if category_id:
                    session.run("""
                        MATCH (m:DesignMethod {id: $method_id})
                        MATCH (c:DesignMethodCategory {id: $category_id})
                        MERGE (m)-[r:IS_TYPE_OF]->(c)
                        ON CREATE SET
                            r.seed_source = $seed_source,
                            r.seed_version = $seed_version
                    """,
                        method_id=node_id,
                        category_id=category_id,
                        seed_source=self.source,
                        seed_version=self.version
                    )
                    self.stats["relationships"] += 1

            self.stats["methods"] += 1
            print(f"[OK] Created DesignMethod: {title}")

    def seed(self):
        """执行预注入"""
        print(f"\n{'='*60}")
        print(f"Starting Ontology Seed v{self.version}")
        print(f"Source: {self.json_path}")
        print(f"Dry Run: {self.dry_run}")
        print(f"{'='*60}\n")

        # 清空现有概念节点（如果需要）
        if self.clear_existing:
            self.clear_concept_nodes()

        # 1. 创建医院根节点
        print("\n[1/6] Creating Hospital node...")
        hospital_id = self.create_hospital_node()

        # 2. 创建部门节点
        print("\n[2/6] Creating Department nodes...")
        dept_ids = self.create_department_nodes(hospital_id)

        # 3. 创建功能分区节点
        print("\n[3/6] Creating FunctionalZone nodes...")
        zone_ids = self.create_zone_nodes(dept_ids)

        # 4. 创建空间节点
        print("\n[4/6] Creating Space nodes...")
        self.create_space_nodes(zone_ids)

        # 5. 创建设计方法分类
        print("\n[5/6] Creating DesignMethodCategory nodes...")
        category_ids = self.create_design_method_categories()

        # 6. 创建种子设计方法
        print("\n[6/6] Creating seed DesignMethod nodes...")
        self.create_design_methods(category_ids)

        # 统计
        print(f"\n{'='*60}")
        print("Seeding Complete!")
        print(f"{'='*60}")
        print(f"Hospital nodes:              {self.stats['hospital']}")
        print(f"Department nodes:            {self.stats['departments']}")
        print(f"FunctionalZone nodes:        {self.stats['zones']}")
        print(f"Space nodes:                 {self.stats['spaces']}")
        print(f"DesignMethodCategory nodes:  {self.stats['method_categories']}")
        print(f"DesignMethod nodes:          {self.stats['methods']}")
        print(f"Relationships created:       {self.stats['relationships']}")
        print(f"\nTotal concept nodes:         {sum([self.stats[k] for k in ['hospital', 'departments', 'zones', 'spaces', 'method_categories', 'methods']])}")
        print(f"{'='*60}\n")

    def close(self):
        """关闭数据库连接"""
        self.driver.close()


def main():
    parser = argparse.ArgumentParser(description="Seed ontology skeleton v2.0 into Neo4j")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅解析但不写入Neo4j",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="清空现有的概念节点",
    )
    args = parser.parse_args()

    seeder = OntologySeederV2(
        dry_run=args.dry_run,
        clear_existing=args.clear
    )
    try:
        seeder.seed()
    finally:
        seeder.close()


if __name__ == "__main__":
    main()
