"""
Router de Tareas Académicas, Entregas Individuales por Estudiante y Anuncios (Fase 4).
Respaldo real en Supabase PostgreSQL + Supabase Storage, con fallback offline en db.json para pytest.
"""
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from auth import require_role
from database import (
    fetch_all_as_dict,
    fetch_one_as_dict,
    get_pg_connection,
    load_db,
    save_db,
)
from routers.usuarios import parse_course_list

router_tareas = APIRouter(prefix="/tareas", tags=["Tareas Académicas"])
router_anuncios = APIRouter(prefix="/anuncios", tags=["Anuncios Académicos"])


# --- MODELOS PYDANTIC ---

class TareaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    id: Optional[str] = None
    curso_id: str = Field(..., validation_alias=AliasChoices("cursoId", "curso_id"))
    ciclo_id: Optional[str] = Field(None, validation_alias=AliasChoices("cicloId", "ciclo_id"))
    curso: str
    tipo: str = "Tarea Calificada"
    titulo: str
    descripcion: Optional[str] = ""
    fecha_limite: str = Field(..., validation_alias=AliasChoices("fechaLimite", "fecha_limite"))
    puntos: str = "20 pts"
    docente_id: Optional[str] = Field(None, validation_alias=AliasChoices("docenteId", "docente_id"))
    docente: str
    estado: str = "Por entregar"
    archivo_url: Optional[str] = Field(None, validation_alias=AliasChoices("archivoUrl", "archivo_url"))
    archivo_path: Optional[str] = Field(None, validation_alias=AliasChoices("archivoPath", "archivo_path"))
    archivo_nombre: Optional[str] = Field(None, validation_alias=AliasChoices("archivoNombre", "archivo_nombre"))


class TareaUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    estado: Optional[str] = None
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    fecha_limite: Optional[str] = Field(None, validation_alias=AliasChoices("fechaLimite", "fecha_limite"))
    puntos: Optional[str] = None
    archivo_url: Optional[str] = Field(None, validation_alias=AliasChoices("archivoUrl", "archivo_url"))
    archivo_path: Optional[str] = Field(None, validation_alias=AliasChoices("archivoPath", "archivo_path"))
    archivo_nombre: Optional[str] = Field(None, validation_alias=AliasChoices("archivoNombre", "archivo_nombre"))


class TareaEntregaInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    archivo_path: Optional[str] = Field(None, validation_alias=AliasChoices("archivoPath", "archivo_path"))
    archivo_url: str = Field(..., validation_alias=AliasChoices("archivoUrl", "archivo_url"))
    archivo_nombre: str = Field(..., validation_alias=AliasChoices("archivoNombre", "archivo_nombre"))
    tamano_bytes: int = Field(0, validation_alias=AliasChoices("tamanoBytes", "tamano_bytes"))
    comentario_alumno: Optional[str] = Field(
        "",
        validation_alias=AliasChoices("comentarioAlumno", "comentario_alumno", "comentario"),
    )


class CalificarEntregaInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    nota: Optional[float] = None
    comentario_docente: Optional[str] = Field(
        "",
        validation_alias=AliasChoices("comentarioDocente", "comentario_docente", "comentario"),
    )
    estado: str = "Calificado"


class AnuncioCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    id: Optional[str] = None
    curso_id: str = Field(..., validation_alias=AliasChoices("cursoId", "curso_id"))
    ciclo_id: Optional[str] = Field(None, validation_alias=AliasChoices("cicloId", "ciclo_id"))
    curso: str
    titulo: str
    contenido: str
    prioridad: str = "Normal"
    docente_id: Optional[str] = Field(None, validation_alias=AliasChoices("docenteId", "docente_id"))
    docente: str
    archivo_url: Optional[str] = Field(None, validation_alias=AliasChoices("archivoUrl", "archivo_url"))
    archivo_path: Optional[str] = Field(None, validation_alias=AliasChoices("archivoPath", "archivo_path"))
    archivo_nombre: Optional[str] = Field(None, validation_alias=AliasChoices("archivoNombre", "archivo_nombre"))


# --- HELPERS INTERNOS ---

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_entrega_row(row: Dict[str, Any]) -> Dict[str, Any]:
    nota_raw = row.get("nota")
    entregado_raw = row.get("entregado_en") or row.get("entregadoEn")
    calificado_raw = row.get("calificado_en") or row.get("calificadoEn")
    return {
        "id": row.get("id"),
        "tareaId": row.get("tarea_id") or row.get("tareaId"),
        "cursoId": row.get("curso_id") or row.get("cursoId"),
        "estudianteId": row.get("estudiante_id") or row.get("estudianteId"),
        "estudianteNombre": row.get("estudiante_nombre") or row.get("estudianteNombre") or "",
        "estudianteCorreo": row.get("estudiante_correo") or row.get("estudianteCorreo") or "",
        "archivoPath": row.get("archivo_path") or row.get("archivoPath"),
        "archivoUrl": row.get("archivo_url") or row.get("archivoUrl"),
        "archivoNombre": row.get("archivo_nombre") or row.get("archivoNombre"),
        "tamanoBytes": int(row.get("tamano_bytes") or row.get("tamanoBytes") or 0),
        "comentarioAlumno": (
            row.get("comentario_alumno")
            if row.get("comentario_alumno") is not None
            else (row.get("comentarioAlumno") or "")
        ),
        "estado": row.get("estado") or "Entregado",
        "nota": round(float(nota_raw), 2) if nota_raw is not None else None,
        "comentarioDocente": (
            row.get("comentario_docente")
            if row.get("comentario_docente") is not None
            else (row.get("comentarioDocente") or "")
        ),
        "entregadoEn": entregado_raw.isoformat() if hasattr(entregado_raw, "isoformat") else (entregado_raw or None),
        "calificadoEn": calificado_raw.isoformat() if hasattr(calificado_raw, "isoformat") else (calificado_raw or None),
    }


