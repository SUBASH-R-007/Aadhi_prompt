import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv(override=True)

# Use SQLite for local development, Postgres if DATABASE_URL is provided (e.g., on Railway)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./projects.db")

# If using Railway Postgres, ensure the URL starts with 'postgresql://' instead of 'postgres://' for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def ensure_schema(engine, column_additions, indexes=None):
    """The project's small migration step: create_all makes missing tables but never adds columns to a
    table that already exists, so columns added later are added here ({table: {column: SQL type}}), and
    named indexes created when missing. Additive only: existing rows and columns are never changed.
    Returns the columns that were added."""
    from sqlalchemy import inspect, text
    added = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in column_additions.items():
            if table not in existing_tables:
                continue  # created by create_all with every column
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
                    added.append(f"{table}.{name}")
        for name, (table, cols) in (indexes or {}).items():
            if table in existing_tables or table in column_additions:
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({", ".join(cols)})'))
    return added

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
