import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import jitiFactory from "../node_modules/.pnpm/jiti@2.6.1/node_modules/jiti/lib/jiti.mjs"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const jiti = jitiFactory(import.meta.url)
const mod = jiti(join(__dirname, "..", "lib", "chat", "markdown-display.ts"))

test("citation markup preserves markdown list structure", () => {
  const input = "1. 一级\n   - 二级说明[1][2]\n2. 第二项"
  const output = mod.applyCitationMarkup(input, 3)
  assert.match(output, /^1\. 一级\n   - 二级说明/)
  assert.doesNotMatch(output, /二级说明 \[1\]\[2\]/)
})

test("table normalizer restores collapsed markdown table rows", () => {
  const input = "重点如下： | 核查维度 | 核查要点 | 参考依据 |\n| --- | --- | --- |\n| 地下层合规 | 车位数量 | 图号01 |"
  const output = mod.normalizeMarkdownTables(input)
  assert.match(output, /重点如下：\n\n\| 核查维度 \| 核查要点 \| 参考依据 \|/)
  assert.match(output, /\| 地下层合规 \| 车位数量 \| 图号01 \|/)
})

test("table normalizer expands fully collapsed table rows after a numbered item", () => {
  const input = "1. 地下空间分层管控 | 层数 | 管控重点 | 图号 | 依据 | | --- | --- | --- | --- | | 地下一层 | 商业、停车及设备用房布局 | 04 | 建筑图 | | 地下二至三层 | 停车场、人防设施及管线综合 | 05 |"
  const output = mod.normalizeMarkdownTables(input)

  assert.match(
    output,
    /^1\. 地下空间分层管控\n\n\| 层数 \| 管控重点 \| 图号 \| 依据 \|\n\| --- \| --- \| --- \| --- \|/
  )
  assert.match(output, /\n\| 地下一层 \| 商业、停车及设备用房布局 \| 04 \| 建筑图 \|/)
  assert.match(output, /\n\| 地下二至三层 \| 停车场、人防设施及管线综合 \| 05 \|/)
})

test("component uses normalized markdown pipeline", () => {
  const source = readFileSync(join(__dirname, "..", "components", "chat", "message-with-sources.tsx"), "utf8")
  assert.match(source, /buildMarkdownDisplayContent\(content, maxCitation\)/)
  assert.match(source, /figure:\s*\(\{/)
  assert.match(source, /data-chat-image-index/)
})

test("image placeholder transform keeps later sections outside 检索综述", () => {
  const input = [
    "### 检索综述",
    "",
    "1. 检索重点一",
    "2. 检索重点二",
    "",
    "（图1：总体平面）",
    "[image:0]",
    "",
    "### 注",
    "- 这一段必须仍然属于“注”板块",
    "",
    "### 优化建议",
    "1. 保持列表续号稳定",
  ].join("\n")

  const output = mod.replaceImagePlaceholders(input)

  assert.doesNotMatch(output, /\[image:0\]/)
  assert.match(output, /<figure[^>]+data-chat-image-index="0"/)
  assert.match(output, /### 检索综述[\s\S]*<figure[^>]+><\/figure>\n\n### 注/)
  assert.match(output, /### 注\n- 这一段必须仍然属于“注”板块/)
  assert.match(output, /### 优化建议\n1\. 保持列表续号稳定/)
})

test("full markdown display pipeline preserves ordered lists around image placeholders", () => {
  const input = [
    "1. 一级结论",
    "2. 二级结论",
    "",
    "（图1：局部详图）",
    "[image:0]",
    "",
    "3. 继续补充",
  ].join("\n")

  const output = mod.buildMarkdownDisplayContent(input, 0)

  assert.match(output, /^1\. 一级结论\n2\. 二级结论/)
  assert.match(output, /<figure[^>]+data-chat-image-index="0"[^>]*><\/figure>/)
  assert.match(output, /<\/figure>\n\n3\. 继续补充$/)
})

test("full markdown display pipeline inserts a blank line so a table after an ordered item remains parseable", () => {
  const input = [
    "- 要求建筑退线形成连续步行网络，结合绿化打造立体慢行系统（1-1）",
    "",
    "<figure data-chat-image-index=\"0\"></figure>",
    "",
    "1. 地下空间（三层分级管控）",
    "| 层数 | 管控重点 | 依据 |",
    "| --- | --- | --- |",
    "| 地下一层 | 商业/交通 | 图1 |",
    "| 地下二、三层 | 集约停车/设备空间 | 图2 |",
  ].join("\n")

  const normalized = mod.buildMarkdownDisplayContent(input, 0)
  const html = renderToStaticMarkup(
    React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeRaw] }, normalized)
  )

  assert.match(normalized, /1\. 地下空间（三层分级管控）\n\n   \| 层数 \| 管控重点 \| 依据 \|/)
  assert.match(html, /<table>/)
  assert.match(html, /<li>\s*<p>地下空间（三层分级管控）<\/p>[\s\S]*<table>/)
})

test("full markdown display pipeline preserves ordered-list hierarchy across image placeholders and nested bullets", () => {
  const input = [
    "一、空间分层管控（三维控制）",
    "",
    "1. 地上空间（0-8.4米层）",
    "",
    "- 重点控制首层界面连续性，强化商业/公共功能渗透（见空间控制图（图1））",
    "",
    "[image:0]",
    "",
    "- 要求建筑退线形成连续步行网络，结合绿化打造立体慢行系统（1-1）",
    "",
    "1. 地下空间（三层分级管控）",
    "| 层数 | 管控重点 | 依据 |",
    "| --- | --- | --- |",
    "| 地下一层 | 商业/交通 | 图1 |",
  ].join("\n")

  const normalized = mod.buildMarkdownDisplayContent(input, 0)
  const html = renderToStaticMarkup(
    React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeRaw] }, normalized)
  )

  assert.match(normalized, /1\. 地上空间（0-8.4米层）\n\n   - 重点控制首层界面连续性/)
  assert.match(normalized, /\n   <figure data-chat-image-index="0"><\/figure>\n/)
  assert.match(normalized, /\n   - 要求建筑退线形成连续步行网络/)
  assert.match(normalized, /\n1\. 地下空间（三层分级管控）\n\n   \| 层数 \| 管控重点 \| 依据 \|/)
  assert.match(html, /<ol>\s*<li>\s*<p>地上空间（0-8\.4米层）<\/p>/)
  assert.match(html, /<figure data-chat-image-index="0"><\/figure>/)
  assert.match(html, /<ul>\s*<li>重点控制首层界面连续性/)
  assert.match(html, /<ul>\s*<li>要求建筑退线形成连续步行网络/)
  assert.match(html, /<ol>[\s\S]*<li>\s*<p>地下空间（三层分级管控）<\/p>[\s\S]*<table>[\s\S]*<\/li>\s*<\/ol>/)
  assert.equal((html.match(/<ol/g) || []).length, 1)
  assert.match(html, /<table>/)
})