def _obtener_matriculados_curso_pg(cur, curso_id: str) -> List[Dict[str, str]]:
    """Resuelve en una sola conexión a todos los alumnos inscritos en un curso."""
    estudiantes_map: Dict[str, Dict[str, str]] = {}

    cur.execute(
        """
        SELECT id, nombres, apellidos, correo, "cursosInscritos"
        FROM users
        WHERE LOWER(rol) = 'estudiante' AND COALESCE(estado, 'Activo') = 'Activo';
        """
    )
    for u in fetch_all_as_dict(cur) or []:
        cursos = parse_course_list(u.get("cursosInscritos"))
        if curso_id in cursos:
            uid = str(u["id"])
            nombre = f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip() or u.get("correo", uid)
            estudiantes_map[uid] = {
                "estudianteId": uid,
                "nombre": nombre,
                "correo": u.get("correo") or "",
            }

    cur.execute(
        """
        SELECT m.estudiante_id, m.estudiante, m.correo, m."cursosInscritos", u.id AS resolved_uid
        FROM matriculas m
        LEFT JOIN users u ON LOWER(u.correo) = LOWER(m.correo);
        """
    )
    for m in fetch_all_as_dict(cur) or []:
        cursos = parse_course_list(m.get("cursosInscritos"))
        if curso_id in cursos:
            uid = str(m.get("estudiante_id") or m.get("resolved_uid") or "").strip()
            if uid and uid not in estudiantes_map:
                estudiantes_map[uid] = {
                    "estudianteId": uid,
                    "nombre": m.get("estudiante") or m.get("correo") or uid,
                    "correo": m.get("correo") or "",
                }

    return sorted(estudiantes_map.values(), key=lambda x: x["nombre"].lower())


def _obtener_matriculados_curso_json(db: Dict[str, Any], curso_id: str) -> List[Dict[str, str]]:
    estudiantes_map: Dict[str, Dict[str, str]] = {}
    users_by_email: Dict[str, Dict[str, Any]] = {}

    for u in db.get("users", []):
        correo_norm = str(u.get("correo") or "").strip().lower()
        if correo_norm:
            users_by_email[correo_norm] = u
        if str(u.get("rol") or "").strip().lower() != "estudiante":
            continue
        if str(u.get("estado") or "Activo") != "Activo":
            continue
        cursos = parse_course_list(u.get("cursosInscritos"))
        if curso_id in cursos and u.get("id"):
            uid = str(u["id"])
            nombre = f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip() or correo_norm or uid
            estudiantes_map[uid] = {
                "estudianteId": uid,
                "nombre": nombre,
                "correo": u.get("correo") or "",
            }

    for m in db.get("matriculas", []):
        cursos = parse_course_list(m.get("cursosInscritos"))
        if curso_id not in cursos:
            continue
        correo_norm = str(m.get("correo") or "").strip().lower()
        uid = str(m.get("estudianteId") or m.get("estudiante_id") or "").strip()
        if not uid and correo_norm in users_by_email:
            uid = str(users_by_email[correo_norm].get("id") or "")
        if uid and uid not in estudiantes_map:
            estudiantes_map[uid] = {
                "estudianteId": uid,
                "nombre": m.get("estudiante") or correo_norm or uid,
                "correo": m.get("correo") or "",
            }

    return sorted(estudiantes_map.values(), key=lambda x: x["nombre"].lower())


