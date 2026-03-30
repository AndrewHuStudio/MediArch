$env:DEBUG = "false"
Set-Location "E:\MyPrograms\MediArch_System"

& "E:\my_envs\agent_env_2\python.exe" -m backend.api --port 8011
