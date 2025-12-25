"""
MediArch CLI Tools
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Backend 命令行工具集，用于数据库管理和批量操作。

Available Commands:
    python -m backend.cli.batch_indexer    # 批量索引文档
    python -m backend.cli.build_kg         # 构建知识图谱
    python -m backend.cli.vlm_backfill_images  # 图片 VLM 回填（不重跑 OCR）
    python -m backend.cli.vlm_doc_status       # VLM 覆盖率报告（按资料）
    python -m backend.cli.vlm_manager          # 交互式 VLM 管理器（选择资料并回填）
"""

__version__ = "1.0.0"
