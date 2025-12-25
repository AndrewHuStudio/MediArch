import os
from functools import lru_cache
from typing import Optional, Any
from neo4j import GraphDatabase

@lru_cache(maxsize=1)
def get_neo4j_driver() -> Optional[Any]:
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    if not (uri and user and password):
        return None
    return GraphDatabase.driver(uri, auth=(user, password))


