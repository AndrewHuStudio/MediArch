# MongoDB Agent 代码审查报告

**审查日期**: 2026-01-30
**审查范围**: `backend/app/agents/mongodb_agent/`

---

## 一、关键问题 (P0 - 必须修复)

### 1.1 __init__.py 注释错误
**位置**: `__init__.py:1`
**问题**: 注释写的是 "Milvus Agent"，但这是 MongoDB Agent
**影响**: 误导性注释，影响代码可维护性
**修复**:
```python
# 修改前
"""Milvus Agent - 只导出 graph"""

# 修改后
"""MongoDB Agent - 只导出 graph"""
```

### 1.2 Emoji 使用问题
**位置**: `agent.py:4-7`
**问题**: 代码注释中使用了 emoji (✅)，在 Windows GBK 环境下可能导致编码问题
**影响**: 根据用户的 CLAUDE.md 配置，应避免使用 emoji
**修复**: 将所有 emoji 替换为纯文本标记
```python
# 修改前
"""MongoDB Agent - 优化版本

核心改进：
- ✅ 删除 BaseAgent 类（只保留 graph）
- ✅ 使用 LLMManager（线程安全）
- ✅ 精简代码结构
- ✅ 规范接口（返回 items）
"""

# 修改后
"""MongoDB Agent - 优化版本

核心改进：
- [DONE] 删除 BaseAgent 类（只保留 graph）
- [DONE] 使用 LLMManager（线程安全）
- [DONE] 精简代码结构
- [DONE] 规范接口（返回 items）
"""
```

### 1.3 过长的函数
**位置**: `agent.py:586-896` (`node_search_mongodb`)
**问题**: 函数长达 310 行，违反单一职责原则，难以测试和维护
**影响**: 代码可读性差，bug 难以定位
**建议**: 拆分为多个子函数
```python
# 建议拆分为：
- _extract_search_params(state) -> SearchParams
- _execute_chunk_id_search(retriever, chunk_ids) -> List[Dict]
- _execute_keyword_search(retriever, search_terms, query, top_k, ...) -> List[Dict]
- _apply_rebalancing(chunks, top_k) -> List[Dict]
- _apply_priority_doc_fallback(chunks, query, retriever, ...) -> List[Dict]
- _apply_image_supplement(chunks, query, retriever, ...) -> List[Dict]
```

---

## 二、重要优化 (P1 - 建议修复)

### 2.1 魔法数字过多
**位置**: 多处
**问题**: 硬编码的数字散落在代码中，缺乏语义
**示例**:
- `agent.py:164`: `max_search_terms=15`
- `agent.py:541`: `hint_entity_names = unified_hints["entity_names"][:15]`
- `agent.py:839`: `img_k_base = max(2, min(8, max(int(top_k) // 3, 2)))`

**建议**: 定义常量
```python
# 在文件顶部添加
class MongoDBAgentConfig:
    """MongoDB Agent 配置常量"""
    MAX_SEARCH_TERMS = 15
    MAX_HINT_ENTITIES = 15
    MAX_PRIORITY_TERMS = 12
    IMAGE_K_BASE_MIN = 2
    IMAGE_K_BASE_MAX = 8
    MAX_DOCS_FOR_HINTS = 3
    MAX_PAGES_PER_DOC = 4
    PAGE_WINDOW_MAX = 10
    SNIPPET_LENGTH = 200
    CITATION_SNIPPET_LENGTH = 150
```

### 2.2 重复的类型转换逻辑
**位置**: `agent.py:618-667`
**问题**: `_normalize_str_list` 和 `_normalize_int_list` 是内部函数，但逻辑复杂且可能在其他地方复用
**建议**: 提取为模块级工具函数或移到 utils 模块
```python
# 移到 backend/app/utils/type_converters.py
def normalize_str_list(value: Any) -> List[str]:
    """标准化为字符串列表"""
    ...

def normalize_int_list(value: Any) -> List[int]:
    """标准化为整数列表"""
    ...
```

