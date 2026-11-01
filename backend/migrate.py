from app.services.workflow_store import migrate


from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent


if __name__ == "__main__":
    load_dotenv(BACKEND_DIR / ".env", override=False)
    from app.services.workflow_store import migrate

    migrate()
    print("CIT Nexus PostgreSQL workflow migrations are applied.")
