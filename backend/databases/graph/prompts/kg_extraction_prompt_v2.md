# 医疗建筑知识图谱实体与关系抽取Prompt v2.0

**更新日期**: 2025-12-16
**目标**: 从医疗建筑文档中抽取高质量的实体和关系，构建以空间为骨干的知识图谱

---

## 一、核心原则

### 1.1 全局适应性原则（最重要）

**关键要求**：系统提供了5个种子设计方法作为参考，但你必须具备全局适应性，动态发现文档中的任何设计方法，不要局限于这5个。

**种子方法**（仅作参考）：
- 三区划分法（感染控制方法）
- 双走廊设计（流线设计方法）
- 单元式护理模式（空间布局方法）
- 梯度压差控制（环境控制方法）
- 中心辐射式布局（效率优化方法）

**设计方法识别标志**：
- 明确的命名："XX法"、"XX模式"、"XX设计"、"XX策略"
- 系统性描述：包含原理、步骤、应用场景
- 可复用性：能应用于多个项目或空间
- 目的性：明确解决某类设计问题

**示例**：
```
文本："柔性模块化设计允许根据患者流量动态调整功能分区，通过可移动隔断实现空间的灵活组合"

抽取：
- 实体：[DesignMethod: "柔性模块化设计", category: "空间布局方法"]
- 关系：该方法 GUIDES → 功能分区
- 分类：自动归类为"空间布局方法"（因为涉及空间组织）
```

### 1.2 质量控制原则

**质量阈值**: quality_score >= 0.7

**评分标准**：
| 维度 | 分值 | 判断标准 |
|------|------|---------|
| 定量数据 | +0.2 | 包含具体数值（面积、尺寸、数量、参数） |
| 清晰方法论 | +0.3 | 描述了明确的设计方法、流程或原则 |
| 规范/研究支持 | +0.2 | 引用了标准规范或研究成果 |
| 应用场景 | +0.2 | 明确了适用条件、场景或案例 |
| 视觉资料 | +0.1 | 关联了图片、图纸或表格 |

**低质量示例**（不抽取）：
- "医院需要有急诊部" （常识性描述，无信息增量）
- "手术室很重要" （空洞陈述）
- "某医院面积很大" （缺乏定量数据）

**高质量示例**（应抽取）：
- "手术室净面积应≥30m²，净高≥2.8m（GB 51039-2014 4.2.3）" （定量+规范）
- "采用三区划分法，通过+5Pa/+2.5Pa/0Pa的梯度压差控制" （方法论+定量）

### 1.3 概念引用原则

**预注入骨架**（全局唯一，is_concept=true）：
- Hospital: 综合医院
- DepartmentGroup: 急诊部、门诊部、医技部、住院部
- FunctionalZone: ~20个功能分区
- Space: ~150个标准空间类型

**引用规则**：
1. 遇到"综合医院"、"急诊部"、"手术室"等通用类型名称 → 不创建新节点，引用概念
2. 遇到"北京协和医院"、"某三甲医院急诊楼改扩建" → 创建Case节点
3. 遇到"面积60m²的手术室A3"、"配备GE Discovery CT的影像室" → 创建具体实例节点

---

## 二、实体抽取策略

### 2.1 实体类型判断

#### A. 概念节点（引用预注入骨架，不要创建）

**判断标准**：
- 只有类型名称，没有具体医院名或项目名
- 使用通用术语（"综合医院"、"急诊部"、"手术间"）
- 描述的是一般性概念或标准

**示例**：
```
文本："手术部应设置洁净手术室和普通手术室"

抽取：
- [REFERS_TO] → [Space:手术间(概念)]  # 引用预注入节点，不创建新节点
```

#### B. 案例节点（创建Case节点）

**判断标准**：
- 有具体医院名称："北京协和医院"、"上海瑞金医院"
- 有项目描述："某三甲医院改扩建"、"XX医院急诊楼工程"
- 有时间、地点、规模等具体信息

**案例多粒度特性**：
- 医院级案例："北京协和医院整体改造"
- 部门级案例："某三甲医院急诊部优化设计"
- 区域级案例："手术部净化改造项目"
- 房间级案例："ICU单间设计实践"

**案例入库标准**（满足其一即可）：
- has_media = true（关联图片/图纸）
- has_detailed_params = true（有具体参数）
- has_innovation = true（有创新点）
- content_length > 200（内容丰富）

