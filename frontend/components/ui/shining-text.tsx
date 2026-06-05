"use client"
import { motion } from "framer-motion"

interface ShiningTextProps {
  text: string
}

export function ShiningText({ text }: ShiningTextProps) {
  return (
    <motion.span
      className="bg-[linear-gradient(110deg,#12323a,35%,#0e7490,50%,#12323a,75%,#12323a)] bg-[length:200%_100%] bg-clip-text text-base font-regular text-transparent inline-block"
      initial={{ backgroundPosition: "200% 0" }}
      animate={{ backgroundPosition: "-200% 0" }}
      transition={{
        repeat: Number.POSITIVE_INFINITY,
        duration: 2,
        ease: "linear",
      }}
    >
      {text}
    </motion.span>
  )
}
