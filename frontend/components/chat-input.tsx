"use client"

import type React from "react"

import { useState, useRef, useEffect, type KeyboardEvent, type ChangeEvent } from "react"

interface ChatInputProps {
  onSend: (message: string) => void
  placeholder?: string
  disabled?: boolean
  maxRows?: number
  className?: string
}

export function ChatInput({
  onSend,
  placeholder = "输入您的问题...",
  disabled = false,
  maxRows = 6,
  className = "",
}: ChatInputProps) {
  const [value, setValue] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const adjustHeight = () => {
    const textarea = textareaRef.current
    if (!textarea) return

    textarea.style.height = "auto"
    const lineHeight = 24
    const maxHeight = lineHeight * maxRows
    const newHeight = Math.min(textarea.scrollHeight, maxHeight)
    textarea.style.height = `${newHeight}px`
  }

  useEffect(() => {
    adjustHeight()
  }, [value])

  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSend = () => {
    if (!value.trim() || disabled) return
    onSend(value)
    setValue("")
  }

  return (
    <div
      className={`chat-input-container group ${className}`}
      style={
        {
          "--glow-color-from": "34 197 94",
          "--glow-color-to": "168 85 247",
          "--glow-intensity": "0.4",
          "--glow-blur": "20px",
        } as React.CSSProperties
      }
    >
      <div className="flex items-end gap-3 bg-white/5 backdrop-blur-sm rounded-2xl p-4 border border-white/10 transition-all duration-300 hover:translate-y-[-2px] focus-within:shadow-glow motion-reduce:transition-none motion-reduce:hover:translate-y-0">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          aria-label="聊天输入框"
          className="flex-1 bg-transparent text-white placeholder:text-white/40 resize-none outline-none min-h-[24px] max-h-[144px] leading-6"
        />
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          aria-label="发送消息"
          className="send-button flex-shrink-0 w-10 h-10 rounded-full bg-gradient-to-br from-cyan-500 to-purple-600 flex items-center justify-center text-white transition-all duration-200 hover:translate-y-[-1px] hover:shadow-button-glow active:translate-y-0 active:shadow-button-glow-active focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0 disabled:hover:shadow-none motion-reduce:transition-none motion-reduce:hover:translate-y-0"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M22 2L11 13" />
            <path d="M22 2L15 22L11 13L2 9L22 2Z" />
          </svg>
        </button>
      </div>

      <style jsx>{`
        .chat-input-container {
          --glow-from: rgb(var(--glow-color-from));
          --glow-to: rgb(var(--glow-color-to));
        }

        .focus-within\\:shadow-glow:focus-within {
          box-shadow: 0 0 0 1px rgba(var(--glow-color-from), 0.3),
            0 0 calc(var(--glow-blur) * 0.5) rgba(var(--glow-color-from), var(--glow-intensity)),
            0 0 var(--glow-blur) rgba(var(--glow-color-to), calc(var(--glow-intensity) * 0.6));
        }

        .send-button:hover {
          box-shadow: 0 0 20px rgba(var(--glow-color-from), 0.5),
            0 0 40px rgba(var(--glow-color-to), 0.3);
        }

        .send-button:active {
          box-shadow: 0 0 10px rgba(var(--glow-color-from), 0.4),
            0 0 20px rgba(var(--glow-color-to), 0.2);
        }

        @media (prefers-reduced-motion: reduce) {
          .chat-input-container * {
            transition: none !important;
            animation: none !important;
          }
        }
      `}</style>
    </div>
  )
}

// Example usage
export function ChatInputExample() {
  const handleSend = (message: string) => {
    console.log("Sending message:", message)
  }

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-8">
      <div className="w-full max-w-2xl">
        <ChatInput onSend={handleSend} placeholder="输入您的问题..." />
      </div>
    </div>
  )
}
