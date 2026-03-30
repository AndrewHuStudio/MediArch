"""
data_process API 独立入口

启动: python -m data_process.run_api
或:   python data_process/run_api.py
"""

import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from backend.env_loader import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from data_process.api import router, _maybe_start_auto_kg_build_on_startup


class PollingAccessFilter(logging.Filter):
    """Suppress noisy successful access logs from frontend polling endpoints."""

    QUIET_PATH_PREFIXES = (
        "/data-process/tasks/",
        "/data-process/kg/history",
        "/data-process/kg/strategies",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args or ()
            path = str(args[2] if len(args) > 2 else "")
            status_code = int(args[4] if len(args) > 4 else 0)
        except Exception:
            return True

        if status_code < 400 and any(path.startswith(prefix) for prefix in self.QUIET_PATH_PREFIXES):
            return False
        return True


uvicorn_access_logger = logging.getLogger("uvicorn.access")
if not any(isinstance(f, PollingAccessFilter) for f in uvicorn_access_logger.filters):
    uvicorn_access_logger.addFilter(PollingAccessFilter())

app = FastAPI(
    title="MediArch Data Processing API",
    version="1.0.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root():
    return {"service": "MediArch Data Processing", "docs": "/docs"}


@app.on_event("startup")
async def startup_auto_kg_build():
    _maybe_start_auto_kg_build_on_startup()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "data_process.run_api:app",
        host="0.0.0.0",
        port=8011,
        reload=True,
    )
