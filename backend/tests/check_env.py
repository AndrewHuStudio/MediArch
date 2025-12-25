"""环境变量配置检查脚本

用途：检查所有 Agent 的环境变量配置和 LLM 连接
日期：2025-12-01
"""

import os
import sys
from typing import Dict, List, Tuple

# 添加项目路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


def check_env_vars() -> Dict[str, Dict[str, str]]:
    """检查环境变量配置"""
    print("=" * 60)
    print("环境变量配置检查")
    print("=" * 60)

    # 定义需要检查的环境变量
    env_groups = {
        "全局配置": [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
        ],
        "Neo4j Agent": [
            "NEO4J_AGENT_API_KEY",
            "NEO4J_AGENT_BASE_URL",
            "NEO4J_AGENT_MODEL",
        ],
        "MongoDB Agent": [
            "MONGODB_AGENT_API_KEY",
            "MONGODB_AGENT_BASE_URL",
            "MONGODB_AGENT_MODEL",
        ],
        "Milvus Agent": [
            "MILVUS_AGENT_API_KEY",
            "MILVUS_AGENT_BASE_URL",
            "MILVUS_AGENT_MODEL",
        ],
        "Result Synthesizer": [
            "RESULT_SYNTHESIZER_AGENT_API_KEY",
            "RESULT_SYNTHESIZER_AGENT_BASE_URL",
            "RESULT_SYNTHESIZER_AGENT_MODEL",
            "EVALUATOR_API_KEY",
            "EVALUATOR_BASE_URL",
            "EVALUATOR_MODEL",
        ],
        "数据库配置": [
            "NEO4J_URI",
            "NEO4J_USERNAME",
            "NEO4J_PASSWORD",
            "MONGODB_URI",
            "MILVUS_HOST",
            "MILVUS_PORT",
        ],
    }

    results = {}
    for group_name, env_vars in env_groups.items():
        print(f"\n{group_name}:")
        print("-" * 60)
        group_results = {}
        for var in env_vars:
            value = os.getenv(var)
            if value:
                # 隐藏敏感信息
                if "KEY" in var or "PASSWORD" in var:
                    display_value = value[:8] + "..." if len(value) > 8 else "***"
                else:
                    display_value = value
                print(f"  [OK] {var} = {display_value}")
                group_results[var] = "已设置"
            else:
                print(f"  [MISSING] {var} = 未设置")
                group_results[var] = "未设置"
        results[group_name] = group_results

    return results


