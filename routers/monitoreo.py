import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from database import load_db, get_pg_connection, fetch_all_as_dict
from auth import require_role

router = APIRouter(prefix="/monitoreo", tags=["Monitoreo y Reglas"])


def _clean_teacher_name(val: Optional[str]) -> str:
    if not val:
        return ""
    cleaned = re.sub(
        r"^(prof\.|profesora|profesor|dra\.|dr\.|ing\.|lic\.|mg\.)\s*",
        "",
        str(val).strip(),
        flags=re.IGNORECASE,
    )
    return cleaned.strip().lower()


def _parse_time(time_str: Any):
    raw = str(time_str or "").strip()
    if not raw:
        return None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Formato de hora inválido: '{raw}'. Use HH:MM (24 horas).")


def obtener_cursos_para_validacion() -> List[Dict[str, Any]]:
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, asignatura, area, carrera, "docenteId", docente,
                           dias, "horaInicio", "horaFin", "meetUrl", repositorio, estado, ciclovirtual
                    FROM courses;
                    """
                )
                rows = fetch_all_as_dict(cur)
                if rows:
                    return rows
        except Exception as e:
            print(f"[Monitoreo] Fallback de Postgres a db.json: {e}")
        finally:
            conn.close()
    db = load_db()
    return db.get("courses", [])


def verificar_cruce_virtual(nuevo_curso: dict, cursos_existentes: list) -> Dict[str, Any]:
    curso_id_actual = str(nuevo_curso.get("id") or nuevo_curso.get("codigo") or "").strip()
    estado_nuevo = str(nuevo_curso.get("estado") or "Activo").strip().lower()

    # Si el curso que se está validando está Inactivo, no genera conflicto horario activo
    if estado_nuevo and estado_nuevo != "activo":
        return {
            "status": "success",
            "valido": True,
            "cruce": False,
            "mensaje": "Curso en estado inactivo. Sin cruces horarios activos.",
        }

    docente_id_nuevo = str(nuevo_curso.get("docenteId") or "").strip()
    docente_nombre_nuevo = _clean_teacher_name(nuevo_curso.get("docente"))
    dias_nuevos = nuevo_curso.get("dias") or []
    if isinstance(dias_nuevos, str):
        dias_nuevos = [d.strip() for d in dias_nuevos.split(",") if d.strip()]

    if not dias_nuevos or not nuevo_curso.get("horaInicio") or not nuevo_curso.get("horaFin"):
        return {
            "status": "success",
            "valido": True,
            "cruce": False,
            "mensaje": "Horario disponible. Sin cruces para el docente.",
        }

    inicio_nuevo = _parse_time(nuevo_curso.get("horaInicio"))
    fin_nuevo = _parse_time(nuevo_curso.get("horaFin"))

    for c in cursos_existentes:
        # 1. Excluir el mismo curso cuando se está editando (evita falso positivo consigo mismo)
        c_id = str(c.get("id") or c.get("codigo") or "").strip()
        if curso_id_actual and c_id and curso_id_actual == c_id:
            continue

        # 2. Solo evaluar cursos con estado == "Activo"
        c_estado = str(c.get("estado") or "Activo").strip().lower()
        if c_estado != "activo":
            continue

        # 3. Verificar coincidencia en el mismo docente (por docenteId o por nombre normalizado)
        c_doc_id = str(c.get("docenteId") or "").strip()
        c_doc_nombre = _clean_teacher_name(c.get("docente"))

        mismo_docente = False
        if docente_id_nuevo and c_doc_id and docente_id_nuevo == c_doc_id:
            mismo_docente = True
        elif docente_nombre_nuevo and c_doc_nombre:
            if (
                docente_nombre_nuevo == c_doc_nombre
                or docente_nombre_nuevo.startswith(c_doc_nombre)
                or c_doc_nombre.startswith(docente_nombre_nuevo)
            ):
                mismo_docente = True

        if not mismo_docente:
            continue

        # 4. Verificar si comparten al menos un día
        dias_existentes = c.get("dias") or []
        if isinstance(dias_existentes, str):
            dias_existentes = [d.strip() for d in dias_existentes.split(",") if d.strip()]

        dias_en_comun = [d for d in dias_nuevos if d in dias_existentes]
        if not dias_en_comun:
            continue

        # 5. Verificar solapamiento estricto: nuevo_inicio < existente_fin and nuevo_fin > existente_inicio
        try:
            inicio_ex = _parse_time(c.get("horaInicio"))
            fin_ex = _parse_time(c.get("horaFin"))
        except ValueError:
            continue

        if not inicio_ex or not fin_ex:
            continue

        if inicio_nuevo < fin_ex and fin_nuevo > inicio_ex:
            dias_str = ", ".join(dias_en_comun)
            doc_display = c.get("docente") or nuevo_curso.get("docente") or docente_id_nuevo
            return {
                "status": "conflict",
                "valido": False,
                "cruce": True,
                "curso": c,
                "detail": (
                    f"Conflicto: El docente {doc_display} ya tiene dictando el curso "
                    f"'{c.get('asignatura')}' en el mismo horario ({c.get('horaInicio')} - {c.get('horaFin')}) "
                    f"los días [{dias_str}]."
                ),
            }

    return {
        "status": "success",
        "valido": True,
        "cruce": False,
        "mensaje": "Horario disponible. Sin cruces para el docente.",
    }


@router.post("/validar-curso")
def evaluar_nuevo_curso(curso: dict, current_user: dict = Depends(require_role(["admin", "docente"]))):
    cursos_existentes = obtener_cursos_para_validacion()

    try:
        resultado = verificar_cruce_virtual(curso, cursos_existentes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if resultado["status"] == "conflict":
        raise HTTPException(status_code=400, detail=resultado["detail"])

    return {
        "valido": True,
        "cruce": False,
        "mensaje": resultado["mensaje"],
        "estado": "Aprobado",
    }
