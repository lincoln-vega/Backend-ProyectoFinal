"""
Motor de Clases en Vivo por Google Meet y Asistencia Híbrida (Fase 3).
Persistencia de estado en servidor (PostgreSQL + fallback offline db.json para pytest),
tope contractual de horas por bloque, auto-cierre ante olvido (+20 min) y validación
silenciosa de permanencia en 2.º plano para estudiantes.
"""
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from auth import get_current_user, require_role
from database import (
    fetch_all_as_dict,
    fetch_one_as_dict,
    get_pg_connection,
    load_db,
    save_db,
)
from routers.usuarios import parse_course_list

router = APIRouter(prefix="/clases-vivo", tags=["Clases en Vivo y Asistencia Híbrida"])

LIMA_TZ = timezone(timedelta(hours=-5))
GRACIA_AUTOCIERRE_MIN = 20
TOLERANCIA_ALUMNO_MIN = 10
MIN_PERMANENCIA_ESTANDAR = 15

ESTADOS_ASISTENCIA_VALIDOS = {"Presente", "Tardanza", "Ausente", "Justificado"}


# --- MODELOS PYDANTIC ---

class IniciarClaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    curso_id: str = Field(..., validation_alias=AliasChoices("cursoId", "curso_id"))


class LatidoRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    ventana_abierta: bool = Field(
        default=True,
        validation_alias=AliasChoices("ventanaAbierta", "ventana_abierta"),
    )


class ActualizarAsistenciaAlumnoRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    estado: str
    observacion: Optional[str] = ""


# --- UTILIDADES DE TIEMPO Y REGLAS DE NEGOCIO ---

def _now_lima() -> datetime:
    return datetime.now(LIMA_TZ)


def _to_lima(dt_val: Any) -> datetime:
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=LIMA_TZ)
        return dt_val.astimezone(LIMA_TZ)
    if isinstance(dt_val, str) and dt_val.strip():
        raw = dt_val.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=LIMA_TZ)
            return parsed.astimezone(LIMA_TZ)
        except ValueError:
            pass
    return _now_lima()


def _parse_hhmm(hhmm: Optional[str], default: Tuple[int, int] = (8, 0)) -> Tuple[int, int]:
    if not hhmm:
        return default
    parts = str(hhmm).strip().split(":")
    try:
        h = max(0, min(23, int(parts[0])))
        m = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
        return h, m
    except (ValueError, IndexError):
        return default


def _calcular_bloque_programado_min(hora_inicio: Optional[str], hora_fin: Optional[str]) -> int:
    h1, m1 = _parse_hhmm(hora_inicio, (8, 0))
    h2, m2 = _parse_hhmm(hora_fin, (10, 0))
    diff = (h2 * 60 + m2) - (h1 * 60 + m1)
    return diff if diff >= 30 else 120


def _combinar_fecha_hora(fecha_val: Any, hhmm: Optional[str]) -> datetime:
    if isinstance(fecha_val, datetime):
        d = fecha_val.astimezone(LIMA_TZ).date()
    elif isinstance(fecha_val, date):
        d = fecha_val
    elif isinstance(fecha_val, str) and len(fecha_val) >= 10:
        try:
            d = date.fromisoformat(fecha_val[:10])
        except ValueError:
            d = _now_lima().date()
    else:
        d = _now_lima().date()
    h, m = _parse_hhmm(hhmm, (8, 0))
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=LIMA_TZ)


def _calcular_limite_autocierre(
    fecha_val: Any,
    hora_fin: Optional[str],
    hora_real_inicio: datetime,
    minutos_bloque: int,
) -> Tuple[datetime, datetime]:
    """
    Retorna (fin_tope_bloque, instante_autocierre).
    - Si la clase inició dentro o antes de la ventana horaria del curso, el tope es horaFin y
      se auto-cierra en horaFin + 20 min si el docente olvidó finalizarla.
    - Si se abrió fuera de horario (recuperación / seminario / demo), el tope es
      hora_real_inicio + minutos_bloque y se auto-cierra +20 min después.
    """
    prog_fin_dt = _combinar_fecha_hora(fecha_val, hora_fin)
    if hora_real_inicio <= prog_fin_dt:
        fin_bloque = prog_fin_dt
    else:
        fin_bloque = hora_real_inicio + timedelta(minutes=minutos_bloque)
    return fin_bloque, fin_bloque + timedelta(minutes=GRACIA_AUTOCIERRE_MIN)


def _umbral_permanencia_min(minutos_reales_brutos: float) -> int:
    """
    En clases regulares exige >= 15 min con la ventana de Meet abierta para consolidar AUTO_MEET.
    En sesiones breves de prueba (< 30 min), exige al menos el 50% de la sesión (mínimo 1 min).
    """
    if minutos_reales_brutos >= 30.0:
        return MIN_PERMANENCIA_ESTANDAR
    return min(MIN_PERMANENCIA_ESTANDAR, max(1, int(minutos_reales_brutos * 0.5)))


def _es_alerta_abandono(
    origen: str,
    estado: str,
    ventana_abierta: bool,
    minutos_permanencia: int,
    hora_registro: Any,
    now_dt: datetime,
) -> bool:
    if origen != "AUTO_MEET" or estado not in ("Presente", "Tardanza"):
        return False
    if ventana_abierta or minutos_permanencia >= MIN_PERMANENCIA_ESTANDAR:
        return False
    reg_dt = _to_lima(hora_registro)
    # Alerta inmediata si cerró la ventana antes de cumplir 15 min
    return (now_dt - reg_dt).total_seconds() >= 15 or not ventana_abierta


