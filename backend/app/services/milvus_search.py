# app/tools/milvus_search.py
"""
Milvus 实体属性检索工具

功能：
1. 从 Milvus Collection 检索实体属性（quantitative + qualitative）
2. 基于语义相似度查找相关实体
3. 返回属性的原始文档来源

数据源：
- Collection: entity_attributes
- Vector: 动态维度（取决于 KG_EMBEDDING_DIM 环境变量）
- Similarity: COSINE
"""

import os
from typing import List, Dict, Any, Optional
from backend.env_loader import load_dotenv
from langchain_core.tools import tool
from pymilvus import Collection, connections
from openai import OpenAI
from backend.llm_env import get_api_key, get_kg_base_url, get_kg_embedding_model

load_dotenv()


class MilvusAttributeRetriever:
    """Milvus 实体属性检索器"""
    
    def __init__(self):
        """初始化 Milvus 连接和 OpenAI 客户端"""
        self.host = os.getenv("MILVUS_HOST", "localhost")
        self.port = os.getenv("MILVUS_PORT", "19530")
        self.collection_name = "entity_attributes"
        
        # 连接 Milvus
        try:
            connections.connect(
                alias="default",
                host=self.host,
                port=self.port
            )
            self.collection = Collection(self.collection_name)
            self.collection.load()
            print(f"[OK] Milvus连接成功: {self.host}:{self.port}")
        except Exception as e:
            print(f"[ERR] Milvus连接失败: {e}")
            raise
        
        # 初始化 OpenAI 客户端（用于生成查询向量）
        self.openai_client = OpenAI(
            api_key=get_api_key(),
            base_url=get_kg_base_url()
        )
        self.embedding_model = get_kg_embedding_model("text-embedding-3-small")
        # 动态读取向量维度（支持 text-embedding-3-large 的 3072 维）
        self.embedding_dim = int(os.getenv("KG_EMBEDDING_DIM", "1536"))
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        生成查询文本的向量表示

        Args:
            text: 查询文本

        Returns:
            动态维度向量（取决于 KG_EMBEDDING_DIM 环境变量）
        """
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text,
                dimensions=self.embedding_dim  # 使用动态维度
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"[ERR] 向量生成失败: {e}")
            raise
    
    def search_attributes(
        self,
        query: str,
        k: int = 5,
        attribute_type: Optional[str] = None,
        min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        检索实体属性
        
        Args:
            query: 查询文本（如"抢救室 面积"）
            k: 返回结果数量
            attribute_type: 属性类型过滤（"quantitative"/"qualitative"/None）
            min_similarity: 最小相似度阈值（0.0-1.0）
        
        Returns:
            属性列表，每个属性包含：
            - entity_name: 实体名称
            - entity_id: 实体ID
            - attribute_type: 属性类型
            - attribute_text: 属性值
            - source_document: 来源文档
            - chunk_id: 来源chunk
            - similarity: 相似度分数
        """
        # 1. 生成查询向量
        query_embedding = self.generate_embedding(query)
        
        # 2. 构建过滤表达式
        expr = None
        if attribute_type:
            expr = f'attribute_type == "{attribute_type}"'
        
        # 3. 执行检索
        try:
            search_params = {
                "metric_type": "COSINE",
                "params": {"nprobe": 10}
            }
            
            results = self.collection.search(
                data=[query_embedding],
                anns_field="vector",
                param=search_params,
                limit=k * 2,  # 多检索一些，用于过滤
                expr=expr,
                output_fields=[
                    "entity_name",
                    "entity_id",
                    "attribute_type",
                    "attribute_text",
                    "source_document",
                    "chunk_id"
                ]
            )
            
            # 4. 格式化结果
            formatted_results = []
            for hits in results:
                for hit in hits:
                    # 跳过低相似度的结果
                    if hit.score < min_similarity:
                        continue
                    
                    formatted_results.append({
                        "entity_name": hit.entity.get("entity_name", ""),
                        "entity_id": hit.entity.get("entity_id", ""),
                        "attribute_type": hit.entity.get("attribute_type", ""),
                        "attribute_text": hit.entity.get("attribute_text", ""),
                        "source_document": hit.entity.get("source_document", ""),
                        "chunk_id": hit.entity.get("chunk_id", ""),
                        "similarity": round(hit.score, 4)
                    })
                    
                    # 达到目标数量后停止
                    if len(formatted_results) >= k:
                        break
            
            return formatted_results[:k]
            
        except Exception as e:
            print(f"[ERR] Milvus检索失败: {e}")
            return []
    
    def format_results_for_display(self, results: List[Dict[str, Any]], query: str) -> str:
        """
        将检索结果格式化为用户友好的文本
        
        Args:
            results: 检索结果列表
            query: 原始查询
        
        Returns:
            格式化的文本输出
        """
        if not results:
            return f"未在Milvus中找到与'{query}'相关的实体属性信息。"
        
        lines = [f"[Milvus检索] 找到 {len(results)} 条相关属性信息：\n"]
        
        # 按实体分组
        entities_map: Dict[str, List[Dict[str, Any]]] = {}
        for result in results:
            entity_name = result["entity_name"]
            if entity_name not in entities_map:
                entities_map[entity_name] = []
            entities_map[entity_name].append(result)
        
        # 格式化输出
        for entity_name, attrs in entities_map.items():
            lines.append(f"\n### {entity_name}")
            
            # 分类显示：定量 vs 定性
            quantitative = [a for a in attrs if a["attribute_type"] == "quantitative"]
            qualitative = [a for a in attrs if a["attribute_type"] == "qualitative"]
            
            if quantitative:
                lines.append("\n**定量属性：**")
                for attr in quantitative:
                    lines.append(
                        f"- {attr['attribute_text']} "
                        f"(相似度: {attr['similarity']:.2f}, "
                        f"来源: {attr['source_document']})"
                    )
            
            if qualitative:
                lines.append("\n**定性属性：**")
                for attr in qualitative:
                    # 截取较长的定性描述
                    text = attr['attribute_text']
                    if len(text) > 100:
                        text = text[:100] + "..."
                    
                    lines.append(
                        f"- {text} "
                        f"(相似度: {attr['similarity']:.2f}, "
                        f"来源: {attr['source_document']})"
                    )
        
        lines.append("\n")
        lines.append("---")
        lines.append("**数据来源**: Milvus Vector Database (entity_attributes)")
        
        return "\n".join(lines)
    
    def close(self):
        """关闭 Milvus 连接"""
        try:
            connections.disconnect("default")
        except:
            pass