### 2.3 复杂的条件判断
**位置**: `agent.py:739-826` (资料优先级兜底逻辑)
**问题**: 嵌套过深，逻辑复杂，难以理解
**建议**: 提取为独立函数并添加详细注释
```python
async def _apply_priority_document_fallback(
    chunks: List[Dict[str, Any]],
    query: str,
    retriever: Any,
    search_terms: List[str],
    doc_ids: List[str],
    source_documents: List[str],
) -> tuple[List[Dict[str, Any]], int]:
    """
    资料优先级兜底策略

    目标：对"手术室设计规范"类问题，确保覆盖高价值资料
    - 规范/标准类文档
    - 详图集/图集类文档

    Args:
        chunks: 已检索到的文档块
        query: 用户查询
        retriever: MongoDB 检索器
        search_terms: 搜索关键词
        doc_ids: 文档ID过滤（如果有）
        source_documents: 文档名过滤（如果有）

    Returns:
        (补充后的chunks, 新增数量)
    """
    ...
```

### 2.4 异常处理不一致
**位置**: 多处
**问题**: 有些地方用 `logger.warning`，有些用 `logger.error`，有些直接返回空结果
**示例**:
- `agent.py:127`: `logger.warning` + `raise`
- `agent.py:183`: `logger.warning` + 回退逻辑
- `agent.py:609`: `logger.error` + 返回错误字典

**建议**: 统一异常处理策略
```python
# 定义异常处理策略
class MongoDBAgentError(Exception):
    """MongoDB Agent 基础异常"""
    pass

class LLMInitError(MongoDBAgentError):
    """LLM 初始化失败"""
    pass

class RetrieverError(MongoDBAgentError):
    """检索器错误"""
    pass

# 在关键位置使用自定义异常
try:
    retriever = await asyncio.to_thread(get_retriever)
except Exception as e:
    logger.error(f"[MongoDB->Search] Retriever 获取失败: {e}")
    raise RetrieverError(f"Failed to initialize retriever: {e}") from e
```

### 2.5 缺少类型提示
**位置**: 多处辅助函数
**问题**: 部分函数缺少返回类型提示
**示例**:
- `agent.py:148`: `heuristic_rewrite(query: str)` 缺少返回类型
- `agent.py:206`: `_want_images(text: str)` 缺少返回类型

**建议**: 添加完整类型提示
```python
def heuristic_rewrite(query: str) -> Dict[str, Any]:
    """启发式查询改写（LLM 失败时的兜底）"""
    ...

def _want_images(text: str) -> bool:
    """判断用户是否"明确想要图片/图纸/图示"。"""
    ...
```

---

## 三、性能优化 (P2 - 可选优化)

### 3.1 重复的正则编译
**位置**: `agent.py:914, 922, 930, 935, 1013`
**问题**: 正则表达式在函数内部编译，每次调用都会重新编译
**建议**: 在模块级别预编译
```python
# 在文件顶部添加
import re

# 预编译正则表达式
SECTION_PATTERN_1 = re.compile(r"(第\d+章)\s*([^\-]+?)(?:\s*-\s*(\d+\.\d+\s*.+))?$")
SECTION_PATTERN_2 = re.compile(r"(\d+\.\d+)\s+(.+)")
SECTION_PATTERN_3 = re.compile(r"(第\d+章)\s+(.+)")
PATH_PATTERN = re.compile(r'^.*?[/\\]backend[/\\]databases[/\\]documents[/\\]')

# 在函数中使用
def _parse_section_hierarchy(section: str) -> tuple[str, str, str]:
    match = SECTION_PATTERN_1.match(section)
    if match:
        ...
```

### 3.2 可以缓存的计算
**位置**: `agent.py:271-281` (`_count_doc_distribution`)
**问题**: 在多个地方重复计算文档分布
**建议**: 考虑在 state 中缓存结果
```python
# 在 MongoDBState 中添加
class MongoDBState(TypedDict, total=False):
    ...
    doc_distribution: Dict[str, int]  # 缓存文档分布
```

