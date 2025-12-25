"""
seed_ontology_v2.py 单元测试模块

测试范围:
1. FunctionalZone 写入 zone_type / is_physical

运行方式:
  pytest backend/databases/graph/tests/test_seed_ontology_v2.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 添加项目根目录到路径（必须在导入 backend 模块之前）
# 路径层级: test_seed_ontology_v2.py -> tests -> graph -> databases -> backend -> 项目根目录
project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.databases.graph.utils.seed_ontology_v2 import OntologySeederV2


@pytest.fixture
def mocked_neo4j_driver():
    session = MagicMock()
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    driver.session.return_value.__exit__.return_value = False
    return driver, session


def test_functional_zone_writes_zone_type_and_is_physical(tmp_path: Path, mocked_neo4j_driver):
    driver, session = mocked_neo4j_driver

    seed_data = {
        "_version": "test",
        "hospital": {"name": "综合医院"},
        "departments": [
            {
                "name": "急诊部",
                "functional_zones": [
                    {
                        "name": "公共区",
                        "is_physical": False,
                        "zone_type": "公共区",
                        "spaces": [{"name": "护士站"}],
                    }
                ],
            }
        ],
    }

    json_path = tmp_path / "ontology_seed_data.json"
    json_path.write_text(json.dumps(seed_data, ensure_ascii=False), encoding="utf-8")

    with patch("backend.databases.graph.utils.seed_ontology_v2.GraphDatabase.driver", return_value=driver):
        seeder = OntologySeederV2(json_path=json_path, dry_run=False, clear_existing=False)
        seeder.create_zone_nodes({"急诊部": "dept_id_001"})

    zone_calls = [
        call
        for call in session.run.call_args_list
        if call.args and "MERGE (z:FunctionalZone" in call.args[0]
    ]
    assert zone_calls, "Expected FunctionalZone MERGE query to be executed"

    zone_query = zone_calls[0].args[0]
    zone_kwargs = zone_calls[0].kwargs

    assert "z.is_physical = $is_physical" in zone_query
    assert "z.zone_type = $zone_type" in zone_query
    assert zone_kwargs["is_physical"] is False
    assert zone_kwargs["zone_type"] == "公共区"

