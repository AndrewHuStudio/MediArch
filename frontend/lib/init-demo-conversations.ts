"use client"

/**
 * 初始化示例对话数据
 * 这个文件用于在首次加载时创建一些示例对话
 */

const CONVERSATION_STORAGE_PREFIX = "mediarch-conversation-"
const CURRENT_CONVERSATION_KEY = "mediarch-current-conversation-id"

interface StoredConversation {
  id: string
  title: string
  summary?: string
  messages: Array<{
    id: string
    role: "user" | "assistant"
    content: string
    timestamp: Date
    sources?: any[]
    images?: string[]
  }>
  timestamp: Date
  isPinned: boolean
}

const exampleConversations: Omit<StoredConversation, 'timestamp'>[] = [
  {
    id: "demo-conv-1",
    title: "医疗流程设计咨询",
    summary: "请帮我设计一个高效的医疗流程",
    isPinned: false,
    messages: [
      {
        id: "1",
        role: "user",
        content: "请帮我设计一个高效的医疗流程",
        timestamp: new Date(Date.now() - 1000 * 60 * 30),
      },
      {
        id: "2",
        role: "assistant",
        content: `我很乐意帮您设计一个高效的医疗流程。以下是一个综合性医院的医疗流程设计建议：

## 门诊流程优化

1. **预约挂号** - 支持线上/线下多渠道预约
2. **分诊导引** - 智能分诊系统，减少等待时间
3. **诊疗服务** - 电子病历系统，提高效率
4. **检查检验** - 一站式检查，结果电子化推送
5. **取药缴费** - 自助机+人工窗口，灵活选择

## 急诊流程

1. **快速分级** - 按病情紧急程度分级处理
2. **绿色通道** - 危重症患者优先救治
3. **多学科会诊** - 快速调配专家资源

## 住院流程

1. **床位管理** - 智能床位分配系统
2. **护理管理** - 电子护理记录，实时监控
3. **出院管理** - 结算简化，随访跟踪

建议采用信息化系统支撑，实现数据互联互通，提升整体效率。`,
        timestamp: new Date(Date.now() - 1000 * 60 * 29),
        sources: [
          {
            id: "pdf-1",
            title: "医疗流程优化指南",
            pageNumber: 15,
            snippet: "现代化医疗流程设计应以患者为中心...",
            highlightText: "采用信息化手段，实现医疗流程的标准化和智能化。",
          },
        ],
      },
    ],
  },
  {
    id: "demo-conv-2",
    title: "建筑规范查询",
    summary: "医疗建筑设计有哪些规范要求？",
    isPinned: false,
    messages: [
      {
        id: "3",
        role: "user",
        content: "医疗建筑设计有哪些规范要求？",
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 2),
      },
      {
        id: "4",
        role: "assistant",
        content: `医疗建筑设计需要遵守多项规范标准，以下是主要的规范要求：

## 国家标准规范

1. **《综合医院建筑设计规范》GB 51039-2014**
   - 总体布局要求
   - 功能分区标准
   - 建筑面积指标

2. **《医院洁净手术部建筑技术规范》GB 50333-2013**
   - 洁净度等级
   - 空调净化系统
   - 医疗气体系统

3. **《医疗建筑电气设计规范》JGJ 312-2013**
   - 供配电系统
   - 应急电源
   - 智能化系统

## 设计要点

### 功能分区
- 医疗区（门诊、急诊、住院）
- 医技区（检验、影像、手术）
- 后勤区（供应、设备、后勤）
- 行政区（办公、会议）

### 流线设计
- 医患分流
- 洁污分流
- 人物分流

### 感染控制
- 负压隔离病房
- 空气净化系统
- 医疗废物处理

这些规范是确保医疗建筑安全、高效运行的基础，设计时必须严格遵守。`,
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 2 + 60000),
        sources: [
          {
            id: "pdf-2",
            title: "综合医院建筑设计规范 GB 51039-2014",
            pageNumber: 8,
            snippet: "综合医院建筑设计应遵循以患者为中心的原则...",
            highlightText: "功能分区应明确，流线组织应合理，避免交叉感染。",
          },
        ],
      },
    ],
  },
  {
    id: "demo-conv-3",
    title: "手术室空间规划",
    summary: "如何合理规划手术室的空间布局？",
    isPinned: false,
    messages: [
      {
        id: "5",
        role: "user",
        content: "如何合理规划手术室的空间布局？",
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 24),
      },
      {
        id: "6",
        role: "assistant",
        content: `手术室的空间规划是医疗建筑设计的重点，需要综合考虑多方面因素：

## 手术部总体布局

### 分区原则
1. **清洁区** - 更衣、值班、办公
2. **准清洁区** - 术前准备、麻醉诱导
3. **洁净区** - 手术间、洗手间
4. **污染区** - 污物处理、清洗消毒

### 流线组织
- 医护人员流线
- 患者流线
- 洁净物品流线
- 污染物品流线

## 手术间设计要点

### 面积配置
- 普通手术间：30-40m²
- 专科手术间：40-60m²
- 大型手术间：60-80m²

### 洁净等级
- I级（特别洁净）：≥10000级，用于器官移植、心脏手术
- II级（标准洁净）：10万级，用于关节置换、脑外科
- III级（一般洁净）：30万级，用于普通外科
- IV级（准洁净）：非洁净手术

### 配套设施
- 空调净化系统
- 医用气体系统
- 手术视频系统
- 层流天花

## 关键要求

1. 手术部应独立成区，位于安静、清洁区域
2. 洁净手术部应远离污染源
3. 手术间应设置观察窗和应急通道
4. 严格控制人员和物品流动

合理的手术室规划能够有效降低感染风险，提高手术效率和安全性。`,
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 24 + 120000),
        sources: [
          {
            id: "pdf-3",
            title: "医院洁净手术部建筑技术规范 GB 50333-2013",
            pageNumber: 25,
            snippet: "洁净手术部应采用垂直层流和水平层流相结合的方式...",
            highlightText: "手术间面积应根据手术类型和设备配置合理确定。",
          },
        ],
      },
    ],
  },
  {
    id: "demo-conv-4",
    title: "感染控制设计要点",
    summary: "医院感染控制的设计要点是什么？",
    isPinned: false,
    messages: [
      {
        id: "7",
        role: "user",
        content: "医院感染控制的设计要点是什么？",
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 48),
      },
      {
        id: "8",
        role: "assistant",
        content: `医院感染控制是医疗建筑设计的核心内容之一，直接关系到患者和医护人员的安全。

## 核心设计原则

### 1. 分区隔离
- 清洁区与污染区严格分离
- 传染病区独立设置
- 负压隔离病房配置

### 2. 流线组织
- 医患分流：避免交叉感染
- 洁污分流：单向流动
- 人物分流：分别设置通道

### 3. 通风系统
- 负压隔离病房：12次/小时换气
- 普通病房：6次/小时
- 手术室：洁净空调系统

## 关键设施要求

### 隔离病房
- 前室缓冲设计
- 负压控制（-5Pa）
- 独立卫生间
- 观察窗设置

### 污物处理
- 污物暂存间
- 医疗废物处理室
- 洗消中心

### 清洁设施
- 手卫生设施齐全
- 消毒供应中心
- 洁净物品储存

## 材料选择

1. 墙面：易清洁、耐腐蚀
2. 地面：防滑、防渗、易消毒
3. 门窗：密闭性好、易清洁

通过科学的设计和管理，可以有效控制院内感染，保障医疗安全。`,
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 48 + 90000),
        sources: [
          {
            id: "pdf-4",
            title: "医院感染控制标准",
            pageNumber: 32,
            snippet: "医院感染控制设计应遵循预防为主的原则...",
            highlightText: "通过建筑设计手段，从源头上降低院内感染风险。",
          },
        ],
      },
    ],
  },
  {
    id: "demo-conv-5",
    title: "ICU 病房设计标准",
    summary: "重症监护室的设计有什么特殊要求？",
    isPinned: false,
    messages: [
      {
        id: "9",
        role: "user",
        content: "重症监护室的设计有什么特殊要求？",
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 72),
      },
      {
        id: "10",
        role: "assistant",
        content: `重症监护室（ICU）是医院的核心科室，设计标准严格且特殊。

## 空间配置要求

### 床位设置
- 单床位面积：≥15-18m²
- 床间距：≥1.0m
- 护士站视线覆盖所有床位

### 功能分区
1. **治疗区** - ICU 床位、抢救室
2. **辅助区** - 药品准备、治疗准备
3. **医护区** - 医生办公、护士站
4. **家属区** - 等候、探视

## 设备配置

### 基本设备
- 多参数监护仪
- 呼吸机接口
- 医用气体终端（氧气、负压吸引、压缩空气）
- 输液架、治疗台

### 特殊设施
- 中央监护系统
- 移动 X 光机位置
- 血液净化设备接口
- 应急电源插座

## 环境控制

### 空调系统
- 温度：22-26°C
- 湿度：40-60%
- 换气次数：8-12次/小时
- 正压控制

### 照明系统
- 普通照明：300-500lux
- 治疗照明：≥1000lux
- 夜间照明：≤100lux
- 应急照明

## 特殊要求

1. **隔离功能**：至少1间负压隔离单元
2. **噪音控制**：≤45dB（白天），≤40dB（夜间）
3. **材料要求**：易清洁、防滑、防静电
4. **智能化**：呼叫系统、视频探视、电子病历

ICU 设计需要充分考虑医疗功能、感染控制和人性化需求的平衡。`,
        timestamp: new Date(Date.now() - 1000 * 60 * 60 * 72 + 150000),
        sources: [
          {
            id: "pdf-5",
            title: "重症监护病房设计规范",
            pageNumber: 18,
            snippet: "ICU 应设置在便于患者转运和治疗的位置...",
            highlightText: "病床单元应保证医护人员便捷地接触患者和使用医疗设备。",
          },
        ],
      },
    ],
  },
]

