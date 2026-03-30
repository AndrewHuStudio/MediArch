from __future__ import annotations

from pathlib import Path
import sys
project_root = Path(__file__).resolve().parents[4]
sys.path.append(str(project_root))
import argparse
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple
import re

from backend.env_loader import load_dotenv
from neo4j import GraphDatabase

from backend.databases.graph.optimization.name_normalizer import (
    canonicalize,
    compose_scope_key,
    load_alias_map,
)


@dataclass
class ParsedNode:
    """Markdown 中解析出的节点"""

    name: str
    level: int
    parent: Optional["ParsedNode"] = None
    node_id: Optional[str] = None
    schema_type: Optional[str] = None
    neo_label: Optional[str] = None

    def ancestors(self) -> List[str]:
        chain: List[str] = []
        current = self.parent
        while current:
            chain.append(current.name)
            current = current.parent
        return list(reversed(chain))


class OntologySeeder:
    """将 Markdown 骨架注入 Neo4j"""

    LABEL_MAP = {
        "医院": "Hospital",
        "部门": "DepartmentGroup",
        "功能分区": "FunctionalZone",
        "空间": "Space",
    }
    TABLE_SEPARATOR_PATTERN = re.compile(r"^\|\s*[:\-]+\s*(\|\s*[:\-]+\s*)+\|$")

    def __init__(
        self,
        markdown_path: Path,
        hospital_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> None:
        load_dotenv()
        self.markdown_path = markdown_path
        self.dry_run = dry_run
        self.markdown_text = self.markdown_path.read_text(encoding="utf-8")
        self.hospital_name = hospital_name or self._extract_title() or self.markdown_path.stem
        self.md_hash = hashlib.sha256(self.markdown_text.encode("utf-8")).hexdigest()[:12]
        self.seed_version = f"{self.markdown_path.name}:{self.md_hash}"
        self.seed_source = str(self.markdown_path)
        self.alias_map = load_alias_map(os.getenv("KG_ALIAS_PATH", ""))
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
        )

    def _extract_title(self) -> Optional[str]:
        for line in self.markdown_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if title:
                    return title
        return None

    def _schema_type_for_level(self, level: int) -> Tuple[str, str]:
        if level < 0:
            return "医院", self.LABEL_MAP["医院"]
        if level == 0:
            return "部门", self.LABEL_MAP["部门"]
        if level == 1:
            return "功能分区", self.LABEL_MAP["功能分区"]
        return "空间", self.LABEL_MAP["空间"]

    def _generate_stable_entity_id(
        self,
        name: str,
        entity_type: str,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        canonical = canonicalize(name, entity_type, self.alias_map)
        scope = compose_scope_key(scope_chain or [])
        unique_key = f"{entity_type}:{canonical}|{scope}".lower().strip()
        digest = hashlib.sha256(unique_key.encode("utf-8")).hexdigest()[:16]
        return f"entity_{digest}"

    def _clean_inline_text(self, text: str) -> str:
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
        cleaned = cleaned.replace("*", "").replace("_", "")
        return cleaned.strip()

    def _parse_markdown(self) -> List[ParsedNode]:
        root = ParsedNode(name=self.hospital_name, level=-1, parent=None)
        nodes: List[ParsedNode] = [root]
        stack: List[Tuple[ParsedNode, int]] = [(root, -1)]

        for raw_line in self.markdown_text.splitlines():
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("|") and stripped.endswith("|"):
                if stripped.count("|") < 3:
                    continue
                if self.TABLE_SEPARATOR_PATTERN.match(stripped):
                    continue
                row = stripped.strip("|")
                cells: List[str] = []
                for cell in row.split("|"):
                    raw_cell = cell.strip()
                    if not raw_cell:
                        continue
                    if set(raw_cell) <= {":", "-", " "}:
                        continue
                    cleaned_cell = self._clean_inline_text(raw_cell)
                    trimmed = cleaned_cell.strip(":- ").strip()
                    if not trimmed:
                        continue
                    cells.append(trimmed)
                if not cells:
                    continue
                parent = stack[-1][0] if stack else root
                current_level = (stack[-1][1] + 1) if stack else 0
                for cell in cells:
                    node = ParsedNode(name=cell, level=current_level, parent=parent)
                    nodes.append(node)
                continue
            lstrip = line.lstrip(" \t")
            if not lstrip.startswith(("-", "*", "+")):
                ordered_match = re.match(r"(\d+)[\.\)]\s+(.*)", lstrip)
                if not ordered_match:
                    continue
                content = ordered_match.group(2).strip()
            else:
                content = lstrip[1:].strip()
            if not content:
                continue
            content = self._clean_inline_text(content)
            if not content:
                continue
            indent = len(line) - len(lstrip)
            level = max(indent // 2, 0)
            while stack and stack[-1][1] >= level:
                stack.pop()
            parent = stack[-1][0] if stack else root
            node = ParsedNode(name=content, level=level, parent=parent)
            nodes.append(node)
            stack.append((node, level))
        return nodes

    def _prepare_nodes(self, parsed: List[ParsedNode]) -> List[ParsedNode]:
        prepared: List[ParsedNode] = []
        for node in parsed:
            schema_type_cn, neo_label = self._schema_type_for_level(node.level)
            scope_chain = node.ancestors()
            node_id = self._generate_stable_entity_id(node.name, schema_type_cn, scope_chain)
            node.node_id = node_id
            node.schema_type = schema_type_cn
            node.neo_label = neo_label
            prepared.append(node)
        return prepared

    def _upsert_node(self, node: ParsedNode) -> None:
        assert node.node_id and node.schema_type and node.neo_label
        scope_value = " > ".join(node.ancestors())
        with self.driver.session(database=self.database) as session:
            session.run(
                f"""
                MERGE (n:`{node.neo_label}` {{id: $id}})
                ON CREATE SET
                    n.name = $name,
                    n.schema_type = $schema_type,
                    n.seed_source = $seed_source,
                    n.seed_version = $seed_version,
                    n.seed_scope = $seed_scope,
                    n.created_at = datetime($created_at)
                ON MATCH SET
                    n.seed_source = $seed_source,
                    n.seed_version = $seed_version,
                    n.seed_scope = $seed_scope,
                    n.updated_at = datetime($created_at)
                """,
                id=node.node_id,
                name=node.name,
                schema_type=node.schema_type,
                seed_source=self.seed_source,
                seed_version=self.seed_version,
                seed_scope=scope_value,
                created_at=datetime.utcnow().isoformat(),
            )

    def _link_parent(self, node: ParsedNode) -> None:
        if not node.parent or not node.parent.node_id or not node.node_id:
            return
        parent_label = node.parent.neo_label or self.LABEL_MAP["功能分区"]
        with self.driver.session(database=self.database) as session:
            session.run(
                f"""
                MATCH (p:`{parent_label}` {{id: $parent_id}})
                MATCH (c:`{node.neo_label}` {{id: $child_id}})
                MERGE (p)-[r:CONTAINS]->(c)
                ON CREATE SET
                    r.seed_source = $seed_source,
                    r.seed_version = $seed_version
                ON MATCH SET
                    r.seed_version = $seed_version
                """,
                parent_id=node.parent.node_id,
                child_id=node.node_id,
                seed_source=self.seed_source,
                seed_version=self.seed_version,
            )

    def seed(self) -> None:
        parsed = self._parse_markdown()
        prepared = self._prepare_nodes(parsed)
        if self.dry_run:
            print(f"[DRY-RUN] Parsed {len(prepared)} nodes from {self.markdown_path}")
            for node in prepared[:10]:
                scope = " > ".join(node.ancestors())
                print(f"  - {node.name} ({node.schema_type}) [{scope}] -> {node.node_id}")
            if len(prepared) > 10:
                print(f"  ... {len(prepared) - 10} more nodes")
            return

        created = 0
        for node in prepared:
            self._upsert_node(node)
            self._link_parent(node)
            created += 1
        print(f"[OK] Seeded {created} nodes into Neo4j from {self.markdown_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ontology skeleton into Neo4j")
    parser.add_argument(
        "--markdown",
        default="backend/databases/graph/schemas/医院功能分区图.md",
        help="Markdown 文件路径（默认：schemas/医院功能分区图.md）",
    )
    parser.add_argument(
        "--hospital",
        default=None,
        help="根医院名称（默认使用 Markdown 标题或文件名）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅解析但不写入 Neo4j",
    )
    args = parser.parse_args()

    markdown_path = Path(args.markdown)
    if not markdown_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {markdown_path}")

    seeder = OntologySeeder(markdown_path=markdown_path, hospital_name=args.hospital, dry_run=args.dry_run)
    seeder.seed()


if __name__ == "__main__":
    main()
