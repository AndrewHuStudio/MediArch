export default function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-black px-6">
      <div className="w-full max-w-3xl space-y-8 text-center text-white">
        <div className="space-y-4">
          <p className="text-xs uppercase tracking-[0.4em] text-white/40">MediArch</p>
          <h1 className="text-4xl font-semibold">正在唤醒首页体验</h1>
          <p className="text-sm text-white/60">加载医院设计知识、案例与团队展示...</p>
        </div>

        <div className="relative mx-auto h-3 w-full max-w-md overflow-hidden rounded-full bg-white/10">
          <div className="absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full bg-white/70" />
        </div>
      </div>
    </div>
  )
}