def _consolidar_lista_entregas(
    tarea_dict: Dict[str, Any],
    matriculados: List[Dict[str, str]],
    entregas_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Cruza la nómina oficial del curso con las filas reales de tarea_entregas."""
    entregas_by_est: Dict[str, Dict[str, Any]] = {}
    for raw in entregas_rows:
        fmt = _format_entrega_row(raw)
        est_id = str(fmt.get("estudianteId") or "").strip()
        if est_id:
            entregas_by_est[est_id] = fmt

    estudiantes_final: List[Dict[str, Any]] = []
    vistos = set()

    for m in matriculados:
        eid = str(m["estudianteId"])
        vistos.add(eid)
        ent = entregas_by_est.get(eid)
        if ent:
            estudiantes_final.append({
                "estudianteId": eid,
                "estudianteNombre": m["nombre"] or ent["estudianteNombre"],
                "estudianteCorreo": m["correo"] or ent["estudianteCorreo"],
                "entregado": bool(ent.get("archivoUrl") or ent.get("archivoNombre") or ent.get("entregadoEn")),
                "entregaId": ent["id"],
                "estado": ent["estado"],
                "archivoPath": ent["archivoPath"],
                "archivoUrl": ent["archivoUrl"],
                "archivoNombre": ent["archivoNombre"],
                "tamanoBytes": ent["tamanoBytes"],
                "comentarioAlumno": ent["comentarioAlumno"],
                "entregadoEn": ent["entregadoEn"],
                "nota": ent["nota"],
                "comentarioDocente": ent["comentarioDocente"],
                "calificadoEn": ent["calificadoEn"],
            })
        else:
            estudiantes_final.append({
                "estudianteId": eid,
                "estudianteNombre": m["nombre"],
                "estudianteCorreo": m["correo"],
                "entregado": False,
                "entregaId": None,
                "estado": "Sin entregar",
                "archivoPath": None,
                "archivoUrl": None,
                "archivoNombre": None,
                "tamanoBytes": 0,
                "comentarioAlumno": "",
                "entregadoEn": None,
                "nota": None,
                "comentarioDocente": "",
                "calificadoEn": None,
            })

    # Incluir cualquier entrega histórica o extra de un alumno que ya no esté en matriculados activos
    for eid, ent in entregas_by_est.items():
        if eid not in vistos:
            estudiantes_final.append({
                "estudianteId": eid,
                "estudianteNombre": ent["estudianteNombre"] or eid,
                "estudianteCorreo": ent["estudianteCorreo"] or "",
                "entregado": bool(ent.get("archivoUrl") or ent.get("archivoNombre") or ent.get("entregadoEn")),
                "entregaId": ent["id"],
                "estado": ent["estado"],
                "archivoPath": ent["archivoPath"],
                "archivoUrl": ent["archivoUrl"],
                "archivoNombre": ent["archivoNombre"],
                "tamanoBytes": ent["tamanoBytes"],
                "comentarioAlumno": ent["comentarioAlumno"],
                "entregadoEn": ent["entregadoEn"],
                "nota": ent["nota"],
                "comentarioDocente": ent["comentarioDocente"],
                "calificadoEn": ent["calificadoEn"],
            })

    # Ordenar: primero quienes ya entregaron (pendientes de calificar arriba), luego alfabético
    prioridad_estado = {"Entregado": 0, "Observado": 1, "Calificado": 2, "Sin entregar": 3}
    estudiantes_final.sort(
        key=lambda x: (prioridad_estado.get(x["estado"], 9), (x["estudianteNombre"] or "").lower())
    )

    total_entregados = sum(1 for e in estudiantes_final if e["entregado"])
    total_calificados = sum(1 for e in estudiantes_final if e["estado"] == "Calificado")
    total_pendientes_calificar = sum(
        1 for e in estudiantes_final if e["entregado"] and e["estado"] in ("Entregado", "Observado")
    )
    total_sin_entregar = sum(1 for e in estudiantes_final if not e["entregado"])

    return {
        "tarea": tarea_dict,
        "resumen": {
            "matriculados": len(estudiantes_final),
            "entregados": total_entregados,
            "calificados": total_calificados,
            "pendientesCalificar": total_pendientes_calificar,
            "sinEntregar": total_sin_entregar,
        },
        "estudiantes": estudiantes_final,
    }


# --- ENDPOINTS TAREAS Y ENTREGAS ---

@router_tareas.get("/")
def listar_tareas(curso_id: Optional[str] = None, ciclo_id: Optional[str] = None):
    """
    Retorna la lista de tareas académicas con conteos agregados de entregas en 1 sola consulta SQL.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT t.id, t.curso_id AS "cursoId", t.ciclo_id AS "cicloId", t.curso, t.tipo, t.titulo,
                           t.descripcion, t.fecha_limite AS "fechaLimite", t.puntos,
                           t.docente_id AS "docenteId", t.docente, t.estado,
                           t.archivo_url AS "archivoUrl", t.archivo_path AS "archivoPath",
                           t.archivo_nombre AS "archivoNombre", t.created_at AS "createdAt",
                           COALESCE(agg.entregas_count, 0) AS "entregasCount",
                           COALESCE(agg.calificadas_count, 0) AS "calificadasCount",
                           COALESCE(agg.pendientes_calificar_count, 0) AS "pendientesCalificarCount"
                    FROM tareas t
                    LEFT JOIN (
                        SELECT tarea_id,
                               COUNT(*) FILTER (WHERE archivo_url IS NOT NULL OR archivo_nombre IS NOT NULL)::int AS entregas_count,
                               COUNT(*) FILTER (WHERE estado = 'Calificado')::int AS calificadas_count,
                               COUNT(*) FILTER (
                                   WHERE (archivo_url IS NOT NULL OR archivo_nombre IS NOT NULL)
                                     AND estado IN ('Entregado', 'Observado')
                               )::int AS pendientes_calificar_count
                        FROM tarea_entregas
                        GROUP BY tarea_id
                    ) agg ON agg.tarea_id = t.id
                    WHERE 1=1
                """
                params: List[Any] = []
                if curso_id:
                    query += " AND t.curso_id = %s"
                    params.append(curso_id)
                if ciclo_id:
                    query += " AND t.ciclo_id = %s"
                    params.append(ciclo_id)
                query += " ORDER BY t.created_at DESC;"
                cur.execute(query, tuple(params))
                rows = fetch_all_as_dict(cur)
                if rows is not None and (len(rows) > 0 or curso_id or ciclo_id):
                    return rows
        except Exception as e:
            print(f"[Listar Tareas PG] Error: {e}")
        finally:
            conn.close()

    # Fallback db.json (modo offline / pytest)
    db = load_db()
    tareas = list(db.get("tareas", []))
    entregas = db.get("tareaEntregas", [])
    if curso_id:
        tareas = [t for t in tareas if t.get("curso_id") == curso_id or t.get("cursoId") == curso_id]
    if ciclo_id:
        tareas = [t for t in tareas if t.get("ciclo_id") == ciclo_id or t.get("cicloId") == ciclo_id]

    enriched = []
    for t in tareas:
        tid = t.get("id")
        mis_ent = [
            e for e in entregas
            if (e.get("tarea_id") == tid or e.get("tareaId") == tid)
        ]
        con_archivo = [e for e in mis_ent if e.get("archivo_url") or e.get("archivoUrl") or e.get("archivo_nombre") or e.get("archivoNombre")]
        calificadas = [e for e in mis_ent if e.get("estado") == "Calificado"]
        pendientes = [e for e in con_archivo if e.get("estado") in ("Entregado", "Observado")]
        enriched.append({
            **t,
            "entregasCount": len(con_archivo),
            "calificadasCount": len(calificadas),
            "pendientesCalificarCount": len(pendientes),
        })
    return enriched


@router_tareas.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def crear_tarea(
    tarea: TareaCreate,
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    """
    Crea una nueva tarea académica (con guía adjunta opcional) en PostgreSQL y db.json.
    """
    if str(current_user.get("rol", "")).casefold() == "docente":
        if current_user.get("id"):
            tarea.docente_id = current_user["id"]
        nombre_completo = f"{current_user.get('nombres', '')} {current_user.get('apellidos', '')}".strip()
        if nombre_completo:
            tarea.docente = nombre_completo

    tarea_id = tarea.id or f"TSK-{int(time.time() * 1000) % 1000000}"
    resolved_ciclo = tarea.ciclo_id

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                if not resolved_ciclo and tarea.curso_id:
                    cur.execute("SELECT ciclo_id FROM courses WHERE id = %s LIMIT 1;", (tarea.curso_id,))
                    c_row = fetch_one_as_dict(cur)
                    if c_row:
                        resolved_ciclo = c_row.get("ciclo_id")
                cur.execute(
                    """
                    INSERT INTO tareas (
                        id, curso_id, ciclo_id, curso, tipo, titulo, descripcion,
                        fecha_limite, puntos, docente_id, docente, estado,
                        archivo_url, archivo_path, archivo_nombre
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        tarea_id,
                        tarea.curso_id,
                        resolved_ciclo,
                        tarea.curso,
                        tarea.tipo,
                        tarea.titulo,
                        tarea.descripcion,
                        tarea.fecha_limite,
                        tarea.puntos,
                        tarea.docente_id,
                        tarea.docente,
                        "Por entregar",
                        tarea.archivo_url,
                        tarea.archivo_path,
                        tarea.archivo_nombre,
                    ),
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Crear Tarea PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error en base de datos: {e}")
        finally:
            conn.close()

    db = load_db()
    if "tareas" not in db:
        db["tareas"] = []
    if not resolved_ciclo and tarea.curso_id:
        for c in db.get("courses", []):
            if c.get("id") == tarea.curso_id:
                resolved_ciclo = c.get("cicloId") or c.get("ciclo_id")
                break

    nueva_tarea_dict = {
        "id": tarea_id,
        "cursoId": tarea.curso_id,
        "curso_id": tarea.curso_id,
        "cicloId": resolved_ciclo,
        "ciclo_id": resolved_ciclo,
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
        "estado": "Por entregar",
        "archivoUrl": tarea.archivo_url,
        "archivoPath": tarea.archivo_path,
        "archivoNombre": tarea.archivo_nombre,
        "entregasCount": 0,
        "calificadasCount": 0,
        "pendientesCalificarCount": 0,
    }
    db["tareas"].insert(0, nueva_tarea_dict)
    save_db(db)
    return nueva_tarea_dict


@router_tareas.get(
    "/mis-entregas",
    dependencies=[Depends(require_role(["estudiante", "docente", "admin"]))],
)
def listar_mis_entregas(
    curso_id: Optional[str] = None,
    ciclo_id: Optional[str] = None,
    current_user: dict = Depends(require_role(["estudiante", "docente", "admin"])),
):
    """
    Devuelve únicamente las entregas individuales, notas (0-20) y comentarios del alumno autenticado.
    """
    uid = str(current_user.get("id") or "").strip()
    correo = str(current_user.get("correo") or "").strip().lower()

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT te.*
                    FROM tarea_entregas te
                    LEFT JOIN tareas t ON t.id = te.tarea_id
                    WHERE (
                        te.estudiante_id = %s
                        OR (%s <> '' AND LOWER(COALESCE(te.estudiante_correo, '')) = %s)
                    )
                """
                params: List[Any] = [uid, correo, correo]
                if curso_id:
                    query += " AND te.curso_id = %s"
                    params.append(curso_id)
                if ciclo_id:
                    query += " AND t.ciclo_id = %s"
                    params.append(ciclo_id)
                query += " ORDER BY te.entregado_en DESC NULLS LAST;"
                cur.execute(query, tuple(params))
                rows = fetch_all_as_dict(cur) or []
                return [_format_entrega_row(r) for r in rows]
        except Exception as e:
            print(f"[Mis Entregas PG] Error: {e}")
        finally:
            conn.close()

    db = load_db()
    tareas_by_id = {str(t.get("id")): t for t in db.get("tareas", [])}
    entregas = []
    for raw in db.get("tareaEntregas", []):
        est_id = str(raw.get("estudiante_id") or raw.get("estudianteId") or "").strip()
        est_mail = str(raw.get("estudiante_correo") or raw.get("estudianteCorreo") or "").strip().lower()
        if est_id != uid and (not correo or est_mail != correo):
            continue
        cid = str(raw.get("curso_id") or raw.get("cursoId") or "")
        if curso_id and cid != curso_id:
            continue
        if ciclo_id:
            t_obj = tareas_by_id.get(str(raw.get("tarea_id") or raw.get("tareaId") or "")) or {}
            t_ciclo = t_obj.get("ciclo_id") or t_obj.get("cicloId")
            if t_ciclo != ciclo_id:
                continue
        entregas.append(_format_entrega_row(raw))
    return entregas


@router_tareas.post(
    "/{tarea_id}/entregar",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(["estudiante"]))],
)
def entregar_tarea(
    tarea_id: str,
    payload: TareaEntregaInput,
    current_user: dict = Depends(require_role(["estudiante"])),
):
    """
    Registra o reemplaza la entrega individual real del estudiante en tarea_entregas.
    Bloquea entregas en ciclos finalizados (Modo Historial) o si la entrega ya fue calificada.
    """
    archivo_url = (payload.archivo_url or "").strip()
    archivo_nombre = (payload.archivo_nombre or "").strip()
    if not archivo_url or not archivo_nombre:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes subir un archivo válido antes de confirmar la entrega.",
        )

    estudiante_id = str(current_user.get("id") or "").strip()
    if not estudiante_id:
        raise HTTPException(status_code=401, detail="No se pudo identificar al estudiante autenticado.")
    estudiante_nombre = (
        f"{current_user.get('nombres', '')} {current_user.get('apellidos', '')}".strip()
        or current_user.get("correo")
        or estudiante_id
    )
    estudiante_correo = str(current_user.get("correo") or "").strip()
    entrega_id = f"ENT-{int(time.time() * 1000) % 100000000}"

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.curso_id, COALESCE(t.ciclo_id, c.ciclo_id) AS resolved_ciclo_id,
                           ca.estado AS ciclo_estado
                    FROM tareas t
                    LEFT JOIN courses c ON c.id = t.curso_id
                    LEFT JOIN ciclos_academicos ca ON ca.id = COALESCE(t.ciclo_id, c.ciclo_id)
                    WHERE t.id = %s
                    LIMIT 1;
                    """,
                    (tarea_id,),
                )
                t_row = fetch_one_as_dict(cur)
                if not t_row:
                    raise HTTPException(status_code=404, detail="La tarea indicada no existe.")

                if str(t_row.get("ciclo_estado") or "").strip().lower() in ("finalizado", "cerrado", "concluido"):
                    raise HTTPException(
                        status_code=400,
                        detail="Este ciclo académico ya concluyó (Modo Historial). No se admiten entregas.",
                    )

                cur.execute(
                    "SELECT id, estado FROM tarea_entregas WHERE tarea_id = %s AND estudiante_id = %s LIMIT 1;",
                    (tarea_id, estudiante_id),
                )
                existing = fetch_one_as_dict(cur)
                if existing and existing.get("estado") == "Calificado":
                    raise HTTPException(
                        status_code=409,
                        detail="Tu entrega ya fue calificada por el docente y no puede modificarse.",
                    )

                cur.execute(
                    """
                    INSERT INTO tarea_entregas (
                        id, tarea_id, curso_id, estudiante_id, estudiante_nombre, estudiante_correo,
                        archivo_path, archivo_url, archivo_nombre, tamano_bytes, comentario_alumno,
                        estado, entregado_en
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Entregado', NOW())
                    ON CONFLICT (tarea_id, estudiante_id) DO UPDATE SET
                        estudiante_nombre = EXCLUDED.estudiante_nombre,
                        estudiante_correo = EXCLUDED.estudiante_correo,
                        archivo_path = EXCLUDED.archivo_path,
                        archivo_url = EXCLUDED.archivo_url,
                        archivo_nombre = EXCLUDED.archivo_nombre,
                        tamano_bytes = EXCLUDED.tamano_bytes,
                        comentario_alumno = EXCLUDED.comentario_alumno,
                        estado = 'Entregado',
                        entregado_en = NOW()
                    RETURNING *;
                    """,
                    (
                        entrega_id,
                        tarea_id,
                        t_row["curso_id"],
                        estudiante_id,
                        estudiante_nombre,
                        estudiante_correo,
                        payload.archivo_path,
                        archivo_url,
                        archivo_nombre,
                        max(0, int(payload.tamano_bytes or 0)),
                        (payload.comentario_alumno or "").strip(),
                    ),
                )
                saved = fetch_one_as_dict(cur)
                conn.commit()
                return _format_entrega_row(saved)
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Entregar Tarea PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"No se pudo registrar la entrega: {e}")
        finally:
            conn.close()

    # Fallback db.json (modo offline / pytest)
    db = load_db()
    tarea_obj = next((t for t in db.get("tareas", []) if t.get("id") == tarea_id), None)
    if not tarea_obj:
        raise HTTPException(status_code=404, detail="La tarea indicada no existe.")

    curso_id = str(tarea_obj.get("curso_id") or tarea_obj.get("cursoId") or "")
    resolved_ciclo_id = tarea_obj.get("ciclo_id") or tarea_obj.get("cicloId")
    if not resolved_ciclo_id and curso_id:
        curso_obj = next((c for c in db.get("courses", []) if c.get("id") == curso_id), None)
        if curso_obj:
            resolved_ciclo_id = curso_obj.get("ciclo_id") or curso_obj.get("cicloId")

    if resolved_ciclo_id:
        ciclos_list = db.get("ciclos_academicos") or db.get("ciclosAcademicos") or []
        ciclo_obj = next((ci for ci in ciclos_list if ci.get("id") == resolved_ciclo_id), None)
        if ciclo_obj and str(ciclo_obj.get("estado") or "").strip().lower() in ("finalizado", "cerrado", "concluido"):
            raise HTTPException(
                status_code=400,
                detail="Este ciclo académico ya concluyó (Modo Historial). No se admiten entregas.",
            )

    entregas_list = db.setdefault("tareaEntregas", [])
    existing_idx = next(
        (
            i for i, e in enumerate(entregas_list)
            if (e.get("tarea_id") == tarea_id or e.get("tareaId") == tarea_id)
            and (e.get("estudiante_id") == estudiante_id or e.get("estudianteId") == estudiante_id)
        ),
        None,
    )
    if existing_idx is not None and entregas_list[existing_idx].get("estado") == "Calificado":
        raise HTTPException(
            status_code=409,
            detail="Tu entrega ya fue calificada por el docente y no puede modificarse.",
        )

    now_str = _now_iso()
    prev = entregas_list[existing_idx] if existing_idx is not None else {}
    record = {
        "id": prev.get("id") or entrega_id,
        "tarea_id": tarea_id,
        "curso_id": curso_id,
        "estudiante_id": estudiante_id,
        "estudiante_nombre": estudiante_nombre,
        "estudiante_correo": estudiante_correo,
        "archivo_path": payload.archivo_path,
        "archivo_url": archivo_url,
        "archivo_nombre": archivo_nombre,
        "tamano_bytes": max(0, int(payload.tamano_bytes or 0)),
        "comentario_alumno": (payload.comentario_alumno or "").strip(),
        "estado": "Entregado",
        "nota": prev.get("nota"),
        "comentario_docente": prev.get("comentario_docente", ""),
        "entregado_en": now_str,
        "calificado_en": prev.get("calificado_en"),
    }
    if existing_idx is not None:
        entregas_list[existing_idx] = record
    else:
        entregas_list.insert(0, record)
    save_db(db)
    return _format_entrega_row(record)


