"""
VLM 图片描述生成器 - 优化版

基于 qwen3-vl-plus 为图片生成语义描述，使图片可被向量化检索
- 支持智能 prompt 根据章节自适应
- 内置缓存机制（MD5哈希）避免重复调用
- 自动合并 OCR 文本与 VLM 描述
- 健壮的错误处理与降级策略

使用示例：
    from backend.databases.ingestion.indexing.vision_describer import generate_image_description

    caption = generate_image_description(
        image_path="/path/to/image.png",
        ocr_text="图3-2 门诊大厅布局",
        section="第三章 门诊部设计",
        page=24
    )
    # 返回: "[图片: 图3-2 门诊大厅布局] 该平面图展示了一个500平米的综合医院门诊大厅..."
"""

import os
import base64
import hashlib
import json
import logging
import atexit
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class VisionDescriber:
    """使用 VLM 为图片生成专业描述"""

    def __init__(self):
        # Prefer VLM_* env vars; keep KG_VISION_* as fallback
        self.api_key = os.getenv("VLM_API_KEY") or os.getenv("KG_VISION_API_KEY")
        self.base_url = (os.getenv("VLM_BASE_URL") or os.getenv("KG_VISION_BASE_URL") or "").rstrip("/")
        self.model = (os.getenv("VLM_MODEL") or os.getenv("VLM_MODE") or os.getenv("KG_VISION_MODEL") or "qwen3-vl-plus")
        self.timeout = int(os.getenv("VLM_TIMEOUT", "60"))
        self.max_tokens = int(os.getenv("VLM_MAX_TOKENS", "800"))
        self.temperature = float(os.getenv("VLM_TEMPERATURE", "0.3"))

        if not self.api_key or not self.base_url:
            logger.warning("VLM not configured; falling back to OCR text. Set VLM_API_KEY and VLM_BASE_URL (or KG_VISION_API_KEY / KG_VISION_BASE_URL).")
            self.enabled = False
        else:
            self.enabled = True

        # 缓存配置
        self.cache_enabled = os.getenv("VLM_CACHE", "1") == "1"
        self.cache_file = Path(os.getenv("VLM_CACHE_FILE", "backend/databases/ingestion/vlm_cache.json"))
        self.cache_flush_every = int(os.getenv("VLM_CACHE_FLUSH_EVERY", "1") or 1)
        if self.cache_flush_every <= 0:
            self.cache_flush_every = 1
        self._cache_dirty = 0
        self._cache: Dict[str, str] = self._load_cache()
        if self.cache_enabled:
            atexit.register(self.flush_cache)

        # 监控/费用（可选）
        self.usage_log_enabled = os.getenv("VLM_USAGE_LOG", "1").lower() in {"1", "true", "yes"}
        self.usage_log_file = Path(os.getenv("VLM_USAGE_LOG_FILE", "backend/databases/ingestion/vlm_usage.jsonl"))
        self.log_cache_hits = os.getenv("VLM_USAGE_LOG_CACHE_HITS", "0").lower() in {"1", "true", "yes"}

        # 成本估算（可选）：优先使用按次，其次按 token
        self.price_per_call_usd = self._safe_float(os.getenv("VLM_PRICE_PER_CALL_USD"))
        self.price_prompt_per_mtok = self._safe_float(os.getenv("VLM_PROMPT_PRICE_PER_MTOK"))
        self.price_completion_per_mtok = self._safe_float(os.getenv("VLM_COMPLETION_PRICE_PER_MTOK"))
        self.price_total_per_mtok = self._safe_float(os.getenv("VLM_PRICE_PER_MTOK"))

    def describe_image(
        self,
        image_path: str,
        ocr_text: str = "",
        section: str = "",
        page: int = 0,
        custom_prompt: Optional[str] = None
    ) -> str:
        """生成图片描述

        Args:
            image_path: 图片绝对路径
            ocr_text: MinerU 识别的文字（如图注、标题）
            section: 所在章节（用于构建上下文感知的 prompt）
            page: 页码
            custom_prompt: 自定义提示词（覆盖默认 prompt）

        Returns:
            格式化的图片描述，如: "[图片: 图3-2] 该平面图展示了..."
        """
        # 0. 前置检查
        if not self.enabled:
            return self._format_fallback(ocr_text)

        img_path = Path(image_path)
        if not img_path.exists():
            logger.error(f"图片不存在: {image_path}")
            return self._format_fallback(ocr_text)

        # 1. 检查缓存
        cache_key = self._get_cache_key(image_path)
        if self.cache_enabled and cache_key in self._cache:
            logger.debug(f"VLM 缓存命中: {img_path.name}")
            if self.usage_log_enabled and self.log_cache_hits:
                self._append_usage_log(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "model": self.model,
                        "cached": True,
                        "ok": True,
                        "image_name": img_path.name,
                        "image_md5": cache_key,
                        "duration_s": 0.0,
                        "cost_usd": 0.0,
                    }
                )
            return self._format_output(self._cache[cache_key], ocr_text)

        # 2. 读取图片并编码
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
                # 检查文件大小（超过5MB跳过）
                size_mb = len(image_bytes) / (1024 * 1024)
                if size_mb > 5:
                    logger.warning(f"图片过大({size_mb:.1f}MB)，跳过VLM: {img_path.name}")
                    return self._format_fallback(ocr_text)

                image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as e:
            logger.error(f"读取图片失败 {image_path}: {e}")
            return self._format_fallback(ocr_text)

        # 3. 构建 prompt
        prompt = custom_prompt or self._build_smart_prompt(section, ocr_text, page)

        # 4. 调用 VLM API
        t0 = time.perf_counter()
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}"
                                    }
                                }
                            ]
                        }
                    ],
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            duration_s = float(time.perf_counter() - t0)

            # 提取描述
            raw_caption = result["choices"][0]["message"]["content"].strip()
            usage = result.get("usage") if isinstance(result, dict) else None
            cost_usd = self._estimate_cost_usd(usage if isinstance(usage, dict) else None)

            # 5. 保存缓存
            if self.cache_enabled:
                self._cache[cache_key] = raw_caption
                self._cache_dirty += 1
                if self._cache_dirty >= self.cache_flush_every:
                    self.flush_cache()

            logger.info(f"VLM 生成成功: {img_path.name} ({len(raw_caption)}字)")

            if self.usage_log_enabled:
                self._append_usage_log(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "model": self.model,
                        "cached": False,
                        "ok": True,
                        "image_name": img_path.name,
                        "image_md5": cache_key,
                        "duration_s": round(duration_s, 3),
                        "prompt_tokens": self._safe_int((usage or {}).get("prompt_tokens")),
                        "completion_tokens": self._safe_int((usage or {}).get("completion_tokens")),
                        "total_tokens": self._safe_int((usage or {}).get("total_tokens")),
                        "cost_usd": cost_usd,
                        "page": page,
                        "section": section or None,
                    }
                )
            return self._format_output(raw_caption, ocr_text)

        except requests.exceptions.Timeout:
            logger.warning(f"VLM 调用超时({self.timeout}s): {img_path.name}")
            if self.usage_log_enabled:
                duration_s = float(time.perf_counter() - t0)
                self._append_usage_log(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "model": self.model,
                        "cached": False,
                        "ok": False,
                        "error": "timeout",
                        "image_name": img_path.name,
                        "image_md5": cache_key,
                        "duration_s": round(duration_s, 3),
                        "page": page,
                        "section": section or None,
                    }
                )
            return self._format_fallback(ocr_text)
        except Exception as e:
            logger.warning(f"VLM 调用失败: {e}")
            if self.usage_log_enabled:
                duration_s = float(time.perf_counter() - t0)
                self._append_usage_log(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "model": self.model,
                        "cached": False,
                        "ok": False,
                        "error": str(e)[:200],
                        "image_name": img_path.name,
                        "image_md5": cache_key,
                        "duration_s": round(duration_s, 3),
                        "page": page,
                        "section": section or None,
                    }
                )
            return self._format_fallback(ocr_text)

    def _build_smart_prompt(self, section: str, ocr_text: str, page: int) -> str:
        """根据章节上下文构建智能 prompt"""
        section_lower = (section or "").lower()

        # 根据章节关键词选择专业 prompt
        if any(kw in section_lower for kw in ["平面", "布局", "分区"]):
            base = """你是医院建筑设计专家。这是一张医院平面布局图，请提取：
1. **功能分区**：各区域名称、用途、面积（如标注）
2. **流线组织**：患者流线、医护流线、物流动线的设计特点
3. **空间关系**：各功能区的相对位置和衔接方式
4. **关键尺寸**：房间尺寸、走廊宽度等数值（如有标注）
5. **设计要点**：符合规范的关键设计（如洁污分流、单向流线等）

用专业术语，简明扼要，重点突出数值和规范要求。"""

        elif any(kw in section_lower for kw in ["剖面", "立面", "断面"]):
            base = """你是医院建筑设计专家。这是一张医院剖面图或立面图，请描述：
1. **竖向布局**：建筑层数、各层功能分布
2. **层高数据**：标注的层高、净高数值
3. **结构体系**：可见的结构形式（梁、柱、楼板等）
4. **竖向交通**：电梯、楼梯的位置和类型
5. **技术细节**：通风井、管道井等竖向设备空间

保持客观，数据优先。"""

        elif any(kw in section_lower for kw in ["表", "标准", "参数", "规范"]):
            base = """你是医院建筑设计专家。这是一张表格或图表，请提取：
1. **表格标题**：说明表格主题和适用范围
2. **关键数据**：重要的面积、人数、设备数量等参数（逐行提取）
3. **分类维度**：表格的行列标题和分类逻辑
4. **备注条件**：表格底部的备注、适用条件、引用规范

以结构化方式呈现，保留数值精度。"""

        elif any(kw in section_lower for kw in ["效果", "外观", "透视", "鸟瞰"]):
            base = """你是医院建筑设计专家。这是一张建筑效果图，请描述：
1. **整体风格**：建筑造型特征、设计风格
2. **材质肌理**：可见的外墙材料、色彩、质感
3. **环境配置**：景观、绿化、广场等室外空间
4. **视角信息**：视图类型（鸟瞰、人视、透视等）

用简洁专业的语言，避免主观评价。"""

        elif any(kw in section_lower for kw in ["系统", "流程", "示意"]):
            base = """你是医院建筑设计专家。这是一张系统示意图或流程图，请说明：
1. **系统类型**：图示的系统名称和功能
2. **组成要素**：系统的关键组件和节点
3. **流程逻辑**：箭头或连线表示的流向和逻辑关系
4. **关键参数**：标注的技术参数、尺寸、数量

重点提取技术信息。"""

        else:
            # 通用 prompt
            base = """你是医院建筑设计专家。请分析这张图片并提取关键信息：
1. **图片类型**：平面图/剖面图/效果图/表格/照片等
2. **核心内容**：图片展示的主要设计内容
3. **技术信息**：尺寸、面积、材料、设备等数据
4. **设计要点**：值得关注的设计特点或规范要求

用专业术语，突出数据和关键信息。"""

        # 附加上下文信息
        context_parts = []
        if ocr_text:
            context_parts.append(f"【图注】：{ocr_text}")
        if section:
            context_parts.append(f"【所在章节】：{section}")
        if page:
            context_parts.append(f"【页码】：第{page}页")

        if context_parts:
            base += f"\n\n上下文信息：\n" + "\n".join(context_parts)
            base += "\n\n请结合上述上下文理解图片内容。"

        return base

    def _format_output(self, vlm_caption: str, ocr_text: str) -> str:
        """格式化最终输出"""
        if ocr_text:
            # OCR 文本作为标签，VLM 描述作为正文
            return f"[图片: {ocr_text}] {vlm_caption}"
        else:
            return f"[图片] {vlm_caption}"

    def _format_fallback(self, ocr_text: str) -> str:
        """降级输出（无VLM时）"""
        if ocr_text:
            return f"[图片: {ocr_text}]"
        else:
            return "[图片]"

    def _get_cache_key(self, image_path: str) -> str:
        """计算图片内容哈希作为缓存键"""
        try:
            with open(image_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            # 降级：使用文件路径+修改时间
            p = Path(image_path)
            mtime = p.stat().st_mtime if p.exists() else 0
            return hashlib.md5(f"{image_path}:{mtime}".encode()).hexdigest()

    def _load_cache(self) -> Dict[str, str]:
        """加载缓存"""
        if not self.cache_enabled:
            return {}

        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    logger.info(f"VLM 缓存加载成功: {len(cache)} 条记录")
                    return cache
        except Exception as e:
            logger.warning(f"加载 VLM 缓存失败: {e}")

        return {}

    def _save_cache(self):
        """保存缓存"""
        if not self.cache_enabled:
            return

        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 VLM 缓存失败: {e}")

    def flush_cache(self) -> None:
        """Flush cache to disk (best-effort)."""
        if not self.cache_enabled:
            return
        if self._cache_dirty <= 0:
            return
        self._save_cache()
        self._cache_dirty = 0

    # ----------------- usage/cost helpers -----------------
    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(str(value).strip())
        except Exception:
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _estimate_cost_usd(self, usage: Optional[Dict[str, Any]]) -> Optional[float]:
        """
        Estimate cost for a single VLM call.

        Priority:
        1) VLM_PRICE_PER_CALL_USD (per request)
        2) token-based pricing:
           - VLM_PROMPT_PRICE_PER_MTOK + VLM_COMPLETION_PRICE_PER_MTOK
           - or VLM_PRICE_PER_MTOK (total tokens)
        """
        if self.price_per_call_usd and self.price_per_call_usd > 0:
            return float(self.price_per_call_usd)

        if not usage or not isinstance(usage, dict):
            return None

        prompt = self._safe_int(usage.get("prompt_tokens"))
        completion = self._safe_int(usage.get("completion_tokens"))
        total = self._safe_int(usage.get("total_tokens")) or (prompt + completion)

        if (self.price_prompt_per_mtok and self.price_prompt_per_mtok > 0) or (
            self.price_completion_per_mtok and self.price_completion_per_mtok > 0
        ):
            cost = (prompt / 1_000_000.0) * float(self.price_prompt_per_mtok) + (completion / 1_000_000.0) * float(
                self.price_completion_per_mtok
            )
            return round(cost, 8)

        if self.price_total_per_mtok and self.price_total_per_mtok > 0 and total > 0:
            return round((total / 1_000_000.0) * float(self.price_total_per_mtok), 8)

        return None

    def _append_usage_log(self, record: Dict[str, Any]) -> None:
        """Append one JSON line (best-effort). Never raises."""
        try:
            if not self.usage_log_enabled:
                return
            self.usage_log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # avoid breaking VLM flow due to logging
            return


# ============================================================================
# 全局单例与便捷函数
# ============================================================================

_describer_instance: Optional[VisionDescriber] = None


def get_describer() -> VisionDescriber:
    """获取全局单例"""
    global _describer_instance
    if _describer_instance is None:
        _describer_instance = VisionDescriber()
    return _describer_instance


def generate_image_description(
    image_path: str,
    ocr_text: str = "",
    section: str = "",
    page: int = 0,
    custom_prompt: Optional[str] = None
) -> str:
    """便捷函数：生成图片描述

    Args:
        image_path: 图片绝对路径
        ocr_text: OCR 识别的文字（图注、标题等）
        section: 所在章节
        page: 页码
        custom_prompt: 自定义 prompt（可选）

    Returns:
        格式化的图片描述

    Example:
        >>> caption = generate_image_description(
        ...     image_path="/path/to/layout.png",
        ...     ocr_text="图3-2 门诊大厅平面图",
        ...     section="第三章 门诊部设计",
        ...     page=24
        ... )
        >>> print(caption)
        "[图片: 图3-2 门诊大厅平面图] 该平面图展示了一个500平米的综合医院门诊大厅..."
    """
    describer = get_describer()
    return describer.describe_image(
        image_path=image_path,
        ocr_text=ocr_text,
        section=section,
        page=page,
        custom_prompt=custom_prompt
    )


# ============================================================================
# 测试函数
# ============================================================================

def test_vlm(image_path: str):
    """测试 VLM 功能"""
    print(f"\n[测试] 正在处理: {image_path}")
    print("=" * 80)

    result = generate_image_description(
        image_path=image_path,
        ocr_text="测试图片",
        section="测试章节",
        page=1
    )

    print(f"\n[结果]\n{result}")
    print("=" * 80)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_vlm(sys.argv[1])
    else:
        print("用法: python vision_describer.py <图片路径>")

