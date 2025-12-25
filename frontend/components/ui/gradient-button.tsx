"use client"

import type React from "react"

import type { HTMLAttributes } from "react"

interface GradientButtonProps extends HTMLAttributes<HTMLDivElement> {
  children?: React.ReactNode
  width?: string
  height?: string
  onClick?: () => void
  disabled?: boolean
}

const GradientButton = ({
  children,
  width = "200px",
  height = "50px",
  className = "",
  onClick,
  disabled = false,
  ...props
}: GradientButtonProps) => {
  const commonGradientStyles = `
    relative rounded-[50px] cursor-pointer
    after:content-[""] after:block after:absolute after:bg-black
    after:inset-[2px] after:rounded-[48px] after:z-[1]
    after:transition-opacity after:duration-300 after:ease-linear
    flex items-center justify-center
    ${disabled ? "opacity-50 cursor-not-allowed" : ""}
  `

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      onClick?.()
    }
  }

  return (
    <div className="text-white text-center">
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        className={`
          ${commonGradientStyles}
          rotatingGradient
          ${className}
        `}
        style={
          {
            "--r": "0deg",
            minWidth: width,
            height: height,
          } as React.CSSProperties
        }
        onClick={disabled ? undefined : onClick}
        onKeyDown={handleKeyDown}
        aria-disabled={disabled}
        {...props}
      >
        <span className="relative z-10 text-white flex items-center justify-center font-semibold">{children}</span>
      </div>
    </div>
  )
}

export default GradientButton
