import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role

router = APIRouter(prefix="/asistencia", tags=["Asistencia y Auditoría Meet"])


class EstudianteAsistencia(BaseModel):
    estudianteId: str
    estudiante: str
    horaRegistro: str


class EstudianteAusente(BaseModel):
    estudianteId: str
    estudiante: str


class SessionAsistenciaCreate(BaseModel):
    id: str
    cursoId: str
    asignatura: str
    docenteId: str
    docente: str
    fecha: str
    horaApertura: str
    modalidad: str
    asistentesPuntuales: List[EstudianteAsistencia] = []
    tardanzas: List[EstudianteAsistencia] = []
    ausentes: List[EstudianteAusente] = []


class MarcacionEstudiante(BaseModel):
    estudianteId: str
    estudiante: str
    horaRegistro: str  # Formato "HH:MM" (24 horas)


class MeetEventRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    cursoId: str = Field(..., alias="curso_id")


@router.get("/sesiones", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_sesiones_asistencia():
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, "cursoId", asignatura, "docenteId", docente, fecha,
                           "horaApertura", modalidad, "asistentesPuntuales", tardanzas, ausentes
                    FROM attendance_sessions
                    ORDER BY created_at DESC;
                    """
                )
                rows = fetch_all_as_dict(cur)
                if rows is not None and len(rows) > 0:
                    return rows
        except Exception as e:
            print(f"[Asistencia] Fallback listar_sesiones_asistencia: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("attendanceSessions", [])


@router.post("/sesiones", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def registrar_sesion_asistencia(session: SessionAsistenciaCreate):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM attendance_sessions WHERE id = %s LIMIT 1;", (session.id,))
                if fetch_one_as_dict(cur):
                    raise HTTPException(status_code=400, detail="El ID de la sesión de asistencia ya existe")
                cur.execute(
                    """
                    INSERT INTO attendance_sessions (
                        id, "cursoId", asignatura, "docenteId", docente, fecha,
                        "horaApertura", modalidad, "asistentesPuntuales", tardanzas, ausentes
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb
                    );
                    """,
                    (
                        session.id,
                        session.cursoId,
                        session.asignatura,
                        session.docenteId,
                        session.docente,
                        session.fecha,
                        session.horaApertura,
                        session.modalidad,
                        json.dumps([e.model_dump() for e in session.asistentesPuntuales]),
                        json.dumps([e.model_dump() for e in session.tardanzas]),
                        json.dumps([e.model_dump() for e in session.ausentes]),
                    ),
                )
                conn.commit()
                return {"mensaje": "Sesión de asistencia registrada correctamente", "id": session.id}
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Asistencia] Fallback crear sesión: {e}")
        finally:
            conn.close()

    db = load_db()
    sessions = db.get("attendanceSessions", [])
    if any(s["id"] == session.id for s in sessions):
        raise HTTPException(status_code=400, detail="El ID de la sesión de asistencia ya existe")

    sessions.append(session.model_dump())
    db["attendanceSessions"] = sessions
    save_db(db)
    return {"mensaje": "Sesión de asistencia registrada correctamente", "id": session.id}


@router.post("/sesiones/{sesion_id}/marcar", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def registrar_marcacion_automatica(sesion_id: str, marcacion: MarcacionEstudiante):
    try:
        fmt = "%H:%M"
        hora_registro = datetime.strptime(marcacion.horaRegistro.strip(), fmt)
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de hora inválido. Use HH:MM en formato de 24 horas.")

    datos_estudiante = {
        "estudianteId": marcacion.estudianteId,
        "estudiante": marcacion.estudiante,
        "horaRegistro": marcacion.horaRegistro,
    }

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM attendance_sessions WHERE id = %s FOR UPDATE;', (sesion_id,))
                sesion_pg = fetch_one_as_dict(cur)
                if sesion_pg:
                    hora_apertura = datetime.strptime(str(sesion_pg["horaApertura"]).strip(), "%H:%M")
                    limite_puntualidad = hora_apertura + timedelta(minutes=10)
                    puntuales = sesion_pg.get("asistentesPuntuales") or []
                    tardanzas = sesion_pg.get("tardanzas") or []
                    if hora_registro <= limite_puntualidad:
                        if not any(e.get("estudianteId") == marcacion.estudianteId for e in puntuales):
                            puntuales.append(datos_estudiante)
                        estado_resultado = "Puntual"
                    else:
                        if not any(e.get("estudianteId") == marcacion.estudianteId for e in tardanzas):
                            tardanzas.append(datos_estudiante)
                        estado_resultado = "Tardanza"
                    cur.execute(
                        'UPDATE attendance_sessions SET "asistentesPuntuales" = %s::jsonb, tardanzas = %s::jsonb WHERE id = %s;',
                        (json.dumps(puntuales), json.dumps(tardanzas), sesion_id),
                    )
                    conn.commit()
                    return {
                        "mensaje": f"Marcación procesada exitosamente como: {estado_resultado}",
                        "estado": estado_resultado,
                        "estudiante": marcacion.estudiante,
                    }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Asistencia] Fallback marcar asistencia: {e}")
        finally:
            conn.close()

    db = load_db()
    sessions = db.get("attendanceSessions", [])
    sesion_encontrada = next((s for s in sessions if s["id"] == sesion_id), None)
    if not sesion_encontrada:
        raise HTTPException(status_code=404, detail="Sesión de asistencia no encontrada")

    hora_apertura = datetime.strptime(sesion_encontrada["horaApertura"].strip(), "%H:%M")
    limite_puntualidad = hora_apertura + timedelta(minutes=10)

    if hora_registro <= limite_puntualidad:
        if not any(e["estudianteId"] == marcacion.estudianteId for e in sesion_encontrada.get("asistentesPuntuales", [])):
            sesion_encontrada.setdefault("asistentesPuntuales", []).append(datos_estudiante)
        estado_resultado = "Puntual"
    else:
        if not any(e["estudianteId"] == marcacion.estudianteId for e in sesion_encontrada.get("tardanzas", [])):
            sesion_encontrada.setdefault("tardanzas", []).append(datos_estudiante)
        estado_resultado = "Tardanza"

    save_db(db)
    return {
        "mensaje": f"Marcación procesada exitosamente como: {estado_resultado}",
        "estado": estado_resultado,
        "estudiante": marcacion.estudiante,
    }


# --- ENDPOINTS AUDITORÍA MEET (Reemplaza las antiguas Edge Functions de InsForge) ---

@router.post("/meet/entrada", status_code=status.HTTP_201_CREATED)
def registrar_entrada_meet(
    req: MeetEventRequest,
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    now_iso = datetime.now(timezone.utc).isoformat()
    docente_id = current_user.get("id")
    docente_email = current_user.get("correo")

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO meet_attendance (docente_id, docente_email, curso_id, evento, hora_entrada, estado_sesion)
                    VALUES (%s, %s, %s, 'ENTRADA', NOW(), 'EN_PROGRESO')
                    RETURNING id, docente_id, docente_email, curso_id AS "cursoId", evento,
                              hora_entrada AS entrada, hora_salida AS salida,
                              estado_sesion AS estado, minutos_efectivos AS "minutosEfectivos";
                    """,
                    (docente_id, docente_email, req.cursoId),
                )
                row = fetch_one_as_dict(cur)
                conn.commit()
                return row
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"No se pudo registrar la entrada a Meet: {e}")
        finally:
            conn.close()

    db = load_db()
    auditoria = db.setdefault("meet_auditoria", [])
    record = {
        "id": len(auditoria) + 1,
        "docente_id": docente_id,
        "docente_email": docente_email,
        "cursoId": req.cursoId,
        "evento": "ENTRADA",
        "entrada": now_iso,
        "salida": None,
        "estado": "EN_PROGRESO",
        "minutosEfectivos": None,
    }
    auditoria.insert(0, record)
    save_db(db)
    return record


