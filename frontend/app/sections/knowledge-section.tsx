"use client"

import BookShowcase from "@/components/book-showcase/book-showcase"
import LazyMount from "@/components/lazy-mount"
import { useT } from "@/lib/i18n"

interface KnowledgeSectionProps {
  onNavigate: (sectionIndex: number) => void
}

export default function KnowledgeSection({ onNavigate }: KnowledgeSectionProps) {
  const { t } = useT()

  return (
    <section id="section-1" className="relative z-10 overflow-hidden h-screen flex">
      <div className="flex-1">
        <LazyMount
          className="h-full"
          fallback={<div className="flex h-full w-full items-center justify-center text-white/50 text-sm">{t('kb.loading')}</div>}
        >
          <BookShowcase onNavigate={onNavigate} />
        </LazyMount>
      </div>
    </section>
  )
}
