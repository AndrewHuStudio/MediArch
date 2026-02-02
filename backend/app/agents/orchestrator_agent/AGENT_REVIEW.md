# Orchestrator Agent - 功能说明

**更新日期**: 2026-01-27

---

## 功能概述

Orchestrator Agent 是 MediArch 系统的**意图分析与任务调度中心**，负责：

1. 提取用户查询
2. 判断问题是否与医院建筑设计相关
3. 改写含代词引用的查询（如"它"、"这个"）
4. 决定调用哪些 Worker Agents

---

## 内部节点流程

```
用户输入
    ↓
[extract_query] 提取查询文本
    ↓
[analyze_intent] LLM 分析意图
    ↓
[decide_action] 决定下一步动作
    ↓
    ├─ 相关问题 → [prepare_request] 准备请求 → 调用 Workers
    └─ 不相关 → 返回引导回答
```

### 节点说明

| 节点 | 功能 |
|------|------|
| **extract_query** | 从消息列表中提取最后一条用户查询 |
| **analyze_intent** | 使用 LLM 判断相关性、改写查询、输出置信度 |
| **decide_action** | 根据相关性决定调用哪些 Worker Agents |
| **prepare_request** | 构建标准化的 AgentRequest 对象 |

---

## 输入输出

### 输入
- `messages`: 对话历史
- `query`: 用户查询（可选，优先使用）
- `available_workers`: 可用的 Worker 列表

### 输出
- `is_hospital_related`: 是否与医院设计相关
- `rewritten_query`: 改写后的查询
- `agents_to_call`: 需要调用的 Worker 列表
- `request`: 标准化的 AgentRequest 对象
- `diagnostics`: 诊断信息（含置信度和推理理由）

---

## 技术特点

- **Structured Output**: 使用 Pydantic 模型确保 LLM 输出格式正确
- **异步 LLM 调用**: 使用 `asyncio.to_thread()` 避免阻塞
- **上下文感知**: 支持多轮对话，自动处理代词引用
