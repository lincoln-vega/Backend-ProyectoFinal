import time
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field, ConfigDict
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role

router_tareas = APIRouter(prefix="/tareas", tags=["Tareas Académicas"])
router_anuncios = APIRouter(prefix="/anuncios", tags=["Anuncios Académicos"])

# --- MODELOS PYDANTIC ---

class TareaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: Optional[str] = None
    curso_id: str = Field(..., alias="cursoId")
    curso: str
    tipo: str = "Tarea Calificada"
    titulo: str
    descripcion: Optional[str] = ""
    fecha_limite: str = Field(..., alias="fechaLimite")
    puntos: str = "20 pts"
    docente_id: Optional[str] = Field(None, alias="docenteId")
    docente: str
    estado: str = "Por entregar"

class TareaUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    estado: Optional[str] = None
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    fecha_limite: Optional[str] = Field(None, alias="fechaLimite")
    puntos: Optional[str] = None

class AnuncioCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: Optional[str] = None
    curso_id: str = Field(..., alias="cursoId")
    curso: str
    titulo: str
    contenido: str
    prioridad: str = "Normal"
    docente_id: Optional[str] = Field(None, alias="docenteId")
    docente: str

# --- ENDPOINTS TAREAS ---

@router_tareas.get("/")
def listar_tareas(curso_id: Optional[str] = None):
    """
    Retorna la lista de tareas académicas.
    Permite filtrar por curso_id opcionalmente.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                if curso_id:
                    cur.execute("""
                        SELECT id, curso_id AS "cursoId", curso, tipo, titulo, descripcion,
                               fecha_limite AS "fechaLimite", puntos, docente_id AS "docenteId",
                               docente, estado, created_at AS "createdAt"
                        FROM tareas
                        WHERE curso_id = %s
                        ORDER BY created_at DESC;
                    """, (curso_id,))
                else:
                    cur.execute("""
                        SELECT id, curso_id AS "cursoId", curso, tipo, titulo, descripcion,
                               fecha_limite AS "fechaLimite", puntos, docente_id AS "docenteId",
                               docente, estado, created_at AS "createdAt"
                        FROM tareas
                        ORDER BY created_at DESC;
                    """)
                rows = fetch_all_as_dict(cur)
                if rows:
                    return rows
        except Exception as e:
            print(f"[Listar Tareas PG] Error: {e}")
        finally:
            conn.close()

    # Fallback db.json
    db = load_db()
    tareas = db.get("tareas", [])
    if curso_id:
        return [t for t in tareas if t.get("curso_id") == curso_id or t.get("cursoId") == curso_id]
    return tareas

@router_tareas.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def crear_tarea(tarea: TareaCreate):
    """
    Crea una nueva tarea académica tanto en PostgreSQL como en db.json.
    """
    tarea_id = tarea.id or f"TSK-{int(time.time() * 1000) % 1000000}"
    
    # 1. Guardar en PostgreSQL
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tareas (id, curso_id, curso, tipo, titulo, descripcion, fecha_limite, puntos, docente_id, docente, estado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    tarea_id,
                    tarea.curso_id,
                    tarea.curso,
                    tarea.tipo,
                    tarea.titulo,
                    tarea.descripcion,
                    tarea.fecha_limite,
                    tarea.puntos,
                    tarea.docente_id,
                    tarea.docente,
                    tarea.estado
                ))
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Crear Tarea PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error en base de datos: {e}")
        finally:
            conn.close()

    # 2. Sincronizar en db.json
    db = load_db()
    if "tareas" not in db:
        db["tareas"] = []

    nueva_tarea_dict = {
        "id": tarea_id,
        "cursoId": tarea.curso_id,
        "curso_id": tarea.curso_id,
        "curso": tarea.curso,
        "tipo": tarea.tipo,
        "titulo": tarea.titulo,
        "descripcion": tarea.descripcion,
        "fechaLimite": tarea.fecha_limite,
        "fecha_limite": tarea.fecha_limite,
        "puntos": tarea.puntos,
        "docenteId": tarea.docente_id,
        "docente_id": tarea.docente_id,
        "docente": tarea.docente,
        "estado": tarea.estado
    }
    db["tareas"].insert(0, nueva_tarea_dict)
    save_db(db)
    print(f"[OK - CREAR TAREA] Tarea '{tarea.titulo}' (ID: {tarea_id}, Curso: '{tarea.curso}') creada correctamente por '{tarea.docente}'.")

    return nueva_tarea_dict

@router_tareas.patch("/{tarea_id}", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def actualizar_tarea(tarea_id: str, tarea_update: TareaUpdate):
    """
    Actualiza el estado u otros campos de una tarea.
    """
    updates = tarea_update.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")

    # 1. Actualizar en PostgreSQL
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                set_clauses = []
                values = []
                for k, v in updates.items():
                    col = "fecha_limite" if k == "fecha_limite" else k
                    set_clauses.append(f"{col} = %s")
                    values.append(v)
                values.append(tarea_id)

                cur.execute(f"""
                    UPDATE tareas
                    SET {", ".join(set_clauses)}
                    WHERE id = %s
                    RETURNING id, curso_id AS "cursoId", curso, tipo, titulo, descripcion,
                              fecha_limite AS "fechaLimite", puntos, docente_id AS "docenteId",
                              docente, estado;
                """, values)
                row = fetch_one_as_dict(cur)
                conn.commit()
                if row:
                    # Sincronizar en db.json
                    db = load_db()
                    for t in db.get("tareas", []):
                        if t.get("id") == tarea_id:
                            t.update(updates)
                            if "fecha_limite" in updates:
                                t["fechaLimite"] = updates["fecha_limite"]
                            break
                    save_db(db)
                    return row
        except Exception as e:
            conn.rollback()
            print(f"[Actualizar Tarea PG] Error: {e}")
        finally:
            conn.close()

    # 2. Fallback db.json
    db = load_db()
    for t in db.get("tareas", []):
        if t.get("id") == tarea_id:
            t.update(updates)
            if "fecha_limite" in updates:
                t["fechaLimite"] = updates["fecha_limite"]
            save_db(db)
            return t

    raise HTTPException(status_code=404, detail="Tarea no encontrada")

@router_tareas.delete("/{tarea_id}", dependencies=[Depends(require_role(["admin", "docente"]))])
def eliminar_tarea(tarea_id: str):
    """
    Elimina una tarea por su ID.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tareas WHERE id = %s;", (tarea_id,))
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Eliminar Tarea PG] Error: {e}")
        finally:
            conn.close()

    db = load_db()
    original_len = len(db.get("tareas", []))
    db["tareas"] = [t for t in db.get("tareas", []) if t.get("id") != tarea_id]
    if len(db["tareas"]) < original_len:
        save_db(db)

    return {"mensaje": "Tarea eliminada exitosamente", "id": tarea_id}

