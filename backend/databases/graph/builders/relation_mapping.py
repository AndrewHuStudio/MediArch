"""
关系名称规范化映射表

特性：
- 内置常见中文/英文关系别名映射
- 支持从环境变量 `KG_RELATION_ALIASES_PATH` 加载自定义映射（JSON: {"别名": "标准关系"}）
- 未命中时尝试使用 difflib 做模糊匹配（阈值 `KG_RELATION_FUZZY_THRESHOLD`，默认 0.78）
- 未识别的关系写入日志（默认 `backend/databases/graph/output/unknown_relations.log`，可用 `KG_RELATION_LOG_PATH` 覆盖）
"""

import json
import os
import re
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Dict

# 避免循环导入：延迟引用 LLMClient
def _lazy_llm_client():
    from backend.databases.graph.utils.call_llm_api import LLMClient
    return LLMClient

# 基础映射表：全部用小写键，便于统一处理
RELATION_MAPPING: Dict[str, str] = {
    # 空间拓扑关系
    "包含": "CONTAINS",
    "含有": "CONTAINS",
    "包括": "CONTAINS",
    "构成": "CONTAINS",  # 新增：常见的"构成"关系
    "组成": "CONTAINS",  # 新增
    "设有": "CONTAINS",  # 新增：医院设有XX科室
    "设置": "CONTAINS",  # 新增
    "分为": "CONTAINS",  # 新增：分为XX部分
    "contains": "CONTAINS",
    "has feature": "CONTAINS",
    "has_feature": "CONTAINS",
    "has features": "CONTAINS",
    "has_features": "CONTAINS",
    "has part": "CONTAINS",
    "has_part": "CONTAINS",

    "邻近": "ADJACENT_TO",
    "相邻": "ADJACENT_TO",
    "毗邻": "ADJACENT_TO",  # 新增
    "adjacent": "ADJACENT_TO",
    "adjacent_to": "ADJACENT_TO",

    "连接": "CONNECTED_TO",
    "通达": "CONNECTED_TO",
    "连通": "CONNECTED_TO",
    "连结": "CONNECTED_TO",  # 新增
    "connected": "CONNECTED_TO",
    "connected_to": "CONNECTED_TO",

    # 文献溯源关系
    "引用": "REFERENCES",
    "参考": "REFERENCES",
    "reference": "REFERENCES",
    "references": "REFERENCES",

    "符合": "MENTIONED_IN",
    "遵循": "MENTIONED_IN",
    "满足": "MENTIONED_IN",
    "依据": "MENTIONED_IN",  # 新增：依据某规范
    "依照": "MENTIONED_IN",  # 新增
    "按照": "MENTIONED_IN",  # 新增
    "comply": "MENTIONED_IN",
    "complies_with": "MENTIONED_IN",
    "来源": "MENTIONED_IN",
    "源自": "MENTIONED_IN",
    "提取自": "MENTIONED_IN",
    "基于": "MENTIONED_IN",  # 新增
    "derived": "MENTIONED_IN",
    "derived_from": "MENTIONED_IN",
    "提及": "MENTIONED_IN",
    "记载": "MENTIONED_IN",
    "载于": "MENTIONED_IN",

    # 功能依赖关系
    "需要": "REQUIRES",
    "依赖": "REQUIRES",
    "要求": "REQUIRES",  # 新增
    "必须": "REQUIRES",  # 新增
    "应": "REQUIRES",    # 新增：应设置XX
    "应设": "REQUIRES",  # 新增
    "require": "REQUIRES",
    "requires": "REQUIRES",
    "depends on": "REQUIRES",
    "depends_on": "REQUIRES",

    "提供": "PROVIDES",
    "提供服务": "PROVIDES",
    "开展": "PROVIDES",
    "开展于": "PERFORMED_IN",
    "在": "PERFORMED_IN",
    "位于": "PERFORMED_IN",
    "实施于": "PERFORMED_IN",
    "located in": "PERFORMED_IN",
    "located_in": "PERFORMED_IN",
    "used in": "PERFORMED_IN",
    "used_in": "PERFORMED_IN",
    "可进行": "PROVIDES",
    "设置": "PROVIDES",

    "使用": "USES",
    "采用": "USES",
    "利用": "USES",

    "支持": "SUPPORTS",
    "用于": "SUPPORTS",
    "适用于": "SUPPORTS",
    "满足": "SUPPORTS",
    "used for": "SUPPORTS",
    "used_for": "SUPPORTS",

    "指导": "GUIDES",
    "指导着": "GUIDES",
    "规定": "GUIDES",  # 新增：规范规定XX设计
    "建议": "GUIDES",  # 新增
    "推荐": "GUIDES",  # 新增
    "guide": "GUIDES",
    "guides": "GUIDES",

    # 逆向/辅助映射（保留最小必要集合）
    "属于": "BELONGS_TO",
    "隶属": "BELONGS_TO",
    "归属于": "BELONGS_TO",
    "由": "BELONGS_TO",     # 新增：由XX承担
    "承担": "BELONGS_TO",   # 新增：XX承担
    "负责": "BELONGS_TO",   # 新增
    "belongs_to": "BELONGS_TO",
    "belong to": "BELONGS_TO",

    # 降噪兜底：尽量减少RELATED_TO的使用
    "关联": "RELATED_TO",
    "相关": "RELATED_TO",
    "涉及": "RELATED_TO",  # 新增
    "related": "RELATED_TO",
    "related_to": "RELATED_TO",

    # 新增：schema 定义但之前缺失的关系类型
    "is type of": "IS_TYPE_OF",
    "is a type of": "IS_TYPE_OF",
    "type of": "IS_TYPE_OF",
    "属于类型": "IS_TYPE_OF",
    "类型": "IS_TYPE_OF",
    "is_type_of": "IS_TYPE_OF",

    "relates to": "RELATES_TO",
    "related with": "RELATES_TO",
    "relates_to": "RELATES_TO",

    "refers to": "REFERS_TO",
    "refer to": "REFERS_TO",
    "cross-references": "REFERS_TO",
    "refers_to": "REFERS_TO",
    "参见": "REFERS_TO",
    "详见": "REFERS_TO",
    "见": "REFERS_TO",
    
    # 过滤掉不应该作为关系的词（映射为SKIP，后续跳过）
    "具有": "SKIP",  # 这应该是属性，不是关系
    "描述": "SKIP",  # 这应该是属性，不是关系
    "级别": "SKIP",  # 这应该是属性，不是关系
    "优缺点": "SKIP",  # 这应该是属性，不是关系
    "处理": "SKIP",  # 太模糊，应该是属性
    "占": "SKIP",    # "占门诊量比例"应该是属性
    "占比": "SKIP",
    "比例": "SKIP",
    "转变为": "SKIP",  # 太模糊
    "建立": "SKIP",    # 太模糊
    "应对": "SKIP",    # 太模糊
    "has attribute": "SKIP",
    "has_attribute": "SKIP",
    "has specification": "SKIP",
    "has_specification": "SKIP",
    "has quantity": "SKIP",
    "has_quantity": "SKIP",
    "has value": "SKIP",
    "has_value": "SKIP",
    "has size": "SKIP",
    "has_size": "SKIP",
    "has area": "SKIP",
    "has_area": "SKIP",
    "has code": "SKIP",
    "has_code": "SKIP",
    "has room code": "SKIP",
    "has_room_code": "SKIP",
}

