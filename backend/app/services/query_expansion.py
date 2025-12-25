# -*- coding: utf-8 -*-
"""
MediArch Query Expansion Module
================================

通用查询扩展模块，解决关键词过于精确导致无法检索的问题。

核心功能：
1. 中文分词和关键词提取
2. 同义词扩展
3. 领域特定缩写和别名映射
4. 语义相关词扩展
5. N-gram组合生成

作者: MediArch Team
创建时间: 2025-01-15
"""

import re
import os
import warnings
from typing import List, Dict, Set, Optional, Tuple
from collections import defaultdict
import logging

# [FIX 2025-12-04] 抑制 pkg_resources 弃用警告（jieba 依赖）
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

try:
    import jieba
    import jieba.posseg as pseg

    # ⚠️ 2025-01-16: 强制禁用jieba缓存写入，避免LangGraph dev阻塞调用
    # 必须在jieba.initialize()调用前设置才生效

    # 方法1: 禁用缓存目录和文件
    jieba.dt.tmp_dir = None
    jieba.dt.cache_file = None

    # 方法2: 直接修改字典类的缓存属性
    if hasattr(jieba.dt, 'tmp_dir'):
        jieba.dt.tmp_dir = None
    if hasattr(jieba.dt, 'cache_file'):
        jieba.dt.cache_file = None

    # 方法3: 重写缓存写入方法为空操作（最强手段）
    def dummy_cache_file_write(*args, **kwargs):
        """空操作，阻止jieba写缓存文件"""
        pass

    if hasattr(jieba.dt, 'cache_file'):
        # 直接禁用cache相关操作
        try:
            import marshal
            original_dump = marshal.dump
            def safe_dump(*args, **kwargs):
                # 只有在非缓存操作时才执行真正的marshal.dump
                pass
            # 注意：这里不替换marshal.dump，因为会影响其他模块
            jieba.dt.total = 0  # 强制跳过缓存写入
        except:
            pass

    JIEBA_AVAILABLE = True
    logging.info("[QueryExpansion] jieba已导入，已强制禁用缓存写入")
except ImportError:
    JIEBA_AVAILABLE = False
    logging.warning("[QueryExpansion] jieba未安装，将使用基础分词")

logger = logging.getLogger(__name__)


# ============================================================================
# 医疗建筑领域同义词/别名映射
# ============================================================================

MEDICAL_ARCHITECTURE_SYNONYMS = {
    # 医疗空间
    "门诊": ["门诊部", "门诊区", "门诊楼", "门诊大楼", "outpatient", "OPD", "门诊中心"],
    "门诊大厅": ["门诊候诊区", "门诊等候区", "门诊接待区", "门诊前厅", "门诊lobby"],
    "急诊": ["急诊室", "急诊科", "急诊中心", "ER", "emergency", "急救中心"],
    "病房": ["病房区", "病房单元", "病房楼", "住院部", "ward", "病房区域"],
    "手术室": ["手术部", "手术间", "OR", "operating room", "手术中心", "手术区"],
    "ICU": ["重症监护", "重症监护室", "重症监护病房", "intensive care unit", "重症病房"],
    "检验科": ["检验中心", "化验室", "laboratory", "lab", "检验室"],
    "影像科": ["放射科", "X光室", "CT室", "MRI室", "radiology", "医学影像", "影像中心"],
    "药房": ["药剂科", "pharmacy", "配药室", "发药处"],

    # 建筑元素
    "大厅": ["候诊区", "等候区", "lobby", "hall", "等候大厅", "候诊大厅", "接待区"],
    "走廊": ["过道", "通道", "corridor", "廊道"],
    "通道": ["过道", "走廊", "廊道", "通路"],
    "电梯": ["升降梯", "elevator", "lift", "垂直交通"],
    "楼梯": ["stairs", "staircase", "疏散楼梯", "步行梯"],

    # 设计概念
    "设计": ["规划", "布局", "design", "layout", "设计方案", "空间设计"],
    "布局": ["规划", "平面布置", "layout", "空间布局", "功能布局"],
    "流线": ["动线", "circulation", "交通流线", "人流动线"],
    "通风": ["通风系统", "ventilation", "换气", "空气流通"],
    "采光": ["自然采光", "lighting", "照明", "天然光"],
    "隔音": ["噪声控制", "sound insulation", "隔声", "声学设计"],

    # 医疗功能
    "消毒": ["灭菌", "sterilization", "消毒灭菌", "无菌"],
    "感染控制": ["院感控制", "infection control", "感控", "医院感染"],
    "无障碍": ["accessible", "accessibility", "无障碍设计", "无障碍通道"],
    "卫生": ["清洁", "hygiene", "卫生标准"],

    # 建筑类型
    "综合医院": ["general hospital", "综合性医院", "大型医院"],
    "专科医院": ["specialty hospital", "专科", "特色医院"],
    "社区医院": ["community hospital", "社区卫生服务中心", "基层医院"],
    "妇幼医院": ["妇幼保健院", "maternal and child hospital", "妇产医院"],

    # 患者类型
    "患者": ["病人", "patient", "就诊者", "病患"],
    "医护": ["医护人员", "医务人员", "healthcare staff", "医生护士"],
    "家属": ["陪护", "陪同人员", "探视者"],
}


