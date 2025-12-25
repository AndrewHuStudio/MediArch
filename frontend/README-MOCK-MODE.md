# 前端离线演示模式使用说明

## 功能概述

前端现在支持**离线演示模式**（Mock Mode），在没有后端接入的情况下，可以使用预设的 Markdown 内容作为默认输出，展示系统的图文并茂功能。

## 如何启用离线演示模式

### 方法 1：修改环境变量（推荐）

编辑 `frontend/.env.local` 文件：

```bash
# 将这行改为 false
NEXT_PUBLIC_USE_BACKEND_API=false
```

### 方法 2：临时测试

创建一个新的环境变量文件：

```bash
cd frontend
echo "NEXT_PUBLIC_USE_BACKEND_API=false" > .env.local
```

### 重启前端服务

修改环境变量后，需要重启前端开发服务器：

```bash
# 停止当前服务（Ctrl+C）
# 然后重新启动
npm run dev
# 或
pnpm dev
```

## 离线演示模式包含的功能

### 1. 图文并茂的默认回答
- Markdown 格式的结构化内容
- 包含标题、列表、表格等丰富格式
- 自动嵌入图片引用 `[image:1]`、`[image:2]`

### 2. 文献引用标注
- 显示 `[1]`、`[2]`、`[3]` 等引用标记
- 包含来源文档信息
- 模拟 PDF 跳转链接

### 3. 知识图谱可视化
- 显示实体节点和关系连线
- 交互式图形展示
- 推理路径信息

### 4. 智能推荐问题
- 基于上下文的后续问题推荐
- 5个相关问题建议

### 5. 流式输出动画
- 模拟逐字输出效果
- Agent 状态更新动画
- 真实的加载体验

## 文件结构

```
frontend/lib/api/
├── mock-client.ts          # Mock API 客户端实现
├── mock-responses.md       # Markdown 默认内容
├── client.ts               # API 客户端（已支持 Mock 模式切换）
├── types.ts                # 类型定义
└── config.ts               # API 配置
```

## 自定义离线演示内容

### 修改默认 Markdown 内容

编辑 `frontend/lib/api/mock-client.ts` 文件中的 `MOCK_MARKDOWN_CONTENT` 常量：

```typescript
const MOCK_MARKDOWN_CONTENT = `
# 你的自定义标题

你的自定义内容...
`
```

### 修改 Mock 数据

在 `mock-client.ts` 中可以修改：

- `MOCK_CITATIONS` - 引用数据
- `MOCK_IMAGES` - 图片 URL 列表
- `MOCK_RECOMMENDED_QUESTIONS` - 推荐问题
- `MOCK_KNOWLEDGE_GRAPH` - 知识图谱数据

## 切换回真实后端模式

### 1. 修改环境变量

```bash
# .env.local
NEXT_PUBLIC_USE_BACKEND_API=true
```

### 2. 确保后端服务运行

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

### 3. 重启前端服务

```bash
cd frontend
npm run dev
```

## 功能限制

在离线演示模式下，以下功能**不可用**：

- 删除会话（deleteSession）
- 更新会话（updateSession）
- 详细健康检查（health.detailed）
- 系统指标查询（health.metrics）
- 知识库搜索（kb.search）

尝试调用这些功能会抛出 `APIException`：

```
code: 501
message: "Not implemented in mock mode"
```

## 技术实现细节

### 环境变量检测

```typescript
const USE_BACKEND_API = process.env.NEXT_PUBLIC_USE_BACKEND_API !== 'false'
```

- 默认值为 `true`（使用后端）
- 只有显式设置为 `'false'` 时才启用 Mock 模式

### API 调用拦截

```typescript
async send(req: ChatRequest): Promise<ChatResponse> {
  // 检测 Mock 模式
  if (!USE_BACKEND_API) {
    return mockChatRequest(req)
  }

  // 真实 API 调用
  return request<ChatResponse>(...)
}
```

### 流式输出模拟

Mock 流式接口会：
1. 模拟网络延迟（30ms/chunk）
2. 触发所有回调（onContent、onCitations、onImages 等）
3. 按照真实 API 的顺序返回数据

## 常见问题

### Q1：修改环境变量后没有生效？

**A：** 需要重启前端开发服务器。Next.js 在启动时读取环境变量，运行中修改不会生效。

### Q2：如何验证当前是否在 Mock 模式？

**A：** 打开浏览器控制台，发送问题后会看到：
```
[chatApi] Using mock stream mode
```

### Q3：可以同时运行多个前端实例吗（一个 Mock 一个真实）？

**A：** 可以，使用不同的端口：
```bash
# Terminal 1 - Mock 模式（端口 3000）
cd frontend
NEXT_PUBLIC_USE_BACKEND_API=false npm run dev

# Terminal 2 - 真实模式（端口 3001）
cd frontend
PORT=3001 npm run dev
```

### Q4：为什么图片不显示？

**A：** Mock 模式下的图片 URL 是模拟的，不会真实加载。如需显示图片，请：
- 在 `public/api/documents/images/` 目录下放置对应图片
- 或修改 `MOCK_IMAGES` 使用真实的图片路径

## 最佳实践

### 演示场景

离线演示模式适合：
- 前端开发和调试
- 产品演示和展示
- 无网络环境下的功能预览
- UI/UX 测试

### 开发建议

1. **开发前端组件时**：使用 Mock 模式，无需启动后端
2. **集成测试时**：切换到真实后端模式
3. **部署前**：确保 `NEXT_PUBLIC_USE_BACKEND_API=true`

### 性能优化

Mock 模式下没有网络请求，响应速度更快，适合：
- 快速迭代 UI 设计
- 性能基准测试
- 前端逻辑调试

---

## 示例：完整切换流程

### 启用离线演示模式

```bash
# 1. 编辑环境变量
cd frontend
echo "NEXT_PUBLIC_USE_BACKEND_API=false" > .env.local

# 2. 重启服务
npm run dev

# 3. 浏览器访问
# http://localhost:3000
# 发送任何问题，都会看到预设的 Markdown 内容
```

### 恢复真实后端模式

```bash
# 1. 编辑环境变量
echo "NEXT_PUBLIC_USE_BACKEND_API=true" > .env.local

# 2. 启动后端
cd ../backend
python -m uvicorn app.main:app --reload --port 8000

# 3. 重启前端
cd ../frontend
npm run dev
```

---

更新时间：2025-12-18
