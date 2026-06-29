"""Step1 回归测试：放宽证据正文截断 + 打通 mongodb 正文字段断链。

病灶（见 docs/实验部分/R2智能体间信息传递诊断与优化方案_2026-06-14.md）：
R2 把自己检索到的文档正文剁成 100~180 字碎片，比 VRAG 同一篇 240 字喂得更少。
本组测试锁定"喂进 LLM 的正文长度"这一可验证行为。
"""

from backend.app.agents.mongodb_agent import agent as mongodb_agent
from backend.app.agents import mediarch_graph
from backend.app.agents.result_synthesizer_agent import agent as synthesizer_agent
from backend.app.agents.base_agent import AgentItem
from backend.app.agents.base_agent import AgentRequest

import asyncio
import json


_LONG_BODY = (
    "护士站到最远病房门口的距离不宜超过30m。护理单元应结合护理路径、患者隐私与观察"
    "需求统一组织，护士站宜居中布置以缩短服务半径，同时兼顾对病房与走廊的通视。"
    "病房走廊净宽应满足推床通行与双向避让，洁污流线应分设并避免交叉。" * 4
)


def test_mongodb_citation_snippet_preserves_more_than_legacy_150():
    """源头 citation snippet 不再先被砍到 150 字。"""
    chunks = [
        {
            "chunk_id": "code-1",
            "doc_title": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "chunk_text": _LONG_BODY,
            "retrieval_lane": "standards_first",
        }
    ]

    result = asyncio.run(mongodb_agent.node_format_results({"retrieval_results": chunks}))
    citation = result["items"][0].citations[0]

    assert len(citation["snippet"]) > 150
    assert len(citation["snippet"]) == min(
        len(_LONG_BODY), mongodb_agent.CITATION_SNIPPET_CHARS
    )


def test_prompt_citation_keeps_general_body_up_to_240():
    """普通文档正文进 prompt 时保留到 240 字（与 VRAG 对齐），不再砍到 180。"""
    citation = {
        "source": "医院建筑设计指南.pdf",
        "location": "5.5",
        "snippet": "b" * 500,
    }

    compact = synthesizer_agent._compact_citation_for_prompt(citation)

    assert len(compact["snippet"]) == synthesizer_agent.PROMPT_SNIPPET_CHARS + 1  # +1 是省略号
    assert synthesizer_agent.PROMPT_SNIPPET_CHARS >= 240


def test_prompt_citation_keeps_code_spec_body_wider_than_general():
    """code_spec 规范条文正文给更高配额，避免规范要点被截断丢失。"""
    citation = {
        "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
        "location": "5.5.6",
        "snippet": "c" * 800,
        "evidence_tier": "code_spec",
    }

    compact = synthesizer_agent._compact_citation_for_prompt(citation)

    assert len(compact["snippet"]) == synthesizer_agent.PROMPT_SNIPPET_CHARS_CODE_SPEC + 1
    assert (
        synthesizer_agent.PROMPT_SNIPPET_CHARS_CODE_SPEC
        > synthesizer_agent.PROMPT_SNIPPET_CHARS
    )


def test_prompt_citation_recovers_full_body_from_highlight_text():
    """citation 的 snippet 偏短时，回落到更完整的 highlight_text，避免字段断链。"""
    citation = {
        "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
        "snippet": "护士站到最远病房门口的距离不宜超过30m。",  # 短碎片
        "highlight_text": _LONG_BODY,  # 完整正文
        "evidence_tier": "code_spec",
    }

    compact = synthesizer_agent._compact_citation_for_prompt(citation)

    assert len(compact["snippet"]) > 200


def test_citations_catalog_uses_code_spec_budget_and_highlight_fallback():
    """citations_catalog 是主证据通道，code_spec 正文也应保留到更高预算。"""
    citations = [
        {
            "source": "GB 51039-2014 综合医院建筑设计规范.pdf",
            "location": "5.5.6",
            "snippet": "短碎片。",
            "highlight_text": _LONG_BODY,
            "evidence_tier": "code_spec",
        }
    ]

    catalog = synthesizer_agent._format_citations_catalog(citations)

    # 回落到 highlight_text 后正文长度应远超旧的 180/220 上限
    assert len(catalog) > 250
    assert "30m" in catalog


