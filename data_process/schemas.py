"""
Pydantic 请求/响应模型 -- data_process API
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# ============================================================
# 通用
# ============================================================

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_NETWORK = "waiting_network"
    COMPLETED = "completed"
    FAILED = "failed"


class ProgressUpdate(BaseModel):
    """WebSocket 进度消息格式"""
    task_id: str
    module: str          # "ocr" | "vector" | "kg"
    stage: str           # e.g. "ocr_start", "chunking", "ea_recognition"
    current: int
    total: int
    message: str = ""
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    """异步任务句柄"""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    message: str = ""


class TaskStatusResponse(BaseModel):
    """任务状态查询结果"""
    task_id: str
    status: TaskStatus
    progress: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    resume_payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_hint: Optional[str] = None
    created_at: Optional[str] = None


class UploadedFileInfo(BaseModel):
    """上传文件信息"""
    filename: str
    size_bytes: int
    saved_path: str
    category: str = ""


# ============================================================
# 模块1: OCR
# ============================================================

class OcrRequest(BaseModel):
    """OCR 处理请求"""
    file_path: str = Field(..., description="PDF 文件路径")
    category: str = Field(default="", description="文档分类")
    page_start: Optional[int] = Field(default=None, description="起始页码 (1-based)")
    page_end: Optional[int] = Field(default=None, description="结束页码 (1-based)")
    force: bool = Field(default=False, description="是否强制重跑（忽略已完成状态）")


class OcrBatchRequest(BaseModel):
    """批量 OCR 请求"""
    items: List[OcrRequest]


class OcrResultResponse(BaseModel):
    """OCR 处理结果"""
    file_name: str
    markdown: str
    detail: List[Dict[str, Any]]
    total_pages: int
    success_pages: int
    duration_ms: int
    artifacts_dir: Optional[str] = None


# ============================================================
# 模块2: 向量化
# ============================================================

class VectorizeRequest(BaseModel):
    """向量化请求"""
    ocr_result: Dict[str, Any] = Field(..., description="OCR 结果 (TextIn 兼容格式)")
    doc_metadata: Dict[str, Any] = Field(..., description="文档元数据: title, category, file_path 等")
    force: bool = Field(default=False, description="是否强制重跑（忽略已向量化状态）")


class VectorizeFromOcrRequest(BaseModel):
    """从 OCR 落盘产物发起向量化请求"""
    file_path: str = Field(..., description="PDF 文件绝对路径")
    category: str = Field(..., description="文档分类")
    title: Optional[str] = Field(default=None, description="可选文档标题")
    force: bool = Field(default=False, description="是否强制重跑（忽略已向量化状态）")


class VectorizeResultResponse(BaseModel):
    """向量化结果"""
    doc_id: str
    total_chunks: int
    text_chunks: int
    image_chunks: int
    table_chunks: int
    embeddings_written: int
    chunks_inserted: int
    duration_s: float


class RerankRequest(BaseModel):
    """Rerank 请求"""
    query: str
    chunks: List[Dict[str, Any]] = Field(..., description="需包含 'content' 字段的 chunk 列表")
    top_k: int = Field(default=10, ge=1, le=100)


class RerankResultResponse(BaseModel):
    """Rerank 结果"""
    query: str
    results: List[Dict[str, Any]]
    total: int


# ============================================================
# 模块3: 知识图谱
# ============================================================

class KgBuildRequest(BaseModel):
    """KG 构建请求"""
    source: str = Field(default="mongodb", description="'mongodb' 从数据库读取, 'chunks' 内联提供")
    mongo_doc_ids: Optional[List[str]] = Field(default=None, description="指定 MongoDB doc ID 列表")
    chunks: Optional[List[Dict[str, Any]]] = Field(default=None, description="内联 chunks (source='chunks' 时)")

    # 策略参数 (新增)
    strategy: str = Field(default="B1", description="构建策略: B0/B1/B2/B3 (兼容 E1/E2/E3)")
    custom_config: Optional[Dict[str, Any]] = Field(default=None, description="自定义配置 (strategy='custom' 时)")
    experiment_label: Optional[str] = Field(default=None, description="实验标签 (用于对比实验)")
    save_to_history: bool = Field(default=True, description="是否保存到历史记录")

    # 兼容旧参数
    ea_max_rounds: int = Field(default=5, ge=1, le=10)
    ea_threshold: int = Field(default=3, ge=1)
    rel_max_rounds: int = Field(default=4, ge=1, le=10)
    rel_threshold: int = Field(default=2, ge=1)
    enable_fusion: Optional[bool] = Field(default=None, description="可选: 覆盖策略自带的融合开关")


class StageResultResponse(BaseModel):
    """单阶段结果"""
    stage: str
    rounds: int
    ea_pairs_count: int = 0
    triplets_count: int = 0
    stats: Dict[str, Any] = Field(default_factory=dict)


class KgResultResponse(BaseModel):
    """完整 KG 构建结果"""
    total_entities: int
    total_relations: int
    total_triplets: int
    nodes_written: int
    edges_written: int
    stages: List[StageResultResponse] = Field(default_factory=list)
    fusion_stats: Dict[str, Any] = Field(default_factory=dict)


class KgStageOnlyRequest(BaseModel):
    """单阶段 KG 请求 (步进式控制)"""
    stage: str = Field(..., description="'ea_recognition' | 'relation_extraction' | 'triplet_optimization' | 'cross_document_fusion'")
    strategy: str = Field(default="B1", description="构建策略: B0/B1/B2/B3 (兼容 E1/E2/E3)")
    chunks: Optional[List[Dict[str, Any]]] = None
    ea_pairs: Optional[List[Dict[str, Any]]] = None
    triplets: Optional[List[Dict[str, Any]]] = None
    mongo_doc_ids: Optional[List[str]] = None
