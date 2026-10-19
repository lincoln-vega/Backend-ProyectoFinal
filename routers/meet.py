from datetime import datetime, timezone
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import require_role

router = APIRouter(prefix="/meet", tags=["Asistencia Docente"])
DB_PATH = "db.json"


def get_db() -> dict:
    if not os.path.exists(DB_PATH):
        return {"courses": [], "meet_auditoria": []}
    with open(DB_PATH, "r", encoding="utf-8") as database_file:
        return json.load(database_file)


def save_db(data: dict) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as database_file:
        json.dump(data, database_file, indent=4, ensure_ascii=False)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def find_course(db: dict, course_id: str) -> dict:
    courses = db.get("courses", db.get("cursos", []))
    course = next((item for item in courses if item.get("id") == course_id), None)
    if course is None:
        raise HTTPException(status_code=404, detail="Curso no encontrado")
    return course


def course_meet_url(course: dict) -> str:
    meet_url = course.get("meetUrl") or course.get("meet_url")
    if not meet_url:
        raise HTTPException(
            status_code=409,
            detail="El curso no tiene un enlace de Google Meet configurado",
        )
    return meet_url


def ensure_teacher(
    docente_email: str, current_user: dict
) -> None:
    if docente_email != current_user.get("correo"):
        raise HTTPException(
            status_code=403,
            detail="Solo puedes registrar tu propia asistencia",
        )


@router.post("/cursos/{curso_id}/entrada")
def registrar_entrada(
    curso_id: str,
    docente_email: str = Query(..., min_length=3),
    current_user: dict = Depends(require_role(["docente"])),
):
    db = get_db()
    course = find_course(db, curso_id)
    ensure_teacher(docente_email, current_user)

    if course.get("docenteId") != current_user.get("id"):
        raise HTTPException(
            status_code=403,
            detail="Solo el docente asignado puede registrar asistencia en este curso",
        )

    meet_url = course_meet_url(course)
    auditorias = db.setdefault("meet_auditoria", [])
    active_session = next(
        (
            item
            for item in reversed(auditorias)
            if item.get("docente_email") == docente_email
            and item.get("curso_id") == curso_id
            and item.get("estado_sesion") == "EN_PROGRESO"
        ),
        None,
    )
    if active_session:
        raise HTTPException(
            status_code=409,
            detail="Ya existe una sesión de asistencia activa para este curso",
        )

    entered_at = now_utc()
    auditorias.append(
        {
            "docente_email": docente_email,
            "curso_id": curso_id,
            "evento": "ENTRADA",
            "hora_entrada": entered_at.isoformat(),
            "hora_salida": None,
            "estado_sesion": "EN_PROGRESO",
            "minutos_efectivos": None,
        }
    )
    save_db(db)

    return {
        "status": "Entrada registrada exitosamente",
        "meet_url": meet_url,
        "hora_ingreso": entered_at.isoformat(),
        "estado_sesion": "EN_PROGRESO",
    }


@router.post("/cursos/{curso_id}/salida")
def registrar_salida(
    curso_id: str,
    docente_email: str = Query(..., min_length=3),
    current_user: dict = Depends(require_role(["docente"])),
):
    db = get_db()
    find_course(db, curso_id)
    ensure_teacher(docente_email, current_user)

    auditorias = db.setdefault("meet_auditoria", [])
    active_session = next(
        (
            item
            for item in reversed(auditorias)
            if item.get("docente_email") == docente_email
            and item.get("curso_id") == curso_id
            and item.get("estado_sesion") == "EN_PROGRESO"
        ),
        None,
    )
    if active_session is None:
        raise HTTPException(
            status_code=400,
            detail="No se encontró una entrada activa para finalizar",
        )

    exited_at = now_utc()
    try:
        entered_at = datetime.fromisoformat(active_session["hora_entrada"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="La sesión activa tiene una hora de entrada inválida",
        ) from exc

    minutes = round((exited_at - entered_at).total_seconds() / 60, 2)
    active_session.update(
        {
            "evento": "SALIDA",
            "hora_salida": exited_at.isoformat(),
            "estado_sesion": "FINALIZADO",
            "minutos_efectivos": minutes,
        }
    )
    save_db(db)

    return {
        "status": "Salida registrada exitosamente",
        "hora_salida": exited_at.isoformat(),
        "minutos_efectivos": minutes,
        "estado_sesion": "FINALIZADO",
    }


@router.get("/cursos/{curso_id}/auditoria")
def listar_auditoria(
    curso_id: str,
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    db = get_db()
    course = find_course(db, curso_id)
    if current_user.get("rol") == "docente" and course.get("docenteId") != current_user.get("id"):
        raise HTTPException(
            status_code=403,
            detail="Solo puedes consultar tu propia asistencia",
        )
    return [
        item for item in db.get("meet_auditoria", [])
        if item.get("curso_id") == curso_id
    ]