**抽取示例**：
```
文本："北京协和医院急诊部设置了5间抢救室，每间面积≥30m²，配备完整监护设备，采用三区划分法进行感染控制"

抽取：
{
  "entities": [
    {
      "name": "北京协和医院急诊部改造",
      "type": "案例",
      "label": "Case",
      "properties": {
        "project": "北京协和医院",
        "summary": "急诊部设置5间抢救室，每间≥30m²",
        "has_detailed_params": true,
        "content_length": 250,
        "quality_score": 0.9
      }
    }
  ],
  "relations": [
    ["北京协和医院急诊部改造", "REFERS_TO", "综合医院(概念)"],
    ["北京协和医院急诊部改造", "REFERS_TO", "急诊部(概念)"],
    ["北京协和医院急诊部改造", "REFERS_TO", "抢救室(概念)"],
    ["北京协和医院急诊部改造", "REFERS_TO", "三区划分法(方法)"]
  ]
}
```

#### C. 设计方法节点（动态发现，全局适应）

**识别特征**：
1. **命名特征**：
   - 包含"法"、"模式"、"设计"、"策略"、"原则"
   - 如："三区划分法"、"柔性模块化设计"、"流线优化策略"

2. **描述特征**：
   - 说明了"如何做"或"为什么这样做"
   - 包含设计原理、实施步骤、适用场景
   - 可复用于不同项目

3. **功能特征**：
   - 解决特定设计问题（感染控制、流线优化、空间效率等）
   - 提供可操作的设计指导

**设计方法分类**（5大类，可扩展）：
- **流线设计方法**：人流、物流、洁污流线组织
- **空间布局方法**：功能分区、平面组织
- **感染控制方法**：洁净区划分、污染控制
- **环境控制方法**：空气净化、温湿度、声光环境
- **效率优化方法**：空间利用率、流程优化

**自动分类逻辑**：
```python
if "流线" in description or "走廊" in description:
    category = "流线设计方法"
elif "洁净" in description or "污染" in description or "分区" in description:
    category = "感染控制方法"
elif "布局" in description or "组织" in description:
    category = "空间布局方法"
elif "压差" in description or "净化" in description or "温湿度" in description:
    category = "环境控制方法"
elif "效率" in description or "优化" in description:
    category = "效率优化方法"
else:
    category = "空间布局方法"  # 默认
```

**抽取示例**：
```
文本："单通道设计通过统一的出入口和缓冲空间，简化流线组织，降低交叉感染风险，适用于小型手术部或独立检验科"

抽取：
{
  "entities": [
    {
      "name": "单通道设计",
      "type": "设计方法",
      "label": "DesignMethod",
      "properties": {
        "title": "单通道设计",
        "category": "流线设计方法",  # 自动分类
        "methodology_type": "布局法",
        "description": "通过统一的出入口和缓冲空间，简化流线组织，降低交叉感染风险",
        "applicable_spaces": ["手术部", "检验科"],
        "applicability": "可选",
        "quality_score": 0.85
      }
    }
  ],
  "relations": [
    ["单通道设计", "IS_TYPE_OF", "流线设计方法(分类)"],
    ["单通道设计", "GUIDES", "手术部(概念)"],
    ["单通道设计", "GUIDES", "检验科(概念)"]
  ]
}
```

### 2.2 设计方法间关系抽取

**关系类型**：
- **互补**：两个方法可以同时使用，效果叠加
- **冲突**：两个方法不能同时使用
- **依赖**：方法A的实施前提是方法B
- **替代**：两个方法解决同一问题，可互相替换

**示例**：
```
文本："三区划分法通常与梯度压差控制配合使用，通过物理分隔和压力梯度双重保障感染控制效果"

抽取：
{
  "relations": [
    {
      "subject": "三区划分法",
      "predicate": "RELATES_TO",
      "object": "梯度压差控制",
      "properties": {
        "relationship_type": "互补",
        "strength": 0.9,
        "description": "物理分隔与压力梯度配合使用"
      }
    }
  ]
}
```

---

## 三、关系抽取策略

### 3.1 核心关系类型

#### GUIDES（设计方法 → 空间）

**触发词**：指导、适用于、用于、应用于

**属性**：
- applicability: 强制/推荐/可选/特定条件
- design_phase: 方案设计/初步设计/施工图设计/全阶段
- effectiveness: 0-1（有效性评分）
- conditions: 应用条件描述

