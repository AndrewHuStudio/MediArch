"""
测试单页 OCR - 快速定位问题

使用方法:
    python test_ocr_single_page.py
"""

import os
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.resolve()
BACKEND_ROOT = PROJECT_ROOT / "backend"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

print(f"[OK] Project root: {PROJECT_ROOT}")
print(f"[OK] Backend root: {BACKEND_ROOT}")

from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline
from dotenv import load_dotenv

# 加载环境变量
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)
print(f"[OK] Loaded environment from {env_path}")

def test_single_page():
    """测试处理单页文档"""

    # 选择一个测试文档
    documents_dir = BACKEND_ROOT / "databases" / "documents"

    # 尝试找到第一个可用的 PDF
    test_pdf = None
    test_category = None

    categories = {
        "标准规范": "标准规范",
        "参考论文": "参考论文",
        "书籍报告": "书籍报告",
        "政策文件": "政策文件",
    }

    for category_name, category_dir in categories.items():
        category_path = documents_dir / category_dir
        if category_path.exists():
            pdf_files = list(category_path.glob("*.pdf"))
            if pdf_files:
                test_pdf = pdf_files[0]
                test_category = category_name
                break

    if not test_pdf:
        print("[FAIL] 未找到任何 PDF 文件进行测试")
        return

    print(f"\n{'='*80}")
    print(f"[OK] 测试文档: {test_pdf.name}")
    print(f"[OK] 类别: {test_category}")
    print(f"[OK] 路径: {test_pdf}")
    print(f"{'='*80}\n")

    # 设置强制重新索引（必须在初始化 Pipeline 之前）
    os.environ["FORCE_REINGEST"] = "1"

    # 初始化 pipeline
    print("[OK] 初始化 Pipeline...")
    try:
        pipeline = DocumentIngestionPipeline(engine="mineru")
        print("[OK] Pipeline 初始化成功\n")
    except Exception as e:
        print(f"[FAIL] Pipeline 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 测试处理第 1 页
    print("[OK] 开始处理第 1 页...")
    print("-" * 80)

    try:
        result = pipeline.process_document(
            pdf_path=str(test_pdf),
            category=test_category,
            page_range=(1, 1)  # 只处理第 1 页
        )

        print("-" * 80)
        print(f"\n[OK] 处理完成！")
        print(f"状态: {result.get('status')}")

        if result.get('status') == 'success':
            print(f"MongoDB Doc ID: {result.get('mongo_doc_id')}")
            print(f"总页数: {result.get('total_pages')}")
            print(f"总块数: {result.get('total_chunks')}")
            print(f"文本块: {result.get('text_chunks')}")
            print(f"图片块: {result.get('image_chunks')}")
            print(f"VLM 处理: {result.get('vlm_processed')}")
            print(f"向量写入: {result.get('embeddings_written')}")
            print(f"耗时: {result.get('timings', {}).get('total_s', 0):.2f}s")
        else:
            print(f"原因: {result.get('reason', 'unknown')}")
            if 'error' in result:
                print(f"错误: {result['error']}")

    except Exception as e:
        print("-" * 80)
        print(f"\n[FAIL] 处理失败")
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {e}")
        print("\n完整错误堆栈:")
        import traceback
        traceback.print_exc()

        # 额外诊断信息
        print("\n" + "="*80)
        print("诊断信息:")
        print("="*80)

        # 检查 MinerU 配置
        print(f"OCR_ENGINE: {os.getenv('OCR_ENGINE')}")
        print(f"MINERU_API_URL: {os.getenv('MINERU_API_URL')}")
        print(f"MINERU_API_MODE: {os.getenv('MINERU_API_MODE')}")
        print(f"MINERU_BACKEND: {os.getenv('MINERU_BACKEND')}")
        print(f"MINERU_API_KEY: {'已设置' if os.getenv('MINERU_API_KEY') else '未设置'}")

        # 检查临时目录
        tmpdir = os.getenv('TMPDIR') or os.getenv('TEMP') or os.getenv('TMP')
        print(f"临时目录: {tmpdir}")
        if tmpdir:
            print(f"临时目录存在: {Path(tmpdir).exists()}")

        # 检查输出目录
        ocr_output = os.getenv('OCR_OUTPUT_DIR', 'backend/databases/documents_ocr')
        print(f"OCR 输出目录: {ocr_output}")
        print(f"输出目录存在: {Path(ocr_output).exists()}")

if __name__ == "__main__":
    try:
        test_single_page()
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FAIL] 发生未捕获错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
