# MediArch FastAPI Backend - API Documentation

## 概述

MediArch FastAPI 是一个现代化的、企业级的后端API服务，为综合医院设计问答助手提供强大的智能检索和咨询功能。

### 核心特性

- **可扩展性**: 模块化路由设计，支持API版本管理
- **适应性**: 多环境配置，CORS支持，中间件架构
- **健壮性**: 完善的错误处理、日志记录、类型验证
- **流式响应**: Server-Sent Events 支持，实现实时对话
- **LangGraph集成**: 深度整合智能体系统，支持多Agent协作

### 技术栈

- **FastAPI** 0.119.0 - 现代化的Python Web框架
- **Pydantic** 2.x - 数据验证和序列化
- **LangGraph** 1.0.2 - 智能体编排和对话管理
- **Uvicorn** - ASGI服务器
- **Python** 3.11+

---

## 快速开始

### 1. 环境配置

```bash
# 克隆项目
cd backend/api

# 安装依赖
pip install -r ../../requirements.txt

# 配置环境变量
cp ../../.env.example ../../.env
# 编辑 .env 文件，填入必要的配置
```

### 2. 启动开发服务器

```bash
# 方式1：使用 main.py（推荐开发环境）
python backend/api/main.py

# 方式2：使用 uvicorn（更灵活）
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000

# 方式3：使用 uvicorn 并自定义配置
uvicorn backend.api.main:app --reload --port 8000 --log-level info
```

### 3. 访问API文档

启动后，访问以下地址：

- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc
- **OpenAPI JSON**: http://localhost:8000/api/openapi.json

---

## API端点

### 1. 对话聊天 API

#### POST /api/v1/chat

发送消息并获取完整回复（非流式）

**请求体**:
```json
{
  "message": "医院手术室的设计标准是什么？",
  "session_id": "optional-session-id",
  "history": [],
  "top_k": 8,
  "include_online_search": true,
  "stream": false,
  "include_citations": true,
  "include_diagnostics": false
}
```

**响应**:
```json
{
  "message": "详细回答...",
  "session_id": "session-abc123",
  "knowledge_graph_path": {
    "original_query": "...",
    "expanded_entities": [...]
  },
  "citations": [
    {
      "source": "综合医院建筑设计规范",
      "location": "第45页 第3章 手术部设计",
      "snippet": "..."
    }
  ],
  "recommended_questions": [
    "手术室的空气净化等级要求是什么？"
  ],
  "took_ms": 1500,
  "agents_used": ["neo4j_agent", "milvus_agent", "mongodb_agent"]
}
```

#### POST /api/v1/chat/stream

发送消息并获取流式回复（Server-Sent Events）

**请求体**: 同上

**响应**: text/event-stream

```
data: {"chunk_type":"session","content":"session-abc123","is_final":false}

data: {"chunk_type":"content","content":"医院手术室的设计标准主要包括...","is_final":false}

data: {"chunk_type":"content","content":"功能布局、空气净化、设备配置等方面...","is_final":false}

data: {"chunk_type":"citations","citations":[...],"is_final":false}

data: {"chunk_type":"recommendations","recommended_questions":[...],"is_final":false}

data: {"chunk_type":"done","is_final":true}
```

**前端示例**:
```javascript
const eventSource = new EventSource('/api/v1/chat/stream');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);

  switch (data.chunk_type) {
    case 'session':
      console.log('Session ID:', data.content);
      break;
    case 'content':
      // 逐步显示回复内容
      appendMessage(data.content);
      break;
    case 'citations':
      // 显示引用信息
      displayCitations(data.citations);
      break;
    case 'done':
      // 完成
      eventSource.close();
      break;
  }
};
```

#### GET /api/v1/chat/sessions

获取所有会话列表

**响应**:
```json
{
  "sessions": [
    {
      "session_id": "session-abc123",
      "created_at": 1700000000.0,
      "last_active": 1700001000.0,
      "message_count": 10,
      "title": "医院手术室的设计标准是什么？"
    }
  ],
  "total": 1
}
```

#### GET /api/v1/chat/sessions/{session_id}/history

获取指定会话的对话历史

**响应**:
```json
{
  "session_id": "session-abc123",
  "messages": [
    {
      "role": "user",
      "content": "医院手术室的设计标准是什么？",
      "timestamp": 1700000000.0,
      "citations": []
    },
    {
      "role": "assistant",
      "content": "详细回答...",
      "timestamp": 1700000001.5,
      "citations": [...]
    }
  ],
  "total": 2
}
```

#### DELETE /api/v1/chat/sessions/{session_id}

删除指定会话

**响应**:
```json
{
  "message": "会话已删除",
  "session_id": "session-abc123"
}
```

---

### 2. 知识库 API

#### GET /api/v1/kb/categories

获取知识库分类列表

