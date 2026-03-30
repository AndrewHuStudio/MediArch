"""
名称标准化与别名归一

目标：
- 统一全/半角与罗马/中文数字写法（如 Ⅰ/一/1 级）
- 应用别名到标准名映射（按实体类型）
- 生成作用域键（用于作用域消歧）

依赖：纯标准库
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List


ROMAN_TO_ARABIC = {
    "Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5",
    "I": "1", "II": "2", "III": "3", "IV": "4", "V": "5",
}

CHINESE_TO_ARABIC = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
}


def _to_half_width(s: str) -> str:
    # 全角到半角
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize_numbers(text: str) -> str:
    if not text:
        return text
    s = _to_half_width(text)
    # 罗马数字/中文数字 → 阿拉伯数字（仅在“级/类/型”等上下文附近做替换，以降低误替换）
    for k, v in ROMAN_TO_ARABIC.items():
        s = re.sub(fr"{re.escape(k)}(?=\s*[级类型室间])", v, s)
    for k, v in CHINESE_TO_ARABIC.items():
        s = re.sub(fr"{re.escape(k)}(?=\s*[级类型室间区室])", v, s)
    # 统一空格
    s = re.sub(r"\s+", "", s)
    return s


def canonicalize(name: str, entity_type: str, alias_map: Dict[str, Dict[str, str]] | None = None) -> str:
    """返回标准化后的实体名。
    顺序：数字归一 → 小写/去空格 → 应用别名映射（按类型）。
    """
    if not isinstance(name, str):
        return str(name)
    s = normalize_numbers(name.strip())
    # 大小写规范（主要为了英文字母）
    s = s.lower()
    # 应用别名
    if alias_map and isinstance(alias_map.get(entity_type), dict):
        mapped = alias_map[entity_type].get(s)
        if mapped:
            return mapped
    return s


def load_alias_map(path: str) -> Dict[str, Dict[str, str]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 统一 key 的数字写法
            out: Dict[str, Dict[str, str]] = {}
            for etype, mapping in data.items():
                out[etype] = {normalize_numbers(k).lower(): normalize_numbers(v).lower() for k, v in mapping.items()}
            return out
    except Exception:
        return {}


def detect_synonyms_llm(
    entity_names: List[str],
    entity_types: Dict[str, str],
    llm_client: Any,
    batch_size: int = 30,
) -> Dict[str, str]:
    """使用 LLM 识别同类型实体名中的语义同义词。

    返回值格式：{alias_name: canonical_name}
    """
    if not entity_names or not llm_client:
        return {}

    grouped_names: Dict[str, List[str]] = {}
    for name in entity_names:
        entity_type = entity_types.get(name, "").strip()
        if not name or not entity_type:
            continue
        grouped_names.setdefault(entity_type, [])
        if name not in grouped_names[entity_type]:
            grouped_names[entity_type].append(name)

    merge_map: Dict[str, str] = {}

    for entity_type, names in grouped_names.items():
        if len(names) < 2:
            continue
        for start in range(0, len(names), max(2, batch_size)):
            batch = names[start : start + max(2, batch_size)]
            if len(batch) < 2:
                continue

            names_text = "\n".join(f"- {name}" for name in batch)
            prompt = (
                f"以下是同一类型（{entity_type}）的实体名称列表。"
                "请识别其中指代相同概念的同义词组，并为每组选择最规范的名称作为标准名。\n"
                "只输出存在同义关系的组，不要输出独立实体。\n"
                f"实体列表:\n{names_text}\n\n"
                '返回JSON数组: [{"standard": "标准名", "synonyms": ["同义词1", "同义词2"]}]'
            )

            try:
                response = llm_client.chat_json(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
            except Exception:
                continue

            if isinstance(response, list):
                groups = response
            elif isinstance(response, dict):
                groups = response.get("groups") or response.get("result") or []
            else:
                groups = []

            for group in groups:
                if not isinstance(group, dict):
                    continue
                standard = str(group.get("standard", "")).strip()
                synonyms = group.get("synonyms") or []
                if not standard:
                    continue
                for synonym in synonyms:
                    synonym_name = str(synonym).strip()
                    if synonym_name and synonym_name != standard:
                        merge_map[synonym_name] = standard

    return merge_map


def compose_scope_key(scope_names: List[str]) -> str:
    """将作用域名称列表标准化后合成为稳定作用域键。"""
    if not scope_names:
        return ""
    cleaned = [normalize_numbers(s).lower().strip() for s in scope_names if isinstance(s, str) and s.strip()]
    if not cleaned:
        return ""
    cleaned = sorted(set(cleaned))
    return "/".join(cleaned)