# 全局实例
_retriever = None


def get_retriever() -> MilvusAttributeRetriever:
    """获取全局检索器实例（单例模式）"""
    global _retriever
    if _retriever is None:
        _retriever = MilvusAttributeRetriever()
    return _retriever


# ========================================
# LangChain Tool 封装
# ========================================

@tool("milvus_attribute_search")
def milvus_attribute_search(query: str, k: int = 5) -> str:
    """
    从Milvus检索实体属性信息（包括定量和定性属性）。
    
    适用场景：
    - 查询设计规范参数（如"抢救室面积标准"）
    - 查询技术要求（如"手术室净高要求"）
    - 查询功能要求（如"门诊大厅配置"）
    
    输入参数：
    - query: 查询关键词（如"抢救室 面积 净高"）
    - k: 返回结果数量（默认5）
    
    输出：
    返回格式化的属性列表，包含：
    - 实体名称
    - 属性值（定量/定性）
    - 来源文档和相似度
    """
    try:
        retriever = get_retriever()
        results = retriever.search_attributes(query=query, k=k)
        return retriever.format_results_for_display(results, query)
    except Exception as e:
        return f"Milvus检索失败：{e}"


@tool("milvus_quantitative_search")
def milvus_quantitative_search(query: str, k: int = 5) -> str:
    """
    从Milvus检索定量属性（数值型参数）。
    
    适用场景：
    - 查询面积标准
    - 查询净高要求
    - 查询尺寸参数
    
    输入参数：
    - query: 查询关键词（如"抢救室 面积"）
    - k: 返回结果数量（默认5）
    
    输出：
    返回格式化的定量属性列表，重点突出数值信息。
    """
    try:
        retriever = get_retriever()
        results = retriever.search_attributes(
            query=query,
            k=k,
            attribute_type="quantitative"
        )
        return retriever.format_results_for_display(results, query)
    except Exception as e:
        return f"Milvus检索失败：{e}"


@tool("milvus_qualitative_search")
def milvus_qualitative_search(query: str, k: int = 5) -> str:
    """
    从Milvus检索定性属性（描述性信息）。
    
    适用场景：
    - 查询功能要求
    - 查询设计原则
    - 查询配置说明
    
    输入参数：
    - query: 查询关键词（如"急诊部 功能要求"）
    - k: 返回结果数量（默认5）
    
    输出：
    返回格式化的定性属性列表，重点突出描述性信息。
    """
    try:
        retriever = get_retriever()
        results = retriever.search_attributes(
            query=query,
            k=k,
            attribute_type="qualitative"
        )
        return retriever.format_results_for_display(results, query)
    except Exception as e:
        return f"Milvus检索失败：{e}"


# 导出工具列表
tools = [
    milvus_attribute_search,
    milvus_quantitative_search,
    milvus_qualitative_search
]


# ========================================
# 测试代码
# ========================================

if __name__ == "__main__":
    print("=" * 80)
    print("Milvus 实体属性检索工具测试")
    print("=" * 80)
    print()
    
    # 测试查询
    test_queries = [
        ("抢救室 面积 净高", 5),
        ("门诊大厅 配置", 3),
        ("手术室 设计要求", 5)
    ]
    
    retriever = get_retriever()
    
    for query, k in test_queries:
        print(f"\n查询：{query} (Top {k})")
        print("-" * 80)
        
        # 测试综合检索
        result = milvus_attribute_search.invoke({"query": query, "k": k})
        print(result)
        print()
    
    # 测试定量属性检索
    print("\n" + "=" * 80)
    print("定量属性专项测试")
    print("=" * 80)
    result = milvus_quantitative_search.invoke({"query": "抢救室 面积", "k": 3})
    print(result)
    
    retriever.close()

