from pymongo import MongoClient
from bson import ObjectId
import json

client = MongoClient('mongodb://admin:mediarch2024@localhost:27017/')
db = client['mediarch']

# 尝试两种格式的 doc_id
doc_id_str = '6944bd320e5acfd26a8509cc'
doc_id_obj = ObjectId(doc_id_str)

print('[INFO] Checking both ObjectId and string formats...')
print('')

# 先统计两种格式的数量
count_obj = db.mediarch_chunks.count_documents({'doc_id': doc_id_obj, 'content_type': 'image'})
count_str = db.mediarch_chunks.count_documents({'doc_id': doc_id_str, 'content_type': 'image'})

print(f'[OK] Image chunks with ObjectId: {count_obj}')
print(f'[OK] Image chunks with string: {count_str}')
print('')

# 使用有数据的格式
doc_id = doc_id_obj if count_obj > 0 else doc_id_str
total_images = max(count_obj, count_str)

if total_images == 0:
    print('[FAIL] No image chunks found for this doc_id')
    exit(1)

# 查找前 10 个图片 chunk
chunks = list(db.mediarch_chunks.find({'doc_id': doc_id, 'content_type': 'image'}).limit(10))

print(f'[OK] Found {len(chunks)} sample images')
print('')
print('=' * 80)
print('VLM Description Samples')
print('=' * 80)
print('')

for i, chunk in enumerate(chunks, 1):
    vlm_desc = chunk.get('vlm_description', '')
    content = chunk.get('content', '')
    metadata = chunk.get('metadata', {})

    # 尝试多种方式获取页码
    page = metadata.get('page') or metadata.get('page_number') or '?'

    print(f'[{i}] Page: {page}')
    print(f'    Content length: {len(content)} chars')
    print(f'    VLM length: {len(vlm_desc)} chars')
    print(f'    VLM description: "{vlm_desc}"')
    print('')

# 统计 VLM 长度分布
print('=' * 80)
print('VLM Length Statistics (all images)')
print('=' * 80)
print('')

all_chunks = list(db.mediarch_chunks.find({'doc_id': doc_id, 'content_type': 'image'}))
vlm_lengths = [len(c.get('vlm_description', '')) for c in all_chunks]

if vlm_lengths:
    print(f'[OK] Total images: {len(vlm_lengths)}')
    print(f'[OK] Min VLM length: {min(vlm_lengths)} chars')
    print(f'[OK] Max VLM length: {max(vlm_lengths)} chars')
    print(f'[OK] Avg VLM length: {sum(vlm_lengths) / len(vlm_lengths):.1f} chars')
    print(f'[OK] Images with VLM < 50 chars: {sum(1 for l in vlm_lengths if l < 50)}')
    print(f'[OK] Images with VLM < 100 chars: {sum(1 for l in vlm_lengths if l < 100)}')
    print(f'[OK] Images with VLM > 200 chars: {sum(1 for l in vlm_lengths if l > 200)}')
else:
    print('[FAIL] No VLM data found')
