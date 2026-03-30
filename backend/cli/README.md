# MediArch Backend CLI Tools

统一的命令行工具集，用于数据库管理和批量操作。

## 📦 安装依赖

确保已安装所有必要的依赖：

```bash
pip install rich pymongo pymilvus python-dotenv
```

## 🚀 可用命令

### 1. 批量文档索引（向量化）

**功能**: OCR → 分块 → 向量化 → 写入 MongoDB + Milvus

```bash
# 基本用法：批量索引所有文档
python -m backend.cli.batch_indexer

# 强制重新索引（忽略已存在的文档）
python -m backend.cli.batch_indexer --force

# 仅处理指定类别
python -m backend.cli.batch_indexer --category 标准规范

# 详细模式（显示每个文档的处理结果）
python -m backend.cli.batch_indexer --force --verbose

# 跳过最后的验证步骤
python -m backend.cli.batch_indexer --skip-validation

# 使用不同的 OCR 引擎
python -m backend.cli.batch_indexer --engine marker
```

**参数说明**:
- `--force`: 强制重新索引（忽略 MongoDB 中已存在的文档）
- `--category`: 指定处理的类别（标准规范/参考论文/书籍报告/政策文件）
- `--engine`: OCR 引擎（mineru/marker，默认 mineru）
- `--verbose`: 显示详细日志
- `--skip-validation`: 跳过验证步骤

**输出示例**:
```
┌──────────────────────────────────────────────┐
│         MediArch 批量文档索引器              │
│         向量化 + MongoDB + Milvus            │
└──────────────────────────────────────────────┘

正在初始化索引 Pipeline...
Pipeline 初始化完成

╭─────── 配置信息 ───────╮
│ OCR 引擎      mineru   │
│ 强制重新索引  是        │
│ 类别过滤      无        │
│ 详细日志      否        │
╰────────────────────────╯

正在扫描文档目录...
找到 15 个 PDF 文件

处理文档... ━━━━━━━━━━━━━━━━━ 100% 0:05:23

╭────────── 批量索引统计 ──────────╮
│ 总文件数   15                    │
│ 成功       14                    │
│ 跳过       0                     │
│ 失败       1                     │
│ 总耗时     323.45 秒             │
│ 平均速度   23.10 秒/文档         │
╰──────────────────────────────────╯
```

---

### 1.5 图片 VLM 回填（不重跑 OCR）

**功能**: 基于已入库的 `image chunks` 与已落盘的图片文件，补齐 VLM 描述并同步 Milvus 向量（不触发 MinerU/OCR）。

```bash
# 先看会处理多少（不调用 VLM，不写库）
python -m backend.cli.vlm_backfill_images

# 真正执行（会调用 VLM + embedding，并更新 Mongo + Milvus）
python -m backend.cli.vlm_backfill_images --apply

# 仅处理某个资料（doc_id 可重复）
python -m backend.cli.vlm_backfill_images --doc-id <doc_id> --apply

# 控制成本：每份资料最多处理 30 张图
python -m backend.cli.vlm_backfill_images --max-images-per-doc 30 --apply
```

**推荐用法**：如果你的 `.env` 配置了 `MINERU_API_URL`（远程 OCR），优先用这个命令做 VLM 全量覆盖，避免 `--force` 重跑 OCR。

---

### 1.6 VLM 覆盖率报告（按资料统计）

**功能**: 一键列出每份资料的图片数量、可用 image_url 数、VLM 成功数与覆盖率，方便你“挑资料分批做 VLM”。

```bash
# 列出所有资料的图片 VLM 覆盖率
python -m backend.cli.vlm_doc_status

# 只看未完成的资料
python -m backend.cli.vlm_doc_status --only-missing

# 只看覆盖率 < 60% 的资料
python -m backend.cli.vlm_doc_status --min-ratio 0.6
```

---

### 1.7 交互式 VLM 管理器（终端选择资料并开始回填）