@router_tareas.get(
    "/{tarea_id}/entregas",
    dependencies=[Depends(require_role(["admin", "docente"]))],
)
def listar_entregas_por_tarea(tarea_id: str):
    """
    Cruza los alumnos matriculados en el curso con las entregas recibidas para la bandeja de revisión docente.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, curso_id AS "cursoId", ciclo_id AS "cicloId", curso, tipo, titulo,
                           descripcion, fecha_limite AS "fechaLimite", puntos,
                           docente_id AS "docenteId", docente, estado,
                           archivo_url AS "archivoUrl", archivo_path AS "archivoPath",
                           archivo_nombre AS "archivoNombre", created_at AS "createdAt"
                    FROM tareas
                    WHERE id = %s
                    LIMIT 1;
                    """,
                    (tarea_id,),
                )
                tarea_row = fetch_one_as_dict(cur)
                if not tarea_row:
                    raise HTTPException(status_code=404, detail="La tarea indicada no existe.")

                matriculados = _obtener_matriculados_curso_pg(cur, tarea_row["cursoId"])
                cur.execute(
                    "SELECT * FROM tarea_entregas WHERE tarea_id = %s ORDER BY entregado_en DESC NULLS LAST;",
                    (tarea_id,),
                )
                entregas_rows = fetch_all_as_dict(cur) or []
                return _consolidar_lista_entregas(tarea_row, matriculados, entregas_rows)
        except HTTPException:
            raise
        except Exception as e:
            print(f"[Listar Entregas Tarea PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error consultando entregas: {e}")
        finally:
            conn.close()

    db = load_db()
    tarea_obj = next((t for t in db.get("tareas", []) if t.get("id") == tarea_id), None)
    if not tarea_obj:
        raise HTTPException(status_code=404, detail="La tarea indicada no existe.")
    curso_id = str(tarea_obj.get("curso_id") or tarea_obj.get("cursoId") or "")
    matriculados = _obtener_matriculados_curso_json(db, curso_id)
    entregas_rows = [
        e for e in db.get("tareaEntregas", [])
        if e.get("tarea_id") == tarea_id or e.get("tareaId") == tarea_id
    ]
    return _consolidar_lista_entregas(tarea_obj, matriculados, entregas_rows)


