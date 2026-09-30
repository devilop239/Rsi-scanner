import sqlite3
import os

db_path = "dev.db"

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE subscribers ADD COLUMN custom_stoch_low FLOAT")
        conn.execute("ALTER TABLE subscribers ADD COLUMN custom_stoch_high FLOAT")
        conn.commit()
        print("Successfully added custom thresholds to subscribers.")
    except sqlite3.OperationalError as e:
        print(f"Migration error (might already exist): {e}")
    finally:
        conn.close()
else:
    print("dev.db not found.")