**功能**: 在终端里以表格方式展示每份资料的 VLM 覆盖率/质量，并支持选择资料直接启动 `vlm_backfill_images` 回填。

```bash
python -m backend.cli.vlm_manager

# 只看“未完成”的资料
python -m backend.cli.vlm_manager --only missing
```

**时间/费用估算**：
- 默认会读取 `VLM_USAGE_LOG_FILE`（JSONL）里的最近调用日志，计算“每张图平均耗时/费用”。
- 若你想显示 USD 费用，建议在 `.env` 里设置其一：
  - `VLM_PRICE_PER_CALL_USD=0.013`（按次估算，最简单）
  - 或 `VLM_PROMPT_PRICE_PER_MTOK` + `VLM_COMPLETION_PRICE_PER_MTOK`（按 token 估算）

---

### 2. 构建知识图谱

**功能**: 从 MongoDB chunks 抽取实体/关系 → 写入 Neo4j

```bash
# 基本用法：构建知识图谱
python -m backend.cli.build_kg

# 跳过磁盘空间检查
set SKIP_DISK_CHECK=1
python -m backend.cli.build_kg

# 配置 Schema 和种子数据路径
set KG_SCHEMA_PATH=backend/databases/graph/schemas/medical_architecture.json
set KG_SEED_DATA_PATH=backend/databases/graph/schemas/ontology_seed_data.json
python -m backend.cli.build_kg
```

**环境变量配置**:
```bash
# 必需环境变量（在 .env 文件中配置）
MEDIARCH_API_KEY=your_deepseek_api_key
MEDIARCH_KG_BASE_URL=https://api.deepseek.com/v1
MEDIARCH_KG_MODEL=deepseek-v3
MONGODB_URI=mongodb://admin:mediarch2024@localhost:27017/mediarch?authSource=admin
NEO4J_URI=bolt://localhost:7687
KG_SCHEMA_PATH=backend/databases/graph/schemas/medical_architecture.json
KG_SEED_DATA_PATH=backend/databases/graph/schemas/ontology_seed_data.json

# 可选配置
SKIP_DISK_CHECK=0                     # 是否跳过磁盘检查

# 成本估算参数（如有变化可调整）
KG_AVG_PROMPT_TOKENS=1500
KG_AVG_COMPLETION_TOKENS=620
KG_PROMPT_TOKEN_MULTIPLIER=0.125
KG_COMPLETION_TOKEN_MULTIPLIER=4.0
KG_PROMPT_PRICE_PER_MTOK=0.25
KG_COMPLETION_PRICE_PER_MTOK=1.0
```

**输出示例**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        医疗建筑知识图谱构建
              DeepSeek V3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

╭────────── 配置信息 ──────────╮
│ LLM模型    deepseek-v3       │
│ MongoDB    mongodb://...     │
│ Neo4j      bolt://...        │
╰──────────────────────────────╯

正在初始化构建器...
✓ 构建器初始化完成

抽取进度 5000/5000 | 剩余 0 | 成功 4850 跳过 100 失败 50 | ETA 00:00
✓ 实体关系抽取完成
✓ 数据写入完成

╭────────── 构建统计 ──────────╮
│ 处理chunks数     5000        │
│ 成功chunks数     4850        │
│ NetworkX节点数   12500       │
│ NetworkX边数     35000       │
│ 总耗时           3425.67秒   │
│ 平均速度         87.6 chunks/分钟 │
│ Neo4j节点数      11800       │
│ Neo4j边数        32000       │
╰──────────────────────────────╯