### 3.3 并行搜索优化
**位置**: `agent.py:771-826` (优先文档搜索)
**问题**: 使用 `await` 串行搜索多个文档，可以并行化
**建议**: 使用 `asyncio.gather` 并行搜索
```python
# 修改前
if need_standard:
    await _search_in_doc("GB 51039-2014 综合医院建筑设计规范.pdf", ...)
    await _search_in_doc("GB51039-2014综合医院建筑设计标准.pdf", ...)

if need_atlas:
    await _search_in_doc("医疗功能房间详图集3.pdf", ...)

# 修改后
tasks = []
if need_standard:
    tasks.append(_search_in_doc("GB 51039-2014 综合医院建筑设计规范.pdf", ...))
    tasks.append(_search_in_doc("GB51039-2014综合医院建筑设计标准.pdf", ...))
if need_atlas:
    tasks.append(_search_in_doc("医疗功能房间详图集3.pdf", ...))

if tasks:
    await asyncio.gather(*tasks)
```

---

## 四、代码质量改进 (P3 - 长期优化)

### 4.1 文档字符串不完整
**位置**: 多处
**问题**: 部分复杂函数缺少详细的参数说明和返回值说明
**建议**: 补充完整的 docstring
```python
def _rebalance_chunks_by_doc(
    chunks: List[Dict[str, Any]],
    limit: int,
    max_per_doc: Optional[int] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    按来源文档做 Round-Robin 重排以提升跨资料覆盖

    Args:
        chunks: 待重排的文档块列表
        limit: 最终返回的文档块数量上限
        max_per_doc: 每个文档最多返回的块数，None 表示不限制

    Returns:
        tuple[List[Dict], Dict[str, int]]:
            - 重排后的文档块列表（长度 <= limit）
            - 文档分布统计 {doc_name: count}

    Example:
        >>> chunks = [{"doc_title": "A", ...}, {"doc_title": "B", ...}, ...]
        >>> mixed, dist = _rebalance_chunks_by_doc(chunks, limit=10, max_per_doc=3)
        >>> print(dist)
        {'A': 3, 'B': 3, 'C': 3, 'D': 1}
    """
    ...
```

### 4.2 测试覆盖率
**问题**: 没有看到对应的单元测试文件
**建议**: 创建 `test_mongodb_agent.py`，覆盖关键函数
```python
# backend/tests/test_mongodb_agent.py
import pytest
from backend.app.agents.mongodb_agent.agent import (
    deduplicate_terms,
    heuristic_rewrite,
    _parse_section_hierarchy,
    _want_images,
    _is_room_norm_query,
)

class TestDeduplicateTerms:
    def test_basic_deduplication(self):
        terms = ["病房", "病房", "手术室", "病房"]
        result = deduplicate_terms(terms)
        assert result == ["病房", "手术室"]

    def test_preserves_order(self):
        terms = ["C", "A", "B", "A"]
        result = deduplicate_terms(terms)
        assert result == ["C", "A", "B"]

class TestParseSectionHierarchy:
    def test_full_format(self):
        section = "第3章 门诊部设计 - 3.1 功能布局"
        chapter, title, sub = _parse_section_hierarchy(section)
        assert chapter == "第3章"
        assert title == "门诊部设计"
        assert sub == "3.1 功能布局"

    def test_chapter_only(self):
        section = "第3章 门诊部设计"
        chapter, title, sub = _parse_section_hierarchy(section)
        assert chapter == "第3章"
        assert title == "门诊部设计"
        assert sub == ""
```

