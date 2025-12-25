/**
 * Mock API 客户端
 *
 * 在没有后端的情况下，提供模拟数据响应
 * 读取本地 Markdown 文件作为默认输出
 */

import {
  ChatRequest,
  ChatResponse,
  StreamCallbacks,
  Citation,
  KnowledgeGraphData,
  SessionListResponse,
  SessionHistoryResponse,
  QuickHealthResponse,
} from './types'

// 导入 Markdown 内容（在 Next.js 中，需要使用动态导入）
const MOCK_MARKDOWN_CONTENT = `这是一个关于"医疗建筑设计有哪些规范要求？"的专业回答。

作为医疗建筑设计助手，我可以为您提供详细的设计建议和规范标准。

## 主要考虑因素

1. **功能分区**[1] - 合理规划医疗功能区域
2. **流线设计**[2] - 优化人员和物资流动路径
3. **感染控制**[2] - 严格遵守医疗卫生标准

下图展示了典型的医院功能分区布局：

[image:0]

建筑空间布局示例：

\`\`\`javascript
// 示例代码：空间计算
function calculateRoomArea(length, width) {
  return length * width;
}

const area = calculateRoomArea(10, 8);
console.log("房间面积:", area);
\`\`\`

根据《综合医院建筑规范》[1]，医疗建筑应充分考虑、医护人员和患者动线管理的使用需求。

以下是医疗建筑流线设计示意图：

[image:1]

感染控制方面需要特别注意空调净化系统[2]。

请问您需要了解更具体的哪方面内容？`

// Mock Citations - 带有高亮位置信息
const MOCK_CITATIONS: Citation[] = [
  {
    source: "综合医院建筑规范",
    location: "第23页",
    snippet: "功能分区的明确和分隔医疗区，行政和后勤制备的各自独立，各区域之间有明确的交通体系。",
    highlight_text: "功能分区的明确和分隔医疗区",
    page_number: 23,
    chapter: "第三章",
    chapter_title: "总体布局",
    section: "功能分区",
    pdf_url: "https://mediarch-kb.oss-cn-beijing.aliyuncs.com/policy/%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E8%A7%84%E8%8C%83/%E7%BB%BC%E5%90%88%E5%8C%BB%E9%99%A2%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E8%A7%84%E8%8C%83GB51039-2014.pdf",
    document_path: "policy/建筑设计规范/综合医院建筑设计规范GB51039-2014.pdf",
    doc_category: "guide",
    chunk_id: "chunk-001",
    positions: [
      {
        page: 23,
        bbox: [0.15, 0.35, 0.85, 0.42]  // [x0, y0, x1, y1] 百分比坐标
      }
    ]
  },
  {
    source: "综合医院建筑设计规范",
    location: "第23页",
    snippet: "医疗建筑应充分考虑空间舒适性、流线优化、感染控制等，确保患者及医护人员的安全。",
    highlight_text: "空间舒适性、流线优化、感染控制",
    page_number: 23,
    chapter: "第三章",
    chapter_title: "总体布局",
    section: "功能分区",
    pdf_url: "https://mediarch-kb.oss-cn-beijing.aliyuncs.com/policy/%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E8%A7%84%E8%8C%83/%E7%BB%BC%E5%90%88%E5%8C%BB%E9%99%A2%E5%BB%BA%E7%AD%91%E8%AE%BE%E8%AE%A1%E8%A7%84%E8%8C%83GB51039-2014.pdf",
    document_path: "policy/建筑设计规范/综合医院建筑设计规范GB51039-2014.pdf",
    doc_category: "detail_atlas",
    chunk_id: "chunk-002",
    positions: [
      {
        page: 23,
        bbox: [0.15, 0.50, 0.85, 0.57]
      }
    ]
  }
]

// Mock 图片 URLs - 使用 public 目录下的真实图片
const MOCK_IMAGES = [
  "/hospital-architectural-aerial-view-with-department.jpg",
  "/medical-building-floor-plan-layout-diagram.jpg"
]

