import json
import os
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# Cargar variables de entorno desde .env si existe
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

DB_FILE = os.path.join(os.path.dirname(__file__), "db.json")
DATABASE_URL = os.getenv("DATABASE_URL")

def get_pg_connection():
    """
    Retorna una conexión activa a la base de datos PostgreSQL en InsForge.
    Si DATABASE_URL no está configurada, retorna None.
    """
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"[Database] Error conectando a PostgreSQL: {e}")
        return None

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": []}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
