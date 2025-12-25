"""
v5.4 生产级图谱清洗脚本

用途：
    - 在无需重新构建知识图谱的情况下，对现有 Neo4j 数据执行三阶段治理：
        1. 身份合并（同名/别名 + 属性归一）
        2. 孤儿节点自动挂载（关键词优先级 + 审计字段）
        3. 粒度纠偏（FunctionalZone ↔ Space 标签调整）
    - 生成 run_id / script_ver 以便回溯和批量回滚

运行：
    python backend/databases/graph/optimization/clean_existing_graph.py
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from neo4j import GraphDatabase, Session
from neo4j import GraphDatabase, Record
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

console = Console()


@dataclass(frozen=True)
class PhaseResult:
    name: str
    affected: int
    extras: Dict[str, object]


class GraphCleaner:
    """面向生产环境的知识图谱清洗器。"""

    SCRIPT_VERSION = "v5.4"

    PROMOTE_QUERY = """
        WITH
            $promote_list AS promoteList,
            $run_id AS run_id,
            $script_ver AS script_ver
        MATCH (z:FunctionalZone)
        WHERE z.name IN promoteList
            OR (
                z.name ENDS WITH '科'
                AND NOT z.name CONTAINS '病区'
                AND NOT (z)<-[:CONTAINS]-(:DepartmentGroup)
            )
            OR (
                z.name ENDS WITH '部'
                AND z.name <> '手术部'
                AND NOT (z)<-[:CONTAINS]-(:DepartmentGroup)
            )
            OR (
                z.name ENDS WITH '中心'
                AND NOT z.name IN ['配液中心', '洗消中心']
                AND NOT (z)<-[:CONTAINS]-(:DepartmentGroup)
            )
        WITH DISTINCT z, run_id, script_ver
        REMOVE z:FunctionalZone
        SET z:DepartmentGroup,
            z.auto_promoted = true,
            z.promote_run_id = run_id,
            z.promote_ver = script_ver,
            z.promoted_at = datetime()
        RETURN count(z) AS promoted
    """

    EXACT_NAME_QUERY = """
        CALL apoc.periodic.iterate(
            "MATCH (z:FunctionalZone)
             MATCH (d:DepartmentGroup {name: z.name})
             WHERE id(z) <> id(d)
             RETURN id(d) AS did, id(z) AS zid",
            "MATCH (d:DepartmentGroup) WHERE id(d) = did
             MATCH (z:FunctionalZone) WHERE id(z) = zid
             CALL apoc.refactor.mergeNodes([d, z], {properties: 'combine', mergeRels: true}) YIELD node
             RETURN count(node)",
            {batchSize: 100, parallel: false}
        ) YIELD total
        RETURN total AS merged
    """

    ALIAS_MERGE_QUERY = """
        CALL apoc.periodic.iterate(
            "UNWIND $aliases AS alias
             MATCH (z:FunctionalZone {name: alias.wrong})
             MATCH (d:DepartmentGroup {name: alias.right})
             WHERE id(z) <> id(d)
             RETURN id(d) AS did, id(z) AS zid",
            "MATCH (d:DepartmentGroup) WHERE id(d) = did
             MATCH (z:FunctionalZone) WHERE id(z) = zid
             CALL apoc.refactor.mergeNodes([d, z], {properties: 'combine', mergeRels: true}) YIELD node
             RETURN count(node)",
            {batchSize: 50, parallel: false, params: {aliases: $aliases}}
        ) YIELD total
        RETURN total AS merged
    """

    PROPERTY_NORMALIZE_QUERY = """
    CALL apoc.periodic.iterate(
        "MATCH (n:DepartmentGroup)
         WITH n, keys(n) AS allKeys
         UNWIND allKeys AS key
         WITH n, key
         WHERE NOT key IN $whitelist
             AND n[key] IS NOT NULL
         RETURN id(n) AS nid, key, n[key] AS val",
        "MATCH (n) WHERE id(n) = nid
         WITH n, key, val
         WHERE size(val) = 1
         WITH n, key, val[0] AS newVal
         CALL apoc.create.setProperty(n, key, newVal) YIELD node
         RETURN node",
        {batchSize: 50, parallel: false, params: {whitelist: $whitelist}, iterateList: true}
    ) YIELD batches, total, errorMessages
    RETURN total AS normalized
    """

    AUTO_ATTACH_QUERY = """
        WITH $rules AS rules, $run_id AS run_id, $script_ver AS script_ver
        MATCH (z:FunctionalZone)
        WHERE NOT (z)<-[:CONTAINS]-(:DepartmentGroup)
        WITH z, run_id, script_ver,
             head([r IN rules WHERE toUpper(z.name) CONTAINS r.key_upper]) AS bestMatch
        WHERE bestMatch IS NOT NULL
        MATCH (d:DepartmentGroup {name: bestMatch.dept})
        MERGE (d)-[rel:CONTAINS]->(z)
        ON CREATE SET
            rel.matched_by = bestMatch.key,
            rel.auto_generated = true,
            rel.script_ver = script_ver,
            rel.run_id = run_id,
            rel.created_at = datetime()
        RETURN count(rel) AS relationships,
               collect(DISTINCT bestMatch.key) AS triggered_rules
    """

    DOWNGRADE_QUERY = """
    WITH
        $force_list AS forceList,
        $suffix_list AS suffixList,
        $protected_list AS protectedList,
        $run_id AS run_id,
        $script_ver AS script_ver
    MATCH (n:FunctionalZone)
    WHERE NOT (n)<-[:CONTAINS]-(:DepartmentGroup)
      AND (
        (n.name IN forceList)
        OR (
            any(s IN suffixList WHERE n.name ENDS WITH s)
            AND NOT any(p IN protectedList WHERE n.name CONTAINS p)
            AND NOT n.name CONTAINS "科"
            AND NOT n.name CONTAINS "部"
        )
      )
    WITH DISTINCT n, run_id, script_ver
    REMOVE n:FunctionalZone
    SET n:Space,
        n.auto_downgrade = true,
        n.downgrade_run_id = run_id,
        n.downgrade_ver = script_ver
    RETURN count(n) AS downgraded

    """

    DOWNGRADE_ZONE_QUERY = """
        WITH $run_id AS run_id, $script_ver AS script_ver
        MATCH (n:FunctionalZone)
        WHERE NOT (n)<-[:CONTAINS]-(:DepartmentGroup)
          AND (
            n.name ENDS WITH '区'
            OR n.name IN [
                '清洁区','污染区','半污染区','医辅区','家属区',
                '感染区','中间护理区','加强护理区','新生儿监护病区'
            ]
          )
          AND NOT n.name CONTAINS '病区'
          AND NOT n.name CONTAINS '科'
          AND NOT n.name CONTAINS '部'
        WITH DISTINCT n, run_id, script_ver
        REMOVE n:FunctionalZone
        SET n:Space,
            n.auto_downgrade = true,
            n.downgrade_run_id = run_id,
            n.downgrade_ver = script_ver,
            n.downgrade_reason = 'zone_suffix'
        RETURN count(n) AS downgraded
    """

    REPORT_QUERY = """
        CALL {
            MATCH (z:FunctionalZone)
            WHERE NOT (z)<-[:CONTAINS]-(:DepartmentGroup)
            RETURN count(z) AS orphan_count
        }
        CALL {
            MATCH (d:DepartmentGroup)-[r:CONTAINS]->()
            WHERE r.run_id = $run_id
            RETURN count(r) AS new_links
        }
        CALL {
            MATCH (s:Space)
            WHERE s.downgrade_run_id = $run_id
            RETURN count(s) AS downgraded_spaces
        }
        CALL {
            MATCH (s:Space)
            RETURN count(s) AS total_spaces
        }
        RETURN orphan_count, new_links, downgraded_spaces, total_spaces
    """

    ALIASES = [
        {"wrong": "急诊科", "right": "急诊部"},
        {"wrong": "门诊科", "right": "门诊部"},
        {"wrong": "医技科室", "right": "医技部"},
        {"wrong": "检验", "right": "检验科"},
        {"wrong": "放射", "right": "放射科"},
        {"wrong": "病理", "right": "病理科"},
        {"wrong": "康复部", "right": "康复医学科"},
        {"wrong": "手术部", "right": "医技部"},
        {"wrong": "重症监护", "right": "重症医学科"},
    ]

    PROPERTY_WHITELIST = [
        "regulatory_requirements",
        "media_refs",
    ]

    PROMOTE_TO_DEPARTMENT = [
        "儿童康复科",
        "儿童保健科",
        "放射科",
        "放射诊断科",
        "口腔科",
        "口腔诊疗中心",
        "牙体科",
        "康复医学科",
        "康复部",
        "检验医学科",
        "检验科",
        "病理科",
        "区域教育中心",
        "中心实验室",
        "实验室中心",
        "内镜中心",
        "静脉药物配液中心",
        "后勤保障",
        "科研教学",
        "日间手术中心",
        "健康管理中心",
        "预防保健科",
    ]

    PRIORITY_RULES = [
        # High priority
        {"key": "药房", "dept": "药剂科"},
        {"key": "药库", "dept": "药剂科"},
        {"key": "静配", "dept": "药剂科"},
        {"key": "检验", "dept": "检验科"},
        {"key": "化验", "dept": "检验科"},
        {"key": "病理", "dept": "病理科"},
        {"key": "CT", "dept": "医学影像科"},
        {"key": "MR", "dept": "医学影像科"},
        {"key": "影像", "dept": "医学影像科"},
        {"key": "超声", "dept": "超声科"},
        {"key": "供应", "dept": "消毒供应中心"},
        {"key": "消毒", "dept": "消毒供应中心"},
        {"key": "膳食", "dept": "营养部"},
        {"key": "食堂", "dept": "营养部"},
        {"key": "厨房", "dept": "营养部"},
        # Medium priority
        {"key": "ICU", "dept": "重症医学科"},
        {"key": "CCU", "dept": "重症医学科"},
        {"key": "NICU", "dept": "儿科"},
        {"key": "产房", "dept": "产科"},
        {"key": "分娩", "dept": "产科"},
        {"key": "透析", "dept": "血液透析中心"},
        # Low priority
        {"key": "急诊", "dept": "急诊部"},
        {"key": "急救", "dept": "急诊部"},
        {"key": "120", "dept": "急诊部"},
        {"key": "门诊", "dept": "门诊部"},
        {"key": "诊室", "dept": "门诊部"},
        {"key": "候诊", "dept": "门诊部"},
        {"key": "住院", "dept": "住院部"},
        {"key": "病房", "dept": "住院部"},
        {"key": "护理单元", "dept": "住院部"},
        {"key": "儿科", "dept": "儿科"},
        {"key": "妇科", "dept": "妇科"},
        # Additional mappings
        {"key": "骨科病区", "dept": "骨科"},
        {"key": "神经科病区", "dept": "神经内科"},
        {"key": "心血管科病区", "dept": "心血管内科"},
        {"key": "精神科病区", "dept": "精神科"},
        {"key": "感染病区", "dept": "感染科"},
        {"key": "感染单元", "dept": "感染科"},
        {"key": "卒中单元", "dept": "神经内科"},
        {"key": "急性卒中单元", "dept": "神经内科"},
        {"key": "实验室", "dept": "检验科"},
        {"key": "PCR实验室", "dept": "检验科"},
        {"key": "PCR", "dept": "检验科"},
        {"key": "口腔", "dept": "口腔科"},
        {"key": "牙体", "dept": "口腔科"},
        {"key": "康复", "dept": "康复医学科"},
        {"key": "内镜", "dept": "内镜中心"},
        {"key": "行政", "dept": "行政管理"},
        {"key": "教育", "dept": "教育培训"},
        {"key": "介入", "dept": "介入科"},
        {"key": "化疗", "dept": "肿瘤科"},
        {"key": "医技", "dept": "医技部"},
        {"key": "放射治疗", "dept": "放射科"},
        {"key": "放疗", "dept": "放射科"},
        {"key": "核医学", "dept": "核医学科"},
        {"key": "电生理", "dept": "心血管内科"},
        {"key": "内窥镜", "dept": "内镜中心"},
        {"key": "肿瘤", "dept": "肿瘤科"},
        {"key": "血库", "dept": "输血科"},
        {"key": "留观", "dept": "急诊部"},
        {"key": "普通门", "dept": "门诊部"},
        {"key": "呼吸道发热门", "dept": "急诊部"},
        {"key": "分诊", "dept": "门诊部"},
        {"key": "日间", "dept": "日间手术中心"},
        {"key": "预检", "dept": "急诊部"},
        {"key": "问诊", "dept": "门诊部"},
        {"key": "诊疗单元", "dept": "门诊部"},
        {"key": "封闭式单元", "dept": "精神科"},
        {"key": "开放式单元", "dept": "精神科"},
        {"key": "隔离单元", "dept": "感染科"},
        {"key": "隔离观察", "dept": "感染科"},
        {"key": "物质依赖", "dept": "精神科"},
        {"key": "老年痴呆", "dept": "老年科"},
        {"key": "青春期", "dept": "儿科"},
        {"key": "躯体合并症", "dept": "综合内科"},
        {"key": "办公楼", "dept": "行政管理"},
        {"key": "教学楼", "dept": "科研教学"},
        {"key": "后勤", "dept": "后勤保障"},
        {"key": "暖通", "dept": "后勤保障"},
        {"key": "给排水", "dept": "后勤保障"},
        {"key": "电气", "dept": "后勤保障"},
        {"key": "污水", "dept": "后勤保障"},
        {"key": "废物", "dept": "后勤保障"},
        {"key": "洗衣", "dept": "后勤保障"},
        {"key": "物流", "dept": "后勤保障"},
        {"key": "停车", "dept": "后勤保障"},
        {"key": "科研", "dept": "科研教学"},
        {"key": "教学", "dept": "科研教学"},
        {"key": "研究", "dept": "科研教学"},
        {"key": "培养", "dept": "科研教学"},
        {"key": "管理", "dept": "行政管理"},
        {"key": "办公", "dept": "行政管理"},
        {"key": "预防保健", "dept": "预防保健科"},
        {"key": "健康管理", "dept": "健康管理中心"},
        # === v5.4 新增挂载规则 ===
        # 手术类
        {"key": "手术室", "dept": "手术部"},
        {"key": "手术综合体", "dept": "手术部"},
        {"key": "OR", "dept": "手术部"},
        {"key": "中央手术", "dept": "手术部"},
        {"key": "复合手术", "dept": "手术部"},
        {"key": "多联手术", "dept": "手术部"},
        {"key": "通仓手术", "dept": "手术部"},
        {"key": "手术部门", "dept": "手术部"},
        # 重症监护类
        {"key": "重症监护室", "dept": "重症医学科"},
        {"key": "重症监护单元", "dept": "重症医学科"},
        {"key": "IMCU", "dept": "重症医学科"},
        {"key": "PACU", "dept": "重症医学科"},
        {"key": "新生儿重症监护", "dept": "儿科"},
        # 单元类补充
        {"key": "B单元", "dept": "住院部"},
        # 部门类
        {"key": "传染病部门", "dept": "感染科"},
        {"key": "灭菌部门", "dept": "消毒供应中心"},
        {"key": "后勤部门", "dept": "后勤保障"},
        # 设施类（挂载到后勤保障）
        {"key": "保障系统", "dept": "后勤保障"},
        {"key": "垃圾污水", "dept": "后勤保障"},
        {"key": "高压线设施", "dept": "后勤保障"},
        {"key": "技术保障设施", "dept": "后勤保障"},
        {"key": "人员运输设施", "dept": "后勤保障"},
        {"key": "给水排水设施", "dept": "后勤保障"},
        {"key": "消防设施", "dept": "后勤保障"},
        {"key": "废水处理", "dept": "后勤保障"},
        {"key": "采暖通风", "dept": "后勤保障"},
        {"key": "智能化系统", "dept": "后勤保障"},
        {"key": "医用气体", "dept": "后勤保障"},
        {"key": "蒸汽系统", "dept": "后勤保障"},
        {"key": "直接饮用水", "dept": "后勤保障"},
        {"key": "空气调节", "dept": "后勤保障"},
        {"key": "热水系统", "dept": "后勤保障"},
        # 科室/建筑类
        {"key": "产科楼", "dept": "产科"},
        {"key": "产科室", "dept": "产科"},
        {"key": "临床科室", "dept": "门诊部"},
        {"key": "外科操作用房", "dept": "外科"},
        {"key": "外科操作室", "dept": "外科"},
        {"key": "外科操作环境", "dept": "外科"},
        # 楼宇建筑
        {"key": "主楼", "dept": "行政管理"},
        {"key": "治疗大楼", "dept": "医技部"},
        # 其他
        {"key": "特殊检查", "dept": "医技部"},
        {"key": "特诊种植", "dept": "口腔科"},
        {"key": "儿研所", "dept": "儿科"},
        {"key": "院内生活", "dept": "后勤保障"},
    ]

    FORCE_SPACE = [
        "护士站",
        "服务台",
        "锅炉房",
        "配液中心",
        "候诊室",
        "治疗室",
        "换药室",
        "注射室",
        "诊区",
        "候诊厅",
        "清洁区",
        "污染区",
        "半污染区",
        "医辅区",
        "家属区",
        "感染区",
        "中间护理区",
        "加强护理区",
        "新生儿监护病区",
        "公共区域",
        "公共服务",
        "服务区域",
        "交通区域",
        "患者区域",
        "限制区域",
        "负压区域",
        "无菌环境",
        "走廊",
        "清洁区走廊",
        "半清洁区走廊",
        "污物走廊",
        "无菌走廊",
        "中央花园",
        "花园",
        "庭院",
        "公园",
        "环境",
        "通道",
        "中央通道",
        "林荫大道",
        "医疗街",
        "医院街",
        "院区出入口",
        "一层",
        "二层",
        "三层",
        "四层",
        "五层",
        "地下一层",
        # === v5.4 新增降级规则 ===
        # 楼层类
        "b一层",
        "上层",
        "底层",
        # 通道类
        "病患者使用通道",
        "医务工作人员通道",
        "体检通道",
        "循环通道",
        "设备移动通道",
        "运输",
        # 区域/场所类
        "少年儿童活动密集场所",
        "一类医疗场所",
        "二类医疗场所",
        "医疗场所",
        "场所",
        "备用诊断和治疗区域",
        "呼吸道传染病人收治区域",
        "咨询与检查区域",
        "检查区域",
        # 处理类
        "污物处理",
        "内部庭院",
    ]

    SUFFIX_SPACE = ["室", "房", "间", "厅", "台", "站", "中心", "区"]

    PROTECTED_SPACE = [
        "病房",
        "监护室",
        "重症监护室",
        "手术室",
        "分娩室",
        "待产室",
        "IMCU",
        "ICU",
        "AMU",
        "PACU",
        "OR",
        "Intensive Care Unit",
        # 特殊中心
        "配液中心",
        "洗消中心",
        "内镜中心",
        # 实验室
        "实验室",
        # 病区 - 用通用词保护所有病区
        "病区",
    ]

    def __init__(self) -> None:
        uri = os.getenv("NEO4J_URI")
        user = os.getenv("NEO4J_USER")
        password = os.getenv("NEO4J_PASSWORD")

        if not all([uri, user, password]):
            raise RuntimeError("缺少 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD 环境变量,无法连接 Neo4j。")

        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.run_id = str(uuid.uuid4())
        self._prepare_rules()

    def _prepare_rules(self) -> None:
        """为规则添加大写版本以便匹配英文大小写。"""
        for rule in self.PRIORITY_RULES:
            key = rule["key"]
            rule["key_upper"] = key.upper()

    def _exec(self, session: Session, query: str, **params) -> Record:
        return session.run(query, **params).single()

    def _run_phase_zero(self, session: Session) -> PhaseResult:
        console.print("[cyan]Phase 0 · 科室节点升级[/cyan]")
        result = self._exec(
            session,
            self.PROMOTE_QUERY,
            promote_list=self.PROMOTE_TO_DEPARTMENT,
            run_id=self.run_id,
            script_ver=self.SCRIPT_VERSION,
        )
        return PhaseResult(name="节点升级", affected=result["promoted"], extras={})

    def _run_phase_one(self, session: Session) -> List[PhaseResult]:
        console.print("[cyan]Phase 1 · 身份合并 & 属性归一[/cyan]")
        results: List[PhaseResult] = []

        exact = self._exec(session, self.EXACT_NAME_QUERY)
        results.append(PhaseResult(name="同名合并", affected=exact["merged"], extras={}))

        alias = self._exec(session, self.ALIAS_MERGE_QUERY, aliases=self.ALIASES)
        results.append(PhaseResult(name="别名合并", affected=alias["merged"], extras={}))

        normalized = self._exec(
            session,
            self.PROPERTY_NORMALIZE_QUERY,
            whitelist=self.PROPERTY_WHITELIST,
        )
        results.append(PhaseResult(name="属性归一", affected=normalized["normalized"], extras={}))

        return results

    def _run_phase_two(self, session: Session) -> PhaseResult:
        console.print("\n[cyan]Phase 2 · 孤儿自动挂载[/cyan]")
        result = self._exec(
            session,
            self.AUTO_ATTACH_QUERY,
            rules=self.PRIORITY_RULES,
            run_id=self.run_id,
            script_ver=self.SCRIPT_VERSION,
        )
        triggered = result.get("triggered_rules") or []
        return PhaseResult(
            name="孤儿挂载",
            affected=result["relationships"],
            extras={"rules": triggered},
        )

    def _run_phase_three(self, session: Session) -> List[PhaseResult]:
        console.print("\n[cyan]Phase 3 · 粒度纠偏[/cyan]")
        results: List[PhaseResult] = []

        generic = self._exec(
            session,
            self.DOWNGRADE_QUERY,
            force_list=self.FORCE_SPACE,
            suffix_list=self.SUFFIX_SPACE,
            protected_list=self.PROTECTED_SPACE,
            run_id=self.run_id,
            script_ver=self.SCRIPT_VERSION,
        )
        results.append(PhaseResult("通用降级", generic["downgraded"], {}))

        zone_specific = self._exec(
            session,
            self.DOWNGRADE_ZONE_QUERY,
            run_id=self.run_id,
            script_ver=self.SCRIPT_VERSION,
        )
        results.append(PhaseResult("区类降级", zone_specific["downgraded"], {}))

        return results

    def _render_phase_summary(self, phase_results: List[PhaseResult]) -> None:
        table = Table(
            title="阶段执行结果",
            show_header=True,
            header_style="bold cyan",
            box=box.SIMPLE_HEAD,
        )
        table.add_column("阶段 / 子任务", style="magenta")
        table.add_column("影响数量", justify="right")
        table.add_column("额外信息", overflow="fold")

        for res in phase_results:
            extras = ", ".join(f"{k}: {v}" for k, v in res.extras.items() if v)
            table.add_row(res.name, f"{res.affected:,}", extras or "-")

        console.print(table)

    def _report(self, session: Session) -> Dict[str, object]:
        record = self._exec(session, self.REPORT_QUERY, run_id=self.run_id)
        return record.data()

    def _render_report(self, data: Dict[str, object]) -> None:
        table = Table(title="清洗审计", show_header=False, box=box.SIMPLE, padding=(0, 1))
        table.add_row("本次 run_id", self.run_id)
        table.add_row("脚本版本", self.SCRIPT_VERSION)
        table.add_row("新增挂载关系", str(data["new_links"]))
        table.add_row("降级为 Space", str(data["downgraded_spaces"]))
        table.add_row("当前 Space 总数", str(data["total_spaces"]))
        table.add_row("剩余孤儿数", str(data["orphan_count"]))
        console.print(table)

    def run(self) -> None:
        console.print(Panel.fit(f"[bold cyan]医疗建筑知识图谱 · {self.SCRIPT_VERSION} 清洗脚本[/bold cyan]", border_style="cyan"))
        start = datetime.now()
        phase_results: List[PhaseResult] = []

        with self.driver.session() as session:
            phase_results.append(self._run_phase_zero(session))
            phase_results.extend(self._run_phase_one(session))
            phase_results.append(self._run_phase_two(session))
            phase_results.extend(self._run_phase_three(session))

            console.print()
            self._render_phase_summary(phase_results)

            report = self._report(session)
            self._render_report(report)

        elapsed = (datetime.now() - start).total_seconds()
        console.print(
            Panel.fit(
                f"[bold green]✅ 完成！[/bold green]\n"
                f"run_id: {self.run_id}\n"
                f"耗时：{elapsed:.1f}s",
                border_style="green",
            )
        )

    def close(self) -> None:
        self.driver.close()


def main() -> None:
    cleaner = GraphCleaner()
    try:
        cleaner.run()
    finally:
        cleaner.close()


if __name__ == "__main__":
    main()