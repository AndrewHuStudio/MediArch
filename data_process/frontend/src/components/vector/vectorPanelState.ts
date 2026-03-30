export const VECTOR_ALL_CATEGORY = '全部'
export const VECTOR_DOC_CATEGORIES = ['标准规范', '参考论文', '书籍报告', '政策文件'] as const
export const VECTOR_CATEGORIES = [VECTOR_ALL_CATEGORY, ...VECTOR_DOC_CATEGORIES] as const

export function getVectorQueryCategory(activeCategory: string) {
  return activeCategory === VECTOR_ALL_CATEGORY ? undefined : activeCategory
}

export function isSelectableForVector(item: { status: string; can_vectorize: boolean }) {
  return (item.status === 'pending' || item.status === 'failed') && item.can_vectorize
}

export function isForceRerunnableForVector(item: { status: string; ocr_ready?: boolean }) {
  return item.status === 'completed' && item.ocr_ready !== false
}
