# Ensure environment variables from .env are loaded early
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


