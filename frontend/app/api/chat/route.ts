import { type NextRequest, NextResponse } from "next/server"

export async function POST(req: NextRequest) {
  try {
    const { message, files } = await req.json()

    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      async start(controller) {
        const response = `这是一个关于"${message}"的专业回答。

作为医疗建筑设计助手，我可以为您提供详细的设计建议和规范标准。

## 主要考虑因素

1. **功能分区** - 合理规划医疗功能区域
2. **流线设计** - 优化人员和物资流动路径
3. **感染控制** - 严格遵守医疗卫生标准

\`\`\`javascript
// 示例代码：空间计算
function calculateRoomArea(length, width) {
  return length * width;
}
\`\`\`

请问您需要了解更具体的哪方面内容？`

        // Stream the response character by character
        for (let i = 0; i < response.length; i++) {
          controller.enqueue(encoder.encode(response[i]))
          await new Promise((resolve) => setTimeout(resolve, 20))
        }

        controller.close()
      },
    })

    return new Response(stream, {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Transfer-Encoding": "chunked",
      },
    })
  } catch (error) {
    console.error("[v0] Chat API error:", error)
    return NextResponse.json({ error: "Failed to process request" }, { status: 500 })
  }
}
