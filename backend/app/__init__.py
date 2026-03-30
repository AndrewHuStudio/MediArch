# Ensure environment variables from .env are loaded early
try:
    from backend.env_loader import load_dotenv
    load_dotenv()
except Exception:
    pass


