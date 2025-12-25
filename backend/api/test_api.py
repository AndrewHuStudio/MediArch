# backend/api/test_api.py
"""
FastAPI 快速测试脚本

用于验证API各个端点是否正常工作
"""

import asyncio
import json
import requests
import time
from typing import Dict, Any

# API基础URL
BASE_URL = "http://localhost:8000"

# 测试配置
TEST_CONFIG = {
    "verbose": True,  # 是否显示详细信息
    "run_all": True,  # 是否运行所有测试
}


def print_test_header(test_name: str):
    """打印测试标题"""
    print("\n" + "=" * 60)
    print(f"[测试] {test_name}")
    print("=" * 60)


def print_result(success: bool, message: str):
    """打印测试结果"""
    status = "[OK]" if success else "[FAIL]"
    print(f"{status} {message}")


def test_root_endpoint():
    """测试根路径"""
    print_test_header("根路径健康检查")

    try:
        response = requests.get(f"{BASE_URL}/")
        success = response.status_code == 200

        if TEST_CONFIG["verbose"] and success:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))

        print_result(success, f"根路径响应 | 状态码: {response.status_code}")
        return success

    except Exception as e:
        print_result(False, f"根路径请求失败: {e}")
        return False


def test_ping_endpoint():
    """测试ping端点"""
    print_test_header("Ping 端点")

    try:
        response = requests.get(f"{BASE_URL}/ping")
        success = response.status_code == 200 and response.json().get("message") == "pong"

        print_result(success, f"Ping 响应 | 状态码: {response.status_code}")
        return success

    except Exception as e:
        print_result(False, f"Ping 请求失败: {e}")
        return False


def test_health_endpoint():
    """测试快速健康检查"""
    print_test_header("快速健康检查")

    try:
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v1/health")
        elapsed = time.time() - start_time

        success = response.status_code == 200 and response.json().get("status") == "ok"

        if TEST_CONFIG["verbose"] and success:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))

        print_result(success, f"健康检查响应 | 状态码: {response.status_code} | 用时: {elapsed:.3f}s")
        return success

    except Exception as e:
        print_result(False, f"健康检查请求失败: {e}")
        return False


def test_detailed_health_endpoint():
    """测试详细健康状态"""
    print_test_header("详细健康状态检查")

    try:
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v1/health/detailed")
        elapsed = time.time() - start_time

        success = response.status_code == 200

        if TEST_CONFIG["verbose"] and success:
            data = response.json()
            print(f"\n整体状态: {data['overall_status']}")
            print(f"Agent数量: {len(data['agents'])}")
            print(f"数据库数量: {len(data['databases'])}")

            print("\nAgent状态:")
            for agent in data["agents"]:
                print(f"  - {agent['name']}: {agent['status']} ({agent['compilation_status']})")

            print("\n数据库状态:")
            for db in data["databases"]:
                print(f"  - {db['name']}: {db['status']}")

        print_result(success, f"详细健康检查 | 状态码: {response.status_code} | 用时: {elapsed:.3f}s")
        return success

    except Exception as e:
        print_result(False, f"详细健康检查失败: {e}")
        return False


def test_kb_categories_endpoint():
    """测试知识库分类列表"""
    print_test_header("知识库分类列表")

    try:
        response = requests.get(f"{BASE_URL}/api/v1/kb/categories")
        success = response.status_code == 200

        if TEST_CONFIG["verbose"] and success:
            data = response.json()
            print(f"\n分类总数: {data['total']}")
            for cat in data["categories"]:
                print(f"  - {cat['name']} ({cat['id']}): {cat['item_count']} 条目")

        print_result(success, f"知识库分类 | 状态码: {response.status_code}")
        return success

    except Exception as e:
        print_result(False, f"知识库分类请求失败: {e}")
        return False


