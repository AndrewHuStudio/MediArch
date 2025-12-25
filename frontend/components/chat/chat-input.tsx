"use client"

import { useRef, useCallback, useEffect } from "react"
import { Paperclip, ArrowUpIcon, FileText, ImageIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

interface AutoResizeProps {
  minHeight: number
  maxHeight?: number
}

function useAutoResizeTextarea({ minHeight, maxHeight }: AutoResizeProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const adjustHeight = useCallback(
    (reset?: boolean) => {
      const textarea = textareaRef.current
      if (!textarea) return

      if (reset) {
        textarea.style.height = `${minHeight}px`
        return
      }

      textarea.style.height = `${minHeight}px`
      const scrollHeight = textarea.scrollHeight

      if (maxHeight && scrollHeight > maxHeight) {
        textarea.style.height = `${maxHeight}px`
        textarea.style.overflowY = "auto"
      } else {
        textarea.style.height = `${scrollHeight}px`
        textarea.style.overflowY = "hidden"
      }
    },
    [minHeight, maxHeight],
  )

  useEffect(() => {
    if (textareaRef.current) textareaRef.current.style.height = `${minHeight}px`
  }, [minHeight])

  return { textareaRef, adjustHeight }
}

interface ChatInputProps {
  message: string
  setMessage: (message: string) => void
  uploadedFiles: File[]
  setUploadedFiles: (files: File[]) => void
  onSend: () => void
  disabled?: boolean
  placeholder?: string
  variant?: "initial" | "conversation"
}

export function ChatInput({
  message,
  setMessage,
  uploadedFiles,
  setUploadedFiles,
  onSend,
  disabled = false,
  placeholder = "输入您的问题...",
  variant = "conversation",
}: ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { textareaRef, adjustHeight } = useAutoResizeTextarea({
    minHeight: variant === "initial" ? 80 : 48,
    maxHeight: 200,
  })

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setUploadedFiles(Array.from(e.target.files))
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      onSend()
    }
  }

  const canSend = (message.trim() || uploadedFiles.length > 0) && !disabled

  // 初始状态的样式
  if (variant === "initial") {
    return (
      <div className="w-full">
        <div className="relative bg-black/40 backdrop-blur-xl rounded-2xl border border-white/10 shadow-2xl overflow-hidden group hover:border-white/20 transition-colors duration-300">
          <Textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => {
              setMessage(e.target.value)
              adjustHeight()
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className={cn(
              "w-full px-6 py-5 resize-none border-none",
              "bg-transparent text-white text-lg",
              "focus-visible:ring-0 focus-visible:ring-offset-0",
              "placeholder:text-neutral-500 min-h-[80px] max-h-[200px]",
              "overflow-y-auto",
            )}
          />

          {uploadedFiles.length > 0 && (
            <div className="px-6 pb-2 flex flex-wrap gap-2">
              {uploadedFiles.map((file, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-2 bg-white/10 rounded-lg px-3 py-1.5 text-xs text-white backdrop-blur-md"
                >
                  {file.type.startsWith("image/") ? <ImageIcon className="w-3 h-3" /> : <FileText className="w-3 h-3" />}
                  <span className="truncate max-w-[150px]">{file.name}</span>
                  <button
                    onClick={() => setUploadedFiles(uploadedFiles.filter((_, i) => i !== idx))}
                    className="hover:text-red-400 transition-colors"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between px-4 py-3 bg-white/5 border-t border-white/5">
            <input ref={fileInputRef} type="file" multiple onChange={handleFileChange} className="hidden" />
            <Button
              variant="ghost"
              size="icon"
              onClick={() => fileInputRef.current?.click()}
              className="text-neutral-400 hover:text-white hover:bg-white/10 transition-all"
            >
              <Paperclip className="w-5 h-5" />
            </Button>

            <Button
              disabled={!canSend}
              onClick={onSend}
              className={cn(
                "flex items-center gap-2 px-4 py-2 rounded-xl transition-all duration-300",
                canSend
                  ? "bg-white text-black hover:bg-neutral-200 shadow-lg shadow-white/10"
                  : "bg-white/10 text-neutral-500 cursor-not-allowed",
              )}
            >
              <span className="text-sm font-medium">发送</span>
              <ArrowUpIcon className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // 对话状态的样式
  return (
    <div className="space-y-3">
      <div className="relative bg-black/60 backdrop-blur-md rounded-xl border border-neutral-700">
        <Textarea
          ref={textareaRef}
          value={message}
          onChange={(e) => {
            setMessage(e.target.value)
            adjustHeight()
          }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className={cn(
            "w-full px-4 py-3 resize-none border-none",
            "bg-transparent text-white text-sm",
            "focus-visible:ring-0 focus-visible:ring-offset-0",
            "placeholder:text-neutral-400 min-h-[48px]",
            "[&::-webkit-scrollbar]:w-2",
            "[&::-webkit-scrollbar-track]:bg-transparent",
            "[&::-webkit-scrollbar-thumb]:bg-white/20",
            "[&::-webkit-scrollbar-thumb]:rounded-full",
            "[&::-webkit-scrollbar-thumb]:hover:bg-white/30",
          )}
        />

        {uploadedFiles.length > 0 && (
          <div className="px-4 pb-2 flex flex-wrap gap-2">
            {uploadedFiles.map((file, idx) => (
              <div key={idx} className="flex items-center gap-2 bg-neutral-700/50 rounded-lg px-3 py-1.5 text-xs text-white">
                {file.type.startsWith("image/") ? <ImageIcon className="w-3 h-3" /> : <FileText className="w-3 h-3" />}
                <span className="truncate max-w-[150px]">{file.name}</span>
                <button
                  onClick={() => setUploadedFiles(uploadedFiles.filter((_, i) => i !== idx))}
                  className="hover:text-red-400"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between p-3">
          <input ref={fileInputRef} type="file" multiple onChange={handleFileChange} className="hidden" />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            className="text-white hover:bg-neutral-700"
          >
            <Paperclip className="w-4 h-4" />
          </Button>

          <Button
            disabled={!canSend}
            onClick={onSend}
            className={cn(
              "flex items-center gap-1 px-3 py-2 rounded-lg transition-colors",
              canSend ? "bg-white text-black hover:bg-neutral-200" : "bg-neutral-700 text-neutral-400 cursor-not-allowed",
            )}
          >
            <ArrowUpIcon className="w-4 h-4" />
          </Button>
        </div>
      </div>

      <p className="text-center text-[11px] text-gray-400">MediArch 的回答未必正确无误,请注意核查</p>
    </div>
  )
}