test("final render keeps repeated top-level `1.` items in one continuous ordered list after images and citations", () => {
  const input = [
    "1. 地下空间（B3-B1层） B3-B2层：集中布局设备用房与停车设施，强调交通流线隔离（见图号05）。",
    "",
    "- B1层：设置商业服务与公共通道，要求预留与地铁站点的无缝连接（图号04）[1-3]。",
    "",
    "[image:0]",
    "",
    "1. 地面至8.4米层",
    "",
    "- 控制首层架空率≥30%，保障公共活动与通风廊道（图号02）[1-2]；",
    "",
    "- 二层连廊系统串联建筑组团，步行宽度≥6米（图1）。",
  ].join("\n")

  const normalized = mod.buildMarkdownDisplayContent(input, 3)
  const html = renderToStaticMarkup(
    React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeRaw] }, normalized)
  )

  assert.match(normalized, /^1\. 地下空间（B3-B1层）/)
  assert.match(normalized, /\n\n1\. 地面至8\.4米层\n\n   - 控制首层架空率/)
  assert.equal((html.match(/<ol/g) || []).length, 1)
  assert.doesNotMatch(html, /<ol start="2">/)
  assert.match(html, /<figure data-chat-image-index="0"><\/figure>/)
  assert.match(html, /<span data-citation="1"/)
  assert.match(html, /<li>\s*<p>地面至8\.4米层<\/p>/)
})

test("citation-only lines preserve the newline before the next ordered sibling item", () => {
  const input = [
    "1. 一级分项",
    "",
    "- 二级说明",
    "[1][2]",
    "",
    "1. 下一同级分项",
    "",
    "- 后续说明",
  ].join("\n")

  const normalized = mod.buildMarkdownDisplayContent(input, 2)
  const html = renderToStaticMarkup(
    React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeRaw] }, normalized)
  )

  assert.match(normalized, /<\/span><span data-citation="2"[\s\S]*<\/span>\n\n1\. 下一同级分项/)
  assert.equal((html.match(/<ol/g) || []).length, 1)
  assert.match(html, /<li>\s*<p>一级分项<\/p>[\s\S]*<li>\s*<p>下一同级分项<\/p>/)
})

test("inline citation clusters preserve the following same-line space and text", () => {
  const input = "连续引用见依据[1] [2] 后续说明仍应保持分隔。"

  const normalized = mod.buildMarkdownDisplayContent(input, 2)
  const html = renderToStaticMarkup(
    React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm], rehypePlugins: [rehypeRaw] }, normalized)
  )

  assert.match(normalized, /<\/span><span data-citation="2"[\s\S]*<\/span> 后续说明仍应保持分隔。/)
  assert.match(html, /<p>连续引用见依据<span data-citation="1"[\s\S]*<\/span><span data-citation="2"[\s\S]*<\/span> 后续说明仍应保持分隔。<\/p>/)
})
