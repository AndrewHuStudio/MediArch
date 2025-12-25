"""检查 Milvus Collection Schema"""

from pymilvus import Collection, connections

# 连接 Milvus
connections.connect(host='localhost', port='19530')

# 获取集合
coll = Collection('entity_attributes')

# 打印 Schema
print("=" * 60)
print("Milvus Collection Schema")
print("=" * 60)
print(f"Collection Name: {coll.name}")
print(f"Description: {coll.description}")
print(f"")
print("Fields:")

for field in coll.schema.fields:
    print(f"  - {field.name}: {field.dtype}", end='')
    if field.dtype.name == 'FLOAT_VECTOR':
        dim = field.params.get('dim', 'N/A')
        print(f" (dim={dim})", end='')
    print()

print()
print("=" * 60)

# 关闭连接
connections.disconnect("default")