### 4.3 配置管理
**问题**: 配置项散落在代码中
**建议**: 集中管理配置
```python
# backend/app/agents/mongodb_agent/config.py
from pydantic import BaseModel, Field
from typing import List

class MongoDBAgentConfig(BaseModel):
    """MongoDB Agent 配置"""

    # LLM 配置
    rewrite_model: str = Field(default="gpt-4o-mini")
    temperature: float = Field(default=0.0)
    max_tokens: int = Field(default=12000)

    # 搜索配置
    max_search_terms: int = Field(default=15)
    max_hint_entities: int = Field(default=15)
    default_top_k: int = Field(default=5)

    # 图片补充配置
    image_k_base_min: int = Field(default=2)
    image_k_base_max: int = Field(default=8)
    max_docs_for_hints: int = Field(default=3)
    max_pages_per_doc: int = Field(default=4)

    # 优先文档列表
    priority_standard_docs: List[str] = Field(default=[
        "GB 51039-2014 综合医院建筑设计规范.pdf",
        "GB51039-2014综合医院建筑设计标准.pdf",
    ])
    priority_atlas_docs: List[str] = Field(default=[
        "医疗功能房间详图集3.pdf",
    ])

    @classmethod
    def from_env(cls) -> "MongoDBAgentConfig":
        """从环境变量加载配置"""
        import os
        return cls(
            rewrite_model=os.getenv("MONGODB_AGENT_MODEL", "gpt-4o-mini"),
        )
```

### 4.4 日志级别优化
**位置**: 多处
**问题**: 部分 `logger.info` 应该是 `logger.debug`
**建议**: 区分日志级别
```python
# 修改前
logger.info(f"[MongoDB→Rewrite] LLM 原始输出: {raw_result.content[:500]}...")

# 修改后
logger.debug(f"[MongoDB→Rewrite] LLM 原始输出: {raw_result.content[:500]}...")

# 原则：
# - DEBUG: 详细的调试信息（LLM 输出、中间结果）
# - INFO: 关键流程节点（开始搜索、完成搜索、结果数量）
# - WARNING: 可恢复的异常（LLM 失败回退、部分功能降级）
# - ERROR: 严重错误（检索器初始化失败、无法返回结果）
```

---

## 五、架构建议

### 5.1 策略模式重构
**问题**: `node_search_mongodb` 中包含多种搜索策略，耦合度高
**建议**: 使用策略模式
```python
# backend/app/agents/mongodb_agent/strategies.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any

class SearchStrategy(ABC):
    """搜索策略基类"""

    @abstractmethod
    async def search(
        self,
        retriever: Any,
        state: MongoDBState,
    ) -> tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
        """
        执行搜索

        Returns:
            (chunks, strategy_name, diagnostics)
        """
        pass

class ChunkIDSearchStrategy(SearchStrategy):
    """基于 chunk_ids 的精确搜索"""

    async def search(self, retriever, state):
        chunk_ids = state.get("chunk_ids", [])
        chunks = await asyncio.to_thread(
            retriever.get_chunks_by_ids,
            chunk_ids
        )
        return chunks, "chunk_ids", {}

class KeywordSearchStrategy(SearchStrategy):
    """基于关键词的搜索"""

    async def search(self, retriever, state):
        search_terms = state.get("search_terms", [])
        query = state.get("query", "")
        top_k = state.get("top_k", 5)

        chunks, strategy, diag = await asyncio.to_thread(
            retriever.smart_keyword_search,
            search_terms,
            query,
            top_k,
            None,
            None,
        )
        return chunks, strategy, diag

# 在 node_search_mongodb 中使用
async def node_search_mongodb(state: MongoDBState) -> Dict[str, Any]:
    retriever = await asyncio.to_thread(get_retriever)

    # 选择策略
    if state.get("chunk_ids"):
        strategy = ChunkIDSearchStrategy()
    else:
        strategy = KeywordSearchStrategy()

    chunks, strategy_name, diag = await strategy.search(retriever, state)

    # 应用后处理
    chunks = await apply_rebalancing(chunks, state)
    chunks = await apply_priority_fallback(chunks, state, retriever)
    chunks = await apply_image_supplement(chunks, state, retriever)

    return {"retrieval_results": chunks, ...}
```

