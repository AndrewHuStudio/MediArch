# -*- coding: utf-8 -*-
"""测试 VLM 配置是否正确"""

import os
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

def test_vlm_config():
    """测试VLM配置"""
    print("\n" + "=" * 80)
    print("测试 VLM (视觉语言模型) 配置")
    print("=" * 80 + "\n")

    # 检查环境变量（优先 VLM_*，兼容旧 KG_VISION_*）
    api_key = os.getenv("VLM_API_KEY") or os.getenv("KG_VISION_API_KEY")
    base_url = os.getenv("VLM_BASE_URL") or os.getenv("KG_VISION_BASE_URL")
    model = os.getenv("VLM_MODEL") or os.getenv("VLM_MODE") or os.getenv("KG_VISION_MODEL", "qwen3-vl-plus")

    print("[1] 环境变量检查:")
    print(f"  VLM_API_KEY/KG_VISION_API_KEY: {'[OK] 已配置 (***' + api_key[-10:] + ')' if api_key else '[ERROR] 未配置'}")
    print(f"  VLM_BASE_URL/KG_VISION_BASE_URL: {base_url if base_url else '[ERROR] 未配置'}")
    print(f"  VLM_MODEL/VLM_MODE/KG_VISION_MODEL: {model}")
    print()

    if not api_key or not base_url:
        print("[ERROR] VLM 配置不完整，请检查 .env 文件")
        return False

    # 测试 VisionDescriber 初始化
    print("[2] 测试 VisionDescriber 初始化:")
    try:
        from backend.databases.ingestion.indexing.vision_describer import VisionDescriber

        describer = VisionDescriber()
        if describer.enabled:
            print(f"  [OK] VisionDescriber 初始化成功")
            print(f"  - API URL: {describer.base_url}")
            print(f"  - Model: {describer.model}")
            print(f"  - Timeout: {describer.timeout}s")
            print(f"  - Max Tokens: {describer.max_tokens}")
            print(f"  - Cache: {'启用' if describer.cache_enabled else '禁用'}")
        else:
            print(f"  [WARN] VisionDescriber 未启用")
            return False
    except Exception as e:
        print(f"  [ERROR] 初始化失败: {e}")
        return False
    print()

    # 检查是否有图片可以测试
    print("[3] 查找测试图片:")
    data_dir = PROJECT_ROOT / "data" / "images"
    if data_dir.exists():
        images = list(data_dir.glob("*.png")) + list(data_dir.glob("*.jpg"))
        if images:
            print(f"  [OK] 找到 {len(images)} 张图片")
            print(f"  示例: {images[0].name}")
        else:
            print(f"  [WARN] {data_dir} 中无图片文件")
    else:
        print(f"  [WARN] 目录不存在: {data_dir}")
    print()

    print("=" * 80)
    print("[SUCCESS] VLM 配置检查完成！")
    print("=" * 80)
    return True


if __name__ == "__main__":
    success = test_vlm_config()
    sys.exit(0 if success else 1)
