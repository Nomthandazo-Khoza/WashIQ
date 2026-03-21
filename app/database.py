from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Always use the DB file in the project folder (not the current working directory).
PROJECT_DB_PATH = Path(__file__).resolve().parent.parent / "washiq.db"
RELATIVE_DB_PATH = Path.cwd() / "washiq.db"

# If you previously started the app from a different working directory, copy the old DB
# into the project DB location so existing data remains available.
if RELATIVE_DB_PATH.exists() and not PROJECT_DB_PATH.exists() and RELATIVE_DB_PATH != PROJECT_DB_PATH:
    PROJECT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_DB_PATH.write_bytes(RELATIVE_DB_PATH.read_bytes())

DATABASE_URL = f"sqlite:///{PROJECT_DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