def test_llm_connections():
    """测试 LLM 连接"""
    print("\n" + "=" * 60)
    print("LLM 连接测试")
    print("=" * 60)

    test_results: List[Tuple[str, bool, str]] = []

    # 1. 测试全局 LLM 配置
    print("\n1. 测试全局 LLM 配置")
    print("-" * 60)
    try:
        from langchain.chat_models import init_chat_model

        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        if not api_key:
            print("  [FAIL] 缺少 OPENAI_API_KEY")
            test_results.append(("全局 LLM", False, "缺少 API KEY"))
        else:
            llm = init_chat_model(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0,
                max_tokens=100,
            )
            print(f"  [OK] LLM 初始化成功: {model}")
            test_results.append(("全局 LLM", True, f"{model}"))
    except Exception as e:
        print(f"  [FAIL] LLM 初始化失败: {e}")
        test_results.append(("全局 LLM", False, str(e)))

    # 2. 测试 Neo4j Agent LLM
    print("\n2. 测试 Neo4j Agent LLM")
    print("-" * 60)
    try:
        from backend.app.agents.neo4j_agent.agent import get_analysis_llm
        import asyncio

        llm = asyncio.run(get_analysis_llm())
        print("  [OK] Neo4j Agent LLM 初始化成功")
        test_results.append(("Neo4j Agent", True, "成功"))
    except Exception as e:
        print(f"  [FAIL] Neo4j Agent LLM 初始化失败: {e}")
        test_results.append(("Neo4j Agent", False, str(e)))

    # 3. 测试 MongoDB Agent LLM
    print("\n3. 测试 MongoDB Agent LLM")
    print("-" * 60)
    try:
        from backend.app.agents.mongodb_agent.agent import get_rewrite_llm
        import asyncio

        llm = asyncio.run(get_rewrite_llm())
        print("  [OK] MongoDB Agent LLM 初始化成功")
        test_results.append(("MongoDB Agent", True, "成功"))
    except Exception as e:
        print(f"  [FAIL] MongoDB Agent LLM 初始化失败: {e}")
        test_results.append(("MongoDB Agent", False, str(e)))

    # 4. 测试 Milvus Agent LLM
    print("\n4. 测试 Milvus Agent LLM")
    print("-" * 60)
    try:
        from backend.app.agents.milvus_agent.agent import get_rewrite_llm
        import asyncio

        llm = asyncio.run(get_rewrite_llm())
        print("  [OK] Milvus Agent LLM 初始化成功")
        test_results.append(("Milvus Agent", True, "成功"))
    except Exception as e:
        print(f"  [FAIL] Milvus Agent LLM 初始化失败: {e}")
        test_results.append(("Milvus Agent", False, str(e)))

    # 5. 测试 Result Synthesizer LLM
    print("\n5. 测试 Result Synthesizer LLM")
    print("-" * 60)
    try:
        from backend.app.agents.result_synthesizer_agent.agent import (
            _init_synthesizer_llm,
            _init_evaluator_llm,
        )

        synthesizer_llm = _init_synthesizer_llm()
        print("  [OK] Synthesizer LLM 初始化成功")

        evaluator_llm = _init_evaluator_llm()
        print("  [OK] Evaluator LLM 初始化成功")

        test_results.append(("Result Synthesizer", True, "成功"))
    except Exception as e:
        print(f"  [FAIL] Result Synthesizer LLM 初始化失败: {e}")
        test_results.append(("Result Synthesizer", False, str(e)))

    return test_results


def print_summary(env_results: Dict, llm_results: List[Tuple[str, bool, str]]):
    """打印总结"""
    print("\n" + "=" * 60)
    print("检查总结")
    print("=" * 60)

    # 环境变量总结
    print("\n1. 环境变量配置:")
    total_vars = 0
    missing_vars = 0
    for group_name, vars_dict in env_results.items():
        total_vars += len(vars_dict)
        missing_vars += sum(1 for status in vars_dict.values() if status == "未设置")

    print(f"   总计: {total_vars} 个变量")
    print(f"   已设置: {total_vars - missing_vars} 个")
    print(f"   未设置: {missing_vars} 个")

    if missing_vars > 0:
        print("\n   [WARNING] 以下环境变量未设置:")
        for group_name, vars_dict in env_results.items():
            missing = [var for var, status in vars_dict.items() if status == "未设置"]
            if missing:
                print(f"     {group_name}: {', '.join(missing)}")

    # LLM 连接总结
    print("\n2. LLM 连接测试:")
    success_count = sum(1 for _, success, _ in llm_results if success)
    print(f"   总计: {len(llm_results)} 个 Agent")
    print(f"   成功: {success_count} 个")
    print(f"   失败: {len(llm_results) - success_count} 个")

    if success_count < len(llm_results):
        print("\n   [ERROR] 以下 Agent LLM 初始化失败:")
        for agent_name, success, message in llm_results:
            if not success:
                print(f"     {agent_name}: {message}")

    # 建议
    print("\n3. 修复建议:")
    if missing_vars > 0:
        print("   - 在 .env 文件中添加缺失的环境变量")
        print("   - 或者依赖全局配置 (OPENAI_API_KEY 等)")

    if success_count < len(llm_results):
        print("   - 检查 API KEY 是否正确")
        print("   - 检查 BASE_URL 是否可访问")
        print("   - 检查模型名称是否正确")


def main():
    """主函数"""
    # 1. 检查环境变量
    env_results = check_env_vars()

    # 2. 测试 LLM 连接
    llm_results = test_llm_connections()

    # 3. 打印总结
    print_summary(env_results, llm_results)


if __name__ == "__main__":
    main()
