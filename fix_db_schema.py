import sqlite3
import os

def migrate_schema():
    db_path = "agentic_trader.db"
    if not os.path.exists(db_path):
        print(f"❌ Database {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    tables = ["open_positions", "trades"]
    columns = [
        ("net_delta", "DECIMAL(10, 4)"),
        ("net_gamma", "DECIMAL(10, 4)"),
        ("net_theta", "DECIMAL(10, 4)"),
        ("net_vega", "DECIMAL(10, 4)")
    ]

    for table in tables:
        # Check existing columns
        cursor.execute(f"PRAGMA table_info({table})")
        existing_cols = [row[1] for row in cursor.fetchall()]
        
        for col_name, col_type in columns:
            if col_name not in existing_cols:
                try:
                    print(f"Adding column {col_name} to table {table}...")
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                except Exception as e:
                    print(f"⚠️ Error adding {col_name} to {table}: {e}")
            else:
                print(f"✅ Column {col_name} already exists in {table}.")

    conn.commit()
    conn.close()
    print("\n🎉 Schema Migration Complete!")

if __name__ == "__main__":
    migrate_schema()