def test_document_view_prompt_uses_chunk_text_when_item_snippet_is_short():
    """documents_view 是宽口径正文通道，不能只吃 AgentItem.snippet 的短摘要。"""
    long_chunk = (
        "门诊单元设计需要同时组织咨询、检查、候诊和后台支持功能。"
        "单一功能组织模式强调各诊室前台办公相对独立，多功能组织模式强调跨学科"
        "咨询与共享办公设施。两种方式都应保证患者健康记录可被数字化访问，"
        "并结合接待、等候、检查、诊断和后台设施形成连续流程。"
    ) * 3
    item = AgentItem(
        entity_id="doc-1",
        name="医院建筑设计指南.pdf",
        source="mongodb_agent",
        snippet="短摘要。",
        attrs={
            "source_document": "医院建筑设计指南.pdf",
            "chunk_text": long_chunk,
            "location": "5.2.1",
        },
        citations=[
            {
                "source": "医院建筑设计指南.pdf",
                "location": "5.2.1",
                "snippet": "短摘要。",
                "highlight_text": long_chunk[:400],
                "content_type": "text",
                "chunk_id": "doc-1",
            }
        ],
    )

    views = synthesizer_agent._build_document_views([item], query="请总结门诊单元如何设计")
    prompt_doc = synthesizer_agent._compact_document_view_for_prompt(views[0])
    snippet = prompt_doc["highlights"][0]["snippet"]

    assert len(snippet) > 240
    assert "后台支持功能" in snippet
    assert "数字化访问" in snippet


def test_synthesizer_prompt_keeps_more_than_four_document_views(monkeypatch):
    """Synthesizer 的 max_prompt_documents 不能被 top_documents[:4] 硬截断。"""
    captured = {}

    class _FakeResponse:
        content = "门诊单元应综合组织候诊、咨询、检查和后台支持功能。[1]"

    async def _fake_llm(*, messages, **_kwargs):
        captured["user_prompt"] = messages[1].content
        return _FakeResponse()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", _fake_llm)

    items = []
    for index in range(6):
        doc = f"医院建筑设计指南{index}.pdf"
        body = f"第{index}份资料说明门诊单元设计需要覆盖不同功能区域和流程节点。" * 20
        items.append(
            AgentItem(
                entity_id=f"chunk-{index}",
                name=doc,
                source="mongodb_agent",
                snippet="短摘要。",
                attrs={
                    "source_document": doc,
                    "chunk_text": body,
                    "location": f"5.2.{index}",
                },
                citations=[
                    {
                        "source": doc,
                        "location": f"5.2.{index}",
                        "snippet": body[:300],
                        "highlight_text": body[:400],
                        "content_type": "text",
                        "chunk_id": f"chunk-{index}",
                    }
                ],
            )
        )

    result = asyncio.run(
        synthesizer_agent.node_synthesize(
            {
                "query": "请总结门诊单元如何设计",
                "aggregated_items": items,
                "worker_responses": [],
                "request": AgentRequest(query="请总结门诊单元如何设计", metadata={"retrieval_mode": "R2"}),
            }
        )
    )

    assert result["synthesizer_diagnostics"]["documents_total"] == 6
    prompt_json = captured["user_prompt"].split("请基于以下检索结果生成回答：", 1)[1]
    prompt_context = json.loads(prompt_json)
    assert len(prompt_context["documents_view"]) == 6


def test_synthesizer_keeps_image_refs_for_explicit_visual_queries(monkeypatch):
    """只有明确要图的查询，图片 item 才应进入最终 image_references。"""
    async def _fake_llm(*, messages, **_kwargs):
        class _FakeResponse:
            content = "门诊单元应结合候诊、诊室和后台支持组织。"

        return _FakeResponse()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", _fake_llm)
    image_url = "书籍报告/医疗功能房间详图集3/full/images/page_10.png"
    items = [
        AgentItem(
            entity_id="text-1",
            name="医院建筑设计指南.pdf",
            source="mongodb_agent",
            snippet="门诊单元设计正文。",
            attrs={
                "source_document": "医院建筑设计指南.pdf",
                "content_type": "text",
                "chunk_text": "门诊单元应组织候诊、诊室、检查和后台支持。",
            },
            citations=[
                {
                    "source": "医院建筑设计指南.pdf",
                    "location": "5页",
                    "snippet": "门诊单元应组织候诊、诊室、检查和后台支持。",
                    "content_type": "text",
                    "chunk_id": "text-1",
                }
            ],
        ),
        AgentItem(
            entity_id="image-1",
            name="医疗功能房间详图集3.pdf",
            source="mongodb_agent",
            snippet="[图片] 门诊单元平面图",
            attrs={
                "source_document": "医疗功能房间详图集3.pdf",
                "content_type": "image",
                "image_url": image_url,
            },
            citations=[
                {
                    "source": "医疗功能房间详图集3.pdf",
                    "location": "10页",
                    "snippet": "门诊单元平面图",
                    "content_type": "image",
                    "image_url": image_url,
                    "page_number": 10,
                    "chunk_id": "image-1",
                }
            ],
        ),
    ]

    result = asyncio.run(
        synthesizer_agent.node_synthesize(
                {
                    "query": "门诊单元平面图怎么设计？",
                    "aggregated_items": items,
                    "worker_responses": [],
                    "request": AgentRequest(query="门诊单元平面图怎么设计？", metadata={"retrieval_mode": "R2"}),
                }
            )
        )

    assert result["image_references"]
    assert result["image_references"][0]["image_url"] == image_url


