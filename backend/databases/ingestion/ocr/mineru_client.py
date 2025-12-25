"""
MinerU 本地 OCR 客户端（CLI 封装，兼容 TextIn 的返回结构）

说明：
- 优先通过 MinerU 安装的可执行程序运行（例如 `mineru`）；
- 若找不到可执行程序，则回退为 `python -m mineru`；
- 不依赖 MinerU 的 Python 内部 API，避免环境耦合；
- page_range 目前按“尽力支持”：CLI 若不支持页段，本实现将仍解析整本，但仅将账本合并指定页段，Markdown 在整本解析时整本写入，指定页段时做页段标注（不做严格切片）。

环境变量（可选）：
- MINERU_PROJECT_ROOT：运行 MinerU 的工作目录（如包含模型/配置的根目录），默认当前工作目录；
- MINERU_EXE：mineru 可执行文件名或绝对路径，默认 "mineru"；
- MINERU_PYTHON_EXE：回退调用使用的 Python 可执行程序，默认 "python"；
- MINERU_BACKEND：MinerU 后端（如 "pipeline"），默认 "pipeline"；
- MINERU_USE_CUDA：是否启用 CUDA（"1"/"true"），默认关闭；

返回结构（legacy dict，与 TextInClient.parse_pdf 一致）：
{
  "code": 200,
  "message": "ok",
  "duration": <ms>,
  "result": {
    "markdown": <str>,
    "detail": [],
    "total_page_number": <int>,
    "success_count": <int>
  },
  "metrics": [ { "trace_id": ..., "request_id": "N/A", "warnings": [], "duration": <ms> } ]
}
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from datetime import datetime
import json
import re
import threading
from typing import Any, Dict, Optional, Tuple, Union
import requests
import zipfile
import io
from glob import glob


class MineruClient:
    def __init__(
        self,
        project_root: Optional[Union[str, Path]] = None,
        mineru_exe: Optional[str] = None,
        python_exe: Optional[str] = None,
        backend: Optional[str] = None,
        use_cuda: Optional[bool] = None,
    ) -> None:
        self.project_root = Path(project_root or os.getenv("MINERU_PROJECT_ROOT", ".")).resolve()
        self.mineru_exe = mineru_exe or os.getenv("MINERU_EXE", "mineru")
        self.python_exe = python_exe or os.getenv("MINERU_PYTHON_EXE", "python")
        self.backend = backend or os.getenv("MINERU_BACKEND", "pipeline")
        env_cuda = os.getenv("MINERU_USE_CUDA", "0").lower()
        self.use_cuda = bool(use_cuda) if use_cuda is not None else (env_cuda in {"1", "true", "yes"})
        # 远程 API（可选）
        self.api_url = os.getenv("MINERU_API_URL") or None
        self.api_key = os.getenv("MINERU_API_KEY") or None
        self.api_mode = (os.getenv("MINERU_API_MODE") or "auto").strip().lower()  # auto|batch|task|direct

    # 与 TextInClient 对齐的接口
    def parse_pdf(self, pdf_path: str, legacy: bool = True, page_range: Optional[Tuple[int, int]] = None, **kwargs: Any) -> Dict[str, Any]:
        start = time.perf_counter()
        pdf = Path(pdf_path).resolve()
        if not pdf.exists():
            raise FileNotFoundError(f"MinerU: 文件不存在: {pdf}")

        # 优先走远程 API；若配置了 API 则强制使用，不回退到本地 CLI
        artifacts_base_api: Optional[str] = kwargs.get("artifacts_dir")
        if self.api_url:
            # 模式判定
            mode = self.api_mode
            url_lc = str(self.api_url).lower()
            if mode == "auto":
                if "/file-urls/batch" in url_lc:
                    mode = "batch"
                elif "/extract/task" in url_lc:
                    mode = "task"
                else:
                    mode = "direct"

            # 强制使用 API，失败时直接抛出异常，不回退
            if mode == "batch":
                return self._parse_pdf_via_api_batch(pdf_path=str(pdf), page_range=page_range, artifacts_dir=artifacts_base_api)
            if mode == "task":
                return self._parse_pdf_via_api_task(pdf_path=str(pdf), page_range=page_range, artifacts_dir=artifacts_base_api)
            return self._parse_pdf_via_api(pdf_path=str(pdf), page_range=page_range, artifacts_dir=artifacts_base_api)

        # 准备临时输入/输出目录
        with tempfile.TemporaryDirectory(prefix="mineru_in_") as in_dir, tempfile.TemporaryDirectory(prefix="mineru_out_") as out_dir:
            in_dir_p = Path(in_dir)
            # 若调用方提供 artifacts_dir，则将 MinerU 产物持久化到该目录的子目录中
            artifacts_base: Optional[str] = kwargs.get("artifacts_dir")
            if artifacts_base:
                base = Path(artifacts_base).resolve()
                try:
                    base.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                label = None
                if page_range and isinstance(page_range, tuple):
                    try:
                        s, e = int(page_range[0]), int(page_range[1])
                        label = f"p{s}-{e}"
                    except Exception:
                        label = None
                label = label or "full"
                # 使用稳定子目录名，统一归并到资料目录根，不再保留按页段的子目录
                out_dir_p = base
                try:
                    out_dir_p.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
            else:
                out_dir_p = Path(out_dir)
            # 准备输入 PDF：若指定页段，先裁剪出子 PDF，加速处理
            target_pdf = in_dir_p / pdf.name
            if page_range and isinstance(page_range, tuple):
                s, e = int(page_range[0]), int(page_range[1])
                ranged_pdf = in_dir_p / f"{pdf.stem}_{s}-{e}{pdf.suffix}"
                if self._write_pdf_range(str(pdf), ranged_pdf, s, e):
                    target_pdf = ranged_pdf
                else:
                    shutil.copy2(str(pdf), str(target_pdf))
            else:
                shutil.copy2(str(pdf), str(target_pdf))

            # 解析命令
            cmd_prefix, cmd_used = self._resolve_command()
            mineru_args = ["-p", str(in_dir_p), "-o", str(out_dir_p), "--backend", self.backend]
            # 显式打开 MinerU 的可视化导出与 markdown 导出（不同版本 mineru 会自动导出，但这里强制启用以确保 _layout/_span.md 存在）
            mineru_args.extend(["--f-draw-layout-bbox", "True", "--f-draw-span-bbox", "True", "--f-dump-md", "True"])
            if self.use_cuda:
                # 尝试检测 CUDA 可用性，不可用则回退为 CPU
                if self._cuda_available():
                    mineru_args.extend(["--device", "cuda"])  # 若 CLI 不支持，该参数将被忽略或报错
                else:
                    print("[MinerU] 未检测到可用 CUDA，自动回退为 CPU 模式")

            cmd = cmd_prefix + mineru_args

            # 运行 MinerU
            proc, stdout, stderr = self._run_with_spinner(
                cmd=cmd,
                cwd=self.project_root,
                timeout_s=None,
                label="[MinerU] 正在解析",
            )
            duration_ms = (time.perf_counter() - start) * 1000.0

            if proc.returncode != 0:
                raise RuntimeError(f"MinerU 运行失败（{proc.returncode}）：{(stderr or '').strip() or (stdout or '').strip()}")

            # 尝试找到输出的 Markdown 文件
            md_text = self._read_first_markdown(out_dir_p)
            if md_text is None:
                md_text = ""  # 保底

            # 尝试读取结构化 JSON，并尽力映射到 TextIn 兼容的 detail
            detail = self._read_first_detail(out_dir_p)

            total_pages = self._local_total_pages(str(pdf))
            if total_pages <= 0:
                total_pages = 0

            # success_count：若指定页段，按页段长度；否则为总页数（若未知则为 0）
            if page_range and total_pages > 0:
                s, e = int(page_range[0]), int(page_range[1])
                success_count = max(0, min(e, total_pages) - max(1, s) + 1)
            elif page_range and total_pages <= 0:
                s, e = int(page_range[0]), int(page_range[1])
                success_count = max(0, e - s + 1)
            else:
                success_count = total_pages

            # 构造与 TextIn 兼容的 legacy 结构
            result: Dict[str, Any] = {
                "code": 200,
                "message": "ok",
                "duration": int(duration_ms),
                "result": {
                    "markdown": md_text,
                    "detail": detail or [],
                    "total_page_number": total_pages,
                    "success_count": success_count,
                },
                "metrics": [
                    {
                        "trace_id": "mineru-cli",
                        "request_id": "N/A",
                        "warnings": [],
                        "duration": int(duration_ms),
                    }
                ],
            }
            # 若产物被持久化，返回目录位置给上层
            try:
                if artifacts_base:
                    result["artifacts_dir"] = str(out_dir_p)
            except Exception:
                pass
            return result

    def parse_url(self, file_url: str, legacy: bool = True, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("MinerU 本地模式暂不支持 URL 解析")

    # --- 内部工具 ---

    def get_device_mode(self) -> str:
        """返回当前将要使用的设备模式（cuda/cpu）。

        当配置启用 CUDA 且检测到可用 CUDA 时返回 "cuda"，否则返回 "cpu"。若走远程 API，返回 "remote"。
        """
        try:
            if self.api_url:
                return "remote"
            return "cuda" if (self.use_cuda and self._cuda_available()) else "cpu"
        except Exception:
            return "cpu"

    def get_backend(self) -> str:
        """返回当前 MinerU 后端标识。"""
        try:
            return (str(self.backend) + "@api") if self.api_url else str(self.backend)
        except Exception:
            return str(self.backend)

    # ---- 远程 API 调用 ----
    def _api_session(self) -> requests.Session:
        sess = requests.Session()
        if os.getenv("MINERU_API_IGNORE_PROXY", "0").lower() in {"1", "true"}:
            sess.trust_env = False
            sess.proxies = {}
        return sess

    def _parse_pdf_via_api_task(self, pdf_path: str, page_range: Optional[Tuple[int, int]], artifacts_dir: Optional[str]) -> Dict[str, Any]:
        """基于 URL 的任务创建与结果轮询。注意：该模式要求可公网访问的文件 URL。

        这里仅供完整性保留：若 MINERU_API_URL 指向 /extract/task，则直接抛出提示，因为我们处理本地文件。
        """
        raise RuntimeError("extract/task 模式需要可访问的文件 URL；当前为本地文件，请改用 file-urls/batch 模式或提供可访问的 URL")

    def _parse_pdf_via_api(self, pdf_path: str, page_range: Optional[Tuple[int, int]], artifacts_dir: Optional[str]) -> Dict[str, Any]:
        start = time.perf_counter()
        url = str(self.api_url).rstrip("/")
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # 若指定页段，先在本地裁剪后再上传，降低体积，避免 413
        upload_path = pdf_path
        tmp_ctx = None
        if page_range and isinstance(page_range, tuple):
            try:
                import tempfile as _tf
                tmp_ctx = _tf.TemporaryDirectory(prefix="mineru_api_")
                s, e = int(page_range[0]), int(page_range[1])
                dst = Path(tmp_ctx.name) / f"range_{s}-{e}.pdf"
                if self._write_pdf_range(pdf_path, dst, s, e):
                    upload_path = str(dst)
            except Exception:
                pass

        # 允许通过环境变量忽略系统代理，避免 ProxyError
        ignore_proxy = os.getenv("MINERU_API_IGNORE_PROXY", "0").lower() in {"1", "true"}
        session = self._api_session()

        files = {"file": open(upload_path, "rb")}
        data: Dict[str, Any] = {"backend": self.backend}
        # 设备提示（具体是否生效取决于服务端）
        data["device"] = "cuda" if self.use_cuda else "cpu"
        if page_range:
            try:
                s, e = int(page_range[0]), int(page_range[1])
                data.update({"s": s, "e": e})
            except Exception:
                pass
        try:
            resp = session.post(url, headers=headers, files=files, data=data, timeout=600)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise RuntimeError(f"MinerU API 调用失败: {e}")
        finally:
            try:
                files["file"].close()
            except Exception:
                pass
            if tmp_ctx is not None:
                try:
                    tmp_ctx.cleanup()
                except Exception:
                    pass

        # 兼容多种字段命名
        def _get(obj: dict, *keys, default=None):
            for k in keys:
                if isinstance(obj, dict) and k in obj:
                    return obj[k]
            return default

        container = payload
        if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
            container = payload["result"]
        elif isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
            container = payload["data"]

        md_text = _get(container, "markdown", default="") or ""
        detail = _get(container, "detail", "pages", default=[]) or []
        total_pages = _get(container, "total_page_number", "total_pages", default=0) or 0
        success_pages = _get(container, "success_count", "valid_pages", default=0) or 0

        # 下载附件（若有）
        attachments = _get(container, "attachments", "assets", "files", default=[])
        saved_dir: Optional[Path] = None
        if artifacts_dir and isinstance(attachments, list) and attachments:
            try:
                base = Path(artifacts_dir).resolve()
                base.mkdir(parents=True, exist_ok=True)
                # 统一落地到根目录（覆盖）
                saved_dir = base
                saved_dir.mkdir(parents=True, exist_ok=True)
                for item in attachments:
                    if not isinstance(item, dict):
                        continue
                    url_i = item.get("url") or item.get("href")
                    name_i = item.get("name") or item.get("filename") or os.path.basename(str(url_i or "file"))
                    if not url_i:
                        continue
                    try:
                        r = requests.get(url_i, timeout=300)
                        r.raise_for_status()
                        (saved_dir / name_i).write_bytes(r.content)
                    except Exception:
                        continue
            except Exception:
                saved_dir = None

        duration_ms = (time.perf_counter() - start) * 1000.0
        result: Dict[str, Any] = {
            "code": 200,
            "message": payload.get("message") if isinstance(payload, dict) else "ok",
            "duration": int(duration_ms),
            "result": {
                "markdown": md_text,
                "detail": detail or [],
                "total_page_number": int(total_pages or 0),
                "success_count": int(success_pages or (page_range[1]-page_range[0]+1 if page_range else 0)),
            },
            "metrics": [
                {"trace_id": "mineru-api", "request_id": _get(payload, "request_id", default="N/A"), "warnings": [], "duration": int(duration_ms)}
            ],
        }
        if saved_dir is not None:
            result["artifacts_dir"] = str(saved_dir)
            # 确保生成区域标注 PDF
            try:
                self._ensure_regions_pdf(Path(saved_dir), pdf_path, page_range)
            except Exception:
                pass
        return result

    def _parse_pdf_via_api_batch(self, pdf_path: str, page_range: Optional[Tuple[int, int]], artifacts_dir: Optional[str]) -> Dict[str, Any]:
        """批量预签名上传 + 轮询结果 + 下载 zip 并解析。"""
        start = time.perf_counter()
        api_url = str(self.api_url).rstrip("/")
        # 1) 申请上传链接（POST JSON）
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # page_ranges 转字符串
        pr_str = None
        if page_range and isinstance(page_range, tuple):
            try:
                s, e = int(page_range[0]), int(page_range[1])
                pr_str = f"{s}-{e}"
            except Exception:
                pr_str = None
        model_version = "vlm" if (str(self.backend).lower().startswith("vlm")) else "pipeline"
        body = {
            "enable_formula": True,
            "language": "ch",
            "enable_table": True,
            "files": [{
                "name": Path(pdf_path).name,
                "is_ocr": True,
                "data_id": Path(pdf_path).stem,
                **({"page_ranges": pr_str} if pr_str else {}),
                **({"model_version": model_version} if model_version else {}),
            }]
        }
        sess = self._api_session()
        resp = sess.post(api_url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        js = resp.json()
        if not isinstance(js, dict) or js.get("code") not in (0, 200):
            raise RuntimeError(f"申请上传链接失败: {js}")
        data = js.get("data") or {}
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or data.get("files") or []
        if not batch_id or not file_urls:
            raise RuntimeError("返回缺少 batch_id 或 file_urls")
        upload_url = file_urls[0]

        # 2) 本地页段裁剪并 PUT 上传（带重试机制）
        upload_path = pdf_path
        tmp_ctx = None
        if page_range and isinstance(page_range, tuple):
            try:
                import tempfile as _tf
                tmp_ctx = _tf.TemporaryDirectory(prefix="mineru_api_")
                s, e = int(page_range[0]), int(page_range[1])
                dst = Path(tmp_ctx.name) / f"range_{s}-{e}.pdf"
                if self._write_pdf_range(pdf_path, dst, s, e):
                    upload_path = str(dst)
            except Exception:
                pass

        # 重试上传（最多3次）
        max_retries = int(os.getenv("MINERU_API_UPLOAD_RETRIES", "3"))
        upload_timeout = int(os.getenv("MINERU_API_UPLOAD_TIMEOUT", "1800"))

        for retry in range(max_retries):
            try:
                with open(upload_path, "rb") as f:
                    put_resp = sess.put(upload_url, data=f, timeout=upload_timeout)
                    put_resp.raise_for_status()
                break  # 成功则退出循环
            except Exception as e:
                if retry < max_retries - 1:
                    wait_time = (retry + 1) * 5  # 递增等待时间：5s, 10s, 15s
                    print(f"[MinerU] 上传失败 (尝试 {retry+1}/{max_retries})，{wait_time}秒后重试: {e}")
                    time.sleep(wait_time)
                else:
                    raise RuntimeError(f"PDF 上传失败，已重试 {max_retries} 次: {e}")

        if tmp_ctx is not None:
            try:
                tmp_ctx.cleanup()
            except Exception:
                pass

        # 3) 轮询结果（GET extract-results/batch/{batch_id}）
        # 结果 URL：同域替换 file-urls/batch -> extract-results/batch；或读取 MINERU_API_RESULT_URL
        result_base = os.getenv("MINERU_API_RESULT_URL")
        if not result_base:
            result_base = api_url.replace("file-urls/batch", "extract-results/batch")
        polling_url = result_base.rstrip("/") + f"/{batch_id}"
        poll_headers = {"Authorization": headers.get("Authorization")} if self.api_key else {}
        max_wait_s = int(os.getenv("MINERU_API_MAX_WAIT_SEC", "1200"))  # 20 min
        interval_s = int(os.getenv("MINERU_API_POLL_INTERVAL", "5"))
        target_name = Path(pdf_path).name
        saved_dir: Optional[Path] = None
        full_zip_url: Optional[str] = None
        elapsed = 0
        while elapsed <= max_wait_s:
            r = sess.get(polling_url, headers=poll_headers, timeout=60)
            r.raise_for_status()
            info = r.json() if r.content else {}
            ext = (info.get("data") or {}).get("extract_result")
            if isinstance(ext, list):
                for item in ext:
                    if str(item.get("file_name") or "").strip() == target_name:
                        state = (item.get("state") or "").lower()
                        if state == "done":
                            full_zip_url = item.get("full_zip_url")
                            break
                        if state == "failed":
                            raise RuntimeError(f"远程解析失败: {item.get('err_msg')}")
                if full_zip_url:
                    break
            time.sleep(interval_s)
            elapsed += interval_s
        if not full_zip_url:
            raise RuntimeError("等待远程结果超时或无 zip 地址")

        # 4) 下载 zip 并解压到 artifacts_dir
        if artifacts_dir:
            base = Path(artifacts_dir).resolve()
            base.mkdir(parents=True, exist_ok=True)
            # 统一落地到根目录（覆盖）
            saved_dir = base
            saved_dir.mkdir(parents=True, exist_ok=True)
        # 下载
        zr = sess.get(full_zip_url, timeout=600)
        zr.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(zr.content)) as zf:
            if saved_dir is None:
                # 若未指定 artifacts_dir，则用临时目录，仅用于解析出 md 与 detail
                tmp = tempfile.TemporaryDirectory(prefix="mineru_zip_")
                saved_dir = Path(tmp.name)
            zf.extractall(str(saved_dir))

        # 5) 读取 md 与 detail
        md_text = self._read_first_markdown(saved_dir) or ""
        detail = self._read_first_detail(saved_dir) or []
        duration_ms = (time.perf_counter() - start) * 1000.0
        # 页数估计：以页段长度为 success_count；total_pages 取本地探测
        total_pages = self._local_total_pages(pdf_path)
        success_count = 0
        if page_range and isinstance(page_range, tuple):
            s, e = int(page_range[0]), int(page_range[1])
            success_count = max(0, e - s + 1)
        elif total_pages and total_pages > 0:
            success_count = total_pages
        result: Dict[str, Any] = {
            "code": 200,
            "message": "ok",
            "duration": int(duration_ms),
            "result": {
                "markdown": md_text,
                "detail": detail or [],
                "total_page_number": int(total_pages or 0),
                "success_count": int(success_count or 0),
            },
            "metrics": [
                {"trace_id": "mineru-api-batch", "request_id": "N/A", "warnings": [], "duration": int(duration_ms)}
            ],
        }
        if saved_dir is not None:
            result["artifacts_dir"] = str(saved_dir)
            # 确保生成区域标注 PDF
            try:
                self._ensure_regions_pdf(Path(saved_dir), pdf_path, page_range)
            except Exception:
                pass
        return result

    # ---- 辅助：生成/查找区域识别 PDF ----
    def _ensure_regions_pdf(self, base_dir: Path, input_pdf_path: str, page_range: Optional[Tuple[int, int]]) -> Optional[str]:
        # 已存在的候选
        candidates = []
        for pat in ("*layout*.pdf", "*annotated*.pdf", "*region*.pdf", "regions.pdf"):
            candidates.extend(list(base_dir.rglob(pat)))
        if candidates:
            return str(sorted(candidates, key=lambda p: p.name.lower())[0])

        # 源 PDF（优先 *_origin.pdf）
        origin = None
        origin_cands = list(base_dir.rglob("*_origin.pdf")) + list(base_dir.rglob("origin.pdf"))
        if origin_cands:
            origin = str(sorted(origin_cands, key=lambda p: p.name.lower())[0])
        else:
            origin = input_pdf_path

        # 解析 layout json / 其他 json 中的 bbox
        layout_json = None
        for pat in ("layout.json", "*_layout.json", "*_middle.json", "*_content_list.json"):
            found = list(base_dir.rglob(pat))
            if found:
                layout_json = found[0]
                break
        if layout_json is None:
            return None

        try:
            import json as _json
            import fitz  # type: ignore
            with open(layout_json, "r", encoding="utf-8", errors="ignore") as f:
                data = _json.load(f)
            # 统一抽取：返回 {page_id: [ [x0,y0,x1,y1], ... ]}
            def collect_bboxes(obj) -> dict[int, list[list[float]]]:
                boxes: dict[int, list[list[float]]] = {}
                if isinstance(obj, dict):
                    pages = None
                    if isinstance(obj.get("pages"), list):
                        pages = obj["pages"]
                    elif isinstance(obj.get("data"), dict) and isinstance(obj["data"].get("pages"), list):
                        pages = obj["data"]["pages"]
                    if pages is not None:
                        for idx, page in enumerate(pages):
                            page_id = page.get("page") if isinstance(page, dict) else None
                            page_id = int(page_id) if isinstance(page_id, int) and page_id > 0 else (idx + 1)
                            blocks = None
                            if isinstance(page, dict):
                                for key in ("blocks", "elements", "items"):
                                    val = page.get(key)
                                    if isinstance(val, list):
                                        blocks = val
                                        break
                            if not isinstance(blocks, list):
                                continue
                            for b in blocks:
                                if not isinstance(b, dict):
                                    continue
                                bb = b.get("bbox") or b.get("position")
                                if isinstance(bb, list) and len(bb) >= 4:
                                    boxes.setdefault(page_id, []).append([float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])])
                    elif isinstance(obj, list):
                        for it in obj:
                            if not isinstance(it, dict):
                                continue
                            typ = str(it.get("type") or "").lower()
                            if typ not in ("text", "image", "table", "block", "region"):
                                continue
                            page_id = int(it.get("page_idx", 0)) + 1
                            bb = it.get("bbox") or it.get("position")
                            if isinstance(bb, list) and len(bb) >= 4:
                                boxes.setdefault(page_id, []).append([float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])])
                return boxes

            boxes = collect_bboxes(data)
            if not boxes:
                return None

            doc = fitz.open(origin)
            # 若有页段，仅处理对应页（坐标默认以 PDF 像素为准；不同来源可能需要缩放，这里按原值绘制）
            out_path = base_dir / "regions.pdf"
            new = fitz.open()
            s, e = 1, doc.page_count
            if page_range and isinstance(page_range, tuple):
                s, e = int(page_range[0]), min(int(page_range[1]), doc.page_count)
            for pno in range(s, e + 1):
                page = doc.load_page(pno - 1)
                # 克隆页到新文档
                new.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1)
                npg = new.load_page(new.page_count - 1)
                for bb in boxes.get(pno, []):
                    try:
                        rect = fitz.Rect(bb[0], bb[1], bb[2], bb[3])
                        npg.draw_rect(rect, color=(1, 0, 0), width=0.8)
                    except Exception:
                        continue
            new.save(str(out_path))
            new.close()
            doc.close()
            return str(out_path)
        except Exception:
            return None
    def _resolve_command(self) -> Tuple[list[str], str]:
        mineru_path = shutil.which(self.mineru_exe)
        if mineru_path:
            return [mineru_path], mineru_path
        py_path = shutil.which(self.python_exe)
        if py_path:
            return [py_path, "-m", "mineru"], py_path
        raise FileNotFoundError("未找到 MinerU 可执行程序或 Python 解释器（用于 python -m mineru）")

    def _read_first_markdown(self, out_dir: Path) -> Optional[str]:
        # 常见输出结构：out_dir 下或其子目录有 *.md
        md_files = list(out_dir.rglob("*.md"))
        if not md_files:
            return None
        try:
            # 读取第一个 MD 文件（按名称排序稳定）
            md_files = sorted(md_files, key=lambda p: p.name.lower())
            return md_files[0].read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    def _read_first_detail(self, out_dir: Path) -> Optional[list[dict]]:
        """尽力解析 MinerU 产出的 JSON 为 TextIn 兼容的 detail 列表。

        由于 MinerU 的 JSON 结构可能因版本/后端不同而变化，这里采用启发式：
        - 寻找第一个 .json 文件；
        - 支持常见字段：pages -> (blocks/elements/items)
        - 映射字段：text, page/page_id, bbox/position, type/role -> outline_level
        """
        try:
            # 优先选择更具结构信息的文件
            preferred_patterns = [
                "*_content_list.json",
                "*_middle.json",
                "*_model.json",
            ]
            selected: Optional[Path] = None
            for pat in preferred_patterns:
                cand = list(out_dir.rglob(pat))
                if cand:
                    cand = sorted(cand, key=lambda p: p.name.lower())
                    selected = cand[0]
                    break
            if selected is None:
                # 兜底：任意 json
                json_files = list(out_dir.rglob("*.json"))
                if not json_files:
                    return None
                json_files = sorted(json_files, key=lambda p: p.name.lower())
                selected = json_files[0]
            data = json.loads(selected.read_text(encoding="utf-8", errors="ignore"))

            details: list[dict] = []
            paragraph_id = 0

            if isinstance(data, dict) and isinstance(data.get("pages"), list):
                # 结构：{"pages": [{"blocks"|"elements"|"items": [...]}, ...]}
                pages = data["pages"]
                for page_index, page in enumerate(pages):
                    page_id = page.get("page") if isinstance(page, dict) else None
                    page_id = int(page_id) if isinstance(page_id, int) and page_id > 0 else (page_index + 1)
                    blocks = None
                    if isinstance(page, dict):
                        for key in ("blocks", "elements", "items"):
                            val = page.get(key)
                            if isinstance(val, list):
                                blocks = val
                                break
                    if not isinstance(blocks, list):
                        continue
                    for b in blocks:
                        if not isinstance(b, dict):
                            continue
                        text = (b.get("text") or b.get("content") or "").strip()
                        btype = (b.get("type") or b.get("category") or "").lower()
                        role = (b.get("role") or b.get("style") or "").lower()
                        bbox = b.get("bbox") or b.get("position") or []

                        if text:
                            paragraph_id += 1
                            outline_level = 0 if (btype == "title" or role == "title") else -1
                            details.append({
                                "outline_level": outline_level,
                                "text": text,
                                "page_id": page_id,
                                "paragraph_id": paragraph_id,
                                "type": "paragraph",
                                "sub_type": btype or None,
                                "position": bbox if isinstance(bbox, list) else [],
                            })
                        elif btype == "image":
                            details.append({
                                "outline_level": -1,
                                "text": "",
                                "page_id": page_id,
                                "paragraph_id": None,
                                "type": "image",
                                "sub_type": None,
                                "position": bbox if isinstance(bbox, list) else [],
                            })

            elif isinstance(data, list):
                # 结构：扁平 content_list（每条包含 type/text/page_idx/bbox/...）
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    typ = str(item.get("type") or "").lower()
                    page_id = int(item.get("page_idx", 0)) + 1
                    bbox = item.get("bbox") or []

                    if typ == "text":
                        txt = (item.get("text") or "").strip()
                        if not txt:
                            continue
                        paragraph_id += 1
                        level = item.get("text_level")
                        outline_level = 0 if isinstance(level, int) and level >= 1 else -1
                        details.append({
                            "outline_level": outline_level,
                            "text": txt,
                            "page_id": page_id,
                            "paragraph_id": paragraph_id,
                            "type": "paragraph",
                            "sub_type": None,
                            "position": bbox if isinstance(bbox, list) else [],
                        })
                    elif typ == "image":
                        img_path = item.get("img_path")
                        details.append({
                            "outline_level": -1,
                            "text": "",
                            "page_id": page_id,
                            "paragraph_id": None,
                            "type": "image",
                            "sub_type": None,
                            "position": bbox if isinstance(bbox, list) else [],
                            "image_url": str((out_dir / img_path).resolve()) if isinstance(img_path, str) else None,
                        })
                    elif typ == "table":
                        # 将表格映射为段落占位，保留标题并粗略去 HTML 标签
                        cap = " ".join(item.get("table_caption") or [])
                        body = item.get("table_body") or ""
                        # 粗糙去标签，仅保留单元格文本
                        body_text = re.sub(r"<[^>]+>", " ", body)
                        text = (cap + "\n" + body_text).strip()
                        if text:
                            paragraph_id += 1
                            details.append({
                                "outline_level": -1,
                                "text": f"[表] {text}",
                                "page_id": page_id,
                                "paragraph_id": paragraph_id,
                                "type": "paragraph",
                                "sub_type": "table",
                                "position": bbox if isinstance(bbox, list) else [],
                            })
            else:
                return None

            return details or None
        except Exception:
            return None

    def _write_pdf_range(self, src_pdf: str, dst_pdf: Path, start: int, end: int) -> bool:
        try:
            if start <= 0 or end < start:
                return False
            # 优先 pypdf
            try:
                from pypdf import PdfReader, PdfWriter
                r = PdfReader(src_pdf)
                w = PdfWriter()
                total = len(r.pages)
                s = max(1, start)
                e = min(end, total)
                for i in range(s - 1, e):
                    w.add_page(r.pages[i])
                with open(dst_pdf, "wb") as f:
                    w.write(f)
                return True
            except Exception:
                pass
            # 退回 PyPDF2
            try:
                from PyPDF2 import PdfReader, PdfWriter  # type: ignore
                r = PdfReader(src_pdf)
                w = PdfWriter()
                total = len(r.pages)
                s = max(1, start)
                e = min(end, total)
                for i in range(s - 1, e):
                    w.add_page(r.pages[i])
                with open(dst_pdf, "wb") as f:
                    w.write(f)
                return True
            except Exception:
                pass
            # 最后用 PyMuPDF
            try:
                import fitz  # type: ignore
                doc = fitz.open(src_pdf)
                s = max(1, start)
                e = min(end, doc.page_count)
                new = fitz.open()
                new.insert_pdf(doc, from_page=s - 1, to_page=e - 1)
                new.save(str(dst_pdf))
                new.close()
                doc.close()
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _run_with_spinner(self, cmd: list[str], cwd: Path, timeout_s: Optional[int], label: str):
        """以非阻塞动画的方式运行子进程：
        - 优先使用 tqdm 展示“旋转+耗时”，与上层 tqdm 完美兼容；
        - 若 tqdm 不可用，回退为最小化的终端打印旋转动画。
        """
        start_t = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stop = threading.Event()
        frames = "|/-\\"
        try:
            from tqdm import tqdm  # type: ignore
        except Exception:
            tqdm = None  # type: ignore

        if tqdm is not None:
            pbar = tqdm(total=0, desc=label, bar_format="{desc} {postfix}", leave=False)
            def spin_tqdm():
                idx = 0
                while not stop.is_set():
                    elapsed = int(time.perf_counter() - start_t)
                    mm = elapsed // 60
                    ss = elapsed % 60
                    try:
                        pbar.set_postfix_str(f"{frames[idx%4]}  {mm:02d}:{ss:02d}")
                        pbar.refresh()
                    except Exception:
                        pass
                    idx += 1
                    time.sleep(0.1)
            t = threading.Thread(target=spin_tqdm, daemon=True)
            t.start()
            try:
                stdout, stderr = proc.communicate()
            finally:
                stop.set()
                t.join(timeout=1.0)
                try:
                    pbar.close()
                except Exception:
                    pass
        else:
            # 回退：纯打印动画
            def spin_print():
                idx = 0
                while not stop.is_set():
                    elapsed = int(time.perf_counter() - start_t)
                    mm = elapsed // 60
                    ss = elapsed % 60
                    msg = f"\r{label} {frames[idx%4]}  {mm:02d}:{ss:02d}"
                    print(msg, end="", flush=True)
                    idx += 1
                    time.sleep(0.1)
            t = threading.Thread(target=spin_print, daemon=True)
            t.start()
            try:
                stdout, stderr = proc.communicate()
            finally:
                stop.set()
                t.join(timeout=1.0)
                try:
                    print("\r" + " " * 60 + "\r", end="", flush=True)
                except Exception:
                    pass
        return proc, stdout, stderr

    def _local_total_pages(self, pdf: str) -> int:
        try:
            try:
                from pypdf import PdfReader
                return len(PdfReader(pdf).pages)
            except Exception:
                pass
            try:
                from PyPDF2 import PdfReader
                return len(PdfReader(pdf).pages)
            except Exception:
                pass
            try:
                import fitz  # PyMuPDF
                return len(fitz.open(pdf))
            except Exception:
                pass
        except Exception:
            pass
        return -1

    def _cuda_available(self) -> bool:
        try:
            import torch  # type: ignore
            return bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        except Exception:
            return False