**示例**：
```
文本："三区划分法强制应用于所有洁净手术部，贯穿方案到施工全阶段"

关系：
["三区划分法", "GUIDES", "手术部(概念)", {
  "applicability": "强制",
  "design_phase": "全阶段",
  "effectiveness": 1.0,
  "conditions": "适用于所有洁净手术部"
}]
```

#### MENTIONED_IN（实体 → Source）

**perspective分类**（4类）：
- **规范要求**：来自国家/行业标准规范
- **设计指导**：来自设计手册、指南、技术文件
- **实践案例**：来自项目案例、工程实录
- **研究洞察**：来自学术论文、研究报告

**示例**：
```
文本来源：GB 51039-2014
内容："手术室净面积应≥30m²"

关系：
["手术室(概念)", "MENTIONED_IN", "GB 51039-2014", {
  "perspective": "规范要求",
  "summary": "手术室净面积应≥30m²",
  "page": 15,
  "quote": "手术室净面积应≥30m²",
  "is_compliance": true
}]
```

---

## 四、质量评分详细标准

### 4.1 案例节点质量评分

```python
def calculate_case_quality(case_data):
    score = 0.0

    # 1. 是否有图片/图纸 (+0.3)
    if case_data.get("has_media"):
        score += 0.3

    # 2. 是否有详细参数 (+0.3)
    if case_data.get("has_detailed_params"):
        score += 0.3

    # 3. 是否有创新点 (+0.2)
    if case_data.get("has_innovation"):
        score += 0.2

    # 4. 内容长度 (+0.2)
    content_length = case_data.get("content_length", 0)
    if content_length >= 500:
        score += 0.2
    elif content_length >= 200:
        score += 0.1

    return score

# 示例
case_example = {
    "has_media": True,          # +0.3
    "has_detailed_params": True, # +0.3
    "has_innovation": False,     # +0.0
    "content_length": 350        # +0.1
}
quality_score = 0.7  # 达到入库标准
```

### 4.2 设计方法质量评分

```python
def calculate_method_quality(method_data):
    score = 0.0

    # 1. 方法论清晰度 (+0.3)
    if has_clear_description(method_data):
        score += 0.3

    # 2. 适用场景明确 (+0.2)
    if method_data.get("applicable_spaces"):
        score += 0.2

    # 3. 有实际应用案例或规范支持 (+0.3)
    if method_data.get("source_standard") or method_data.get("case_reference"):
        score += 0.3

    # 4. 有定量指标 (+0.2)
    if has_quantitative_metrics(method_data):
        score += 0.2

    return score
```

---

## 五、完整抽取示例

### 示例1：规范文本

**输入文本**：
```
手术部应采用三区划分，设置洁净区、半污染区和污染区。洁净区包括洁净手术室、无菌准备间等，应保持正压状态，压力梯度为+5Pa；半污染区包括术前准备区、恢复室等，压力为+2.5Pa；污染区包括污物处理间，压力为0Pa。各区域之间应设置缓冲室。
```

**输出JSON**：
```json
{
  "entities": [
    {
      "name": "三区划分法",
      "type": "设计方法",
      "label": "DesignMethod",
      "properties": {
        "is_concept": true,  // 种子方法
        "title": "三区划分法",
        "category": "感染控制方法",
        "methodology_type": "划分法",
        "applicability": "强制",
        "quality_score": 0.9
      }
    },
    {
      "name": "梯度压差控制",
      "type": "设计方法",
      "label": "DesignMethod",
      "properties": {
        "is_concept": true,  // 种子方法
        "title": "梯度压差控制",
        "category": "环境控制方法",
        "methodology_type": "控制法",
        "applicability": "强制",
        "quality_score": 0.85
      }
    }
  ],
  "relations": [
    ["三区划分法", "GUIDES", "手术部(概念)", {
      "applicability": "强制",
      "design_phase": "全阶段",
      "effectiveness": 1.0
    }],
    ["梯度压差控制", "GUIDES", "手术部(概念)", {
      "applicability": "强制",
      "design_phase": "初步设计",
      "effectiveness": 0.95
    }],
    ["三区划分法", "RELATES_TO", "梯度压差控制", {
      "relationship_type": "互补",
      "strength": 0.9
    }],
    ["手术部(概念)", "MENTIONED_IN", "当前文档", {
      "perspective": "规范要求",
      "summary": "应采用三区划分和梯度压差控制",
      "quote": "手术部应采用三区划分...",
      "is_compliance": true
    }]
  ]
}
```

### 示例2：案例文本

