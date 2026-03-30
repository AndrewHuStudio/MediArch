export type CollectionKey = "standards" | "policy" | "books" | "papers"

export interface CollectionSummary {
  count: number
  documents: string[]
}

export const collectionSummaries: Record<CollectionKey, CollectionSummary> = {
  standards: {
    count: 2,
    documents: [
      "GB 51039-2014 综合医院建筑设计规范.pdf",
      "GB51039-2014综合医院建筑设计标准.pdf",
    ],
  },
  policy: {
    count: 2,
    documents: [
      "医疗机构设置规划指导原则（2021-2025）.pdf",
      "国家医学中心和国家区域医疗中心设置实施方案.pdf",
    ],
  },
  books: {
    count: 3,
    documents: [
      "医院建筑设计指南.pdf",
      "医疗功能房间详图集3.pdf",
      "建筑设计资料集 第6册 医疗.pdf",
    ],
  },
  papers: {
    count: 12,
    documents: [
      "既有大型综合医院门诊部功能布局优化设计研究.pdf",
      "多联手术室布局优化与气流控制.pdf",
      "人性化与健康防疫视角下的护理单元设计探析.pdf",
      "基于人工智能的未来医疗建筑发展思考.pdf",
      "医院导向标识系统设计研究.pdf",
      "日间手术中心建筑设计策略.pdf",
      "门诊流线优化与患者体验.pdf",
      "综合医院感染控制空间设计.pdf",
      "智慧医院建筑数字化转型.pdf",
      "大型医院急诊空间布局研究.pdf",
      "手术部净化与气流组织设计.pdf",
      "医疗建筑人因与服务流程协同.pdf",
    ],
  },
}
