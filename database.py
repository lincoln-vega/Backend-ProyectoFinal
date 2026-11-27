"""
Capa de acceso a datos PostgreSQL (Supabase Session Pooler) con Pool LIFO sin bloqueo
global (LifoQueue): permite abrir conexiones SSL en paralelo sin bloquear hilos vecinos
y reutiliza siempre el socket más cálido (~0 ms getconn), manteniendo aislamiento
automático hacia db.json durante pytest.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import queue
import threading
import time
from dotenv import load_dotenv

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

# Cargar variables de entorno desde .env si existe
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

DB_FILE = os.path.join(os.path.dirname(__file__), "db.json")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

_MAX_IDLE_CONNS = 10
_IDLE_POOL: "queue.LifoQueue" = queue.LifoQueue(maxsize=_MAX_IDLE_CONNS)
_LAST_USED_AT: dict[int, float] = {}
_WARMED_UP = False
_WARM_LOCK = threading.Lock()


def is_pg_configured() -> bool:
    """Indica si hay una cadena PostgreSQL activa hacia Supabase (se desactiva durante pytest para no contaminar la BD real)."""
    if os.getenv("TESTING") == "1" or "PYTEST_CURRENT_TEST" in os.environ:
        return False
    if not DATABASE_URL or "insforge.app" in DATABASE_URL:
        return False
    return True


def _open_new_raw_conn():
    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=8,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )
    _LAST_USED_AT[id(conn)] = time.monotonic()
    return conn


def _return_raw_conn(raw_conn) -> None:
    if raw_conn is None:
        return
    cid = id(raw_conn)
    try:
        if raw_conn.closed != 0:
            _LAST_USED_AT.pop(cid, None)
            return
        if raw_conn.status != psycopg2.extensions.STATUS_READY:
            raw_conn.rollback()
        _LAST_USED_AT[cid] = time.monotonic()
        try:
            _IDLE_POOL.put_nowait(raw_conn)
        except queue.Full:
            _LAST_USED_AT.pop(cid, None)
            raw_conn.close()
    except Exception:
        _LAST_USED_AT.pop(cid, None)
        try:
            raw_conn.close()
        except Exception:
            pass


class _PooledConnectionProxy:
    """Envuelve una conexión PostgreSQL para devolverla al LifoQueue al llamar `conn.close()`."""

    __slots__ = ("_conn", "_returned")

    def __init__(self, conn):
        self._conn = conn
        self._returned = False

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    @property
    def closed(self):
        return self._conn.closed

    @property
    def autocommit(self):
        return self._conn.autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._conn.autocommit = value

    def close(self):
        if self._returned or self._conn is None:
            return
        self._returned = True
        _return_raw_conn(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None and self._conn.closed == 0:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        return False


def _is_conn_alive(raw_conn) -> bool:
    if raw_conn is None or raw_conn.closed != 0:
        return False
    last_t = _LAST_USED_AT.get(id(raw_conn), 0.0)
    if last_t and (time.monotonic() - last_t) < 45.0:
        return True
    try:
        with raw_conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        raw_conn.rollback()
        return True
    except Exception:
        return False


def warmup_pg_pool(target_conns: int = 6) -> None:
    """Pre-abre conexiones SSL en paralelo al iniciar FastAPI sin bloquear ningún hilo."""
    global _WARMED_UP
    if not is_pg_configured() or psycopg2 is None:
        return
    with _WARM_LOCK:
        if _WARMED_UP:
            return
        _WARMED_UP = True

    def _bg_warm():
        n = max(2, min(target_conns, 8))
        try:
            with ThreadPoolExecutor(max_workers=n) as ex:
                conns = list(ex.map(lambda _: _open_new_raw_conn(), range(n)))
            for c in conns:
                _return_raw_conn(c)
        except Exception as exc:
            print(f"[Database Warmup] Aviso: {exc}")

    threading.Thread(target=_bg_warm, daemon=True).start()


def get_pg_connection():
    """
    Obtiene al instante el socket cálido más reciente de la cola LIFO; si no hay libres,
    abre uno nuevo en paralelo sin bloquear a las demás peticiones.
    """
    if not is_pg_configured() or psycopg2 is None:
        return None
    try:
        while True:
            try:
                raw_conn = _IDLE_POOL.get_nowait()
            except queue.Empty:
                raw_conn = None
                break
            if _is_conn_alive(raw_conn):
                _LAST_USED_AT[id(raw_conn)] = time.monotonic()
                return _PooledConnectionProxy(raw_conn)
            _LAST_USED_AT.pop(id(raw_conn), None)
            try:
                raw_conn.close()
            except Exception:
                pass

        fresh = _open_new_raw_conn()
        return _PooledConnectionProxy(fresh)
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
        return {"connected": True, "engine": "supabase_postgresql_pooled"}
    except Exception as e:
        return {"connected": False, "engine": "supabase_postgresql_pooled", "error": str(e)}
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
