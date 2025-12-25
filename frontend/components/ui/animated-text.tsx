"use client"

import { motion } from "framer-motion"

interface AnimatedTextProps {
  text: string
  className?: string
}

export function AnimatedText({ text, className = "" }: AnimatedTextProps) {
  const letters = text.split("")

  return (
    <span className={className}>
      {letters.map((letter, index) => (
        <motion.span
          key={index}
          initial={{ opacity: 0.5 }}
          animate={{
            opacity: [0.5, 1, 0.5],
            scale: [1, 1.05, 1],
          }}
          transition={{
            duration: 2,
            repeat: Number.POSITIVE_INFINITY,
            delay: index * 0.1,
            ease: "easeInOut",
          }}
          style={{ display: "inline-block" }}
        >
          {letter === " " ? "\u00A0" : letter}
        </motion.span>
      ))}
    </span>
  )
}
