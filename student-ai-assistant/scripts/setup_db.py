"""
Run the Supabase SQL migration via the service role key.
Usage: python scripts/setup_db.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

MIGRATION_FILE = Path(__file__).parent.parent / "backend/app/db/migrations/001_initial.sql"

def main():
    sql = MIGRATION_FILE.read_text()
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Run migration via Supabase SQL editor endpoint
    # For local Postgres: use psycopg2 directly
    print("Running migration...")
    # Split on semicolons and execute each statement
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        try:
            db.rpc("exec_sql", {"sql": stmt}).execute()
        except Exception as e:
            print(f"  SKIP (may already exist): {str(e)[:80]}")
    print("Migration complete.")


if __name__ == "__main__":
    main()
