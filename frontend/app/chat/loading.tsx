export default function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-black px-6">
      <div className="space-y-4 text-center">
        <div className="mx-auto h-16 w-16 animate-spin rounded-full border-4 border-white/10 border-t-white" />
        <p className="text-sm text-white/60">正在准备智能问答工作台...</p>
      </div>
    </div>
  )
}