def _serializar_sesion(
    row: Dict[str, Any],
    curso_info: Optional[Dict[str, Any]] = None,
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    now_ref = now_dt or _now_lima()
    inicio_dt = _to_lima(row.get("hora_real_inicio") or row.get("horaRealInicio"))
    fin_raw = row.get("hora_real_fin") or row.get("horaRealFin")
    fin_dt = _to_lima(fin_raw) if fin_raw else None

    hora_prog_ini = row.get("hora_programada_inicio") or row.get("horaProgramadaInicio") or "08:00"
    hora_prog_fin = row.get("hora_programada_fin") or row.get("horaProgramadaFin") or "10:00"
    bloque_min = int(
        row.get("minutos_bloque_programado")
        or row.get("minutosBloqueProgramado")
        or _calcular_bloque_programado_min(hora_prog_ini, hora_prog_fin)
    )
    fin_bloque_dt, autocierre_dt = _calcular_limite_autocierre(
        row.get("fecha"), hora_prog_fin, inicio_dt, bloque_min
    )

    ref_end = fin_dt if fin_dt else now_ref
    segundos_transcurridos = max(0, int((ref_end - inicio_dt).total_seconds()))
    en_tiempo_extra = (row.get("estado") == "EN_CLASE") and (now_ref > fin_bloque_dt)

    c = curso_info or {}
    return {
        "id": row.get("id"),
        "cursoId": row.get("curso_id") or row.get("cursoId"),
        "cicloId": row.get("ciclo_id") or row.get("cicloId") or c.get("ciclo_id") or c.get("cicloId"),
        "cicloNombre": row.get("ciclo_nombre") or c.get("ciclo_nombre") or c.get("ciclovirtual") or "Ciclo Vigente",
        "asignatura": row.get("asignatura") or c.get("asignatura") or "Curso Preuniversitario",
        "seccion": row.get("seccion") or c.get("seccion") or "101",
        "docenteId": row.get("docente_id") or row.get("docenteId"),
        "docenteNombre": row.get("docente_nombre") or row.get("docenteNombre") or c.get("docente") or "Docente",
        "fecha": str(row.get("fecha") or inicio_dt.date().isoformat())[:10],
        "horaProgramadaInicio": hora_prog_ini,
        "horaProgramadaFin": hora_prog_fin,
        "horaRealInicio": inicio_dt.isoformat(),
        "horaRealFin": fin_dt.isoformat() if fin_dt else None,
        "minutosBloqueProgramado": bloque_min,
        "minutosRealesBrutos": float(row.get("minutos_reales_brutos") or row.get("minutosRealesBrutos") or 0),
        "minutosEfectivosDictados": float(
            row.get("minutos_efectivos_dictados") or row.get("minutosEfectivosDictados") or 0
        ),
        "minutosTardanzaDocente": int(
            row.get("minutos_tardanza_docente") or row.get("minutosTardanzaDocente") or 0
        ),
        "segundosTranscurridos": segundos_transcurridos,
        "enTiempoExtra": en_tiempo_extra,
        "autoCierreEn": autocierre_dt.isoformat(),
        "cierreAutomatico": bool(row.get("cierre_automatico") or row.get("cierreAutomatico") or False),
        "estado": row.get("estado", "EN_CLASE"),
        "meetUrl": row.get("meet_url") or row.get("meetUrl") or c.get("meetUrl") or "",
        "serverNow": now_ref.isoformat(),
    }


# --- OBTENCIÓN DE ESTUDIANTES MATRICULADOS EN UN CURSO ---

def _obtener_matriculados_pg(cur, curso_id: str) -> List[Dict[str, str]]:
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


def _obtener_matriculados_json(db: Dict[str, Any], curso_id: str) -> List[Dict[str, str]]:
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


# --- LÓGICA DE CIERRE Y CONSOLIDACIÓN DE ACTA (PG Y JSON) ---

def _finalizar_sesion_pg(cur, sesion_row: Dict[str, Any], fin_dt: datetime, automatico: bool) -> Dict[str, Any]:
    sesion_id = sesion_row["id"]
    curso_id = sesion_row["curso_id"]
    inicio_dt = _to_lima(sesion_row["hora_real_inicio"])
    bloque_min = int(
        sesion_row.get("minutos_bloque_programado")
        or _calcular_bloque_programado_min(
            sesion_row.get("hora_programada_inicio"), sesion_row.get("hora_programada_fin")
        )
    )

    brutos = round(max(0.0, (fin_dt - inicio_dt).total_seconds() / 60.0), 2)
    efectivos = round(min(float(bloque_min), brutos), 2)
    umbral_min = _umbral_permanencia_min(brutos)

    # 1. Actualizar sesión
    cur.execute(
        """
        UPDATE sesiones_clase
        SET hora_real_fin = %s,
            minutos_reales_brutos = %s,
            minutos_efectivos_dictados = %s,
            cierre_automatico = %s,
            estado = 'FINALIZADA'
        WHERE id = %s
        RETURNING *;
        """,
        (fin_dt, brutos, efectivos, automatico, sesion_id),
    )
    updated_sesion = fetch_one_as_dict(cur) or sesion_row

    # 2. Consolidar asistencias: degradar abandonos tempranos AUTO_MEET y completar ausentes
    matriculados = _obtener_matriculados_pg(cur, curso_id)
    cur.execute("SELECT * FROM asistencia_alumnos WHERE sesion_id = %s;", (sesion_id,))
    existentes = {str(r["estudiante_id"]): r for r in (fetch_all_as_dict(cur) or [])}

    puntuales_json = []
    tardanzas_json = []
    ausentes_json = []

    for est in matriculados:
        eid = est["estudianteId"]
        enombre = est["nombre"]
        reg = existentes.get(eid)
        if not reg:
            cur.execute(
                """
                INSERT INTO asistencia_alumnos (
                    sesion_id, curso_id, estudiante_id, estudiante_nombre,
                    estado, origen, ventana_abierta, observacion
                )
                VALUES (%s, %s, %s, %s, 'Ausente', 'AUTO_MEET', FALSE, 'No ingresó durante la sesión')
                ON CONFLICT (sesion_id, estudiante_id) DO NOTHING;
                """,
                (sesion_id, curso_id, eid, enombre),
            )
            ausentes_json.append({"estudianteId": eid, "estudiante": enombre})
        else:
            estado_final = reg["estado"]
            origen = reg["origen"]
            perm = int(reg.get("minutos_permanencia") or 0)
            vent = bool(reg.get("ventana_abierta"))

            # Si tenía la ventana abierta hasta el cierre, sumar tramo final desde el último latido
            if vent:
                ref_last = _to_lima(reg.get("ultimo_latido") or reg.get("hora_registro"))
                extra = max(0, int((fin_dt - ref_last).total_seconds() // 60))
                perm = min(int(brutos) + 1, perm + min(2, extra))

            if (
                origen == "AUTO_MEET"
                and estado_final in ("Presente", "Tardanza")
                and not vent
                and perm < umbral_min
            ):
                estado_final = "Ausente"
                obs = f"Cerró Meet prematuramente ({perm} min < {umbral_min} min requeridos)"
                cur.execute(
                    """
                    UPDATE asistencia_alumnos
                    SET estado = 'Ausente', ventana_abierta = FALSE, minutos_permanencia = %s, observacion = %s
                    WHERE id = %s;
                    """,
                    (perm, obs, reg["id"]),
                )
            else:
                cur.execute(
                    """
                    UPDATE asistencia_alumnos
                    SET ventana_abierta = FALSE, minutos_permanencia = %s
                    WHERE id = %s;
                    """,
                    (perm, reg["id"]),
                )

            hora_txt = _to_lima(reg.get("hora_registro")).strftime("%H:%M")
            if estado_final in ("Presente", "Justificado"):
                puntuales_json.append({"estudianteId": eid, "estudiante": enombre, "horaRegistro": hora_txt})
            elif estado_final == "Tardanza":
                tardanzas_json.append({"estudianteId": eid, "estudiante": enombre, "horaRegistro": hora_txt})
            else:
                ausentes_json.append({"estudianteId": eid, "estudiante": enombre})

    # 3. Guardar acta consolidada en attendance_sessions para reportes globales
    cur.execute('SELECT asignatura, docente FROM courses WHERE id = %s LIMIT 1;', (curso_id,))
    c_row = fetch_one_as_dict(cur) or {}
    asignatura_nom = c_row.get("asignatura") or "Curso"
    docente_nom = updated_sesion.get("docente_nombre") or c_row.get("docente") or "Docente"
    acta_id = f"ACT-{sesion_id}"
    cur.execute(
        """
        INSERT INTO attendance_sessions (
            id, "cursoId", asignatura, "docenteId", docente, fecha, "horaApertura",
            modalidad, "asistentesPuntuales", tardanzas, ausentes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'Virtual en vivo', %s::jsonb, %s::jsonb, %s::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            "asistentesPuntuales" = EXCLUDED."asistentesPuntuales",
            tardanzas = EXCLUDED.tardanzas,
            ausentes = EXCLUDED.ausentes;
        """,
        (
            acta_id,
            curso_id,
            asignatura_nom,
            updated_sesion.get("docente_id"),
            docente_nom,
            str(updated_sesion.get("fecha"))[:10],
            inicio_dt.strftime("%H:%M"),
            json.dumps(puntuales_json),
            json.dumps(tardanzas_json),
            json.dumps(ausentes_json),
        ),
    )

    return updated_sesion


def _auto_finalizar_vencidas_pg(cur, now_dt: datetime) -> bool:
    cur.execute("SELECT * FROM sesiones_clase WHERE estado = 'EN_CLASE';")
    activas = fetch_all_as_dict(cur) or []
    changed = False
    for ses in activas:
        inicio_dt = _to_lima(ses["hora_real_inicio"])
        bloque_min = int(
            ses.get("minutos_bloque_programado")
            or _calcular_bloque_programado_min(
                ses.get("hora_programada_inicio"), ses.get("hora_programada_fin")
            )
        )
        fin_bloque_dt, autocierre_dt = _calcular_limite_autocierre(
            ses.get("fecha"), ses.get("hora_programada_fin"), inicio_dt, bloque_min
        )
        if now_dt > autocierre_dt:
            _finalizar_sesion_pg(cur, ses, fin_bloque_dt, automatico=True)
            changed = True
    return changed


def _finalizar_sesion_json(db: Dict[str, Any], sesion: Dict[str, Any], fin_dt: datetime, automatico: bool) -> Dict[str, Any]:
    sesion_id = sesion["id"]
    curso_id = sesion["curso_id"]
    inicio_dt = _to_lima(sesion["hora_real_inicio"])
    bloque_min = int(sesion.get("minutos_bloque_programado") or 120)

    brutos = round(max(0.0, (fin_dt - inicio_dt).total_seconds() / 60.0), 2)
    efectivos = round(min(float(bloque_min), brutos), 2)
    umbral_min = _umbral_permanencia_min(brutos)

    sesion["hora_real_fin"] = fin_dt.isoformat()
    sesion["minutos_reales_brutos"] = brutos
    sesion["minutos_efectivos_dictados"] = efectivos
    sesion["cierre_automatico"] = automatico
    sesion["estado"] = "FINALIZADA"

    asistencias = db.setdefault("asistenciaAlumnos", [])
    matriculados = _obtener_matriculados_json(db, curso_id)
    existentes = {
        str(a["estudiante_id"]): a
        for a in asistencias
        if a.get("sesion_id") == sesion_id
    }

    puntuales_json = []
    tardanzas_json = []
    ausentes_json = []

    for est in matriculados:
        eid = est["estudianteId"]
        enombre = est["nombre"]
        reg = existentes.get(eid)
        if not reg:
            nuevo = {
                "id": int(time.time() * 1000) % 100000000 + len(asistencias) + 1,
                "sesion_id": sesion_id,
                "curso_id": curso_id,
                "estudiante_id": eid,
                "estudiante_nombre": enombre,
                "estado": "Ausente",
                "origen": "AUTO_MEET",
                "hora_registro": fin_dt.isoformat(),
                "minutos_permanencia": 0,
                "ultimo_latido": None,
                "ventana_abierta": False,
                "observacion": "No ingresó durante la sesión",
            }
            asistencias.append(nuevo)
            ausentes_json.append({"estudianteId": eid, "estudiante": enombre})
        else:
            perm = int(reg.get("minutos_permanencia") or 0)
            vent = bool(reg.get("ventana_abierta"))
            if vent:
                ref_last = _to_lima(reg.get("ultimo_latido") or reg.get("hora_registro"))
                extra = max(0, int((fin_dt - ref_last).total_seconds() // 60))
                perm = min(int(brutos) + 1, perm + min(2, extra))

            if (
                reg.get("origen") == "AUTO_MEET"
                and reg.get("estado") in ("Presente", "Tardanza")
                and not vent
                and perm < umbral_min
            ):
                reg["estado"] = "Ausente"
                reg["observacion"] = f"Cerró Meet prematuramente ({perm} min < {umbral_min} min requeridos)"
            reg["ventana_abierta"] = False
            reg["minutos_permanencia"] = perm

            hora_txt = _to_lima(reg.get("hora_registro")).strftime("%H:%M")
            if reg["estado"] in ("Presente", "Justificado"):
                puntuales_json.append({"estudianteId": eid, "estudiante": enombre, "horaRegistro": hora_txt})
            elif reg["estado"] == "Tardanza":
                tardanzas_json.append({"estudianteId": eid, "estudiante": enombre, "horaRegistro": hora_txt})
            else:
                ausentes_json.append({"estudianteId": eid, "estudiante": enombre})

    att_sessions = db.setdefault("attendanceSessions", [])
    acta_id = f"ACT-{sesion_id}"
    att_sessions[:] = [s for s in att_sessions if s.get("id") != acta_id]
    att_sessions.insert(
        0,
        {
            "id": acta_id,
            "cursoId": curso_id,
            "asignatura": sesion.get("asignatura", "Curso"),
            "docenteId": sesion.get("docente_id"),
            "docente": sesion.get("docente_nombre", "Docente"),
            "fecha": str(sesion.get("fecha"))[:10],
            "horaApertura": inicio_dt.strftime("%H:%M"),
            "modalidad": "Virtual en vivo",
            "asistentesPuntuales": puntuales_json,
            "tardanzas": tardanzas_json,
            "ausentes": ausentes_json,
        },
    )
    return sesion


def _auto_finalizar_vencidas_json(db: Dict[str, Any], now_dt: datetime) -> bool:
    changed = False
    for ses in db.get("sesionesClase", []):
        if ses.get("estado") != "EN_CLASE":
            continue
        inicio_dt = _to_lima(ses.get("hora_real_inicio"))
        bloque_min = int(ses.get("minutos_bloque_programado") or 120)
        fin_bloque_dt, autocierre_dt = _calcular_limite_autocierre(
            ses.get("fecha"), ses.get("hora_programada_fin"), inicio_dt, bloque_min
        )
        if now_dt > autocierre_dt:
            _finalizar_sesion_json(db, ses, fin_bloque_dt, automatico=True)
            changed = True
    return changed


# --- ENDPOINTS ---

@router.post("/iniciar", status_code=status.HTTP_201_CREATED)
def iniciar_clase_vivo(
    payload: IniciarClaseRequest,
    current_user: dict = Depends(require_role(["docente", "admin"])),
):
    """
    Inicia (o recupera si ya estaba abierta) una sesión en vivo para el curso indicado.
    Si el docente ingresa pasado el umbral de tolerancia (10 min), registra la tardanza
    y crea la incidencia pendiente correspondiente.
    """
    now_dt = _now_lima()
    docente_id = str(current_user.get("id") or "USR-107")
    docente_nombre = (
        f"{current_user.get('nombres', '')} {current_user.get('apellidos', '')}".strip()
        or current_user.get("correo")
        or "Docente"
    )
    es_admin = str(current_user.get("rol", "")).casefold() == "admin"

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # Pre-chequeo unificado en 1 solo viaje SQL a Supabase (curso + sesiones activas + tolerancia)
                cur.execute(
                    """
                    SELECT
                      (SELECT row_to_json(c_sub) FROM (
                          SELECT c.*, ci.nombre AS ciclo_nombre
                          FROM courses c
                          LEFT JOIN ciclos_academicos ci ON ci.id = c.ciclo_id
                          WHERE c.id = %s LIMIT 1
                      ) c_sub) AS curso_json,
                      (SELECT COALESCE(json_agg(s_sub), '[]'::json) FROM (
                          SELECT s.*, c.asignatura
                          FROM sesiones_clase s
                          LEFT JOIN courses c ON c.id = s.curso_id
                          WHERE s.estado = 'EN_CLASE'
                      ) s_sub) AS activas_json,
                      (SELECT tolerancia_tardanza_min FROM configuracion WHERE id = 'general' LIMIT 1) AS tolerancia_min;
                    """,
                    (payload.curso_id,),
                )
                pre = fetch_one_as_dict(cur) or {}
                curso = pre.get("curso_json")
                if not curso:
                    raise HTTPException(status_code=404, detail="Curso no encontrado.")

                tolerancia_min = int(pre.get("tolerancia_min") or 10)
                activas_vigentes = []
                changed_expired = False
                for ses_act in pre.get("activas_json") or []:
                    ini_act = _to_lima(ses_act["hora_real_inicio"])
                    blq_act = int(
                        ses_act.get("minutos_bloque_programado")
                        or _calcular_bloque_programado_min(
                            ses_act.get("hora_programada_inicio"), ses_act.get("hora_programada_fin")
                        )
                    )
                    fin_blq_dt, auto_dt = _calcular_limite_autocierre(
                        ses_act.get("fecha"), ses_act.get("hora_programada_fin"), ini_act, blq_act
                    )
                    if now_dt > auto_dt:
                        _finalizar_sesion_pg(cur, ses_act, fin_blq_dt, automatico=True)
                        changed_expired = True
                    else:
                        activas_vigentes.append(ses_act)

                existente_curso = next(
                    (s for s in activas_vigentes if s.get("curso_id") == payload.curso_id),
                    None,
                )
                if existente_curso:
                    if changed_expired:
                        conn.commit()
                    return _serializar_sesion(existente_curso, curso, now_dt)

                effective_docente_id = curso.get("docenteId") if es_admin and curso.get("docenteId") else docente_id
                effective_docente_nom = curso.get("docente") if es_admin and curso.get("docente") else docente_nombre

                otra_activa = next(
                    (s for s in activas_vigentes if s.get("docente_id") == effective_docente_id),
                    None,
                )
                if otra_activa:
                    if changed_expired:
                        conn.commit()
                    raise HTTPException(
                        status_code=409,
                        detail=f"Ya tienes una clase en vivo activa en '{otra_activa.get('asignatura') or otra_activa.get('curso_id')}'. Finalízala antes de iniciar otro bloque.",
                    )

                hora_ini = curso.get("horaInicio") or "08:00"
                hora_fin = curso.get("horaFin") or "10:00"
                bloque_min = _calcular_bloque_programado_min(hora_ini, hora_fin)

                prog_ini_dt = _combinar_fecha_hora(now_dt.date(), hora_ini)
                prog_fin_dt = _combinar_fecha_hora(now_dt.date(), hora_fin)
                minutos_tardanza = 0
                if prog_ini_dt < now_dt <= prog_fin_dt:
                    retraso = int((now_dt - prog_ini_dt).total_seconds() // 60)
                    if retraso > tolerancia_min:
                        minutos_tardanza = retraso

                sesion_id = f"CLS-{int(time.time() * 1000) % 100000000}"
                cur.execute(
                    """
                    INSERT INTO sesiones_clase (
                        id, curso_id, ciclo_id, docente_id, docente_nombre, fecha,
                        hora_programada_inicio, hora_programada_fin, hora_real_inicio,
                        minutos_bloque_programado, minutos_tardanza_docente, estado, meet_url
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'EN_CLASE', %s)
                    RETURNING *;
                    """,
                    (
                        sesion_id,
                        payload.curso_id,
                        curso.get("ciclo_id"),
                        effective_docente_id,
                        effective_docente_nom,
                        now_dt.date(),
                        hora_ini,
                        hora_fin,
                        now_dt,
                        bloque_min,
                        minutos_tardanza,
                        curso.get("meetUrl") or "",
                    ),
                )
                nueva_sesion = fetch_one_as_dict(cur)

                if minutos_tardanza > 0:
                    inc_id = f"INC-TRD-{sesion_id}"
                    cur.execute('SELECT dni, "tipoDocumento", correo FROM users WHERE id = %s LIMIT 1;', (effective_docente_id,))
                    u_doc = fetch_one_as_dict(cur) or {}
                    cur.execute(
                        """
                        INSERT INTO incidents (
                            id, docente, "tipoDocumento", dni, correo, tipo, "fechaPresentacion",
                            materia, horario, motivo, estado, docenteid, cursoid,
                            minutos_tardanza, sesion_clase_id
                        )
                        VALUES (%s, %s, %s, %s, %s, 'Tardanza', %s, %s, %s, %s, 'Pendiente', %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING;
                        """,
                        (
                            inc_id,
                            effective_docente_nom,
                            u_doc.get("tipoDocumento") or "DNI",
                            u_doc.get("dni") or "",
                            u_doc.get("correo") or current_user.get("correo") or "",
                            now_dt.date().isoformat(),
                            curso.get("asignatura") or payload.curso_id,
                            f"{hora_ini} - {hora_fin}",
                            f"Apertura de sala en vivo con {minutos_tardanza} min de retraso (tolerancia: {tolerancia_min} min).",
                            effective_docente_id,
                            payload.curso_id,
                            minutos_tardanza,
                            sesion_id,
                        ),
                    )

                conn.commit()
                return _serializar_sesion(nueva_sesion, curso, now_dt)
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"No se pudo iniciar la clase en vivo: {exc}") from exc
        finally:
            conn.close()

    # Fallback offline db.json (pytest)
    db = load_db()
    _auto_finalizar_vencidas_json(db, now_dt)
    cursos = db.get("courses", [])
    curso = next((c for c in cursos if c.get("id") == payload.curso_id), None)
    if not curso:
        raise HTTPException(status_code=404, detail="Curso no encontrado.")

    sesiones = db.setdefault("sesionesClase", [])
    existente = next(
        (s for s in sesiones if s.get("curso_id") == payload.curso_id and s.get("estado") == "EN_CLASE"),
        None,
    )
    if existente:
        save_db(db)
        return _serializar_sesion(existente, curso, now_dt)

    effective_docente_id = curso.get("docenteId") if es_admin and curso.get("docenteId") else docente_id
    effective_docente_nom = curso.get("docente") if es_admin and curso.get("docente") else docente_nombre

    otra = next(
        (s for s in sesiones if s.get("docente_id") == effective_docente_id and s.get("estado") == "EN_CLASE"),
        None,
    )
    if otra:
        save_db(db)
        raise HTTPException(
            status_code=409,
            detail=f"Ya tienes una clase en vivo activa en '{otra.get('asignatura') or otra.get('curso_id')}'. Finalízala antes de iniciar otro bloque.",
        )

    hora_ini = curso.get("horaInicio") or "08:00"
    hora_fin = curso.get("horaFin") or "10:00"
    bloque_min = _calcular_bloque_programado_min(hora_ini, hora_fin)
    prog_ini_dt = _combinar_fecha_hora(now_dt.date(), hora_ini)
    prog_fin_dt = _combinar_fecha_hora(now_dt.date(), hora_fin)
    minutos_tardanza = 0
    if prog_ini_dt < now_dt <= prog_fin_dt:
        retraso = int((now_dt - prog_ini_dt).total_seconds() // 60)
        if retraso > 10:
            minutos_tardanza = retraso

    sesion_id = f"CLS-{int(time.time() * 1000) % 100000000}"
    nueva = {
        "id": sesion_id,
        "curso_id": payload.curso_id,
        "ciclo_id": curso.get("ciclo_id") or curso.get("cicloId"),
        "asignatura": curso.get("asignatura") or "Curso",
        "docente_id": effective_docente_id,
        "docente_nombre": effective_docente_nom,
        "fecha": now_dt.date().isoformat(),
        "hora_programada_inicio": hora_ini,
        "hora_programada_fin": hora_fin,
        "hora_real_inicio": now_dt.isoformat(),
        "hora_real_fin": None,
        "minutos_bloque_programado": bloque_min,
        "minutos_reales_brutos": 0,
        "minutos_efectivos_dictados": 0,
        "minutos_tardanza_docente": minutos_tardanza,
        "cierre_automatico": False,
        "estado": "EN_CLASE",
        "meet_url": curso.get("meetUrl") or "",
    }
    sesiones.insert(0, nueva)
    if minutos_tardanza > 0:
        db.setdefault("incidents", []).insert(
            0,
            {
                "id": f"INC-TRD-{sesion_id}",
                "docente": effective_docente_nom,
                "tipo": "Tardanza",
                "fechaPresentacion": now_dt.date().isoformat(),
                "materia": curso.get("asignatura") or payload.curso_id,
                "horario": f"{hora_ini} - {hora_fin}",
                "motivo": f"Apertura de sala con {minutos_tardanza} min de retraso.",
                "estado": "Pendiente",
                "docenteId": effective_docente_id,
                "cursoId": payload.curso_id,
                "minutos_tardanza": minutos_tardanza,
                "sesion_clase_id": sesion_id,
            },
        )
    save_db(db)
    return _serializar_sesion(nueva, curso, now_dt)


@router.get("/activa")
def obtener_clase_activa(
    curso_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Devuelve la sesión EN_CLASE activa para un curso específico o para el docente autenticado.
    Ejecuta primero el barrido de auto-cierre (+20 min tras fin de bloque).
    """
    now_dt = _now_lima()
    user_id = str(current_user.get("id") or "")

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                _auto_finalizar_vencidas_pg(cur, now_dt)
                if curso_id:
                    cur.execute(
                        """
                        SELECT s.*, c.asignatura, c."meetUrl" AS curso_meet_url, ci.nombre AS ciclo_nombre
                        FROM sesiones_clase s
                        LEFT JOIN courses c ON c.id = s.curso_id
                        LEFT JOIN ciclos_academicos ci ON ci.id = COALESCE(s.ciclo_id, c.ciclo_id)
                        WHERE s.curso_id = %s AND s.estado = 'EN_CLASE'
                        ORDER BY s.hora_real_inicio DESC
                        LIMIT 1;
                        """,
                        (curso_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT s.*, c.asignatura, c."meetUrl" AS curso_meet_url, ci.nombre AS ciclo_nombre
                        FROM sesiones_clase s
                        LEFT JOIN courses c ON c.id = s.curso_id
                        LEFT JOIN ciclos_academicos ci ON ci.id = COALESCE(s.ciclo_id, c.ciclo_id)
                        WHERE s.docente_id = %s AND s.estado = 'EN_CLASE'
                        ORDER BY s.hora_real_inicio DESC
                        LIMIT 1;
                        """,
                        (user_id,),
                    )
                row = fetch_one_as_dict(cur)
                conn.commit()
                if not row:
                    return {"activa": None, "serverNow": now_dt.isoformat()}
                return {"activa": _serializar_sesion(row, None, now_dt), "serverNow": now_dt.isoformat()}
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al consultar sesión activa: {exc}") from exc
        finally:
            conn.close()

    db = load_db()
    if _auto_finalizar_vencidas_json(db, now_dt):
        save_db(db)
    sesiones = db.get("sesionesClase", [])
    if curso_id:
        ses = next((s for s in sesiones if s.get("curso_id") == curso_id and s.get("estado") == "EN_CLASE"), None)
    else:
        ses = next((s for s in sesiones if s.get("docente_id") == user_id and s.get("estado") == "EN_CLASE"), None)
    if not ses:
        return {"activa": None, "serverNow": now_dt.isoformat()}
    curso = next((c for c in db.get("courses", []) if c.get("id") == ses.get("curso_id")), None)
    return {"activa": _serializar_sesion(ses, curso, now_dt), "serverNow": now_dt.isoformat()}


@router.get("/activas")
def listar_clases_activas(
    ciclo_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Lista todas las sesiones EN_CLASE vigentes. Si consulta un estudiante, adjunta su
    registro individual `miAsistencia` en cada sesión activa.
    """
    now_dt = _now_lima()
    user_id = str(current_user.get("id") or "")
    es_estudiante = str(current_user.get("rol", "")).casefold() == "estudiante"

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                _auto_finalizar_vencidas_pg(cur, now_dt)
                query = """
                    SELECT s.*, c.asignatura, c."meetUrl" AS curso_meet_url, ci.nombre AS ciclo_nombre
                    FROM sesiones_clase s
                    LEFT JOIN courses c ON c.id = s.curso_id
                    LEFT JOIN ciclos_academicos ci ON ci.id = COALESCE(s.ciclo_id, c.ciclo_id)
                    WHERE s.estado = 'EN_CLASE'
                """
                params: List[Any] = []
                if ciclo_id:
                    query += " AND COALESCE(s.ciclo_id, c.ciclo_id) = %s"
                    params.append(ciclo_id)
                query += " ORDER BY s.hora_real_inicio DESC;"
                cur.execute(query, tuple(params))
                rows = fetch_all_as_dict(cur) or []

                mis_asistencias: Dict[str, Dict[str, Any]] = {}
                if es_estudiante and rows and user_id:
                    sids = [r["id"] for r in rows]
                    cur.execute(
                        """
                        SELECT sesion_id, estado, origen, hora_registro, minutos_permanencia, ventana_abierta
                        FROM asistencia_alumnos
                        WHERE estudiante_id = %s AND sesion_id = ANY(%s);
                        """,
                        (user_id, sids),
                    )
                    for a in fetch_all_as_dict(cur) or []:
                        mis_asistencias[str(a["sesion_id"])] = {
                            "estado": a["estado"],
                            "origen": a["origen"],
                            "horaRegistro": _to_lima(a.get("hora_registro")).isoformat(),
                            "minutosPermanencia": int(a.get("minutos_permanencia") or 0),
                            "ventanaAbierta": bool(a.get("ventana_abierta")),
                        }

                conn.commit()
                resultado = []
                for r in rows:
                    item = _serializar_sesion(r, None, now_dt)
                    item["miAsistencia"] = mis_asistencias.get(str(r["id"]))
                    resultado.append(item)
                return {"sesiones": resultado, "serverNow": now_dt.isoformat()}
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al listar clases activas: {exc}") from exc
        finally:
            conn.close()

    db = load_db()
    if _auto_finalizar_vencidas_json(db, now_dt):
        save_db(db)
    cursos_by_id = {c.get("id"): c for c in db.get("courses", [])}
    asistencias = db.get("asistenciaAlumnos", [])
    resultado = []
    for s in db.get("sesionesClase", []):
        if s.get("estado") != "EN_CLASE":
            continue
        c = cursos_by_id.get(s.get("curso_id")) or {}
        eff_ciclo = s.get("ciclo_id") or c.get("ciclo_id") or c.get("cicloId")
        if ciclo_id and eff_ciclo != ciclo_id:
            continue
        item = _serializar_sesion(s, c, now_dt)
        if es_estudiante and user_id:
            propia = next(
                (
                    a for a in asistencias
                    if a.get("sesion_id") == s.get("id") and str(a.get("estudiante_id")) == user_id
                ),
                None,
            )
            item["miAsistencia"] = (
                {
                    "estado": propia["estado"],
                    "origen": propia["origen"],
                    "horaRegistro": _to_lima(propia.get("hora_registro")).isoformat(),
                    "minutosPermanencia": int(propia.get("minutos_permanencia") or 0),
                    "ventanaAbierta": bool(propia.get("ventana_abierta")),
                }
                if propia
                else None
            )
        resultado.append(item)
    return {"sesiones": resultado, "serverNow": now_dt.isoformat()}


@router.post("/{sesion_id}/unirse")
def unirse_a_clase_vivo(
    sesion_id: str,
    current_user: dict = Depends(require_role(["estudiante", "docente", "admin"])),
):
    """
    Registra el ingreso automático (AUTO_MEET) del estudiante al pulsar 'Unirse a Sala Meet'
    y marca su ventana en 2.º plano como abierta para iniciar el conteo de permanencia.
    """
    now_dt = _now_lima()
    estudiante_id = str(current_user.get("id") or "")
    estudiante_nombre = (
        f"{current_user.get('nombres', '')} {current_user.get('apellidos', '')}".strip()
        or current_user.get("correo")
        or "Estudiante"
    )

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM sesiones_clase WHERE id = %s LIMIT 1;", (sesion_id,))
                ses = fetch_one_as_dict(cur)
                if not ses:
                    raise HTTPException(status_code=404, detail="Sesión de clase no encontrada.")
                if ses.get("estado") != "EN_CLASE":
                    raise HTTPException(status_code=400, detail="Esta sesión de clase ya fue finalizada.")

                inicio_dt = _to_lima(ses["hora_real_inicio"])
                retraso_min = (now_dt - inicio_dt).total_seconds() / 60.0
                estado_auto = "Presente" if retraso_min <= TOLERANCIA_ALUMNO_MIN else "Tardanza"

                cur.execute(
                    "SELECT * FROM asistencia_alumnos WHERE sesion_id = %s AND estudiante_id = %s LIMIT 1;",
                    (sesion_id, estudiante_id),
                )
                existente = fetch_one_as_dict(cur)
                if not existente:
                    cur.execute(
                        """
                        INSERT INTO asistencia_alumnos (
                            sesion_id, curso_id, estudiante_id, estudiante_nombre,
                            estado, origen, hora_registro, minutos_permanencia,
                            ultimo_latido, ventana_abierta
                        )
                        VALUES (%s, %s, %s, %s, %s, 'AUTO_MEET', %s, 1, %s, TRUE)
                        RETURNING *;
                        """,
                        (
                            sesion_id,
                            ses["curso_id"],
                            estudiante_id,
                            estudiante_nombre,
                            estado_auto,
                            now_dt,
                            now_dt,
                        ),
                    )
                    reg = fetch_one_as_dict(cur)
                else:
                    # Respetar PASE_LISTA manual del docente; reactivar ventana_abierta si reabre Meet
                    nuevo_estado = existente["estado"]
                    if existente["origen"] == "AUTO_MEET" and existente["estado"] == "Ausente":
                        nuevo_estado = estado_auto
                    cur.execute(
                        """
                        UPDATE asistencia_alumnos
                        SET estado = %s,
                            ventana_abierta = TRUE,
                            ultimo_latido = %s,
                            minutos_permanencia = GREATEST(minutos_permanencia, 1)
                        WHERE id = %s
                        RETURNING *;
                        """,
                        (nuevo_estado, now_dt, existente["id"]),
                    )
                    reg = fetch_one_as_dict(cur)

                conn.commit()
                return {
                    "sesionId": sesion_id,
                    "cursoId": ses["curso_id"],
                    "meetUrl": ses.get("meet_url") or "",
                    "estado": reg["estado"],
                    "origen": reg["origen"],
                    "horaRegistro": _to_lima(reg.get("hora_registro")).isoformat(),
                    "minutosPermanencia": int(reg.get("minutos_permanencia") or 1),
                    "ventanaAbierta": True,
                }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al registrar ingreso a Meet: {exc}") from exc
        finally:
            conn.close()

    db = load_db()
    ses = next((s for s in db.get("sesionesClase", []) if s.get("id") == sesion_id), None)
    if not ses:
        raise HTTPException(status_code=404, detail="Sesión de clase no encontrada.")
    if ses.get("estado") != "EN_CLASE":
        raise HTTPException(status_code=400, detail="Esta sesión de clase ya fue finalizada.")

    inicio_dt = _to_lima(ses["hora_real_inicio"])
    retraso_min = (now_dt - inicio_dt).total_seconds() / 60.0
    estado_auto = "Presente" if retraso_min <= TOLERANCIA_ALUMNO_MIN else "Tardanza"

    asistencias = db.setdefault("asistenciaAlumnos", [])
    existente = next(
        (a for a in asistencias if a.get("sesion_id") == sesion_id and str(a.get("estudiante_id")) == estudiante_id),
        None,
    )
    if not existente:
        existente = {
            "id": int(time.time() * 1000) % 100000000 + len(asistencias) + 1,
            "sesion_id": sesion_id,
            "curso_id": ses["curso_id"],
            "estudiante_id": estudiante_id,
            "estudiante_nombre": estudiante_nombre,
            "estado": estado_auto,
            "origen": "AUTO_MEET",
            "hora_registro": now_dt.isoformat(),
            "minutos_permanencia": 1,
            "ultimo_latido": now_dt.isoformat(),
            "ventana_abierta": True,
            "observacion": "",
        }
        asistencias.append(existente)
    else:
        if existente.get("origen") == "AUTO_MEET" and existente.get("estado") == "Ausente":
            existente["estado"] = estado_auto
        existente["ventana_abierta"] = True
        existente["ultimo_latido"] = now_dt.isoformat()
        existente["minutos_permanencia"] = max(int(existente.get("minutos_permanencia") or 0), 1)

    save_db(db)
    return {
        "sesionId": sesion_id,
        "cursoId": ses["curso_id"],
        "meetUrl": ses.get("meet_url") or "",
        "estado": existente["estado"],
        "origen": existente["origen"],
        "horaRegistro": _to_lima(existente.get("hora_registro")).isoformat(),
        "minutosPermanencia": int(existente["minutos_permanencia"]),
        "ventanaAbierta": True,
    }


@router.post("/{sesion_id}/latido")
def registrar_latido_permanencia(
    sesion_id: str,
    payload: LatidoRequest,
    current_user: dict = Depends(require_role(["estudiante", "docente", "admin"])),
):
    """
    Recibe el pulso silencioso en 2.º plano desde el portal del estudiante cada ~60s
    mientras `!meetWin.closed`, o inmediatamente con `ventanaAbierta=false` si la cerró.
    El incremento de minutos está protegido en servidor por tiempo real transcurrido.
    """
    now_dt = _now_lima()
    estudiante_id = str(current_user.get("id") or "")

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM asistencia_alumnos WHERE sesion_id = %s AND estudiante_id = %s LIMIT 1;",
                    (sesion_id, estudiante_id),
                )
                reg = fetch_one_as_dict(cur)
                if not reg:
                    raise HTTPException(status_code=404, detail="Aún no has registrado ingreso a esta sesión.")

                perm_actual = int(reg.get("minutos_permanencia") or 0)
                ultimo_dt = _to_lima(reg.get("ultimo_latido") or reg.get("hora_registro"))
                elapsed_sec = max(0.0, (now_dt - ultimo_dt).total_seconds())

                nuevo_perm = perm_actual
                nuevo_ultimo = reg.get("ultimo_latido") or now_dt
                if payload.ventana_abierta and elapsed_sec >= 45.0:
                    delta = min(2, max(1, int(round(elapsed_sec / 60.0))))
                    nuevo_perm = perm_actual + delta
                    nuevo_ultimo = now_dt

                cur.execute(
                    """
                    UPDATE asistencia_alumnos
                    SET ventana_abierta = %s,
                        minutos_permanencia = %s,
                        ultimo_latido = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (payload.ventana_abierta, nuevo_perm, nuevo_ultimo, reg["id"]),
                )
                updated = fetch_one_as_dict(cur)
                conn.commit()
                return {
                    "sesionId": sesion_id,
                    "estado": updated["estado"],
                    "minutosPermanencia": int(updated.get("minutos_permanencia") or 0),
                    "ventanaAbierta": bool(updated.get("ventana_abierta")),
                }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al procesar latido: {exc}") from exc
        finally:
            conn.close()

    db = load_db()
    reg = next(
        (
            a for a in db.get("asistenciaAlumnos", [])
            if a.get("sesion_id") == sesion_id and str(a.get("estudiante_id")) == estudiante_id
        ),
        None,
    )
    if not reg:
        raise HTTPException(status_code=404, detail="Aún no has registrado ingreso a esta sesión.")

    perm_actual = int(reg.get("minutos_permanencia") or 0)
    ultimo_dt = _to_lima(reg.get("ultimo_latido") or reg.get("hora_registro"))
    elapsed_sec = max(0.0, (now_dt - ultimo_dt).total_seconds())
    if payload.ventana_abierta and elapsed_sec >= 45.0:
        delta = min(2, max(1, int(round(elapsed_sec / 60.0))))
        reg["minutos_permanencia"] = perm_actual + delta
        reg["ultimo_latido"] = now_dt.isoformat()
    reg["ventana_abierta"] = payload.ventana_abierta
    save_db(db)
    return {
        "sesionId": sesion_id,
        "estado": reg["estado"],
        "minutosPermanencia": int(reg.get("minutos_permanencia") or 0),
        "ventanaAbierta": bool(reg.get("ventana_abierta")),
    }


@router.get("/{sesion_id}/lista")
def obtener_pase_lista_vivo(
    sesion_id: str,
    _user: dict = Depends(require_role(["docente", "admin"])),
):
    """
    Devuelve la nómina completa del curso cruzada en tiempo real con los ingresos
    automáticos (AUTO_MEET), minutos validados en 2.º plano, alertas de abandono
    temprano y subsanaciones manuales (PASE_LISTA).
    """
    now_dt = _now_lima()

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # 1 solo round-trip SQL a Supabase para minimizar latencia de red Lima -> AWS
                cur.execute(
                    """
                    SELECT
                      (SELECT row_to_json(s_sub) FROM (
                          SELECT s.*, c.asignatura, c."meetUrl" AS curso_meet_url, ci.nombre AS ciclo_nombre
                          FROM sesiones_clase s
                          LEFT JOIN courses c ON c.id = s.curso_id
                          LEFT JOIN ciclos_academicos ci ON ci.id = COALESCE(s.ciclo_id, c.ciclo_id)
                          WHERE s.id = %s
                          LIMIT 1
                      ) s_sub) AS sesion_json,
                      (SELECT COALESCE(json_agg(u_sub), '[]'::json) FROM (
                          SELECT id, nombres, apellidos, correo, "cursosInscritos"
                          FROM users
                          WHERE LOWER(rol) = 'estudiante' AND COALESCE(estado, 'Activo') = 'Activo'
                      ) u_sub) AS users_json,
                      (SELECT COALESCE(json_agg(m_sub), '[]'::json) FROM (
                          SELECT m.estudiante_id, m.estudiante, m.correo, m."cursosInscritos", u.id AS resolved_uid
                          FROM matriculas m
                          LEFT JOIN users u ON LOWER(u.correo) = LOWER(m.correo)
                      ) m_sub) AS matriculas_json,
                      (SELECT COALESCE(json_agg(a_sub), '[]'::json) FROM (
                          SELECT * FROM asistencia_alumnos WHERE sesion_id = %s
                      ) a_sub) AS asistencias_json;
                    """,
                    (sesion_id, sesion_id),
                )
                bundle = fetch_one_as_dict(cur) or {}
                ses = bundle.get("sesion_json")
                if not ses:
                    raise HTTPException(status_code=404, detail="Sesión no encontrada.")

                curso_id_ref = ses["curso_id"]
                estudiantes_map: Dict[str, Dict[str, str]] = {}
                for u in bundle.get("users_json") or []:
                    if curso_id_ref in parse_course_list(u.get("cursosInscritos")):
                        uid = str(u["id"])
                        nombre = f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip() or u.get("correo", uid)
                        estudiantes_map[uid] = {
                            "estudianteId": uid,
                            "nombre": nombre,
                            "correo": u.get("correo") or "",
                        }
                for m in bundle.get("matriculas_json") or []:
                    if curso_id_ref in parse_course_list(m.get("cursosInscritos")):
                        uid = str(m.get("estudiante_id") or m.get("resolved_uid") or "").strip()
                        if uid and uid not in estudiantes_map:
                            estudiantes_map[uid] = {
                                "estudianteId": uid,
                                "nombre": m.get("estudiante") or m.get("correo") or uid,
                                "correo": m.get("correo") or "",
                            }
                matriculados = sorted(estudiantes_map.values(), key=lambda x: x["nombre"].lower())
                registros = {str(r["estudiante_id"]): r for r in (bundle.get("asistencias_json") or [])}
        finally:
            conn.close()
        sesion_serializada = _serializar_sesion(ses, None, now_dt)
    else:
        db = load_db()
        ses = next((s for s in db.get("sesionesClase", []) if s.get("id") == sesion_id), None)
        if not ses:
            raise HTTPException(status_code=404, detail="Sesión no encontrada.")
        curso = next((c for c in db.get("courses", []) if c.get("id") == ses.get("curso_id")), None)
        matriculados = _obtener_matriculados_json(db, ses["curso_id"])
        registros = {
            str(a["estudiante_id"]): a
            for a in db.get("asistenciaAlumnos", [])
            if a.get("sesion_id") == sesion_id
        }
        sesion_serializada = _serializar_sesion(ses, curso, now_dt)

    # Incluir también a cualquier estudiante que haya marcado ingreso aunque su matrícula sea reciente
    known_ids = {m["estudianteId"] for m in matriculados}
    for eid, r in registros.items():
        if eid not in known_ids:
            matriculados.append(
                {
                    "estudianteId": eid,
                    "nombre": r.get("estudiante_nombre") or eid,
                    "correo": "",
                }
            )

    lista = []
    presentes = tardanzas = ausentes = justificados = alertas = en_llamada = 0

    for est in matriculados:
        eid = est["estudianteId"]
        reg = registros.get(eid)
        if not reg:
            ausentes += 1
            lista.append(
                {
                    "estudianteId": eid,
                    "nombre": est["nombre"],
                    "correo": est.get("correo") or "",
                    "estado": "Ausente",
                    "origen": None,
                    "horaRegistro": None,
                    "minutosPermanencia": 0,
                    "ventanaAbierta": False,
                    "alertaAbandono": False,
                    "observacion": "",
                }
            )
            continue

        estado = reg.get("estado") or "Ausente"
        origen = reg.get("origen") or "AUTO_MEET"
        perm = int(reg.get("minutos_permanencia") or 0)
        ultimo_raw = reg.get("ultimo_latido") or reg.get("hora_registro")
        # Si han pasado > 150s sin recibir pulso, considerar la ventana cerrada/desconectada
        vent = bool(reg.get("ventana_abierta")) and (
            (now_dt - _to_lima(ultimo_raw)).total_seconds() <= 150
        )
        alerta = _es_alerta_abandono(origen, estado, vent, perm, reg.get("hora_registro"), now_dt)

        if vent:
            en_llamada += 1
        if alerta:
            alertas += 1

        if estado == "Presente":
            presentes += 1
        elif estado == "Tardanza":
            tardanzas += 1
        elif estado == "Justificado":
            justificados += 1
        else:
            ausentes += 1

        lista.append(
            {
                "estudianteId": eid,
                "nombre": est["nombre"] or reg.get("estudiante_nombre") or eid,
                "correo": est.get("correo") or "",
                "estado": estado,
                "origen": origen,
                "horaRegistro": _to_lima(reg.get("hora_registro")).isoformat() if reg.get("hora_registro") else None,
                "minutosPermanencia": perm,
                "ventanaAbierta": vent,
                "alertaAbandono": alerta,
                "observacion": reg.get("observacion") or "",
            }
        )

    return {
        "sesion": sesion_serializada,
        "resumen": {
            "totalMatriculados": len(lista),
            "presentes": presentes,
            "tardanzas": tardanzas,
            "justificados": justificados,
            "ausentes": ausentes,
            "alertasAbandono": alertas,
            "enLlamadaAhora": en_llamada,
        },
        "estudiantes": lista,
    }


@router.put("/{sesion_id}/lista/{estudiante_id}")
def actualizar_asistencia_pase_lista(
    sesion_id: str,
    estudiante_id: str,
    payload: ActualizarAsistenciaAlumnoRequest,
    _user: dict = Depends(require_role(["docente", "admin"])),
):
    """
    Permite al docente validar o subsanar con 1 clic el estado de un estudiante
    (Presente, Tardanza, Ausente, Justificado), marcando origen = 'PASE_LISTA'.
    """
    if payload.estado not in ESTADOS_ASISTENCIA_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"Estado inválido. Valores permitidos: {sorted(ESTADOS_ASISTENCIA_VALIDOS)}",
        )

    now_dt = _now_lima()
    obs = (payload.observacion or "").strip() or "Validado manualmente por el docente"

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM sesiones_clase WHERE id = %s LIMIT 1;", (sesion_id,))
                ses = fetch_one_as_dict(cur)
                if not ses:
                    raise HTTPException(status_code=404, detail="Sesión no encontrada.")

                cur.execute("SELECT nombres, apellidos, correo FROM users WHERE id = %s LIMIT 1;", (estudiante_id,))
                u = fetch_one_as_dict(cur) or {}
                nombre_est = (
                    f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip()
                    or u.get("correo")
                    or estudiante_id
                )

                cur.execute(
                    """
                    INSERT INTO asistencia_alumnos (
                        sesion_id, curso_id, estudiante_id, estudiante_nombre,
                        estado, origen, hora_registro, observacion
                    )
                    VALUES (%s, %s, %s, %s, %s, 'PASE_LISTA', %s, %s)
                    ON CONFLICT (sesion_id, estudiante_id) DO UPDATE SET
                        estado = EXCLUDED.estado,
                        origen = 'PASE_LISTA',
                        observacion = EXCLUDED.observacion
                    RETURNING *;
                    """,
                    (sesion_id, ses["curso_id"], estudiante_id, nombre_est, payload.estado, now_dt, obs),
                )
                row = fetch_one_as_dict(cur)
                conn.commit()
                return {
                    "estudianteId": estudiante_id,
                    "estado": row["estado"],
                    "origen": row["origen"],
                    "observacion": row.get("observacion") or "",
                }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"No se pudo actualizar la asistencia: {exc}") from exc
        finally:
            conn.close()

    db = load_db()
    ses = next((s for s in db.get("sesionesClase", []) if s.get("id") == sesion_id), None)
    if not ses:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")

    u = next((usr for usr in db.get("users", []) if str(usr.get("id")) == estudiante_id), {})
    nombre_est = f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip() or u.get("correo") or estudiante_id

    asistencias = db.setdefault("asistenciaAlumnos", [])
    existente = next(
        (a for a in asistencias if a.get("sesion_id") == sesion_id and str(a.get("estudiante_id")) == estudiante_id),
        None,
    )
    if not existente:
        existente = {
            "id": int(time.time() * 1000) % 100000000 + len(asistencias) + 1,
            "sesion_id": sesion_id,
            "curso_id": ses["curso_id"],
            "estudiante_id": estudiante_id,
            "estudiante_nombre": nombre_est,
            "estado": payload.estado,
            "origen": "PASE_LISTA",
            "hora_registro": now_dt.isoformat(),
            "minutos_permanencia": 0,
            "ultimo_latido": None,
            "ventana_abierta": False,
            "observacion": obs,
        }
        asistencias.append(existente)
    else:
        existente["estado"] = payload.estado
        existente["origen"] = "PASE_LISTA"
        existente["observacion"] = obs

    save_db(db)
    return {
        "estudianteId": estudiante_id,
        "estado": existente["estado"],
        "origen": existente["origen"],
        "observacion": existente["observacion"],
    }


@router.post("/{sesion_id}/finalizar")
def finalizar_clase_vivo(
    sesion_id: str,
    _user: dict = Depends(require_role(["docente", "admin"])),
):
    """
    Cierra la sesión en vivo, aplica el tope contractual de horas del bloque programado
    (descartando minutos extra para pago) y consolida el acta de asistencias.
    """
    now_dt = _now_lima()

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM sesiones_clase WHERE id = %s LIMIT 1;", (sesion_id,))
                ses = fetch_one_as_dict(cur)
                if not ses:
                    raise HTTPException(status_code=404, detail="Sesión no encontrada.")
                cur.execute("SELECT * FROM courses WHERE id = %s LIMIT 1;", (ses["curso_id"],))
                curso = fetch_one_as_dict(cur)
                if ses.get("estado") == "FINALIZADA":
                    return _serializar_sesion(ses, curso, now_dt)

                updated = _finalizar_sesion_pg(cur, ses, now_dt, automatico=False)
                conn.commit()
                return _serializar_sesion(updated, curso, now_dt)
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al finalizar la clase: {exc}") from exc
        finally:
            conn.close()

    db = load_db()
    ses = next((s for s in db.get("sesionesClase", []) if s.get("id") == sesion_id), None)
    if not ses:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    curso = next((c for c in db.get("courses", []) if c.get("id") == ses.get("curso_id")), None)
    if ses.get("estado") == "FINALIZADA":
        return _serializar_sesion(ses, curso, now_dt)

    updated = _finalizar_sesion_json(db, ses, now_dt, automatico=False)
    save_db(db)
    return _serializar_sesion(updated, curso, now_dt)


@router.get("/resumen-asistencia")
def obtener_resumen_asistencia_cursos(
    estudiante_id: Optional[str] = None,
    ciclo_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Calcula el porcentaje real de asistencia por curso para el estudiante indicado
    (usando sesiones_clase + asistencia_alumnos y actas históricas en attendance_sessions).
    Reemplaza los porcentajes fijos decorativos en las tarjetas de cursos.
    """
    target_id = estudiante_id or str(current_user.get("id") or "")
    if str(current_user.get("rol", "")).casefold() == "estudiante":
        target_id = str(current_user.get("id") or target_id)

    stats: Dict[str, Dict[str, int]] = {}
    sesiones_contadas_por_curso: Dict[str, set] = {}

    def _acumular(cid: str, ses_key: str, estado_est: str):
        if not cid:
            return
        vistos = sesiones_contadas_por_curso.setdefault(cid, set())
        if ses_key in vistos:
            return
        vistos.add(ses_key)
        entry = stats.setdefault(cid, {"total": 0, "asistidas": 0, "tardanzas": 0, "faltas": 0})
        entry["total"] += 1
        if estado_est in ("Presente", "Justificado"):
            entry["asistidas"] += 1
        elif estado_est == "Tardanza":
            entry["asistidas"] += 1
            entry["tardanzas"] += 1
        else:
            entry["faltas"] += 1

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                q = """
                    SELECT s.id AS sesion_id, s.curso_id, COALESCE(a.estado, 'Ausente') AS estado_alumno
                    FROM sesiones_clase s
                    LEFT JOIN courses c ON c.id = s.curso_id
                    LEFT JOIN asistencia_alumnos a
                        ON a.sesion_id = s.id AND a.estudiante_id = %s
                    WHERE s.estado = 'FINALIZADA'
                """
                params: List[Any] = [target_id]
                if ciclo_id:
                    q += " AND COALESCE(s.ciclo_id, c.ciclo_id) = %s"
                    params.append(ciclo_id)
                cur.execute(q, tuple(params))
                for r in fetch_all_as_dict(cur) or []:
                    _acumular(r["curso_id"], str(r["sesion_id"]), r["estado_alumno"])

                # Complementar con sesiones históricas en attendance_sessions (no originadas en ACT-CLS-)
                cur.execute(
                    """
                    SELECT a.id, a."cursoId", a."asistentesPuntuales", a.tardanzas, a.ausentes, c.ciclo_id
                    FROM attendance_sessions a
                    LEFT JOIN courses c ON c.id = a."cursoId"
                    WHERE a.id NOT LIKE 'ACT-%%';
                    """
                )
                for s in fetch_all_as_dict(cur) or []:
                    if ciclo_id and s.get("ciclo_id") != ciclo_id:
                        continue
                    cid = s.get("cursoId")
                    punt = s.get("asistentesPuntuales") or []
                    tard = s.get("tardanzas") or []
                    aus = s.get("ausentes") or []
                    if any(str(x.get("estudianteId")) == target_id for x in punt):
                        _acumular(cid, str(s["id"]), "Presente")
                    elif any(str(x.get("estudianteId")) == target_id for x in tard):
                        _acumular(cid, str(s["id"]), "Tardanza")
                    elif any(str(x.get("estudianteId")) == target_id for x in aus):
                        _acumular(cid, str(s["id"]), "Ausente")
        finally:
            conn.close()
    else:
        db = load_db()
        cursos_map = {c.get("id"): c for c in db.get("courses", [])}
        asist_map = {
            (a.get("sesion_id"), str(a.get("estudiante_id"))): a.get("estado", "Ausente")
            for a in db.get("asistenciaAlumnos", [])
        }
        for s in db.get("sesionesClase", []):
            if s.get("estado") != "FINALIZADA":
                continue
            cid = s.get("curso_id")
            c = cursos_map.get(cid) or {}
            if ciclo_id and (s.get("ciclo_id") or c.get("ciclo_id") or c.get("cicloId")) != ciclo_id:
                continue
            est_state = asist_map.get((s.get("id"), target_id), "Ausente")
            _acumular(cid, str(s.get("id")), est_state)

        for s in db.get("attendanceSessions", []):
            if str(s.get("id") or "").startswith("ACT-"):
                continue
            cid = s.get("cursoId")
            c = cursos_map.get(cid) or {}
            if ciclo_id and (c.get("ciclo_id") or c.get("cicloId")) != ciclo_id:
                continue
            punt = s.get("asistentesPuntuales") or []
            tard = s.get("tardanzas") or []
            aus = s.get("ausentes") or []
            if any(str(x.get("estudianteId")) == target_id for x in punt):
                _acumular(cid, str(s.get("id")), "Presente")
            elif any(str(x.get("estudianteId")) == target_id for x in tard):
                _acumular(cid, str(s.get("id")), "Tardanza")
            elif any(str(x.get("estudianteId")) == target_id for x in aus):
                _acumular(cid, str(s.get("id")), "Ausente")

    resumen = {}
    for cid, d in stats.items():
        pct = round((d["asistidas"] / d["total"]) * 100) if d["total"] > 0 else 100
        resumen[cid] = {
            "totalSesiones": d["total"],
            "asistidas": d["asistidas"],
            "tardanzas": d["tardanzas"],
            "faltas": d["faltas"],
            "porcentaje": pct,
        }
    return {"estudianteId": target_id, "cursos": resumen}