// Mock 推荐问题
const MOCK_RECOMMENDED_QUESTIONS = [
  "医院门诊部应该如何布局？",
  "如何设计医疗建筑的洁净空调系统？",
  "医疗建筑的消防设计有哪些特殊要求？",
  "手术室的设计规范有哪些？",
  "医院停车场应该如何规划？"
]

// Mock 知识图谱数据 - 完整版本，匹配D3颜色映射
const MOCK_KNOWLEDGE_GRAPH: KnowledgeGraphData = {
  nodes: [
    { id: "综合医院", label: "综合医院", type: "concept" },      // 黄色 #fbbf24
    { id: "门诊部", label: "门诊部", type: "entity" },           // 蓝色 #3b82f6
    { id: "急诊科", label: "急诊科", type: "entity" },           // 蓝色 #3b82f6
    { id: "建筑设计", label: "建筑设计", type: "attribute" },     // 绿色 #10b981
    { id: "医疗流程", label: "医疗流程", type: "attribute" },     // 绿色 #10b981
    { id: "规范标准", label: "规范标准", type: "relation" },      // 紫色 #a855f7
    { id: "安全要求", label: "安全要求", type: "relation" }       // 紫色 #a855f7
  ],
  links: [
    { source: "综合医院", target: "门诊部", label: "包含", weight: 0.9 },
    { source: "综合医院", target: "急诊科", label: "包含", weight: 0.9 },
    { source: "综合医院", target: "建筑设计", label: "需要", weight: 0.85 },
    { source: "急诊科", target: "医疗流程", label: "遵循", weight: 0.8 },
    { source: "门诊部", target: "医疗流程", label: "遵循", weight: 0.8 },
    { source: "建筑设计", target: "规范标准", label: "符合", weight: 0.7 },
    { source: "医疗流程", target: "安全要求", label: "保证", weight: 0.75 }
  ],
  query_path: {
    expanded_entities: [
      { name: "综合医院", type: "concept", score: 1.0 },
      { name: "门诊部", type: "entity", score: 0.9 },
      { name: "建筑设计", type: "attribute", score: 0.85 }
    ],
    expanded_relations: [
      { source: "综合医院", target: "门诊部", relation: "包含" },
      { source: "综合医院", target: "建筑设计", relation: "需要" }
    ],
    knowledge_coverage: 0.85
  }
}

// ============================================================================
// Mock API 方法
// ============================================================================

/**
 * Mock 聊天接口（非流式）
 */
export async function mockChatRequest(request: ChatRequest): Promise<ChatResponse> {
  // 模拟网络延迟
  await new Promise(resolve => setTimeout(resolve, 1000))

  return {
    message: MOCK_MARKDOWN_CONTENT,
    session_id: 'mock-session-' + Date.now(),
    citations: MOCK_CITATIONS,
    images: MOCK_IMAGES,
    recommended_questions: MOCK_RECOMMENDED_QUESTIONS,
    knowledge_graph_path: MOCK_KNOWLEDGE_GRAPH,
    took_ms: 1000,
    agents_used: ['mock-agent']
  }
}

/**
 * Mock 聊天流式接口
 *
 * 完整模拟前端演示效果：Agent思考流程 + 知识图谱逐步构建 + 流式输出
 */
