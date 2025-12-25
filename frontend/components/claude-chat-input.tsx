"use client"

import type React from "react"

import { useState, useRef, useEffect } from "react"
import { Send, Paperclip, X, FileText } from "lucide-react"

interface UploadedFile {
  file: File
  preview?: string
}

interface ClaudeChatInputProps {
  onSendMessage: (text: string, files: File[]) => void
  isLoading: boolean
  initialValue?: string
}

export function ClaudeChatInput({ onSendMessage, isLoading, initialValue = "" }: ClaudeChatInputProps) {
  const [input, setInput] = useState(initialValue)
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([])
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (initialValue) {
      setInput(initialValue)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [initialValue])

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = "auto"
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 200)}px`
    }
  }, [input])

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    const newFiles: UploadedFile[] = files.map((file) => {
      const uploaded: UploadedFile = { file }
      if (file.type.startsWith("image/")) {
        uploaded.preview = URL.createObjectURL(file)
      }
      return uploaded
    })
    setUploadedFiles((prev) => [...prev, ...newFiles])
  }

  const removeFile = (index: number) => {
    setUploadedFiles((prev) => {
      const newFiles = [...prev]
      if (newFiles[index].preview) {
        URL.revokeObjectURL(newFiles[index].preview!)
      }
      newFiles.splice(index, 1)
      return newFiles
    })
  }

  const handleSend = () => {
    const text = input.trim()
    if ((!text && uploadedFiles.length === 0) || isLoading) return

    onSendMessage(
      text,
      uploadedFiles.map((uf) => uf.file),
    )
    setInput("")
    setUploadedFiles([])
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="w-full max-w-4xl mx-auto px-4 pb-6">
      {/* File Previews */}
      {uploadedFiles.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {uploadedFiles.map((uploadedFile, idx) => (
            <div key={idx} className="relative group">
              {uploadedFile.preview ? (
                <div className="relative w-20 h-20 rounded-lg overflow-hidden border border-gray-700/50 bg-gray-900">
                  <img
                    src={uploadedFile.preview || "/placeholder.svg"}
                    alt={uploadedFile.file.name}
                    className="w-full h-full object-cover"
                  />
                  <button
                    onClick={() => removeFile(idx)}
                    className="absolute top-1 right-1 w-5 h-5 bg-black/80 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black"
                  >
                    <X className="w-3 h-3 text-white" />
                  </button>
                </div>
              ) : (
                <div className="relative flex items-center gap-2 bg-gray-900/80 rounded-lg px-3 py-2 pr-8 border border-gray-700/50">
                  <FileText className="w-4 h-4 text-gray-400" />
                  <span className="text-sm text-gray-300 truncate max-w-[120px]">{uploadedFile.file.name}</span>
                  <button
                    onClick={() => removeFile(idx)}
                    className="absolute top-1/2 -translate-y-1/2 right-2 w-5 h-5 bg-gray-800 rounded-full flex items-center justify-center hover:bg-gray-700 transition-colors"
                  >
                    <X className="w-3 h-3 text-white" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Input Container - Claude Style */}
      <div className="relative rounded-3xl bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.08),0_2px_8px_rgba(0,0,0,0.12)] overflow-hidden transition-shadow hover:shadow-[0_0_0_1px_rgba(0,0,0,0.08),0_4px_16px_rgba(0,0,0,0.16)]">
        <div className="flex items-end gap-2 p-2">
          {/* File Upload Button */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,.pdf,.doc,.docx,.txt"
            onChange={handleFileSelect}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
            className="flex-shrink-0 w-10 h-10 rounded-xl bg-transparent hover:bg-gray-100 transition-colors flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
            title="Attach files"
          >
            <Paperclip className="w-5 h-5 text-gray-600" />
          </button>

          {/* Text Input */}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message MediArch..."
            rows={1}
            disabled={isLoading}
            className="flex-1 bg-transparent text-gray-900 px-2 py-2.5 focus:outline-none placeholder:text-gray-400 resize-none max-h-[200px] disabled:opacity-50"
          />

          {/* Send Button */}
          <button
            onClick={handleSend}
            disabled={(!input.trim() && uploadedFiles.length === 0) || isLoading}
            className="flex-shrink-0 w-10 h-10 rounded-xl bg-black text-white disabled:bg-gray-200 disabled:text-gray-400 hover:bg-gray-800 transition-colors flex items-center justify-center disabled:cursor-not-allowed"
            title="Send message"
          >
            <Send className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Helper Text */}
      <p className="text-xs text-gray-500 mt-2 text-center">
        MediArch can make mistakes. Please verify important information.
      </p>
    </div>
  )
}
