"use client"

import { useEffect, useMemo, useState } from "react"
import { motion } from "framer-motion"
import { EllipsisVertical, Pin, PinOff, Check, X, Trash2, PencilLine } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useT } from "@/lib/i18n"

interface ConversationTopBarProps {
  title: string
  summary?: string  // 可选参数，暂时保留以兼容现有代码
  isPinned: boolean
  onPinToggle: () => void
  onRename: (title: string) => void
  onDelete: () => void
}

export function ConversationTopBar({
  title,
  isPinned,
  onPinToggle,
  onRename,
  onDelete,
}: ConversationTopBarProps) {
  const [isRenaming, setIsRenaming] = useState(false)
  const [draftTitle, setDraftTitle] = useState(title)
  const { t } = useT()

  useEffect(() => {
    if (!isRenaming) {
      setDraftTitle(title)
    }
  }, [title, isRenaming])

  const renameDisabled = useMemo(() => draftTitle.trim().length === 0, [draftTitle])

  const handleRenameConfirm = () => {
    const nextTitle = draftTitle.trim()
    if (!nextTitle) return
    onRename(nextTitle)
    setIsRenaming(false)
  }

  const handleRenameCancel = () => {
    setDraftTitle(title)
    setIsRenaming(false)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={cn(
        "w-full flex items-center justify-start",
        "bg-white/72 backdrop-blur-sm border-b border-[#d9e7eb]",
        "px-2 lg:px-6 py-3"
      )}
    >
      <div className="flex items-center gap-2 min-w-0 max-w-5xl mx-auto w-full pl-70">
        {isRenaming ? (
          <>
            <Input
              value={draftTitle}
              onChange={(event) => setDraftTitle(event.target.value)}
              autoFocus
              className="bg-white/90 border-[#cfe2e7] text-sm text-[#12323a] h-8 max-w-[400px]"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  handleRenameConfirm()
                }
                if (event.key === "Escape") {
                  handleRenameCancel()
                }
              }}
            />
            <Button
              variant="ghost"
              size="icon"
              className="text-[#6c858c] hover:bg-[#e6f4f6] h-8 w-8"
              onClick={handleRenameCancel}
              aria-label={t('chat.aria.cancelRename')}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="text-[#047857] hover:bg-emerald-500/10 disabled:text-[#8ba4ad] h-8 w-8"
              onClick={handleRenameConfirm}
              disabled={renameDisabled}
              aria-label={t('chat.aria.confirmRename')}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
          </>
        ) : (
          <>
            <h2 className="text-sm font-medium text-[#12323a] truncate max-w-[500px]">{title}</h2>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-[#6c858c] hover:text-[#0e7490] hover:bg-[#e6f4f6] h-8 w-8 flex-shrink-0"
                  aria-label={t('chat.aria.actions')}
                >
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="bg-white/95 backdrop-blur-md text-[#12323a] border-[#cfe2e7]">
                <DropdownMenuItem
                  className="gap-2 focus:bg-[#e6f4f6]"
                  onSelect={(event) => {
                    event.preventDefault()
                    setIsRenaming(true)
                  }}
                >
                  <PencilLine className="h-4 w-4" />
                  {t('chat.rename')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="gap-2 focus:bg-[#e6f4f6]"
                  onSelect={(event) => {
                    event.preventDefault()
                    onPinToggle()
                  }}
                >
                  {isPinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                  {isPinned ? t('chat.unpin') : t('chat.pin')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="gap-2 text-red-300 focus:bg-red-500/10 focus:text-red-100"
                  onSelect={(event) => {
                    event.preventDefault()
                    onDelete()
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                  {t('chat.delete')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      </div>
    </motion.div>
  )
}
