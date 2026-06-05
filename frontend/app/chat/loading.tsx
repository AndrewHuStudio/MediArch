export default function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f7fbfc] px-6">
      <div className="space-y-4 text-center">
        <div className="mx-auto h-16 w-16 animate-spin rounded-full border-4 border-[#d9e7eb] border-t-[#0e7490]" />
        <p className="text-sm text-[#516b72]">正在准备智能问答工作台...</p>
      </div>
    </div>
  )
}
