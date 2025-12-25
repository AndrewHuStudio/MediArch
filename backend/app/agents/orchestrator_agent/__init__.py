"""Orchestrator Agent - 导出 graph"""

from .agent import orchestrator_logic_graph

# 同时导出两个名称以兼容不同导入方式
graph = orchestrator_logic_graph

__all__ = ["graph", "orchestrator_logic_graph"]