# 反向关系映射（自动生成）
INVERSE_RELATIONS = {
    "CONTAINS": "BELONGS_TO",
    "BELONGS_TO": "CONTAINS",
    "REQUIRES": "REQUIRED_BY",
    "REQUIRED_BY": "REQUIRES"
}

STANDARD_RELATIONS = [
    "MENTIONED_IN",
    "CONTAINS",
    "BELONGS_TO",
    "ADJACENT_TO",
    "CONNECTED_TO",
    "REQUIRES",
    "REQUIRED_BY",
    "GUIDES",
    "REFERENCES",
    "REFERS_TO",
    "PROVIDES",
    "PERFORMED_IN",
    "USES",
    "SUPPORTS",
    "IS_TYPE_OF",
    "RELATES_TO",
    "RELATED_TO",
]

RELATION_ALIAS_PATH = os.getenv("KG_RELATION_ALIASES_PATH")
FUZZY_THRESHOLD = float(os.getenv("KG_RELATION_FUZZY_THRESHOLD", "0.78"))
UNKNOWN_RELATION_LOG = os.getenv(
    "KG_RELATION_LOG_PATH",
    "backend/databases/graph/output/unknown_relations.log"
)
LLM_FALLBACK_ENABLED = os.getenv("KG_RELATION_LLM_FALLBACK", "0").lower() in {"1", "true", "yes"}
LLM_FALLBACK_MODEL = os.getenv("KG_RELATION_LLM_MODEL", "gpt-4o-mini")
LLM_FALLBACK_TIMEOUT = float(os.getenv("KG_RELATION_LLM_TIMEOUT", "20"))
_QUANTITATIVE_PATTERNS = [
    re.compile(r"\d+\.?\d*\s*[㎡m²mM平方米立方米]"),
    re.compile(r"\d+\.?\d*\s*[床位张台]"),
    re.compile(r"\d+\.?\d*\s*[℃°C度]"),
    re.compile(r"\d+\.?\d*\s*[%百分之]"),
    re.compile(r"\d+\.?\d*\s*[级层楼]"),
    re.compile(r"[≥≤><]\s*\d+"),
    re.compile(r"\d+\s*[-~]\s*\d+"),
]

