import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.databases.graph.utils.pre_build_checklist import PreBuildChecker


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_kg_regression_tests_returns_pytest_summary():
    checker = PreBuildChecker()
    test_files = [
        Path("backend/databases/graph/tests/test_kg_builder_write_metadata.py"),
        Path("backend/databases/graph/tests/test_kg_analyzer_current_model.py"),
    ]

    with patch(
        "backend.databases.graph.utils.pre_build_checklist.subprocess.run",
        return_value=_FakeCompletedProcess(
            returncode=0,
            stdout=".....                                                                    [100%]\n5 passed in 2.35s\n",
        ),
    ) as mocked_run:
        passed, detail = checker._run_kg_regression_tests(test_files)

    assert passed is True
    assert "5 passed" in detail
    assert "pytest" in " ".join(mocked_run.call_args.args[0])
