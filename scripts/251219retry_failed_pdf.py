"""
测试文档信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 功能说明:
   重新处理重建过程中失败的单个PDF文件

🎯 测试目标:
   - 重新处理失败的PDF：结合建筑空间设计的医院导向标识系统设计探讨_龙灏.pdf
   - 查看详细的错误信息
   - 验证重新处理是否成功

📂 涉及的主要文件:
   - backend/databases/ingestion/indexing/pipeline.py (处理pipeline)
   - backend/databases/ingestion/ocr/mineru_client.py (OCR客户端)

🗑️ 删除时机:
   - [✓] 失败文件成功重新处理
   - [✓] 问题原因已确认
   - [ ] 预计可删除时间: 2025-12-20

⚠️ 注意事项:
   - 需要设置FORCE_REINGEST=1以强制重新处理
   - 会生成详细的日志信息
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def retry_failed_pdf():
    """重新处理失败的PDF"""

    # 失败的PDF路径
    pdf_path = "backend/databases/documents/参考论文/结合建筑空间设计的医院导向标识系统设计探讨_龙灏.pdf"

    print("\n" + "="*60)
    print("重新处理失败的PDF文件")
    print("="*60)
    print(f"文件: {pdf_path}")
    print(f"分类: 参考论文")
    print()

    # 设置强制重新处理
    os.environ["FORCE_REINGEST"] = "1"

    try:
        # 初始化pipeline
        print("[1/3] 初始化处理pipeline...")
        pipeline = DocumentIngestionPipeline(engine="mineru")
        print("[OK] Pipeline初始化完成")
        print()

        # 处理文档
        print("[2/3] 开始处理PDF...")
        print("-" * 60)
        result = pipeline.process_document(
            pdf_path=pdf_path,
            category="参考论文"
        )
        print("-" * 60)
        print()

        # 显示结果
        print("[3/3] 处理结果:")
        print(f"状态: {result.get('status')}")

        if result.get('status') == 'success':
            print("[OK] 处理成功!")
            print(f"  - MongoDB Doc ID: {result.get('mongo_doc_id')}")
            print(f"  - 总页数: {result.get('total_pages')}")
            print(f"  - 总chunks: {result.get('total_chunks')}")
            print(f"  - 文本chunks: {result.get('text_chunks')}")
            print(f"  - 图片chunks: {result.get('image_chunks')}")
            print(f"  - 向量数: {result.get('embeddings_written')}")
            print(f"  - 处理时长: {result.get('timings', {}).get('total_s', 0):.2f}秒")
        elif result.get('status') == 'skipped':
            print("[SKIP] 文档已存在，已跳过")
            print(f"  - 原因: {result.get('reason')}")
        else:
            print("[FAIL] 处理失败!")
            print(f"  - 原因: {result.get('reason', result.get('error', 'unknown'))}")
            if result.get('error'):
                print(f"  - 错误详情: {result.get('error')}")

        print()
        print("="*60)

        # 关闭pipeline
        pipeline.close()

        return result

    except Exception as e:
        print()
        print(f"[ERROR] 发生异常: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "failed", "error": str(e)}

if __name__ == "__main__":
    result = retry_failed_pdf()

    # 返回退出码
    sys.exit(0 if result.get('status') == 'success' else 1)