def test_kb_items_endpoint():
    """测试知识库条目列表"""
    print_test_header("知识库条目列表")

    try:
        response = requests.get(
            f"{BASE_URL}/api/v1/kb/categories/regulations/items",
            params={"page": 1, "page_size": 10}
        )
        success = response.status_code == 200

        if TEST_CONFIG["verbose"] and success:
            data = response.json()
            print(f"\n分类: {data['category']}")
            print(f"总数: {data['total']}")
            print(f"当前页: {data['page']}/{data['total_pages']}")
            print(f"条目数: {len(data['items'])}")

        print_result(success, f"知识库条目 | 状态码: {response.status_code}")
        return success

    except Exception as e:
        print_result(False, f"知识库条目请求失败: {e}")
        return False


def test_chat_endpoint():
    """测试对话接口（非流式）"""
    print_test_header("对话接口（非流式）")

    try:
        start_time = time.time()
        response = requests.post(
            f"{BASE_URL}/api/v1/chat",
            json={
                "message": "测试查询：医院门诊空间的设计要点",
                "stream": False,
                "include_citations": True,
                "include_diagnostics": False,
                "top_k": 5,
            },
            timeout=60  # 60秒超时
        )
        elapsed = time.time() - start_time

        success = response.status_code == 200

        if TEST_CONFIG["verbose"] and success:
            data = response.json()
            print(f"\n会话ID: {data['session_id']}")
            print(f"回复长度: {len(data['message'])} 字符")
            print(f"引用数量: {len(data.get('citations', []))}")
            print(f"使用的Agent: {data.get('agents_used', [])}")
            print(f"处理时间: {data.get('took_ms', 0)}ms")

            print(f"\n回复内容（前200字符）:")
            print(data["message"][:200] + "...")

        print_result(success, f"对话响应 | 状态码: {response.status_code} | 用时: {elapsed:.3f}s")
        return success

    except Exception as e:
        print_result(False, f"对话请求失败: {e}")
        return False


def test_sessions_endpoint():
    """测试会话列表"""
    print_test_header("会话列表")

    try:
        response = requests.get(f"{BASE_URL}/api/v1/chat/sessions")
        success = response.status_code == 200

        if TEST_CONFIG["verbose"] and success:
            data = response.json()
            print(f"\n会话总数: {data['total']}")
            for session in data["sessions"][:3]:  # 只显示前3个
                print(f"  - {session['session_id']}: {session['message_count']} 条消息")
                print(f"    标题: {session.get('title', 'N/A')[:50]}...")

        print_result(success, f"会话列表 | 状态码: {response.status_code}")
        return success

    except Exception as e:
        print_result(False, f"会话列表请求失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("[MediArch FastAPI] 接口测试套件")
    print("=" * 60)
    print(f"API地址: {BASE_URL}")

    # 检查服务器是否启动
    print("\n[检查] 正在检测API服务器...")
    try:
        requests.get(f"{BASE_URL}/", timeout=5)
        print("[OK] API服务器运行中")
    except Exception as e:
        print(f"[FAIL] API服务器未启动: {e}")
        print("\n请先启动API服务器:")
        print("  python backend/api/main.py")
        print("或:")
        print("  uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000")
        return

    # 运行测试
    results = {}

    if TEST_CONFIG["run_all"]:
        results["root"] = test_root_endpoint()
        results["ping"] = test_ping_endpoint()
        results["health"] = test_health_endpoint()
        results["detailed_health"] = test_detailed_health_endpoint()
        results["kb_categories"] = test_kb_categories_endpoint()
        results["kb_items"] = test_kb_items_endpoint()
        results["chat"] = test_chat_endpoint()
        results["sessions"] = test_sessions_endpoint()

        # 统计结果
        total = len(results)
        passed = sum(results.values())
        failed = total - passed

        print("\n" + "=" * 60)
        print("[测试总结]")
        print("=" * 60)
        print(f"总测试数: {total}")
        print(f"通过: {passed}")
        print(f"失败: {failed}")
        print(f"成功率: {passed/total*100:.1f}%")

        if failed > 0:
            print("\n[失败的测试]:")
            for test_name, success in results.items():
                if not success:
                    print(f"  - {test_name}")

        print("\n" + "=" * 60)

    else:
        # 运行单个测试（可根据需要调整）
        test_chat_endpoint()


if __name__ == "__main__":
    main()
