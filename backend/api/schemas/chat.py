# backend/api/schemas/chat.py
"""
对话聊天相关数据模型
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.api.schemas.common import Citation, DiagnosticInfo


class ChatMessage(BaseModel):
    """对话消息"""
    role: str = Field(description="角色: user/assistant/system")
    content: str = Field(description="消息内容")
    timestamp: Optional[float] = Field(default=None, description="时间戳")
    citations: Optional[List[Citation]] = Field(default_factory=list, description="引用列表")
    images: Optional[List[str]] = Field(default_factory=list, description="相关图片URL列表")


class ChatRequest(BaseModel):
    """对话请求"""
    message: str = Field(min_length=1, max_length=2000, description="用户问题")
    session_id: Optional[str] = Field(default=None, description="会话ID")
    history: Optional[List[ChatMessage]] = Field(default_factory=list, description="对话历史")

    # 过滤器（可选）：用于限定检索范围（如指定资料/类别/内容类型等）
    filters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="检索过滤器（可选）")

    # 检索参数
    top_k: Optional[int] = Field(default=8, ge=1, le=50, description="返回结果数量")
    include_online_search: Optional[bool] = Field(default=False, description="是否包含在线搜索（测试阶段默认关闭）")

    # 响应配置
    stream: Optional[bool] = Field(default=True, description="是否使用流式响应")
    include_citations: Optional[bool] = Field(default=True, description="是否包含引用信息")
    include_diagnostics: Optional[bool] = Field(default=False, description="是否包含诊断信息")
    max_citations: Optional[int] = Field(default=10, ge=1, le=100, description="最大返回 citations 数量（默认10）")


class AgentStatusUpdate(BaseModel):
    """智能体状态更新"""
    agent_name: str = Field(description="智能体名称")
    status: str = Field(description="状态: pending/running/completed/error")
    thought: Optional[str] = Field(default=None, description="当前思考内容")
    progress: Optional[float] = Field(default=None, ge=0, le=1, description="进度 0-1")
    took_ms: Optional[int] = Field(default=None, description="耗时（毫秒）")


class ChatResponse(BaseModel):
    """对话响应"""
    message: str = Field(description="助手回复")
    session_id: str = Field(description="会话ID")

    # 知识图谱信息
    knowledge_graph_path: Optional[Dict[str, Any]] = Field(default=None, description="知识图谱推理路径")

    # 引用信息
    citations: Optional[List[Citation]] = Field(default_factory=list, description="文档引用")

    # 推荐问题
    recommended_questions: Optional[List[str]] = Field(default_factory=list, description="推荐后续问题")

    # 图片
    images: Optional[List[str]] = Field(default_factory=list, description="相关图片URL列表")

    # 诊断信息（调试用）
    diagnostics: Optional[List[DiagnosticInfo]] = Field(default_factory=list, description="诊断信息")

    # 元数据
    took_ms: Optional[int] = Field(default=None, description="总处理时间（毫秒）")
    agents_used: Optional[List[str]] = Field(default_factory=list, description="使用的智能体列表")


class StreamingChatChunk(BaseModel):
    """流式响应块

    支持的块类型:
    - session: 会话ID
    - agent_status: 智能体状态更新
    - content: 回复内容片段
    - citations: 引用信息
    - knowledge_graph: 知识图谱数据
    - recommendations: 推荐问题
    - images: 相关图片
    - done: 完成信号
    - error: 错误信息
    """
    chunk_type: str = Field(description="块类型")
    content: Optional[str] = Field(default=None, description="内容片段")
    citations: Optional[List[Citation]] = Field(default=None, description="引用信息")
    knowledge_graph_path: Optional[Dict[str, Any]] = Field(default=None, description="知识图谱路径")
    recommended_questions: Optional[List[str]] = Field(default=None, description="推荐问题")
    diagnostics: Optional[List[DiagnosticInfo]] = Field(default=None, description="诊断信息")
    agent_status: Optional[Dict[str, Any]] = Field(default=None, description="智能体状态更新")
    images: Optional[List[str]] = Field(default=None, description="相关图片URL列表")
    is_final: bool = Field(default=False, description="是否为最后一块")


class SessionInfo(BaseModel):
    """会话信息"""
    session_id: str = Field(description="会话ID")
    created_at: float = Field(description="创建时间戳")
    last_active: float = Field(description="最后活跃时间")
    message_count: int = Field(description="消息数量")
    title: Optional[str] = Field(default=None, description="会话标题")
    is_pinned: Optional[bool] = Field(default=False, description="是否置顶")


class SessionListResponse(BaseModel):
    """会话列表响应"""
    sessions: List[SessionInfo] = Field(description="会话列表")
    total: int = Field(description="总数")


class SessionHistoryResponse(BaseModel):
    """会话历史响应"""
    session_id: str = Field(description="会话ID")
    messages: List[ChatMessage] = Field(description="消息历史")
    total: int = Field(description="消息总数")
