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
        "bg-black/30 backdrop-blur-sm border-b border-white/5",
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
              className="bg-white/5 border-white/20 text-sm text-white h-8 max-w-[400px]"
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
              className="text-gray-300 hover:bg-white/10 h-8 w-8"
              onClick={handleRenameCancel}
              aria-label="取消重命名"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="text-emerald-300 hover:bg-emerald-500/10 disabled:text-gray-500 h-8 w-8"
              onClick={handleRenameConfirm}
              disabled={renameDisabled}
              aria-label="确认重命名"
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
          </>
        ) : (
          <>
            <h2 className="text-sm font-medium text-white/90 truncate max-w-[500px]">{title}</h2>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-white/70 hover:text-white hover:bg-white/10 h-8 w-8 flex-shrink-0"
                  aria-label="对话操作"
                >
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="bg-black/90 backdrop-blur-md text-white border-white/10">
                <DropdownMenuItem
                  className="gap-2 focus:bg-white/10"
                  onSelect={(event) => {
                    event.preventDefault()
                    setIsRenaming(true)
                  }}
                >
                  <PencilLine className="h-4 w-4" />
                  重命名
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="gap-2 focus:bg-white/10"
                  onSelect={(event) => {
                    event.preventDefault()
                    onPinToggle()
                  }}
                >
                  {isPinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                  {isPinned ? "取消固定" : "固定对话"}
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="gap-2 text-red-300 focus:bg-red-500/10 focus:text-red-100"
                  onSelect={(event) => {
                    event.preventDefault()
                    onDelete()
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                  删除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      </div>
    </motion.div>
  )
}
