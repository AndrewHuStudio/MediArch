import type { BookData } from "./types"
import type { Locale } from "@/lib/i18n"
import { zh } from "@/lib/i18n/zh"
import { en } from "@/lib/i18n/en"
import { collectionSummaries } from "./collection-summary"

const dicts = { zh, en } as const

function t(locale: Locale, key: string) {
  return dicts[locale][key] ?? dicts.zh[key] ?? key
}

function getCollectionMeta(locale: Locale, key: keyof typeof collectionSummaries) {
  const summary = collectionSummaries[key]

  if (locale === "en") {
    const docsLabel = `${summary.count} uploaded documents`
    switch (key) {
      case "standards":
        return {
          metaLine: `${docsLabel} · standards and specification set`,
          countLabel: `${summary.count} docs`,
          description: [
            "This collection currently contains 2 uploaded standards documents and serves as the core compliance reference in the knowledge base.",
            'Included documents: "GB 51039-2014 Code for Design of General Hospital Buildings" and "GB51039-2014 General Hospital Building Design Standard", suitable for checking zoning, circulation, spatial dimensions, and design requirements.',
          ],
        }
      case "policy":
        return {
          metaLine: `${docsLabel} · policy and planning set`,
          countLabel: `${summary.count} docs`,
          description: [
            "This collection currently contains 2 uploaded policy documents focused on healthcare facility planning and the national medical center system.",
            'Included documents: "Guiding Principles for Medical Institution Setup Planning (2021-2025)" and "Implementation Plan for National Medical Centers and Regional Medical Centers", useful for policy direction, resource allocation, and regional healthcare planning.',
          ],
        }
      case "books":
        return {
          metaLine: `${docsLabel} · books and reports set`,
          countLabel: `${summary.count} docs`,
          description: [
            "This collection currently contains 3 uploaded books and reports covering hospital architecture guidance, room details, and architectural reference material.",
            'Included documents: "Hospital Architecture Design Guide", "Medical Functional Room Detail Atlas 3", and "Architectural Design Data Collection Vol. 6 Medical", suitable for design reference, room detailing, and functional space comparison.',
          ],
        }
      case "papers":
        return {
          metaLine: `${docsLabel} · research paper set`,
          countLabel: `${summary.count} docs`,
          description: [
            "This collection currently contains 12 uploaded research papers covering outpatient optimization, operating room design, nursing units, wayfinding, day surgery, and AI in medical architecture.",
            'Representative documents include "Optimization Design Research on Outpatient Functional Layout in Large General Hospitals", "Multi-operating-room Layout Optimization and Airflow Control", "Humanized and Health-protective Nursing Unit Design", and "Future Medical Architecture Development Thinking Based on AI".',
          ],
        }
    }
  }

  const docsLabel = `已上传 ${summary.count} 份资料`
  switch (key) {
    case "standards":
      return {
        metaLine: `${docsLabel} · 标准规范分类汇总`,
        countLabel: `${summary.count} 份资料`,
        description: [
          "当前已上传 2 份标准规范资料，覆盖综合医院建筑设计规范与设计标准，是知识库中最核心的合规依据。",
          "资料包括：《GB 51039-2014 综合医院建筑设计规范》《GB51039-2014综合医院建筑设计标准》，可用于核对功能分区、流线组织、空间尺度与设计标准。",
        ],
      }
    case "policy":
      return {
        metaLine: `${docsLabel} · 政策文件分类汇总`,
        countLabel: `${summary.count} 份资料`,
        description: [
          "当前已上传 2 份政策文件资料，聚焦医疗机构设置规划和国家医学中心布局。",
          "资料包括：《医疗机构设置规划指导原则（2021-2025）》《国家医学中心和国家区域医疗中心设置实施方案》，可用于把握建设导向、资源配置与区域医疗体系规划。",
        ],
      }
    case "books":
      return {
        metaLine: `${docsLabel} · 书籍报告分类汇总`,
        countLabel: `${summary.count} 份资料`,
        description: [
          "当前已上传 3 份书籍报告资料，覆盖医疗建筑设计指南、功能房间详图和建筑资料集。",
          "资料包括：《医院建筑设计指南》《医疗功能房间详图集3》《建筑设计资料集 第6册 医疗》，适合用于方案参考、空间细部设计与功能房间对照。",
        ],
      }
    case "papers":
      return {
        metaLine: `${docsLabel} · 参考论文分类汇总`,
        countLabel: `${summary.count} 份资料`,
        description: [
          "当前已上传 12 份参考论文，覆盖门诊优化、手术室设计、护理单元、导向标识、日间手术与 AI 医疗建筑等主题。",
          "代表资料包括：《既有大型综合医院门诊部功能布局优化设计研究》《多联手术室布局优化与气流控制》《人性化与健康防疫视角下的护理单元设计探析》《基于人工智能的未来医疗建筑发展思考》等。",
        ],
      }
  }
}

