#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据统计脚本：统计文档/图片页数，并输出 Mongo/Milvus/Neo4j 数据量
"""
import os
import json
from pathlib import Path
from collections import defaultdict
from pymongo import MongoClient
from pymilvus import connections, Collection, exceptions as milvus_exceptions
from neo4j import GraphDatabase

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS_DIR = ROOT / 'databases' / 'documents'
DOCUMENTS_OCR_DIR = ROOT / 'databases' / 'documents_ocr'
OCR_PROGRESS_PATH = ROOT / 'databases' / 'ingestion' / 'ocr_progress.json'

MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://admin:mediarch2024@localhost:27017/')
MONGO_DB = os.getenv('MONGODB_DATABASE', 'mediarch')
MILVUS_HOST = os.getenv('MILVUS_HOST', 'localhost')
MILVUS_PORT = os.getenv('MILVUS_PORT', '19530')
MILVUS_CHUNK_COLLECTION = os.getenv('MILVUS_CHUNK_COLLECTION', 'mediarch_chunks')
MILVUS_ENTITY_COLLECTION = os.getenv('MILVUS_ENTITY_COLLECTION', 'entity_attributes')
NEO4J_URI = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'mediarch2024')


def load_ocr_progress():
    if OCR_PROGRESS_PATH.exists():
        try:
            return json.loads(OCR_PROGRESS_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def probe_pdf_pages(pdf_path: Path) -> int | None:
    """探测 PDF 页数"""
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        pass
    try:
        import fitz
        return len(fitz.open(pdf_path))
    except Exception:
        return None


def gather_pdf_info():
    data = []
    if not DOCUMENTS_DIR.exists():
        return data
    for category_dir in sorted(DOCUMENTS_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        category = category_dir.name
        for pdf_path in sorted(category_dir.glob('**/*.pdf')):
            relative = pdf_path.relative_to(DOCUMENTS_DIR)
            pages = probe_pdf_pages(pdf_path)
            data.append({
                'category': category,
                'path': str(relative),
                'pages': pages,
                'images': None,
                'ocr_done': False,
            })
    return data


def map_ocr_assets(pdf_info):
    progress = load_ocr_progress()
    records = progress.get('records') or {}
    info_map = {Path(info['path']).name: info for info in pdf_info}
    for key, record in records.items():
        doc_name = Path(record.get('pdf_path', Path(key))).name
        info = info_map.get(doc_name)
        if info is None:
            continue
        info['pages'] = record.get('total_pages', info.get('pages'))
        ocr_doc_dir = DOCUMENTS_OCR_DIR / info['category'] / doc_name.replace('.pdf', '')
        images_dir = ocr_doc_dir / 'images'
        if images_dir.exists():
            info['images'] = sum(1 for f in images_dir.iterdir() if f.is_file())
        else:
            info['images'] = 0
        info['ocr_done'] = True

    # fallback: 根据 documents_ocr 目录判断是否 OCR 完成
    for info in pdf_info:
        if info.get('ocr_done'):
            continue
        ocr_dir = DOCUMENTS_OCR_DIR / info['category'] / Path(info['path']).stem
        if ocr_dir.exists():
            images_dir = ocr_dir / 'images'
            if images_dir.exists():
                info['images'] = sum(1 for f in images_dir.iterdir() if f.is_file())
            info['ocr_done'] = True


def summarize_documents(pdf_info):
    summary = defaultdict(lambda: {'pdf_count': 0, 'total_pages': 0, 'total_images': 0})
    overview = {'total_pdf': 0, 'total_pages': 0, 'total_images': 0}
    for info in pdf_info:
        entry = summary[info['category']]
        entry['pdf_count'] += 1
        entry['total_pages'] += info.get('pages') or 0
        entry['total_images'] += info.get('images') or 0
        overview['total_pdf'] += 1
        overview['total_pages'] += info.get('pages') or 0
        overview['total_images'] += info.get('images') or 0
    return summary, overview


def mongo_stats():
    stats = {}
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        stats['database'] = MONGO_DB
        stats['collections'] = {
            name: db[name].count_documents({})
            for name in db.list_collection_names()
        }
    except Exception as exc:
        stats['error'] = str(exc)
    return stats


def milvus_stats():
    stats = []
    collections = []
    if MILVUS_CHUNK_COLLECTION:
        collections.append(MILVUS_CHUNK_COLLECTION)
    if MILVUS_ENTITY_COLLECTION and MILVUS_ENTITY_COLLECTION != MILVUS_CHUNK_COLLECTION:
        collections.append(MILVUS_ENTITY_COLLECTION)
    try:
        connections.connect('default', host=MILVUS_HOST, port=MILVUS_PORT)
        for name in collections or ['mediarch_chunks']:
            try:
                coll = Collection(name)
                stats.append({
                    'collection': name,
                    'entities': coll.num_entities,
                    'error': None,
                })
            except milvus_exceptions.MilvusException as exc:
                stats.append({
                    'collection': name,
                    'entities': None,
                    'error': str(exc),
                })
    except milvus_exceptions.MilvusException as exc:
        stats = [{'collection': ','.join(collections) or 'mediarch_chunks', 'entities': None, 'error': str(exc)}]
    finally:
        try:
            connections.disconnect('default')
        except Exception:
            pass
    return stats


def neo4j_stats():
    stats = {}
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            stats['nodes'] = session.run('MATCH (n) RETURN count(n) AS c').single().get('c')
            stats['relationships'] = session.run('MATCH ()-[r]->() RETURN count(r) AS c').single().get('c')
        driver.close()
    except Exception as exc:
        stats['error'] = str(exc)
    return stats


def print_report():
    console = Console()
    pdf_info = gather_pdf_info()
    map_ocr_assets(pdf_info)
    summary, overview = summarize_documents(pdf_info)

    console.print("\n[bold underline]文档目录统计 (backend/databases/documents)[/]\n")
    if not summary:
        console.print("[yellow]未找到任何 PDF。[/]")
    for category, stats in summary.items():
        avg_pages = stats['total_pages'] / stats['pdf_count'] if stats['pdf_count'] else 0
        heading = (
            f"[bold]{category}[/bold] | "
            f"PDF: {stats['pdf_count']} | "
            f"总页数: {stats['total_pages']} (平均 {avg_pages:.1f}) | "
            f"图片总数: {stats['total_images']}"
        )
        console.print(heading)

        table = Table(box=box.SQUARE, show_lines=False)
        table.add_column("PDF", style="cyan")
        table.add_column("页数", justify="center")
        table.add_column("图片数", justify="center")
        table.add_column("状态", justify="center", style="magenta")
        for info in [item for item in pdf_info if item['category'] == category]:
            pages = info.get('pages') if info.get('pages') is not None else '未知'
            images = info.get('images')
            image_display = images if images is not None else '未知'
            ocr_flag = "完成OCR" if info.get('ocr_done') else "待OCR"
            table.add_row(info["path"], str(pages), str(image_display), ocr_flag)
        console.print(table)
        console.print()  # extra spacing between categories

    if overview['total_pdf']:
        console.print(Panel.fit(
            f"总计：{overview['total_pdf']} 本，{overview['total_pages']} 页，{overview['total_images']} 张图片",
            style="bold green",
        ))

    console.print("\n[bold underline]数据库统计[/]\n")

    mongo = mongo_stats()
    if 'error' in mongo:
        console.print(f"[red]MongoDB 统计失败: {mongo['error']}[/]")
    else:
        table = Table(title=f"MongoDB ({mongo['database']})", box=box.SQUARE)
        table.add_column("集合", style="magenta")
        table.add_column("文档数", justify="right")
        if mongo['collections']:
            for coll, count in sorted(mongo['collections'].items()):
                table.add_row(coll, str(count))
        else:
            table.add_row("[grey50]暂无集合[/]", "-")
        console.print(table)

    milvus_entries = milvus_stats()
    if not milvus_entries:
        console.print("[yellow]Milvus 未配置或无可统计的 collection[/]")
    else:
        table = Table(title="Milvus Collections", box=box.SQUARE)
        table.add_column("Collection", style="cyan")
        table.add_column("Vectors", justify="right")
        table.add_column("状态", justify="center")
        for item in milvus_entries:
            status = "OK" if not item.get('error') else "[red]错误[/red]"
            count = str(item.get('entities')) if item.get('entities') is not None else "-"
            if item.get('error'):
                status = f"[red]{item['error']}[/red]"
            table.add_row(item['collection'], count, status)
        console.print(table)

    neo = neo4j_stats()
    if 'error' in neo:
        console.print(f"[red]Neo4j 统计失败: {neo['error']}[/]")
    else:
        console.print(f"[green]Neo4j: {neo['nodes']} nodes / {neo['relationships']} relationships[/]")


if __name__ == '__main__':
    print_report()
