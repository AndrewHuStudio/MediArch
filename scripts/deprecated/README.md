# 已废弃的脚本

本目录包含已被新的 CLI 模块替代的旧脚本。

## 📁 文件列表

### reindex_documents.py
**状态**: 已废弃
**替代方案**: `python -m backend.cli.batch_indexer`
**废弃原因**: 功能已完全集成到 backend/cli 模块中
**废弃日期**: 2025-12-16

---

## 🔄 迁移指南

如果你之前使用：
```bash
python scripts/reindex_documents.py
```

请改用：
```bash
python -m backend.cli.batch_indexer --force
```

完整功能对照：

| 旧命令 | 新命令 |
|--------|--------|
| `python scripts/reindex_documents.py` | `python -m backend.cli.batch_indexer --force` |
| 无参数 | `--force`: 强制重新索引 |
| 无参数 | `--category 标准规范`: 指定类别 |
| 无参数 | `--verbose`: 详细日志 |

---

## 💡 为什么保留这些文件？

1. **历史参考**: 保留原始实现作为参考
2. **回退方案**: 如果新模块出现问题，可以临时回退
3. **学习资料**: 可以对比新旧实现，了解改进点

---

如需使用这些旧脚本，请确保路径和依赖仍然正确。
建议在 3 个月后（2025-03-16）彻底删除这些文件。
