import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel, Field, ConfigDict
from database import load_db, save_db, get_pg_connection
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/mensajes", tags=["Mensajería y Consultas Académicas"])

# --- MODELOS PYDANTIC ---

class MensajeCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: Optional[str] = None
    estudiante_id: str = Field(..., alias="estudianteId")
    estudiante_nombre: str = Field(..., alias="estudianteNombre")
    estudiante_correo: str = Field(..., alias="estudianteCorreo")
    estudiante_carrera: Optional[str] = Field("", alias="estudianteCarrera")
    docente_id: str = Field(..., alias="docenteId")
    docente_nombre: str = Field(..., alias="docenteNombre")
    curso_id: str = Field(..., alias="cursoId")
    curso: str
    mensaje: str
    fecha: Optional[str] = None
    estado: str = "Pendiente"

class RespuestaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    respuesta: str
    docente_nombre: Optional[str] = Field(None, alias="docenteNombre")

# --- ENDPOINTS ---

@router.get("/")
def listar_mensajes(
    docente_id: Optional[str] = Query(None, alias="docenteId"),
    estudiante_correo: Optional[str] = Query(None, alias="estudianteCorreo"),
    curso_id: Optional[str] = Query(None, alias="cursoId")
):
    """
    Retorna la lista de consultas o mensajes académicos.
    Permite filtrar opcionalmente por docente_id, estudiante_correo o curso_id.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM mensajes WHERE 1=1"
                params = []
                if docente_id:
                    query += " AND (docente_id = %s OR docente_id = 'USR-107')"
                    params.append(docente_id)
                if estudiante_correo:
                    query += " AND LOWER(estudiante_correo) = LOWER(%s)"
                    params.append(estudiante_correo.strip())
                if curso_id:
                    query += " AND curso_id = %s"
                    params.append(curso_id)

                query += " ORDER BY created_at DESC"
                cur.execute(query, tuple(params))
                mensajes = cur.fetchall()
                if mensajes:
                    return list(mensajes)
        except Exception as e:
            print(f"[Mensajes] Error al consultar PostgreSQL: {e}")
        finally:
            conn.close()

    # Fallback a db.json
    db = load_db()
    mensajes = db.get("mensajes", [])
    if docente_id:
        mensajes = [m for m in mensajes if m.get("docente_id") == docente_id or m.get("docenteId") == docente_id]
    if estudiante_correo:
        mensajes = [m for m in mensajes if (m.get("estudiante_correo") or m.get("estudianteCorreo") or "").lower() == estudiante_correo.lower()]
    if curso_id:
        mensajes = [m for m in mensajes if m.get("curso_id") == curso_id or m.get("cursoId") == curso_id]
    return mensajes


@router.post("/", status_code=status.HTTP_201_CREATED)
def enviar_mensaje(mensaje_data: MensajeCreate):
    """
    Registra una nueva consulta de un estudiante hacia un docente.
    Persiste en PostgreSQL y sincroniza con db.json.
    """
    clean_id = mensaje_data.id or f"MSG-{int(time.time() * 1000)}"
    fecha_actual = mensaje_data.fecha or f"Hoy, {time.strftime('%I:%M %p')}"

    payload = {
        "id": clean_id,
        "estudiante_id": mensaje_data.estudiante_id,
        "estudiante_nombre": mensaje_data.estudiante_nombre,
        "estudiante_correo": mensaje_data.estudiante_correo,
        "estudiante_carrera": mensaje_data.estudiante_carrera or "",
        "docente_id": mensaje_data.docente_id,
        "docente_nombre": mensaje_data.docente_nombre,
        "curso_id": mensaje_data.curso_id,
        "curso": mensaje_data.curso,
        "mensaje": mensaje_data.mensaje.strip(),
        "fecha": fecha_actual,
        "estado": "Pendiente",
        "respuesta": None,
        "fecha_respuesta": None
    }

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mensajes (
                        id, estudiante_id, estudiante_nombre, estudiante_correo,
                        estudiante_carrera, docente_id, docente_nombre, curso_id,
                        curso, mensaje, fecha, estado, respuesta, fecha_respuesta, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        mensaje = EXCLUDED.mensaje,
                        estado = EXCLUDED.estado;
                    """,
                    (
                        clean_id, payload["estudiante_id"], payload["estudiante_nombre"],
                        payload["estudiante_correo"], payload["estudiante_carrera"],
                        payload["docente_id"], payload["docente_nombre"], payload["curso_id"],
                        payload["curso"], payload["mensaje"], payload["fecha"],
                        payload["estado"], payload["respuesta"], payload["fecha_respuesta"]
                    )
                )
                conn.commit()
        except Exception as e:
            print(f"[Mensajes] Error al guardar en PostgreSQL: {e}")
        finally:
            conn.close()

    # Sincronizar con db.json
    try:
        db = load_db()
        if "mensajes" not in db:
            db["mensajes"] = []
        db["mensajes"] = [m for m in db["mensajes"] if m.get("id") != clean_id]
        db["mensajes"].insert(0, payload)
        save_db(db)
    except Exception as e:
        print(f"[Mensajes] Error al guardar en db.json: {e}")

    return payload


@router.patch("/{mensaje_id}/respuesta")
def responder_mensaje(mensaje_id: str, respuesta_data: RespuestaCreate):
    """
    Permite al docente responder una consulta y cambiar su estado a 'Respondido'.
    """
    fecha_respuesta = f"Hoy, {time.strftime('%I:%M %p')}"
    resp_texto = respuesta_data.respuesta.strip()
    doc_nombre = respuesta_data.docente_nombre

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                if doc_nombre:
                    cur.execute(
                        """
                        UPDATE mensajes
                        SET respuesta = %s, fecha_respuesta = %s, estado = 'Respondido', docente_nombre = %s
                        WHERE id = %s
                        RETURNING *;
                        """,
                        (resp_texto, fecha_respuesta, doc_nombre, mensaje_id)
                    )
                else:
                    cur.execute(
                        """
                        UPDATE mensajes
                        SET respuesta = %s, fecha_respuesta = %s, estado = 'Respondido'
                        WHERE id = %s
                        RETURNING *;
                        """,
                        (resp_texto, fecha_respuesta, mensaje_id)
                    )
                conn.commit()
        except Exception as e:
            print(f"[Mensajes] Error al actualizar respuesta en PostgreSQL: {e}")
        finally:
            conn.close()

    # Sincronizar en db.json
    try:
        db = load_db()
        for m in db.get("mensajes", []):
            if m.get("id") == mensaje_id:
                m["respuesta"] = resp_texto
                m["fecha_respuesta"] = fecha_respuesta
                m["estado"] = "Respondido"
                if doc_nombre:
                    m["docente_nombre"] = doc_nombre
                break
        save_db(db)
    except Exception as e:
        print(f"[Mensajes] Error actualizando db.json: {e}")

    return {"mensaje": "Respuesta enviada con éxito.", "id": mensaje_id, "estado": "Respondido"}
