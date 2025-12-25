"use client"

import { useState } from "react"
import Link from "next/link"
import { motion, AnimatePresence } from "framer-motion"

interface NavItem {
  label: string
  href: string
  dropdown?: {
    label: string
    href: string
  }[]
}

const navItems: NavItem[] = [
  {
    label: "首页",
    href: "/",
    dropdown: [
      {
        label: "智能问答",
        href: "/chat",
      },
    ],
  },
  {
    label: "知识库",
    href: "/#section-1", // Link to knowledge base section on landing page
  },
  {
    label: "实验室",
    href: "/#section-2", // Link to team section on landing page
  },
]
// </CHANGE>

export function ChatHeader() {
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)

  return (
    <header className="fixed top-0 left-0 right-0 z-50">
      <div className="absolute inset-0 bg-black/20 backdrop-blur-sm -z-10" />
      <div className="max-w-7xl mx-auto flex items-center justify-end py-3 px-6 relative z-10">
        <nav className="flex items-center gap-8">
          {navItems.map((item) => (
            <div
              key={item.label}
              className="relative"
              onMouseEnter={() => setHoveredItem(item.label)}
              onMouseLeave={() => setHoveredItem(null)}
            >
              <Link
                href={item.href}
                className={`text-sm font-medium transition-colors cursor-pointer ${
                  hoveredItem === item.label
                    ? "text-white border-b border-white pb-1"
                    : "text-gray-400 hover:text-white"
                }`}
              >
                {item.label}
              </Link>

              <AnimatePresence>
                {item.dropdown && hoveredItem === item.label && (
                  <motion.div
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    transition={{ duration: 0.2 }}
                    className="absolute top-full left-0 mt-2 min-w-[160px] bg-black/90 backdrop-blur-md border border-white/20 rounded-lg shadow-xl overflow-hidden"
                  >
                    {item.dropdown.map((dropdownItem, index) => (
                      <Link
                        key={dropdownItem.label}
                        href={dropdownItem.href}
                        className="block px-4 py-2.5 text-sm text-gray-300 hover:text-white hover:bg-white/10 transition-all duration-200 cursor-pointer"
                        style={{
                          animationDelay: `${index * 50}ms`,
                        }}
                      >
                        {dropdownItem.label}
                      </Link>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
              {/* </CHANGE> */}
            </div>
          ))}
        </nav>
      </div>
    </header>
  )
}
