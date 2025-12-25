# app/tools/graph_search.py
from typing import Optional, Tuple, List, Dict, Any
import re
from backend.app.services.graph_retriever import GraphRetriever
from backend.app.services.evidence_formatter import enhance_graph_search_result
from langchain_core.tools import tool
import json

retriever = GraphRetriever()

def _candidate_queries(text: str) -> list[str]:
    s = re.sub(r"[，。；;,.!?！？\s]+", " ", text or "").strip()
    tokens = [t for t in s.split(" ") if len(t) >= 3]
    cjks = re.findall(r"[\u4e00-\u9fff]{3,}", text or "")
    cands: list[str] = []
    for t in tokens + cjks:
        if t and t not in cands:
            cands.append(t)
    return cands[:4]

def _graph_search_core(query: str, depth: int = 2, k_edges: int = 200, seeds_count: int = 3) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """共享的图检索核心：返回 (edges, specs, seeds)。
    不做格式化，由上层工具决定输出文本或 JSON。
    """
    candidates = [query] + _candidate_queries(query)
    nodes: List[Dict[str, Any]] = []
    for q in candidates:
        nodes = retriever.search_nodes(q, k=8)
        if nodes:
            break
    if not nodes:
        return [], [], []
    seeds = [n.get("slug") or n.get("name") for n in nodes[:seeds_count]]
    edges = retriever.expand_neighborhood(seeds, depth=depth, k_edges=k_edges)
    try:
        specs = retriever.search_related_specs(query=query, seeds=seeds)
    except Exception:
        specs = []
    return edges, specs, seeds

@tool("graph_search")
def graph_search_tool(query: str) -> str:
    """在医院知识图谱中进行检索，返回自然语言格式的结构化信息和相关设计规范。

    输入: 中文或英文关键词（如"CSSD 功能分区"/"门诊药房位置"/"急诊科设计要求"）
    输出: 返回经过自然语言转述的医院结构关系信息，并根据查询内容自动补充相关的设计规范和标准要求。
    """
    try:
        edges, specs, _seeds = _graph_search_core(query, depth=2, k_edges=200, seeds_count=3)
        if not edges and not specs:
            return "抱歉，在知识图谱中未找到相关的医院结构信息。"
        raw_result = retriever.to_text_context(edges)
        formatted_result = enhance_graph_search_result(raw_result)
        if specs:
            lines = []
            for s in specs:
                name = s.get("name", "规范")
                content = s.get("content", "")
                lines.append(f"**{name}**\n{content}" if content else f"**{name}**")
            formatted_result += "\n\n## 相关设计规范\n" + "\n".join(lines)
        return formatted_result
    except Exception as e:
        print(f"Error in graph_search_tool: {e}")
        return "抱歉，在查询知识图谱时遇到内部错误，请稍后再试。"


@tool("multi_hop_reasoning")
def multi_hop_reasoning_tool(start_concept: str, end_concept: Optional[str] = None, min_hops: int = 2, max_hops: int = 5) -> str:
    """执行多跳推理查询，发现概念之间的深层关联路径。
    
    适用场景：
    - 追溯设计依据："手术室设计的政策依据是什么？"（2-4跳）
    - 影响因素分析："哪些因素会影响ICU的空间布局？"（2-3跳）
    - 概念关联："感染控制和空调系统有什么关系？"（2-5跳）
    
    输入参数：
    - start_concept: 起始概念关键词（如"手术室"）
    - end_concept: 目标概念关键词（可选，如"建筑规范"）
    - min_hops: 最小跳数（默认2）
    - max_hops: 最大跳数（默认5）
    
    输出: 返回推理路径的自然语言描述，展示概念之间的逻辑链条。
    """
    try:
        paths = retriever.multi_hop_reasoning(start_concept, end_concept, min_hops, max_hops)
        if not paths:
            return f"未找到从'{start_concept}'到'{end_concept or '任意概念'}'的{min_hops}-{max_hops}跳推理路径。"
        result_lines = [f"发现 {len(paths)} 条推理路径：\n"]
        for i, path in enumerate(paths, 1):
            nodes = path.get('nodes', [])
            rels = path.get('rels', [])
            hops = path.get('hops', 0)
            path_desc = f"\n路径 {i} ({hops}跳):\n  "
            path_steps = []
            for j, node in enumerate(nodes):
                path_steps.append(f"{node.get('name', node.get('slug'))}")
                if j < len(rels):
                    rel_type = rels[j].get('type', '关联')
                    path_steps.append(f" --[{rel_type}]--> ")
            result_lines.append(path_desc + "".join(path_steps))
        return "\n".join(result_lines)
    except Exception as e:
        print(f"Error in multi_hop_reasoning_tool: {e}")
        return "抱歉，在推理查询时遇到内部错误，请稍后再试。"