def test_synthesizer_recovers_items_from_worker_responses_when_parent_items_are_empty(monkeypatch):
    async def _fake_llm(*, messages, **_kwargs):
        class _FakeResponse:
            content = "门诊应结合候诊、诊室、公共空间和住院部衔接关系组织。"

        return _FakeResponse()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", _fake_llm)
    item = AgentItem(
        entity_id="mongo-1",
        name="医院建筑设计指南.pdf",
        source="mongodb_agent",
        snippet="门诊部设计需要处理候诊、诊室、公共空间和住院部关系。",
        attrs={
            "source_document": "医院建筑设计指南.pdf",
            "content_type": "text",
            "chunk_text": "门诊部设计需要处理候诊、诊室、公共空间和住院部关系。",
        },
        citations=[
            {
                "source": "医院建筑设计指南.pdf",
                "location": "门诊部设计",
                "snippet": "门诊部设计需要处理候诊、诊室、公共空间和住院部关系。",
                "content_type": "text",
                "chunk_id": "mongo-1",
            }
        ],
    )

    result = asyncio.run(
        synthesizer_agent.node_synthesize(
            {
                "query": "综合医院中，门诊如何设计？与住院部的关系是什么？",
                "aggregated_items": [],
                "worker_responses": [
                    {
                        "agent_name": "mongodb_agent",
                        "items": [item],
                        "item_count": 1,
                    }
                ],
                "request": AgentRequest(
                    query="综合医院中，门诊如何设计？与住院部的关系是什么？",
                    metadata={"retrieval_mode": "R2"},
                ),
            }
        )
    )

    assert result["final_answer"] != ""
    assert "未找到可用资料" not in result["final_answer"]
    assert result["synthesizer_diagnostics"]["documents_total"] >= 1


def test_synthesizer_returns_image_refs_for_plain_design_query_by_default(monkeypatch):
    # 2026-06-18 图文并茂默认开:普通设计题也产出 image_references,无关图片由 chat 层相关性过滤兜底。
    async def _fake_llm(*, messages, **_kwargs):
        class _FakeResponse:
            content = "门诊单元应结合候诊、诊室和后台支持组织。"

        return _FakeResponse()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", _fake_llm)
    image_url = "书籍报告/医疗功能房间详图集3/full/images/page_10.png"
    items = [
        AgentItem(
            entity_id="image-1",
            name="医疗功能房间详图集3.pdf",
            source="mongodb_agent",
            snippet="[图片] 门诊单元平面图",
            attrs={"source_document": "医疗功能房间详图集3.pdf", "content_type": "image", "image_url": image_url},
            citations=[
                {
                    "source": "医疗功能房间详图集3.pdf",
                    "snippet": "门诊单元平面图",
                    "content_type": "image",
                    "image_url": image_url,
                    "chunk_id": "image-1",
                }
            ],
        )
    ]

    result = asyncio.run(
        synthesizer_agent.node_synthesize(
            {
                "query": "门诊单元怎么设计？",
                "aggregated_items": items,
                "worker_responses": [],
                "request": AgentRequest(query="门诊单元怎么设计？", metadata={"retrieval_mode": "R2"}),
            }
        )
    )

    assert result["image_references"]
    assert result["image_references"][0]["image_url"] == image_url


def test_synthesizer_omits_image_refs_when_user_declines_images(monkeypatch):
    # 显式否定图片时仍然不产出 image_references。
    async def _fake_llm(*, messages, **_kwargs):
        class _FakeResponse:
            content = "门诊单元应结合候诊、诊室和后台支持组织。"

        return _FakeResponse()

    monkeypatch.setattr(synthesizer_agent, "_call_llm_with_retry", _fake_llm)
    image_url = "书籍报告/医疗功能房间详图集3/full/images/page_10.png"
    items = [
        AgentItem(
            entity_id="image-1",
            name="医疗功能房间详图集3.pdf",
            source="mongodb_agent",
            snippet="[图片] 门诊单元平面图",
            attrs={"source_document": "医疗功能房间详图集3.pdf", "content_type": "image", "image_url": image_url},
            citations=[
                {
                    "source": "医疗功能房间详图集3.pdf",
                    "snippet": "门诊单元平面图",
                    "content_type": "image",
                    "image_url": image_url,
                    "chunk_id": "image-1",
                }
            ],
        )
    ]

    result = asyncio.run(
        synthesizer_agent.node_synthesize(
            {
                "query": "门诊单元怎么设计？不需要图片",
                "aggregated_items": items,
                "worker_responses": [],
                "request": AgentRequest(query="门诊单元怎么设计？不需要图片", metadata={"retrieval_mode": "R2"}),
            }
        )
    )

    assert result["image_references"] == []


def test_mediarch_parent_state_declares_synthesizer_image_references():
    """父图必须声明 image_references，否则子图输出会被 LangGraph 状态过滤掉。"""
    state_fields = mediarch_graph.MediArchGraphState.__annotations__

    assert "image_references" in state_fields
