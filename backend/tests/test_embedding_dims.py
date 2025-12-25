"""测试 Embedding 向量维度

检查不同配置下生成的向量维度
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 当前配置
api_key = os.getenv("KG_OPENAI_API_KEY")
base_url = os.getenv("KG_OPENAI_BASE_URL")
model = os.getenv("KG_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

print("=" * 60)
print("Embedding 配置测试")
print("=" * 60)
print(f"API Key: {api_key[:10]}... (masked)")
print(f"Base URL: {base_url}")
print(f"Model: {model}")
print()

# 测试1: 使用当前配置 + dimensions=1536
print("测试1: dimensions=1536 (当前配置)")
print("-" * 60)
try:
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(
        model=model,
        input="测试文本",
        dimensions=1536
    )
    vec_len = len(response.data[0].embedding)
    print(f"[OK] 向量维度: {vec_len}")
    if vec_len == 1536:
        print("[SUCCESS] 维度正确！")
    else:
        print(f"[FAIL] 维度不匹配！期望 1536，实际 {vec_len}")
except Exception as e:
    print(f"[ERROR] 请求失败: {e}")

print()

# 测试2: 不指定 dimensions
print("测试2: 不指定 dimensions (默认)")
print("-" * 60)
try:
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(
        model=model,
        input="测试文本"
    )
    vec_len = len(response.data[0].embedding)
    print(f"[OK] 向量维度: {vec_len}")
    if vec_len == 1536:
        print("[SUCCESS] 默认维度正确！")
    else:
        print(f"[WARNING] 默认维度不是 1536，是 {vec_len}")
except Exception as e:
    print(f"[ERROR] 请求失败: {e}")

print()

# 测试3: 如果有标准 OpenAI Key，测试官方 API
openai_key = os.getenv("OPENAI_API_KEY")
if openai_key and openai_key != api_key:
    print("测试3: 使用标准 OpenAI API")
    print("-" * 60)
    try:
        client = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input="测试文本",
            dimensions=1536
        )
        vec_len = len(response.data[0].embedding)
        print(f"[OK] 向量维度: {vec_len}")
        if vec_len == 1536:
            print("[SUCCESS] 官方 API 维度正确！")
        else:
            print(f"[FAIL] 官方 API 维度也不对: {vec_len}")
    except Exception as e:
        print(f"[ERROR] 官方 API 请求失败: {e}")
else:
    print("测试3: 跳过（未配置标准 OPENAI_API_KEY）")

print()
print("=" * 60)
print("建议")
print("=" * 60)
print("如果测试1失败（维度不是1536）:")
print("  选项A: 使用标准 OpenAI API (需要 OpenAI API Key)")
print("  选项B: 联系 API Gateway 提供商确认 embedding 支持")
print("  选项C: 重新导入 Milvus 数据（使用当前API实际返回的维度）")
