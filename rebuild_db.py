import sqlite3
import os

DATABASE_NAME = 'word_cache.db'
SCHEMA_FILE = 'schema.sql'

def rebuild_database():
    """
    Rebuilds the database from the schema.sql file.
    Deletes the old database file if it exists.
    """
    if os.path.exists(DATABASE_NAME):
        os.remove(DATABASE_NAME)
        print(f"Removed old database: {DATABASE_NAME}")

    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()

        with open(SCHEMA_FILE, 'r', encoding='utf-8') as f:
            schema_script = f.read()
        
        cursor.executescript(schema_script)
        
        print(f"Successfully executed schema from: {SCHEMA_FILE}")

        conn.commit()
        conn.close()
        
        print("Database rebuilt successfully.")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
    except FileNotFoundError:
        print(f"Error: {SCHEMA_FILE} not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == '__main__':
    rebuild_database() 