@tool("find_related_concepts")
def find_related_concepts_tool(concept: str, min_connections: int = 2) -> str:
    """通过共同邻居发现与指定概念潜在相关的其他概念。
    
    适用场景：
    - 概念扩展："还有哪些设计要素与'无障碍设计'相关？"
    - 关联发现："哪些空间和'感染控制'有密切关系？"
    - 知识补充："围绕'急诊科'有哪些相关概念？"
    
    输入参数：
    - concept: 查询概念关键词
    - min_connections: 最小共同连接数（默认2，表示至少有2个共同邻居）
    
    输出: 返回相关概念列表，按关联强度排序。
    """
    try:
        related = retriever.find_related_entities(concept, min_connections)
        if not related:
            return f"未发现与'{concept}'有{min_connections}个以上共同连接的相关概念。"
        result_lines = [f"发现 {len(related)} 个与'{concept}'相关的概念：\n"]
        for entity in related:
            name = entity.get('name', entity.get('slug'))
            label = entity.get('label', '未知类型')
            common_count = entity.get('common_count', 0)
            result_lines.append(f"  - {name} ({label}) - {common_count}个共同连接")
        return "\n".join(result_lines)
    except Exception as e:
        print(f"Error in find_related_concepts_tool: {e}")
        return "抱歉，在相关概念检索时遇到内部错误，请稍后再试。"


@tool("shortest_path_search")
def shortest_path_tool(source_concept: str, target_concept: str, max_length: int = 6) -> str:
    """查找两个概念之间的最短关联路径。
    
    适用场景：
    - 快速定位关系："'门诊大厅'和'消防规范'之间有什么关系？"
    - 依据追溯："从'手术室'到'建筑标准'的依据链条？"
    - 概念桥接："如何从'患者体验'推导到'空间设计'？"
    
    输入参数：
    - source_concept: 起始概念
    - target_concept: 目标概念
    - max_length: 最大路径长度（默认6跳）
    
    输出: 返回最短路径的自然语言描述。
    """
    try:
        # 让 retriever 侧处理模糊匹配与最短路，以一次查询完成
        # 保持现有 retriever.shortest_path 接口，但上层只传入概念字符串
        # 为兼容，先尝试直接按概念名作为 slug/name 传入
        path_data = retriever.shortest_path(source_concept, target_concept, max_length)
        if not path_data:
            # 回退：各自取最可能节点 slug（一次各自检索）
            source_nodes = retriever.search_nodes(source_concept, k=1)
            target_nodes = retriever.search_nodes(target_concept, k=1)
            if not source_nodes:
                return f"未找到与'{source_concept}'匹配的概念。"
            if not target_nodes:
                return f"未找到与'{target_concept}'匹配的概念。"
            path_data = retriever.shortest_path(source_nodes[0]['slug'], target_nodes[0]['slug'], max_length)
        if not path_data:
            return f"未找到从'{source_concept}'到'{target_concept}'的路径（最大{max_length}跳）。"
        nodes = path_data.get('nodes', [])
        rels = path_data.get('rels', [])
        if not nodes:
            return "路径数据异常。"
        result = f"最短路径 ({len(nodes)-1}跳):\n\n"
        path_steps = []
        for i, node in enumerate(nodes):
            name = node.get('name', node.get('slug'))
            label = node.get('label', '')
            path_steps.append(f"{name} ({label})")
            if i < len(rels):
                rel_type = rels[i].get('type', '关联')
                path_steps.append(f"\n  ↓ [{rel_type}]\n")
        result += "".join(path_steps)
        return result
    except Exception as e:
        print(f"Error in shortest_path_tool: {e}")
        return "抱歉，在最短路径查询时遇到内部错误，请稍后再试。"


@tool("list_neighbors")
def list_neighbors_tool(concept: str, limit: int = 40) -> str:
    """返回某概念的直接邻接清单（1跳），用于快速核对与调试。"""
    try:
        neighbors = retriever.list_neighbors(concept, max_neighbors=limit)
        if not neighbors:
            return f"未找到与“{concept}”直接相连的邻居。"
        lines = [f"“{concept}”的直接邻居（最多{limit}个）："]
        for i, n in enumerate(neighbors, 1):
            lines.append(
                f"{i}. {n.get('neighbor_name')} ({n.get('neighbor_label')}) --[{n.get('rel_type')},{n.get('direction')}]"
            )
        return "\n".join(lines)
    except Exception as e:
        print(f"Error in list_neighbors_tool: {e}")
        return "抱歉，在查询邻居节点时遇到内部错误，请稍后再试。"

@tool("graph_search_json")
def graph_search_json_tool(query: str, depth: int = 2, k_edges: int = 200, seeds_count: int = 3) -> str:
    """
    返回结构化 JSON 字符串：{"query","seeds","edges","specs","meta"}
    - seeds: 选取的种子（slug 或 name）
    - edges: 邻域扩展边列表（a_label/a_slug/a_name/rel_type/b_label/b_slug/b_name）
    - specs: 关联设计规范 [{name, content}]
    """
    try:
        edges, specs, seeds = _graph_search_core(query, depth=depth, k_edges=k_edges, seeds_count=seeds_count)
        found = bool(edges or specs)
        data = {
            "query": query,
            "seeds": seeds,
            "edges": retriever.to_json_context(edges),
            "specs": specs,
            "meta": {
                "found": found,
                "depth": depth,
                "k_edges": k_edges,
            }
        }
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"internal_error: {str(e)}"}, ensure_ascii=False)

tools = [
    graph_search_tool,
    multi_hop_reasoning_tool,
    find_related_concepts_tool,
    shortest_path_tool,
    list_neighbors_tool,
    graph_search_json_tool,
]