export async function mockChatStreamRequest(
  request: ChatRequest,
  callbacks: StreamCallbacks
): Promise<void> {
  try {
    // 1. 首先返回 session_id
    const sessionId = 'mock-session-' + Date.now()
    if (callbacks.onSession) {
      callbacks.onSession(sessionId)
    }

    // 2. 模拟6个Agent逐个思考流程
    const agents = [
      'Orchestrator Agent',
      'Neo4j Agent',
      'Milvus Agent',
      'MongoDB Agent',
      'Online Search Agent',
      'Result Synthesizer Agent'
    ]

    const agentThoughts: Record<string, string[]> = {
      'Orchestrator Agent': ['分析问题结构...', '制定查询策略...', '分配任务给各智能体...'],
      'Neo4j Agent': ['查询知识图谱...', '分析关系网络...', '提取关键节点...'],
      'Milvus Agent': ['向量相似度搜索...', '语义匹配分析...', '排序相关内容...'],
      'MongoDB Agent': ['检索文档数据...', '过滤相关记录...', '聚合结果集...'],
      'Online Search Agent': ['在线资源检索...', '验证最新信息...', '补充外部数据...'],
      'Result Synthesizer Agent': ['整合各方数据...', '生成综合答案...', '优化表达方式...']
    }

    // 遍历每个智能体（除了最后一个综合器）
    for (let i = 0; i < agents.length - 1; i++) {
      const agentName = agents[i]
      const thoughts = agentThoughts[agentName]

      // 逐条显示思考内容
      for (let j = 0; j < thoughts.length; j++) {
        if (callbacks.onAgentStatus) {
          callbacks.onAgentStatus({
            agent_name: agentName,
            status: 'running',
            thought: thoughts[j],
            progress: (i * thoughts.length + j + 1) / (agents.length * 3)
          })
        }
        await new Promise(resolve => setTimeout(resolve, 800)) // 每条思考延迟800ms
      }

      // 如果是 Neo4j Agent，触发知识图谱分阶段构建
      if (agentName === 'Neo4j Agent' && callbacks.onKnowledgeGraph) {
        await simulateKnowledgeGraphBuild(callbacks.onKnowledgeGraph)
      }

      await new Promise(resolve => setTimeout(resolve, 500)) // 切换智能体前暂停
    }

    // 最后执行综合器
    const synthesizerName = 'Result Synthesizer Agent'
    const synthesizerThoughts = agentThoughts[synthesizerName]

    for (let j = 0; j < synthesizerThoughts.length; j++) {
      if (callbacks.onAgentStatus) {
        callbacks.onAgentStatus({
          agent_name: synthesizerName,
          status: 'running',
          thought: synthesizerThoughts[j],
          progress: 0.9 + (j / synthesizerThoughts.length) * 0.1
        })
      }
      await new Promise(resolve => setTimeout(resolve, 800))
    }

    // 3. 流式输出内容（逐字符输出，每个字符10ms，加快速度）
    if (callbacks.onContent) {
      const content = MOCK_MARKDOWN_CONTENT

      for (let i = 0; i < content.length; i++) {
        callbacks.onContent(content[i])
        await new Promise(resolve => setTimeout(resolve, 10)) // 每个字符延迟10ms（加快速度）
      }
    }

    await new Promise(resolve => setTimeout(resolve, 500))

    // 4. 返回 Citations
    if (callbacks.onCitations) {
      callbacks.onCitations(MOCK_CITATIONS)
      await new Promise(resolve => setTimeout(resolve, 200))
    }

    // 5. 返回图片
    if (callbacks.onImages) {
      callbacks.onImages(MOCK_IMAGES)
      await new Promise(resolve => setTimeout(resolve, 200))
    }

    // 6. 返回推荐问题
    if (callbacks.onRecommendations) {
      callbacks.onRecommendations(MOCK_RECOMMENDED_QUESTIONS)
      await new Promise(resolve => setTimeout(resolve, 200))
    }

    // 7. Agent完成状态
    if (callbacks.onAgentStatus) {
      callbacks.onAgentStatus({
        agent_name: 'Result Synthesizer Agent',
        status: 'completed',
        thought: '回答生成完成',
        progress: 1.0
      })
    }

    // 8. 完成
    if (callbacks.onDone) {
      callbacks.onDone()
    }

  } catch (error) {
    if (callbacks.onError) {
      callbacks.onError(error instanceof Error ? error.message : 'Unknown error')
    }
  }
}