💰 估算成本：$1.2450 USD
```

**预计耗时**: 3-6 小时（取决于 chunks 数量）
**预计成本**: $0.50-2.00 USD

---

### 3. 高级批量索引器（支持分页段处理）

**注意**: `run_poc.py` 提供了更高级的功能，包括分页段处理和断点续传。

如需使用高级功能，请直接运行：

```bash
cd backend/databases/ingestion
python run_poc.py
```

**高级功能**:
- ✅ 分页段处理（page_range）
- ✅ 断点续传和缺口填补
- ✅ 更精细的进度追踪
- ✅ 支持 INGEST_PAGE_RANGE 环境变量

---

## 📂 项目结构

```
backend/
├── cli/                           # CLI 工具集（新增）
│   ├── __init__.py
│   ├── batch_indexer.py          # 简单批量索引器
│   ├── build_kg.py               # 知识图谱构建器（包装）
│   └── README.md                 # 本文档
│
├── databases/
│   ├── ingestion/
│   │   ├── run_poc.py            # 高级批量索引器（保留）
│   │   └── indexing/
│   │       └── pipeline.py       # 核心处理流程
│   │
│   └── graph/
│       ├── build_kg_with_deepseek.py  # 知识图谱构建（保留）
│       └── builders/
│           └── kg_builder.py     # 图谱构建器
│
└── ...
```

---

## 🗑️ 已废弃的脚本

以下脚本已被 CLI 模块替代，建议移到 `scripts/deprecated/` 目录：

- ❌ `scripts/reindex_documents.py` - 已被 `backend/cli/batch_indexer.py` 替代

---

## 🔄 完整工作流

### 从零开始构建系统：

```bash
# 1. 确保 Docker 服务运行
docker ps | findstr "mongo\|milvus\|neo4j"

# 2. 批量索引文档（向量化）
python -m backend.cli.batch_indexer --force

# 3. 构建知识图谱
python -m backend.cli.build_kg

# 4. 验证数据
# MongoDB
mongosh "mongodb://admin:mediarch2024@localhost:27017/mediarch?authSource=admin"
> db.mediarch_chunks.countDocuments()
> db.documents.countDocuments()

# Neo4j Browser
http://localhost:7474
MATCH (n) RETURN count(n);

# 5. 启动 API 服务器（在项目根目录执行）
python -m backend.api

# 6. 启动前端
cd frontend
pnpm dev
```

---

## ⚙️ 环境变量参考

请确保 `.env` 文件包含以下配置：

```env
# OCR 配置
OCR_ENGINE=mineru
OCR_OUTPUT_DIR=backend/databases/documents_ocr
MINERU_PROJECT_ROOT=E:/MyPrograms/250804-MediArch System
MINERU_EXE=mineru
MINERU_BACKEND=pipeline
MINERU_USE_CUDA=0

# 分块配置
CHUNK_MAX=1200
CHUNK_MIN=100
CHUNK_OVERLAP=100

# 向量化配置
MEDIARCH_API_KEY=your_openai_api_key
MEDIARCH_LLM_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-large

# MongoDB
MONGODB_URI=mongodb://admin:mediarch2024@localhost:27017/mediarch?authSource=admin
MONGODB_DATABASE=mediarch

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=mediarch2024

# 知识图谱构建
MEDIARCH_API_KEY=your_deepseek_api_key
MEDIARCH_KG_BASE_URL=https://api.deepseek.com/v1
MEDIARCH_KG_MODEL=deepseek-v3

# 强制重新索引（可选）
FORCE_REINGEST=0
```

---

## 🆘 常见问题

**Q: batch_indexer 和 run_poc 有什么区别？**

A:
- `batch_indexer`: 简单易用，适合全量重新索引
- `run_poc`: 功能强大，支持分页段处理和断点续传，适合大规模增量更新

**Q: 如何查看构建进度？**

A: 所有 CLI 工具都使用 Rich 库显示实时进度条和统计信息。

**Q: 构建失败后如何恢复？**

A:
- 向量索引：支持断点续传，已处理的文档会被跳过
- 知识图谱：支持断点续传，已抽取的 chunks 会被跳过（基于 MongoDB extractions 集合）

**Q: 如何清空数据库重新开始？**

A: 参考 `dev_md/Docker命令清空数据库指南.md`

---

## 📝 版本历史

- **v1.0.0** (2025-12-16): 初始版本，集成批量索引和知识图谱构建功能
