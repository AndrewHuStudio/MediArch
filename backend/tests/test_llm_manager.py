# backend/tests/test_llm_manager.py

import os
import sys

# 让 "app/..." 能被找到：把 backend/ 加入 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
from app.agents.base_agent import get_llm_manager
from langchain_openai import ChatOpenAI


def test_llm_manager():
    # 加载 .env（若已在系统环境变量里设置，也兼容）
    load_dotenv()

    api_key = os.getenv("KG_OPENAI_API_KEY")
    base_url = os.getenv("KG_OPENAI_BASE_URL")  # 允许为空
    model = os.getenv("KG_OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        raise ValueError("请先设置环境变量 KG_OPENAI_API_KEY（或在项目根目录的 .env 中配置）")

    manager = get_llm_manager()

    # 第一次创建：应当真正初始化
    def _init():
        # ChatOpenAI 支持 base_url，可为空；不做任何网络调用，仅构造实例
        return ChatOpenAI(model=model, api_key=api_key, base_url=base_url) if base_url \
            else ChatOpenAI(model=model, api_key=api_key)

    llm1 = manager.get_or_create(name="test_llm", init_func=_init)

    # 第二次获取：不应该再次调用 _init，应返回同一个对象
    llm2 = manager.get_or_create(name="test_llm", init_func=lambda: None)

    assert llm1 is llm2, "❌ LLM 单例失败"
    print("✅ LLMManager 单例测试通过")

    # 清理（便于重复运行测试）
    manager.clear("test_llm")


if __name__ == "__main__":
    test_llm_manager()