export function getBooks(locale: Locale): BookData[] {
  const standardsMeta = getCollectionMeta(locale, "standards")
  const policyMeta = getCollectionMeta(locale, "policy")
  const booksMeta = getCollectionMeta(locale, "books")
  const papersMeta = getCollectionMeta(locale, "papers")

  return [
    {
      id: "standards-specification",
      title: t(locale, 'book.standards.title'),
      subtitle: t(locale, 'book.standards.subtitle'),
      author: t(locale, 'book.standards.author'),
      publishedYear: 2024,
      pages: 428,
      metaLine: standardsMeta.metaLine,
      countLabel: standardsMeta.countLabel,
      description: standardsMeta.description,
      genres: t(locale, 'book.standards.genres').split(','),
      rating: 4.8,
      reviews: 2847,
      materialProps: {
        color: "#2563eb",
        metalness: 0.7,
        roughness: 0.3,
        emissive: "#1e40af",
        emissiveIntensity: 0.1,
        texture: {
          front: "/images/standard-front.png",
          back: "/images/standard-back.png",
        },
        offsetX: -0.01,
        offsetY: 0,
      },
      backgroundColor: "#1e40af",
      textColor: "white",
    },
    {
      id: "policy-documents",
      title: t(locale, 'book.policy.title'),
      subtitle: t(locale, 'book.policy.subtitle'),
      author: t(locale, 'book.policy.author'),
      publishedYear: 2024,
      pages: 678,
      metaLine: policyMeta.metaLine,
      countLabel: policyMeta.countLabel,
      description: policyMeta.description,
      genres: t(locale, 'book.policy.genres').split(','),
      rating: 4.6,
      reviews: 1567,
      materialProps: {
        color: "#7c3aed",
        metalness: 0.5,
        roughness: 0.5,
        emissive: "#6d28d9",
        emissiveIntensity: 0.1,
        texture: {
          front: "/images/policy-front.png",
          back: "/images/policy-back.png",
        },
        offsetX: -0.02,
        offsetY: 0,
      },
      backgroundColor: "#6d28d9",
      textColor: "white",
    },
    {
      id: "books-reports",
      title: t(locale, 'book.books.title'),
      subtitle: t(locale, 'book.books.subtitle'),
      author: t(locale, 'book.books.author'),
      publishedYear: 2024,
      pages: 356,
      metaLine: booksMeta.metaLine,
      countLabel: booksMeta.countLabel,
      description: booksMeta.description,
      genres: t(locale, 'book.books.genres').split(','),
      rating: 4.7,
      reviews: 1923,
      materialProps: {
        color: "#059669",
        metalness: 0.6,
        roughness: 0.4,
        emissive: "#047857",
        emissiveIntensity: 0.1,
        texture: {
          front: "/images/book-front.png",
          back: "/images/book-back.png",
        },
        offsetX: -0.02,
        offsetY: 0,
      },
      backgroundColor: "#047857",
      textColor: "white",
    },
    {
      id: "research-papers",
      title: t(locale, 'book.papers.title'),
      subtitle: t(locale, 'book.papers.subtitle'),
      author: t(locale, 'book.papers.author'),
      publishedYear: 2024,
      pages: 512,
      metaLine: papersMeta.metaLine,
      countLabel: papersMeta.countLabel,
      description: papersMeta.description,
      genres: t(locale, 'book.papers.genres').split(','),
      rating: 4.9,
      reviews: 3456,
      materialProps: {
        color: "#dc2626",
        metalness: 0.8,
        roughness: 0.2,
        emissive: "#b91c1c",
        emissiveIntensity: 0.1,
        texture: {
          front: "/images/paper-front.png",
          back: "/images/paper-back.png",
        },
        offsetX: -0.01,
        offsetY: 0,
      },
      backgroundColor: "#b91c1c",
      textColor: "white",
    },
    {
      id: "online-cases",
      title: t(locale, 'book.cases.title'),
      subtitle: t(locale, 'book.cases.subtitle'),
      author: t(locale, 'book.cases.author'),
      publishedYear: 2024,
      pages: 394,
      metaLine: locale === "en" ? "Online references · not synced from uploaded library" : "在线案例 · 未从当前上传资料同步",
      countLabel: locale === "en" ? "external" : "外部内容",
      description: [
        t(locale, 'book.cases.desc.1'),
        t(locale, 'book.cases.desc.2'),
      ],
      genres: t(locale, 'book.cases.genres').split(','),
      rating: 4.8,
      reviews: 2134,
      materialProps: {
        color: "#ea580c",
        metalness: 0.6,
        roughness: 0.4,
        emissive: "#c2410c",
        emissiveIntensity: 0.1,
        texture: {
          front: "/images/onlinecases-front.png",
          back: "/images/onlinecases-back.png",
        },
        offsetX: -0.01,
        offsetY: 0,
      },
      backgroundColor: "#c2410c",
      textColor: "white",
    },
  ]
}

// Backward-compatible default export for components that haven't migrated yet
export const booksData = getBooks('zh')