@router_tareas.patch(
    "/{tarea_id}/calificar/{estudiante_id}",
    dependencies=[Depends(require_role(["admin", "docente"]))],
)
def calificar_entrega_tarea(
    tarea_id: str,
    estudiante_id: str,
    payload: CalificarEntregaInput,
):
    """
    Califica (0 a 20) u observa la entrega individual de un estudiante con retroalimentación del docente.
    """
    estado_norm = (payload.estado or "Calificado").strip()
    if estado_norm not in ("Calificado", "Observado"):
        raise HTTPException(
            status_code=400,
            detail="El estado de revisión debe ser 'Calificado' u 'Observado'.",
        )

    nota_val: Optional[float] = None
    if payload.nota is not None:
        nota_val = round(float(payload.nota), 2)
        if nota_val < 0.0 or nota_val > 20.0:
            raise HTTPException(
                status_code=400,
                detail="La calificación vigesimal debe estar entre 0 y 20.",
            )
    elif estado_norm == "Calificado":
        raise HTTPException(
            status_code=400,
            detail="Debes ingresar una nota entre 0 y 20 para calificar la entrega.",
        )

    comentario_doc = (payload.comentario_docente or "").strip()

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tarea_entregas
                    SET nota = %s,
                        comentario_docente = %s,
                        estado = %s,
                        calificado_en = NOW()
                    WHERE tarea_id = %s AND estudiante_id = %s
                    RETURNING *;
                    """,
                    (nota_val, comentario_doc, estado_norm, tarea_id, estudiante_id),
                )
                updated = fetch_one_as_dict(cur)
                if not updated:
                    # Permitir calificar (ej. nota 0 por no presentar) si tarea y alumno existen en BD
                    cur.execute("SELECT id, curso_id FROM tareas WHERE id = %s LIMIT 1;", (tarea_id,))
                    t_row = fetch_one_as_dict(cur)
                    if not t_row:
                        raise HTTPException(status_code=404, detail="La tarea indicada no existe.")
                    cur.execute(
                        "SELECT id, nombres, apellidos, correo FROM users WHERE id = %s LIMIT 1;",
                        (estudiante_id,),
                    )
                    u_row = fetch_one_as_dict(cur)
                    if not u_row:
                        raise HTTPException(
                            status_code=404,
                            detail="No se encontró entrega ni estudiante con ese identificador.",
                        )
                    est_nom = f"{u_row.get('nombres', '')} {u_row.get('apellidos', '')}".strip() or estudiante_id
                    ent_id = f"ENT-{int(time.time() * 1000) % 100000000}"
                    cur.execute(
                        """
                        INSERT INTO tarea_entregas (
                            id, tarea_id, curso_id, estudiante_id, estudiante_nombre, estudiante_correo,
                            estado, nota, comentario_docente, entregado_en, calificado_en
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NOW())
                        RETURNING *;
                        """,
                        (
                            ent_id,
                            tarea_id,
                            t_row["curso_id"],
                            estudiante_id,
                            est_nom,
                            u_row.get("correo") or "",
                            estado_norm,
                            nota_val,
                            comentario_doc,
                        ),
                    )
                    updated = fetch_one_as_dict(cur)

                conn.commit()
                return _format_entrega_row(updated)
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Calificar Entrega PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"No se pudo guardar la calificación: {e}")
        finally:
            conn.close()

    # Fallback db.json (modo offline / pytest)
    db = load_db()
    tarea_obj = next((t for t in db.get("tareas", []) if t.get("id") == tarea_id), None)
    if not tarea_obj:
        raise HTTPException(status_code=404, detail="La tarea indicada no existe.")

    entregas_list = db.setdefault("tareaEntregas", [])
    target = next(
        (
            e for e in entregas_list
            if (e.get("tarea_id") == tarea_id or e.get("tareaId") == tarea_id)
            and (e.get("estudiante_id") == estudiante_id or e.get("estudianteId") == estudiante_id)
        ),
        None,
    )
    now_str = _now_iso()
    if target is not None:
        target["nota"] = nota_val
        target["comentario_docente"] = comentario_doc
        target["estado"] = estado_norm
        target["calificado_en"] = now_str
        save_db(db)
        return _format_entrega_row(target)

    u_obj = next((u for u in db.get("users", []) if str(u.get("id")) == estudiante_id), None)
    if not u_obj:
        raise HTTPException(status_code=404, detail="No se encontró entrega ni estudiante con ese identificador.")

    est_nom = f"{u_obj.get('nombres', '')} {u_obj.get('apellidos', '')}".strip() or estudiante_id
    new_rec = {
        "id": f"ENT-{int(time.time() * 1000) % 100000000}",
        "tarea_id": tarea_id,
        "curso_id": str(tarea_obj.get("curso_id") or tarea_obj.get("cursoId") or ""),
        "estudiante_id": estudiante_id,
        "estudiante_nombre": est_nom,
        "estudiante_correo": u_obj.get("correo") or "",
        "archivo_path": None,
        "archivo_url": None,
        "archivo_nombre": None,
        "tamano_bytes": 0,
        "comentario_alumno": "",
        "estado": estado_norm,
        "nota": nota_val,
        "comentario_docente": comentario_doc,
        "entregado_en": None,
        "calificado_en": now_str,
    }
    entregas_list.insert(0, new_rec)
    save_db(db)
    return _format_entrega_row(new_rec)


@router_tareas.patch("/{tarea_id}", dependencies=[Depends(require_role(["admin", "docente"]))])
def actualizar_tarea(tarea_id: str, tarea_update: TareaUpdate):
    """
    Actualiza metadatos de una tarea (solo Admin o Docente; jamás modifica entregas individuales).
    """
    updates = tarea_update.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                set_clauses = []
                values = []
                for k, v in updates.items():
                    set_clauses.append(f"{k} = %s")
                    values.append(v)
                values.append(tarea_id)

                cur.execute(
                    f"""
                    UPDATE tareas
                    SET {", ".join(set_clauses)}
                    WHERE id = %s
                    RETURNING id, curso_id AS "cursoId", ciclo_id AS "cicloId", curso, tipo, titulo,
                              descripcion, fecha_limite AS "fechaLimite", puntos, docente_id AS "docenteId",
                              docente, estado, archivo_url AS "archivoUrl", archivo_path AS "archivoPath",
                              archivo_nombre AS "archivoNombre";
                    """,
                    values,
                )
                row = fetch_one_as_dict(cur)
                conn.commit()
                if row:
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
    Elimina una tarea y sus entregas asociadas.
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
    if "tareaEntregas" in db:
        db["tareaEntregas"] = [
            e for e in db["tareaEntregas"]
            if e.get("tarea_id") != tarea_id and e.get("tareaId") != tarea_id
        ]
    if len(db["tareas"]) < original_len or "tareaEntregas" in db:
        save_db(db)

    return {"mensaje": "Tarea eliminada exitosamente", "id": tarea_id}


# --- ENDPOINTS ANUNCIOS ---

@router_anuncios.get("/")
def listar_anuncios(curso_id: Optional[str] = None, ciclo_id: Optional[str] = None):
    """
    Retorna la lista de comunicados o anuncios académicos.
    """
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT id, curso_id AS "cursoId", ciclo_id AS "cicloId", curso, titulo,
                           contenido, prioridad, docente_id AS "docenteId", docente,
                           archivo_url AS "archivoUrl", archivo_path AS "archivoPath",
                           archivo_nombre AS "archivoNombre",
                           created_at AS "createdAt"
                    FROM anuncios
                    WHERE 1=1
                """
                params = []
                if curso_id:
                    query += " AND curso_id = %s"
                    params.append(curso_id)
                if ciclo_id:
                    query += " AND ciclo_id = %s"
                    params.append(ciclo_id)
                query += " ORDER BY created_at DESC;"
                cur.execute(query, tuple(params))
                rows = fetch_all_as_dict(cur)
                if rows is not None and (len(rows) > 0 or curso_id or ciclo_id):
                    return rows
        except Exception as e:
            print(f"[Listar Anuncios PG] Error: {e}")
        finally:
            conn.close()

    db = load_db()
    anuncios = db.get("anuncios", [])
    if curso_id:
        anuncios = [a for a in anuncios if a.get("curso_id") == curso_id or a.get("cursoId") == curso_id]
    if ciclo_id:
        anuncios = [a for a in anuncios if a.get("ciclo_id") == ciclo_id or a.get("cicloId") == ciclo_id]
    return anuncios