/**
 * 模拟知识图谱分阶段构建
 */
async function simulateKnowledgeGraphBuild(
  onKnowledgeGraph: (data: KnowledgeGraphData) => void
): Promise<void> {
  // 阶段1：核心概念节点
  await new Promise(resolve => setTimeout(resolve, 800))
  onKnowledgeGraph({
    nodes: [
      { id: '医疗建筑', label: '医疗建筑', type: 'concept' }
    ],
    links: [],
    query_path: {
      expanded_entities: [
        { name: '医疗建筑', type: 'concept', score: 1.0 }
      ],
      expanded_relations: [],
      knowledge_coverage: 0.2
    }
  })

  // 阶段2：添加关联实体
  await new Promise(resolve => setTimeout(resolve, 800))
  onKnowledgeGraph({
    nodes: [
      { id: '医疗建筑', label: '医疗建筑', type: 'concept' },
      { id: '功能分区', label: '功能分区', type: 'entity' },
      { id: '流线设计', label: '流线设计', type: 'entity' }
    ],
    links: [
      { source: '医疗建筑', target: '功能分区', label: '包含', weight: 0.9 },
      { source: '医疗建筑', target: '流线设计', label: '要求', weight: 0.85 }
    ],
    query_path: {
      expanded_entities: [
        { name: '医疗建筑', type: 'concept', score: 1.0 },
        { name: '功能分区', type: 'entity', score: 0.9 }
      ],
      expanded_relations: [
        { source: '医疗建筑', target: '功能分区', relation: '包含' }
      ],
      knowledge_coverage: 0.5
    }
  })

  // 阶段3：添加属性节点
  await new Promise(resolve => setTimeout(resolve, 800))
  onKnowledgeGraph({
    nodes: [
      { id: '医疗建筑', label: '医疗建筑', type: 'concept' },
      { id: '功能分区', label: '功能分区', type: 'entity' },
      { id: '流线设计', label: '流线设计', type: 'entity' },
      { id: '感染控制', label: '感染控制', type: 'entity' },
      { id: '门急诊', label: '门急诊', type: 'concept' },
      { id: '住院部', label: '住院部', type: 'concept' }
    ],
    links: [
      { source: '医疗建筑', target: '功能分区', label: '包含', weight: 0.9 },
      { source: '医疗建筑', target: '流线设计', label: '要求', weight: 0.85 },
      { source: '医疗建筑', target: '感染控制', label: '规范', weight: 0.8 },
      { source: '功能分区', target: '门急诊', label: '包括', weight: 0.7 },
      { source: '功能分区', target: '住院部', label: '包括', weight: 0.7 }
    ],
    query_path: {
      expanded_entities: [
        { name: '医疗建筑', type: 'concept', score: 1.0 },
        { name: '功能分区', type: 'entity', score: 0.9 },
        { name: '流线设计', type: 'entity', score: 0.8 }
      ],
      expanded_relations: [
        { source: '医疗建筑', target: '功能分区', relation: '包含' },
        { source: '医疗建筑', target: '流线设计', relation: '要求' }
      ],
      knowledge_coverage: 0.75
    }
  })

  // 阶段4：完整图谱
  await new Promise(resolve => setTimeout(resolve, 800))
  onKnowledgeGraph(MOCK_KNOWLEDGE_GRAPH)
}

/**
 * Mock 健康检查
 */
export async function mockHealthCheck(): Promise<QuickHealthResponse> {
  return {
    status: 'ok',
    message: 'Mock mode - 离线演示模式',
    timestamp: Date.now()
  }
}

/**
 * Mock 会话列表
 */
export async function mockGetSessions(): Promise<SessionListResponse> {
  return {
    sessions: [],
    total: 0
  }
}

/**
 * Mock 会话历史
 */
export async function mockGetSessionHistory(sessionId: string): Promise<SessionHistoryResponse> {
  return {
    session_id: sessionId,
    messages: [],
    total: 0
  }
}