**响应**:
```json
{
  "categories": [
    {
      "id": "regulations",
      "name": "规范标准",
      "description": "国家及行业医疗建筑设计规范",
      "icon": "FileText",
      "item_count": 45,
      "tags": ["规范", "标准", "法规"]
    },
    {
      "id": "books",
      "name": "专业书籍",
      "description": "医院建筑设计专业教材和参考书",
      "icon": "BookOpen",
      "item_count": 128,
      "tags": ["教材", "参考书", "设计手册"]
    }
  ],
  "total": 2
}
```

#### GET /api/v1/kb/categories/{category_id}/items

获取分类下的知识库条目（分页）

**查询参数**:
- `page`: 页码（默认1）
- `page_size`: 每页数量（默认20，最大100）

**响应**:
```json
{
  "items": [
    {
      "id": "item-123",
      "title": "综合医院建筑设计规范 GB 51039-2014",
      "category": "regulations",
      "source": "国家标准",
      "description": "规范医院建筑设计的国家标准...",
      "tags": ["规范", "标准"],
      "chunk_count": 150,
      "page_count": 200,
      "created_at": 1700000000.0
    }
  ],
  "category": "regulations",
  "total": 45,
  "page": 1,
  "page_size": 20,
  "total_pages": 3
}
```

#### POST /api/v1/kb/search

搜索知识库

**请求体**:
```json
{
  "query": "手术室设计",
  "category": "regulations",
  "tags": ["规范"],
  "top_k": 10
}
```

**响应**:
```json
{
  "items": [...],
  "query": "手术室设计",
  "total": 5,
  "took_ms": 100
}
```

---

### 3. 健康检查和监控 API

#### GET /api/v1/health

快速健康检查（用于负载均衡器）

**响应**:
```json
{
  "status": "ok",
  "message": "MediArch API is running",
  "timestamp": 1700000000.0
}
```

#### GET /api/v1/health/detailed

详细健康状态检查（检查所有Agent和数据库）

**响应**:
```json
{
  "overall_status": "healthy",
  "timestamp": 1700000000.0,
  "agents": [
    {
      "name": "Neo4j Agent",
      "status": "healthy",
      "agent_type": "worker",
      "compilation_status": "compiled",
      "latency_ms": 25.3,
      "message": "Agent运行正常",
      "last_check": 1700000000.0
    }
  ],
  "databases": [
    {
      "name": "Neo4j",
      "status": "healthy",
      "latency_ms": 15.2,
      "message": "neo4j连接正常",
      "last_check": 1700000000.0
    }
  ],
  "external_services": [],
  "system_metrics": {}
}
```

#### GET /api/v1/metrics

获取系统性能指标

**响应**:
```json
{
  "timestamp": 1700000000.0,
  "api_metrics": {
    "total_requests": 1000,
    "requests_per_second": 10.5,
    "average_response_time_ms": 500.0,
    "error_rate": 0.01
  },
  "agent_metrics": {
    "neo4j_avg_latency_ms": 200.0,
    "milvus_avg_latency_ms": 150.0,
    "mongodb_avg_latency_ms": 100.0
  },
  "system_metrics": {
    "cpu_usage_percent": 45.0,
    "memory_usage_percent": 60.0
  }
}
```

---

## 配置管理

### 环境变量

在 `.env` 文件中配置以下变量：

```bash
# ==================== 应用基础配置 ====================
APP_NAME=MediArch API
VERSION=1.0.0
ENVIRONMENT=development  # development | staging | production
DEBUG=true

# ==================== API 服务器配置 ====================
API_HOST=0.0.0.0
API_PORT=8000
API_PREFIX=/api/v1

# ==================== CORS 配置 ====================
CORS_ORIGINS=["http://localhost:3000","http://localhost:7860"]

# ==================== LangGraph MediArch Graph 配置 ====================
PRELOAD_SUPERVISOR=true  # 启动时预热 MediArch Graph
SUPERVISOR_TIMEOUT_MS=30000

# ==================== 会话管理配置 ====================
SESSION_EXPIRE_HOURS=24
MAX_HISTORY_LENGTH=20

# ==================== 流式响应配置 ====================
ENABLE_SSE=true
SSE_RETRY_MS=3000
SSE_HEARTBEAT_INTERVAL_S=15

# ==================== 日志配置 ====================
LOG_LEVEL=INFO
LOG_FORMAT=%(asctime)s - %(name)s - %(levelname)s - %(message)s
LOG_FILE=logs/mediarch_api.log

# ==================== 数据库配置 ====================
POSTGRES_CHECKPOINT_URI=postgresql://postgres:postgres@localhost:5432/mediarch_checkpoints?sslmode=disable

# ==================== 安全配置 ====================
API_KEY_ENABLED=false
API_KEY_HEADER=X-API-Key
API_KEYS=[]

RATE_LIMIT_ENABLED=false
RATE_LIMIT_PER_MINUTE=60

# ==================== 性能配置 ====================
MAX_CONCURRENT_REQUESTS=10
REQUEST_TIMEOUT_S=60
```

