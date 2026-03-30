import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_process.run_api import PollingAccessFilter


def _make_access_record(path: str, status_code: int) -> logging.LogRecord:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:50000", "GET", path, "1.1", status_code),
        exc_info=None,
    )
    return record


def test_polling_access_filter_suppresses_successful_task_polling():
    access_filter = PollingAccessFilter()

    assert access_filter.filter(_make_access_record("/data-process/tasks/abc123", 200)) is False
    assert access_filter.filter(_make_access_record("/data-process/kg/history", 200)) is False
    assert access_filter.filter(_make_access_record("/data-process/kg/strategies", 200)) is False


def test_polling_access_filter_keeps_errors_and_non_polling_routes():
    access_filter = PollingAccessFilter()

    assert access_filter.filter(_make_access_record("/data-process/tasks/abc123", 500)) is True
    assert access_filter.filter(_make_access_record("/data-process/kg/build", 200)) is True