### 5.2 责任链模式用于后处理
**问题**: 多个后处理步骤（rebalancing, priority fallback, image supplement）串行执行
**建议**: 使用责任链模式
```python
# backend/app/agents/mongodb_agent/postprocessors.py
from abc import ABC, abstractmethod

class ChunkPostprocessor(ABC):
    """文档块后处理器基类"""

    def __init__(self):
        self.next_processor: Optional[ChunkPostprocessor] = None

    def set_next(self, processor: "ChunkPostprocessor") -> "ChunkPostprocessor":
        self.next_processor = processor
        return processor

    async def process(
        self,
        chunks: List[Dict[str, Any]],
        state: MongoDBState,
        retriever: Any,
    ) -> List[Dict[str, Any]]:
        """处理文档块"""
        chunks = await self._do_process(chunks, state, retriever)

        if self.next_processor:
            chunks = await self.next_processor.process(chunks, state, retriever)

        return chunks

    @abstractmethod
    async def _do_process(
        self,
        chunks: List[Dict[str, Any]],
        state: MongoDBState,
        retriever: Any,
    ) -> List[Dict[str, Any]]:
        """具体处理逻辑"""
        pass

class RebalancingProcessor(ChunkPostprocessor):
    """跨资料平衡处理器"""

    async def _do_process(self, chunks, state, retriever):
        top_k = state.get("top_k", 5)
        balanced, dist = _rebalance_chunks_by_doc(chunks, top_k)
        logger.info(f"[Rebalancing] {len(chunks)} -> {len(balanced)}")
        return balanced

class PriorityFallbackProcessor(ChunkPostprocessor):
    """优先文档兜底处理器"""

    async def _do_process(self, chunks, state, retriever):
        query = state.get("query", "")
        if not _is_room_norm_query(query):
            return chunks

        # 执行优先文档补充逻辑
        ...
        return chunks

class ImageSupplementProcessor(ChunkPostprocessor):
    """图片补充处理器"""

    async def _do_process(self, chunks, state, retriever):
        query = state.get("query", "")
        if not (_want_images(query) or _should_auto_include_diagrams(query)):
            return chunks

        # 执行图片补充逻辑
        ...
        return chunks

# 使用示例
async def node_search_mongodb(state: MongoDBState):
    # ... 执行搜索 ...

    # 构建后处理链
    rebalancer = RebalancingProcessor()
    priority = PriorityFallbackProcessor()
    images = ImageSupplementProcessor()

    rebalancer.set_next(priority).set_next(images)

    # 执行后处理
    chunks = await rebalancer.process(chunks, state, retriever)

    return {"retrieval_results": chunks, ...}
```

---

## 六、优先级总结

### 立即修复 (本周)
1. [P0] 修复 `__init__.py` 注释错误
2. [P0] 移除所有 emoji，替换为纯文本标记
3. [P1] 提取魔法数字为常量

### 短期优化 (2周内)
4. [P1] 拆分 `node_search_mongodb` 函数
5. [P1] 统一异常处理策略
6. [P2] 预编译正则表达式
7. [P2] 并行化优先文档搜索

### 中期改进 (1个月内)
8. [P2] 补充完整的类型提示
9. [P3] 编写单元测试
10. [P3] 创建配置管理模块
11. [P3] 优化日志级别

### 长期重构 (可选)
12. [P3] 引入策略模式重构搜索逻辑
13. [P3] 引入责任链模式重构后处理逻辑

---

## 七、代码质量指标

| 指标 | 当前值 | 目标值 | 优先级 |
|------|--------|--------|--------|
| 函数平均行数 | ~80 行 | <50 行 | P1 |
| 最长函数行数 | 310 行 | <100 行 | P0 |
| 类型提示覆盖率 | ~70% | >90% | P2 |
| 单元测试覆盖率 | 0% | >80% | P3 |
| 魔法数字数量 | ~20 个 | <5 个 | P1 |
| 代码重复率 | ~15% | <10% | P2 |

---

## 八、参考资料

- [Python 代码风格指南 (PEP 8)](https://pep8.org/)
- [Google Python 风格指南](https://google.github.io/styleguide/pyguide.html)
- [Clean Code in Python](https://github.com/zedr/clean-code-python)
- [设计模式：可复用面向对象软件的基础](https://refactoring.guru/design-patterns)

---

**审查人**: Claude Sonnet 4.5
**下次审查**: 2026-02-28