# --- ENDPOINTS ANUNCIOS ---

@router_anuncios.get("/")
def listar_anuncios(curso_id: Optional[str] = None):
    """
    Retorna la lista de comunicados o anuncios académicos.
    Permite filtrar por curso_id opcionalmente.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                if curso_id:
                    cur.execute("""
                        SELECT id, curso_id AS "cursoId", curso, titulo, contenido, prioridad,
                               docente_id AS "docenteId", docente, created_at AS "createdAt"
                        FROM anuncios
                        WHERE curso_id = %s
                        ORDER BY created_at DESC;
                    """, (curso_id,))
                else:
                    cur.execute("""
                        SELECT id, curso_id AS "cursoId", curso, titulo, contenido, prioridad,
                               docente_id AS "docenteId", docente, created_at AS "createdAt"
                        FROM anuncios
                        ORDER BY created_at DESC;
                    """)
                rows = fetch_all_as_dict(cur)
                if rows:
                    return rows
        except Exception as e:
            print(f"[Listar Anuncios PG] Error: {e}")
        finally:
            conn.close()

    # Fallback db.json
    db = load_db()
    anuncios = db.get("anuncios", [])
    if curso_id:
        return [a for a in anuncios if a.get("curso_id") == curso_id or a.get("cursoId") == curso_id]
    return anuncios

@router_anuncios.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def crear_anuncio(anuncio: AnuncioCreate):
    """
    Publica un nuevo anuncio o comunicado académico.
    """
    anuncio_id = anuncio.id or f"ANN-{int(time.time() * 1000) % 1000000}"

    # 1. Guardar en PostgreSQL
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO anuncios (id, curso_id, curso, titulo, contenido, prioridad, docente_id, docente)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    anuncio_id,
                    anuncio.curso_id,
                    anuncio.curso,
                    anuncio.titulo,
                    anuncio.contenido,
                    anuncio.prioridad,
                    anuncio.docente_id,
                    anuncio.docente
                ))
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Crear Anuncio PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error en base de datos: {e}")
        finally:
            conn.close()

    # 2. Sincronizar en db.json
    db = load_db()
    if "anuncios" not in db:
        db["anuncios"] = []

    nuevo_anuncio_dict = {
        "id": anuncio_id,
        "cursoId": anuncio.curso_id,
        "curso_id": anuncio.curso_id,
        "curso": anuncio.curso,
        "titulo": anuncio.titulo,
        "contenido": anuncio.contenido,
        "prioridad": anuncio.prioridad,
        "docenteId": anuncio.docente_id,
        "docente_id": anuncio.docente_id,
        "docente": anuncio.docente
    }
    db["anuncios"].insert(0, nuevo_anuncio_dict)
    save_db(db)
    print(f"[OK - CREAR ANUNCIO] Anuncio '{anuncio.titulo}' (ID: {anuncio_id}, Curso: '{anuncio.curso}') publicado correctamente por '{anuncio.docente}'.")

    return nuevo_anuncio_dict

@router_anuncios.delete("/{anuncio_id}", dependencies=[Depends(require_role(["admin", "docente"]))])
def eliminar_anuncio(anuncio_id: str):
    """
    Elimina un anuncio por su ID.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM anuncios WHERE id = %s;", (anuncio_id,))
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Eliminar Anuncio PG] Error: {e}")
        finally:
            conn.close()

    db = load_db()
    original_len = len(db.get("anuncios", []))
    db["anuncios"] = [a for a in db.get("anuncios", []) if a.get("id") != anuncio_id]
    if len(db["anuncios"]) < original_len:
        save_db(db)

    return {"mensaje": "Anuncio eliminado exitosamente", "id": anuncio_id}