# ============================================================================
# 停用词列表
# ============================================================================

STOPWORDS = {
    # 常见虚词
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看",

    # 疑问词
    "怎么", "怎样", "如何", "什么", "哪些", "为什么", "为何",

    # 助词
    "应该", "需要", "可以", "能够", "必须", "要求",

    # 标点（中英文）
    "？", "?", "！", "!", "。", ".", "，", ",", "、", "；", ";", "：", ":",
}


# ============================================================================
# Query Expansion 核心类
# ============================================================================

class QueryExpansion:
    """
    查询扩展器

    用法示例：
    >>> expander = QueryExpansion()
    >>> result = expander.expand("门诊大厅应该如何设计？")
    >>> print(result.keywords)  # ['门诊', '门诊大厅', '大厅', '设计']
    >>> print(result.synonyms)  # ['门诊部', '门诊区', '候诊区', '规划', ...]
    >>> print(result.search_terms)  # 所有搜索词的组合列表
    """

    def __init__(
        self,
        synonyms: Optional[Dict[str, List[str]]] = None,
        stopwords: Optional[Set[str]] = None,
        use_jieba: bool = True,
    ):
        """
        初始化查询扩展器

        Args:
            synonyms: 自定义同义词词典（会合并到默认词典）
            stopwords: 自定义停用词集合（会合并到默认集合）
            use_jieba: 是否使用jieba分词（如果可用）
        """
        self.synonyms = MEDICAL_ARCHITECTURE_SYNONYMS.copy()
        if synonyms:
            self.synonyms.update(synonyms)

        self.stopwords = STOPWORDS.copy()
        if stopwords:
            self.stopwords.update(stopwords)

        self.use_jieba = use_jieba and JIEBA_AVAILABLE

        if self.use_jieba:
            logger.info("[QueryExpansion] 使用jieba分词")
        else:
            logger.info("[QueryExpansion] 使用正则分词")

    def tokenize(self, query: str) -> List[str]:
        """
        中文分词

        Args:
            query: 原始查询字符串

        Returns:
            分词结果列表
        """
        if self.use_jieba:
            # 使用jieba分词
            words = jieba.lcut(query)
            # 过滤单字和停用词
            words = [w for w in words if len(w) > 1 and w not in self.stopwords]
            return words
        else:
            # 使用正则表达式提取中文词
            cleaned = re.sub(r'[，。,。；;.!？?、\s]+', ' ', query)
            # 提取2-6个字的中文词组
            words = re.findall(r'[\u4e00-\u9fa5]{2,6}', cleaned)
            # 过滤停用词
            words = [w for w in words if w not in self.stopwords]
            return words

    def extract_keywords(self, query: str, max_keywords: int = 8) -> List[str]:
        """
        提取关键词（去重、排序）

        Args:
            query: 原始查询
            max_keywords: 最多返回的关键词数

        Returns:
            关键词列表（按长度降序）
        """
        words = self.tokenize(query)

        # 去重并保持顺序
        seen = set()
        unique_words = []
        for w in words:
            if w not in seen:
                seen.add(w)
                unique_words.append(w)

        # 按长度降序排列（长词优先，更精确）
        unique_words.sort(key=len, reverse=True)

        return unique_words[:max_keywords]

    def find_synonyms(self, keyword: str) -> List[str]:
        """
        查找关键词的同义词

        Args:
            keyword: 关键词

        Returns:
            同义词列表（不包含原词）
        """
        # 直接匹配
        if keyword in self.synonyms:
            return self.synonyms[keyword]

        # 部分匹配（keyword是某个同义词组的子串）
        synonyms = []
        for key, values in self.synonyms.items():
            if keyword in key or key in keyword:
                synonyms.extend(values)
                if key != keyword:
                    synonyms.append(key)

        # 去重
        return list(set(synonyms))

    def generate_ngrams(
        self,
        keywords: List[str],
        max_n: int = 3,
        max_combinations: int = 5
    ) -> List[str]:
        """
        生成N-gram组合（2-gram, 3-gram等）

        Args:
            keywords: 关键词列表
            max_n: 最大gram数（2=bigram, 3=trigram）
            max_combinations: 每种gram最多生成的组合数

        Returns:
            N-gram列表
        """
        ngrams = []

        for n in range(2, min(max_n + 1, len(keywords) + 1)):
            count = 0
            for i in range(len(keywords) - n + 1):
                if count >= max_combinations:
                    break
                ngram = "".join(keywords[i:i+n])
                ngrams.append(ngram)
                count += 1

        return ngrams

    def expand(
        self,
        query: str,
        include_synonyms: bool = True,
        include_ngrams: bool = True,
        max_search_terms: int = 20
    ) -> "ExpansionResult":
        """
        执行查询扩展

        Args:
            query: 原始查询
            include_synonyms: 是否包含同义词扩展
            include_ngrams: 是否包含N-gram组合
            max_search_terms: 最多返回的搜索词数

        Returns:
            ExpansionResult对象
        """
        # 1. 提取关键词
        keywords = self.extract_keywords(query)

        # 2. 查找同义词
        synonyms = []
        if include_synonyms:
            for kw in keywords:
                syn_list = self.find_synonyms(kw)
                synonyms.extend(syn_list)

        # 去重
        synonyms = list(set(synonyms))

        # 3. 生成N-gram
        ngrams = []
        if include_ngrams and len(keywords) >= 2:
            ngrams = self.generate_ngrams(keywords, max_n=3)

        # 4. 组合所有搜索词
        # 优先级：keywords > ngrams > synonyms
        search_terms = []

        # 添加原始关键词（最高优先级）
        search_terms.extend(keywords)

        # 添加N-gram（中等优先级）
        search_terms.extend(ngrams)

        # 添加同义词（较低优先级）
        search_terms.extend(synonyms[:10])  # 限制同义词数量

        # 去重（保持顺序）
        seen = set()
        unique_search_terms = []
        for term in search_terms:
            if term not in seen:
                seen.add(term)
                unique_search_terms.append(term)

        # 限制数量
        unique_search_terms = unique_search_terms[:max_search_terms]

        return ExpansionResult(
            original_query=query,
            keywords=keywords,
            synonyms=synonyms,
            ngrams=ngrams,
            search_terms=unique_search_terms,
        )