@router_anuncios.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def crear_anuncio(
    anuncio: AnuncioCreate,
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    """
    Publica un nuevo anuncio o comunicado académico con adjunto opcional.
    """
    if str(current_user.get("rol", "")).casefold() == "docente":
        if current_user.get("id"):
            anuncio.docente_id = current_user["id"]
        nombre_completo = f"{current_user.get('nombres', '')} {current_user.get('apellidos', '')}".strip()
        if nombre_completo:
            anuncio.docente = nombre_completo

    anuncio_id = anuncio.id or f"ANN-{int(time.time() * 1000) % 1000000}"

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                resolved_ciclo = anuncio.ciclo_id
                if not resolved_ciclo and anuncio.curso_id:
                    cur.execute("SELECT ciclo_id FROM courses WHERE id = %s LIMIT 1;", (anuncio.curso_id,))
                    c_row = fetch_one_as_dict(cur)
                    if c_row:
                        resolved_ciclo = c_row.get("ciclo_id")
                cur.execute(
                    """
                    INSERT INTO anuncios (
                        id, curso_id, ciclo_id, curso, titulo, contenido, prioridad,
                        docente_id, docente, archivo_url, archivo_path, archivo_nombre
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        anuncio_id,
                        anuncio.curso_id,
                        resolved_ciclo,
                        anuncio.curso,
                        anuncio.titulo,
                        anuncio.contenido,
                        anuncio.prioridad,
                        anuncio.docente_id,
                        anuncio.docente,
                        anuncio.archivo_url,
                        anuncio.archivo_path,
                        anuncio.archivo_nombre,
                    ),
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Crear Anuncio PG] Error: {e}")
            raise HTTPException(status_code=500, detail=f"Error en base de datos: {e}")
        finally:
            conn.close()

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
        "docente": anuncio.docente,
        "archivoUrl": anuncio.archivo_url,
        "archivoPath": anuncio.archivo_path,
        "archivoNombre": anuncio.archivo_nombre,
    }
    db["anuncios"].insert(0, nuevo_anuncio_dict)
    save_db(db)
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