_llm_client = None


def _normalize_key(key: str) -> str:
    return (key or "").strip().lower()


def _load_dynamic_aliases() -> None:
    if not RELATION_ALIAS_PATH:
        return
    try:
        with open(RELATION_ALIAS_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            print(f"[WARN] KG_RELATION_ALIASES_PATH 内容不是字典：{RELATION_ALIAS_PATH}")
            return
        added = 0
        for raw_key, value in data.items():
            key = _normalize_key(raw_key)
            val = (value or "").strip().upper()
            if not key or not val:
                continue
            RELATION_MAPPING[key] = val
            added += 1
        if added:
            print(f"[INFO] Loaded {added} relation aliases from {RELATION_ALIAS_PATH}")
    except FileNotFoundError:
        print(f"[WARN] 未找到 KG_RELATION_ALIASES_PATH：{RELATION_ALIAS_PATH}")
    except Exception as exc:
        print(f"[WARN] 加载 KG_RELATION_ALIASES_PATH 失败：{exc}")


def _log_unknown_relation(name: str) -> None:
    try:
        log_path = Path(UNKNOWN_RELATION_LOG)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(name.strip() + "\n")
    except Exception:
        pass


def _fuzzy_match(name: str) -> str:
    if not name:
        return ""
    matches = get_close_matches(name.strip().upper(), STANDARD_RELATIONS, n=1, cutoff=FUZZY_THRESHOLD)
    return matches[0] if matches else ""


def _llm_classify_relation(name: str) -> str:
    """调用 LLM 对关系进行分类，返回标准关系或空字符串。"""
    global _llm_client
    if not LLM_FALLBACK_ENABLED:
        return ""
    try:
        if _llm_client is None:
            _llm_client = _lazy_llm_client()(model=LLM_FALLBACK_MODEL)
        allowed = " / ".join(STANDARD_RELATIONS + ["UNKNOWN"])
        prompt = (
            "请只回答以下之一（不解释）：\n"
            f"{allowed}\n"
            "当前关系词：\"" + name.strip() + "\"\n"
            "若不确定，回答 UNKNOWN。"
        )
        timeout = LLM_FALLBACK_TIMEOUT
        client_timeout = getattr(_llm_client, "request_timeout", None)
        if client_timeout is not None:
            timeout = min(float(client_timeout), float(LLM_FALLBACK_TIMEOUT))
        resp = _llm_client.client.chat.completions.create(
            model=_llm_client.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=timeout,
        )
        answer = (resp.choices[0].message.content or "").strip().upper()
        return answer if answer in STANDARD_RELATIONS else ""
    except Exception as exc:
        print(f"[WARN] LLM relation fallback failed: {exc}")
        return ""


_load_dynamic_aliases()


@lru_cache(maxsize=1024)
def _normalize_relation_cached(relation_name: str) -> str:
    if not relation_name:
        return "RELATED_TO"

    key = _normalize_key(relation_name)

    # 1. 显式映射
    if key in RELATION_MAPPING:
        mapped = RELATION_MAPPING[key]
        # 如果映射为SKIP，说明这不应该是关系（应该是属性）
        if mapped == "SKIP":
            return "SKIP"
        return mapped

    # 2. 已经是标准形式
    upper = relation_name.strip().upper()
    if upper in STANDARD_RELATIONS:
        return upper

    # 3. 模糊匹配
    fuzzy = _fuzzy_match(relation_name)
    if fuzzy:
        print(f"[INFO] Fuzzy matched relation '{relation_name}' -> '{fuzzy}'")
        return fuzzy

    # 4. LLM 分类（可选）
    llm_result = _llm_classify_relation(relation_name)
    if llm_result:
        print(f"[INFO] LLM mapped relation '{relation_name}' -> '{llm_result}'")
        return llm_result

    # 5. 记录未知，回退
    print(f"[WARN] Unknown relation '{relation_name}', mapping to RELATED_TO")
    _log_unknown_relation(relation_name)
    return "RELATED_TO"


def normalize_relation(relation_name: str) -> str:
    return _normalize_relation_cached(str(relation_name or ""))


def get_inverse_relation(relation: str) -> str:
    return INVERSE_RELATIONS.get(relation)


def classify_attribute_type(attribute_value: str) -> str:
    text = attribute_value or ""
    for pattern in _QUANTITATIVE_PATTERNS:
        if pattern.search(text):
            return "quantitative"
    return "qualitative"


if __name__ == "__main__":
    tests = ["包含", "contains", "GUIDES", "指导", "依赖", "未知关系"]
    for rel in tests:
        norm = normalize_relation(rel)
        inv = get_inverse_relation(norm)
        print(f"{rel:10s} -> {norm:15s} (inverse: {inv or 'None'})")