@router.post("/meet/salida")
def registrar_salida_meet(
    req: MeetEventRequest,
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE meet_attendance
                    SET evento = 'SALIDA',
                        hora_salida = NOW(),
                        estado_sesion = 'FINALIZADA',
                        minutos_efectivos = ROUND(EXTRACT(EPOCH FROM (NOW() - hora_entrada)) / 60.0, 2)
                    WHERE id = (
                        SELECT id FROM meet_attendance
                        WHERE curso_id = %s AND estado_sesion = 'EN_PROGRESO'
                        ORDER BY hora_entrada DESC
                        LIMIT 1
                    )
                    RETURNING id, docente_id, docente_email, curso_id AS "cursoId", evento,
                              hora_entrada AS entrada, hora_salida AS salida,
                              estado_sesion AS estado, minutos_efectivos AS "minutosEfectivos";
                    """,
                    (req.cursoId,),
                )
                row = fetch_one_as_dict(cur)
                if not row:
                    raise HTTPException(status_code=404, detail="No existe una sesión activa en progreso para finalizar.")
                conn.commit()
                return row
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"No se pudo finalizar la sesión de Meet: {e}")
        finally:
            conn.close()

    db = load_db()
    for r in db.get("meet_auditoria", []):
        if r.get("cursoId") == req.cursoId and r.get("estado") == "EN_PROGRESO":
            r["evento"] = "SALIDA"
            r["salida"] = datetime.now(timezone.utc).isoformat()
            r["estado"] = "FINALIZADA"
            r["minutosEfectivos"] = 1.0
            save_db(db)
            return r
    raise HTTPException(status_code=404, detail="No existe una sesión activa en progreso para finalizar.")


@router.get("/meet/historial/{curso_id}", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def historial_auditoria_meet(curso_id: str):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, docente_id, docente_email, curso_id AS "cursoId", evento,
                           hora_entrada AS entrada, hora_salida AS salida,
                           estado_sesion AS estado, minutos_efectivos AS "minutosEfectivos", created_at
                    FROM meet_attendance
                    WHERE curso_id = %s
                    ORDER BY created_at DESC
                    LIMIT 50;
                    """,
                    (curso_id,),
                )
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Meet Historial] Error consultando PostgreSQL: {e}")
        finally:
            conn.close()

    db = load_db()
    return [r for r in db.get("meet_auditoria", []) if r.get("cursoId") == curso_id]