**输入文本**：
```
某三甲综合医院急诊部改造项目采用了创新的"快速分流模式"：在入口设置智能预检分诊系统，通过AI辅助判断患者病情等级，自动引导至不同诊疗区域。急救区配备5间抢救室，每间45m²，采用开放式布局便于抢救协作。该模式使平均候诊时间从90分钟缩短至35分钟。
```

**输出JSON**：
```json
{
  "entities": [
    {
      "name": "某三甲医院急诊部智能分流改造",
      "type": "案例",
      "label": "Case",
      "properties": {
        "title": "某三甲医院急诊部智能分流改造",
        "project": "某三甲综合医院",
        "summary": "采用AI智能预检分诊，平均候诊时间从90分钟降至35分钟",
        "has_detailed_params": true,
        "has_innovation": true,
        "content_length": 280,
        "quality_score": 0.9
      }
    },
    {
      "name": "快速分流模式",
      "type": "设计方法",
      "label": "DesignMethod",
      "properties": {
        "title": "快速分流模式",
        "category": "效率优化方法",  // 自动分类
        "methodology_type": "优化法",
        "description": "通过智能预检分诊系统和AI辅助判断，自动引导患者至不同诊疗区域",
        "applicable_spaces": ["急诊部"],
        "applicability": "推荐",
        "quality_score": 0.85
      }
    }
  ],
  "relations": [
    ["某三甲医院急诊部智能分流改造", "REFERS_TO", "综合医院(概念)"],
    ["某三甲医院急诊部智能分流改造", "REFERS_TO", "急诊部(概念)"],
    ["某三甲医院急诊部智能分流改造", "REFERS_TO", "快速分流模式"],
    ["快速分流模式", "IS_TYPE_OF", "效率优化方法(分类)"],
    ["快速分流模式", "GUIDES", "急诊部(概念)", {
      "applicability": "推荐",
      "design_phase": "方案设计",
      "effectiveness": 0.9,
      "conditions": "适用于日门急诊量>1000的大型医院"
    }]
  ]
}
```

---

## 六、错误示例与纠正

### 错误1：把案例当作概念

❌ **错误**：
```json
{
  "entities": [
    {"name": "北京协和医院", "type": "医院", "label": "Hospital"}
  ]
}
```

✅ **正确**：
```json
{
  "entities": [
    {"name": "北京协和医院改扩建", "type": "案例", "label": "Case"}
  ],
  "relations": [
    ["北京协和医院改扩建", "REFERS_TO", "综合医院(概念)"]
  ]
}
```

### 错误2：漏掉设计方法

❌ **错误**：
```json
{
  "entities": [
    {"name": "手术部", "type": "功能分区", "label": "FunctionalZone"}
  ]
}
```

文本："手术部采用集中式布局，将所有手术室集中在同一楼层..."

✅ **正确**：
```json
{
  "entities": [
    {"name": "集中式布局", "type": "设计方法", "label": "DesignMethod"}
  ],
  "relations": [
    ["集中式布局", "IS_TYPE_OF", "空间布局方法(分类)"],
    ["集中式布局", "GUIDES", "手术部(概念)"]
  ]
}
```

### 错误3：质量评分过于宽松

❌ **错误**：
```json
{
  "entities": [
    {
      "name": "医院很大",
      "type": "案例",
      "quality_score": 0.8
    }
  ]
}
```

✅ **正确**：不抽取（quality_score < 0.7，缺乏信息增量）

---

## 七、输出格式规范

### 标准JSON格式

```json
{
  "entities": [
    {
      "name": "实体名称",
      "type": "实体类型中文",
      "label": "NodeLabel",
      "properties": {
        "key1": "value1",
        "quality_score": 0.85,
        "is_concept": false
      }
    }
  ],
  "relations": [
    ["主体", "关系类型", "客体", {
      "property1": "value1"
    }]
  ],
  "entity_descriptions": {
    "实体名称": "详细描述"
  }
}
```

---

## 八、Prompt使用指南

### 在kg_builder中调用

```python
prompt = f"""
{open('kg_extraction_prompt_v2.md').read()}

---

现在请从以下文本中抽取实体和关系：

文本：
{chunk_text}

输出JSON：
"""

response = llm.generate(prompt)
```

---

**版本**: 2.0
**更新**: 2025-12-16
**关键改进**:
1. 增加设计方法的全局适应性识别
2. 增强质量控制机制
3. 明确案例多粒度特性
4. 优化perspective分类为4类
