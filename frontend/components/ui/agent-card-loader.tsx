"use client"

export function AgentCardLoader() {
  return (
    <div className="absolute inset-0 rounded-lg overflow-hidden pointer-events-none">
      <div className="absolute inset-0 rounded-lg animate-agentBorder" />
      <style jsx>{`
        @keyframes agentBorder {
          0% {
            transform: rotate(0deg);
            box-shadow:
              0 0 12px 2px rgba(56, 189, 248, 0.4) inset,
              0 0 18px 3px rgba(0, 93, 255, 0.3) inset,
              0 0 24px 4px rgba(30, 64, 175, 0.2) inset,
              0 0 6px 2px rgba(56, 189, 248, 0.5),
              0 0 12px 3px rgba(0, 93, 255, 0.3);
          }
          50% {
            transform: rotate(180deg);
            box-shadow:
              0 0 18px 3px rgba(96, 165, 250, 0.5) inset,
              0 0 12px 2px rgba(2, 132, 199, 0.4) inset,
              0 0 36px 6px rgba(0, 93, 255, 0.3) inset,
              0 0 6px 2px rgba(56, 189, 248, 0.5),
              0 0 12px 3px rgba(0, 93, 255, 0.3);
          }
          100% {
            transform: rotate(360deg);
            box-shadow:
              0 0 12px 2px rgba(77, 200, 253, 0.4) inset,
              0 0 18px 3px rgba(0, 93, 255, 0.3) inset,
              0 0 24px 4px rgba(30, 64, 175, 0.2) inset,
              0 0 6px 2px rgba(56, 189, 248, 0.5),
              0 0 12px 3px rgba(0, 93, 255, 0.3);
          }
        }

        .animate-agentBorder {
          animation: agentBorder 4s linear infinite;
        }
      `}</style>
    </div>
  )
}
