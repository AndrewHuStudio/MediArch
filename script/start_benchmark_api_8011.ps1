$env:DEBUG = "false"
$env:REQUIRE_POSTGRES_PERSISTENCE = "false"
$env:CHECKPOINT_BACKEND = "sqlite"
$env:STORE_BACKEND = "sqlite"
$env:SESSION_STORE_BACKEND = "sqlite"
$env:SQLITE_CHECKPOINT_PATH = ".langgraph_api/benchmark_8011_checkpoints.db"
$env:SQLITE_STORE_PATH = ".langgraph_api/benchmark_8011_store.db"
$env:SQLITE_SESSION_STORE_PATH = ".langgraph_api/benchmark_8011_store.db"
Set-Location "E:\MyPrograms\MediArch_System"

& "E:\my_envs\agent_env_2\python.exe" -m backend.api --port 8011 --no-reload
key