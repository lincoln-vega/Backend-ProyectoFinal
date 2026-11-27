import json
import os
from dotenv import load_dotenv

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# Cargar variables de entorno desde .env si existe
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

DB_FILE = os.path.join(os.path.dirname(__file__), "db.json")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def is_pg_configured() -> bool:
    """Indica si hay una cadena PostgreSQL activa hacia Supabase (se desactiva durante pytest para no contaminar la BD real)."""
    if os.getenv("TESTING") == "1" or "PYTEST_CURRENT_TEST" in os.environ:
        return False
    if not DATABASE_URL or "insforge.app" in DATABASE_URL:
        return False
    return True


def get_pg_connection():
    """
    Retorna una conexión activa a la base de datos PostgreSQL en Supabase.
    Si DATABASE_URL no está configurada (o apunta al antiguo InsForge), retorna None.
    """
    if not is_pg_configured() or psycopg2 is None:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=8)
        return conn
    except Exception as e:
        print(f"[CRITICAL - Database] Error conectando a PostgreSQL en Supabase: {e}")
        return None


def check_pg_health() -> dict:
    """Ejecuta una consulta ligera SELECT 1 para diagnóstico y keep-alive de Supabase Free."""
    conn = get_pg_connection()
    if not conn:
        return {"connected": False, "engine": "offline_fixture"}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok;")
            cur.fetchone()
        return {"connected": True, "engine": "supabase_postgresql"}
    except Exception as e:
        return {"connected": False, "engine": "supabase_postgresql", "error": str(e)}
    finally:
        conn.close()


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": []}


def save_db(data):
    """Solo persiste en db.json cuando se opera en modo fixture offline (p. ej. pytest sin PostgreSQL)."""
    if is_pg_configured():
        return
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def fetch_all_as_dict(cursor):
    """Convierte resultados de una consulta SQL a lista de diccionarios."""
    if not cursor or not cursor.description:
        return []
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def fetch_one_as_dict(cursor):
    """Convierte una fila de una consulta SQL a un diccionario."""
    if not cursor or not cursor.description:
        return None
    row = cursor.fetchone()
    if not row:
        return None
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))