### 多环境配置

支持通过文件后缀区分环境：

```
.env                    # 默认配置
.env.development        # 开发环境
.env.staging            # 预发布环境
.env.production         # 生产环境
```

设置 `ENVIRONMENT` 环境变量自动加载对应配置：

```bash
export ENVIRONMENT=production
python backend/api/main.py
```

---

## 部署指南

### 开发环境

```bash
# 使用内置的 uvicorn 开发服务器
python backend/api/main.py

# 或使用 uvicorn 命令
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 生产环境

#### 方式1：使用 Gunicorn + Uvicorn Workers

```bash
pip install gunicorn

gunicorn backend.api.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile - \
  --log-level info
```

#### 方式2：使用 Docker

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY .env .env

EXPOSE 8000

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# 构建镜像
docker build -t mediarch-api:latest .

# 运行容器
docker run -d \
  --name mediarch-api \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env \
  mediarch-api:latest
```

#### 方式3：使用 Docker Compose（推荐）

```yaml
# docker-compose.yml
version: '3.8'

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - ENVIRONMENT=production
      - POSTGRES_CHECKPOINT_URI=postgresql://postgres:postgres@postgres:5432/mediarch_checkpoints
    depends_on:
      - postgres
      - neo4j
      - mongo
      - milvus
    volumes:
      - ./logs:/app/logs

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: mediarch_checkpoints
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f api

# 停止服务
docker-compose down
```

---

## 错误处理

### 标准错误响应格式

所有错误响应遵循统一格式：

```json
{
  "error": {
    "code": 500,
    "message": "Internal server error",
    "detail": "具体错误信息（仅在非生产环境显示）",
    "path": "/api/v1/chat"
  }
}
```

### 常见错误码

| HTTP状态码 | 含义 | 示例场景 |
|-----------|------|---------|
| 400 | 请求参数错误 | 缺少必填字段、格式不正确 |
| 404 | 资源不存在 | 会话ID不存在、分类不存在 |
| 422 | 请求验证失败 | Pydantic 数据验证错误 |
| 500 | 服务器内部错误 | 智能体执行失败、数据库连接失败 |
| 503 | 服务不可用 | 健康检查失败 |

---

## 性能优化

### 1. 预热 MediArch Graph

在 `.env` 中启用：

```bash
PRELOAD_SUPERVISOR=true
```

### 2. 调整并发限制

```bash
MAX_CONCURRENT_REQUESTS=10  # 根据服务器性能调整
```

### 3. 启用Redis缓存（可选）

```python
# TODO: 实现Redis缓存层
# 缓存知识库分类、热门查询结果等
```

### 4. 使用CDN加速静态资源

将前端静态资源部署到CDN，后端API专注于数据处理。

---

## 监控和日志

### 日志文件

日志文件位置：`logs/mediarch_api.log`

### 日志级别

- `DEBUG`: 详细调试信息
- `INFO`: 一般信息（默认）
- `WARNING`: 警告信息
- `ERROR`: 错误信息
- `CRITICAL`: 严重错误

### 集成 Prometheus + Grafana（待实现）

```python
# TODO: 添加 Prometheus metrics 端点
# /metrics - 暴露系统指标
```

---

## 安全性

### CORS配置

在生产环境，务必配置具体的允许来源：

```bash
CORS_ORIGINS=["https://yourdomain.com","https://app.yourdomain.com"]
```

### API Key 认证（可选）

启用API Key认证：

```bash
API_KEY_ENABLED=true
API_KEYS=["your-secret-key-1","your-secret-key-2"]
```

请求时携带Header：

```
X-API-Key: your-secret-key-1
```

### 速率限制（可选）

启用速率限制：

```bash
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60
```

---

## 常见问题

### Q1: 如何调试Agent执行流程？

在请求中设置 `include_diagnostics=true`：

```json
{
  "message": "测试查询",
  "include_diagnostics": true
}
```

### Q2: 如何持久化会话数据？

当前使用内存存储。生产环境建议：

1. 使用PostgreSQL的checkpointer功能（LangGraph内置）
2. 或实现Redis存储

### Q3: 如何增加新的API端点？

1. 在 `backend/api/routers/` 创建新的路由文件
2. 在 `backend/api/schemas/` 定义数据模型
3. 在 `backend/api/main.py` 注册路由

```python
from backend.api.routers import new_router

app.include_router(
    new_router.router,
    prefix="/api/v1",
    tags=["New Feature"]
)
```

---

## 联系方式

- **项目地址**: [GitHub](https://github.com/your-repo/mediarch)
- **问题反馈**: [Issues](https://github.com/your-repo/mediarch/issues)
- **文档**: [Wiki](https://github.com/your-repo/mediarch/wiki)

---

**最后更新**: 2025-01-16
**版本**: 1.0.0
**作者**: MediArch Team