class ExpansionResult:
    """查询扩展结果"""

    def __init__(
        self,
        original_query: str,
        keywords: List[str],
        synonyms: List[str],
        ngrams: List[str],
        search_terms: List[str],
    ):
        self.original_query = original_query
        self.keywords = keywords
        self.synonyms = synonyms
        self.ngrams = ngrams
        self.search_terms = search_terms

    def to_dict(self) -> Dict[str, any]:
        """转换为字典"""
        return {
            "original_query": self.original_query,
            "keywords": self.keywords,
            "synonyms": self.synonyms[:5],  # 只返回前5个同义词
            "ngrams": self.ngrams,
            "search_terms": self.search_terms,
            "total_terms": len(self.search_terms),
        }

    def __repr__(self) -> str:
        return (
            f"ExpansionResult(\n"
            f"  keywords={self.keywords[:3]}...\n"
            f"  synonyms={self.synonyms[:3]}...\n"
            f"  total_terms={len(self.search_terms)}\n"
            f")"
        )


# ============================================================================
# 全局单例
# ============================================================================

_global_expander: Optional[QueryExpansion] = None


def get_query_expander() -> QueryExpansion:
    """获取全局查询扩展器单例"""
    global _global_expander
    if _global_expander is None:
        _global_expander = QueryExpansion()
    return _global_expander


# ============================================================================
# 便捷函数
# ============================================================================

def expand_query(
    query: str,
    include_synonyms: bool = True,
    include_ngrams: bool = True,
    max_search_terms: int = 20
) -> ExpansionResult:
    """
    便捷函数：扩展查询

    用法：
    >>> result = expand_query("门诊大厅应该如何设计？")
    >>> print(result.search_terms)
    """
    expander = get_query_expander()
    return expander.expand(
        query,
        include_synonyms=include_synonyms,
        include_ngrams=include_ngrams,
        max_search_terms=max_search_terms
    )


def extract_keywords(query: str, max_keywords: int = 8) -> List[str]:
    """
    便捷函数：提取关键词

    用法：
    >>> keywords = extract_keywords("门诊大厅应该如何设计？")
    >>> print(keywords)  # ['门诊大厅', '门诊', '大厅', '设计']
    """
    expander = get_query_expander()
    return expander.extract_keywords(query, max_keywords)


# ============================================================================
# 测试代码
# ============================================================================

if __name__ == "__main__":
    # 测试用例
    test_queries = [
        "门诊大厅应该如何设计？",
        "手术室的通风系统要求",
        "ICU病房布局规范",
        "急诊流线设计要点",
    ]

    print("[QueryExpansion] 测试开始")
    print("=" * 60)

    expander = QueryExpansion()

    for query in test_queries:
        print(f"\n原始查询: {query}")
        result = expander.expand(query)

        print(f"  关键词: {result.keywords}")
        print(f"  N-gram: {result.ngrams}")
        print(f"  同义词: {result.synonyms[:5]}...")
        print(f"  搜索词 ({len(result.search_terms)}): {result.search_terms[:10]}...")
        print("-" * 60)

    print("\n[QueryExpansion] 测试完成")
