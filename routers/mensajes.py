import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel, Field, ConfigDict
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict

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
    destinatario_tipo: Optional[str] = Field("docente", alias="destinatarioTipo")
    destinatario_id: Optional[str] = Field(None, alias="destinatarioId")
    destinatario_nombre: Optional[str] = Field(None, alias="destinatarioNombre")
    destinatario_correo: Optional[str] = Field(None, alias="destinatarioCorreo")

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
    d_id = docente_id if isinstance(docente_id, str) and docente_id.strip() else None
    e_correo = estudiante_correo if isinstance(estudiante_correo, str) and estudiante_correo.strip() else None
    c_id = curso_id if isinstance(curso_id, str) and curso_id.strip() else None

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = "SELECT * FROM mensajes WHERE 1=1"
                params = []
                if d_id:
                    query += " AND (docente_id = %s OR destinatario_id = %s)"
                    params.extend([d_id, d_id])
                if e_correo:
                    query += " AND (LOWER(estudiante_correo) = LOWER(%s) OR LOWER(COALESCE(destinatario_correo, '')) = LOWER(%s))"
                    params.extend([e_correo.strip(), e_correo.strip()])
                if c_id:
                    query += " AND curso_id = %s"
                    params.append(c_id)

                query += " ORDER BY created_at DESC"
                cur.execute(query, tuple(params))
                mensajes = fetch_all_as_dict(cur)
                if mensajes:
                    return list(mensajes)
        except Exception as e:
            print(f"[Mensajes] Error al consultar PostgreSQL: {e}")
        finally:
            conn.close()

    # Fallback a db.json
    db = load_db()
    mensajes = db.get("mensajes", [])
    if d_id:
        mensajes = [
            m for m in mensajes
            if m.get("docente_id") == d_id or m.get("docenteId") == d_id
            or m.get("destinatario_id") == d_id or m.get("destinatarioId") == d_id
        ]
    if e_correo:
        e_low = e_correo.lower()
        mensajes = [
            m for m in mensajes
            if (m.get("estudiante_correo") or m.get("estudianteCorreo") or "").lower() == e_low
            or (m.get("destinatario_correo") or m.get("destinatarioCorreo") or "").lower() == e_low
        ]
    if c_id:
        mensajes = [m for m in mensajes if m.get("curso_id") == c_id or m.get("cursoId") == c_id]
    return mensajes


@router.post("/", status_code=status.HTTP_201_CREATED)
def enviar_mensaje(mensaje_data: MensajeCreate):
    """
    Registra una nueva consulta de un estudiante hacia un docente o compañero.
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
        "fecha_respuesta": None,
        "destinatario_tipo": mensaje_data.destinatario_tipo or "docente",
        "destinatario_id": mensaje_data.destinatario_id or mensaje_data.docente_id,
        "destinatario_nombre": mensaje_data.destinatario_nombre or mensaje_data.docente_nombre,
        "destinatario_correo": mensaje_data.destinatario_correo or ""
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
                        curso, mensaje, fecha, estado, respuesta, fecha_respuesta,
                        destinatario_tipo, destinatario_id, destinatario_nombre, destinatario_correo,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        mensaje = EXCLUDED.mensaje,
                        estado = EXCLUDED.estado;
                    """,
                    (
                        clean_id, payload["estudiante_id"], payload["estudiante_nombre"],
                        payload["estudiante_correo"], payload["estudiante_carrera"],
                        payload["docente_id"], payload["docente_nombre"], payload["curso_id"],
                        payload["curso"], payload["mensaje"], payload["fecha"],
                        payload["estado"], payload["respuesta"], payload["fecha_respuesta"],
                        payload["destinatario_tipo"], payload["destinatario_id"],
                        payload["destinatario_nombre"], payload["destinatario_correo"]
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