export function initializeDemoConversations() {
  try {
    // 检查是否已经初始化过
    const hasConversations = localStorage.getItem(`${CONVERSATION_STORAGE_PREFIX}demo-conv-1`)
    if (hasConversations) {
      console.log("Demo conversations already exist")
      return
    }

    console.log("Initializing demo conversations...")

    // 保存示例对话
    exampleConversations.forEach((conv) => {
      const conversation: StoredConversation = {
        ...conv,
        timestamp: new Date(),
        messages: conv.messages.map((msg) => ({
          ...msg,
          timestamp: msg.timestamp instanceof Date ? msg.timestamp : new Date(msg.timestamp),
        })),
      }

      const key = `${CONVERSATION_STORAGE_PREFIX}${conversation.id}`
      localStorage.setItem(
        key,
        JSON.stringify({
          ...conversation,
          timestamp: conversation.timestamp.toISOString(),
          messages: conversation.messages.map((msg) => ({
            ...msg,
            timestamp: msg.timestamp.toISOString(),
          })),
        }),
      )
    })

    // 注意：不自动设置当前对话，让系统创建新对话
    console.log("Demo conversations initialized successfully (5 conversations)")
  } catch (error) {
    console.error("Failed to initialize demo conversations:", error)
  }
}

// 清除所有对话（用于调试）
export function clearAllConversations() {
  try {
    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith(CONVERSATION_STORAGE_PREFIX)) {
        keys.push(key)
      }
    }

    keys.forEach((key) => localStorage.removeItem(key))
    localStorage.removeItem(CURRENT_CONVERSATION_KEY)

    console.log(`Cleared ${keys.length} conversations`)
  } catch (error) {
    console.error("Failed to clear conversations:", error)
  }
}
