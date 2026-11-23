import json
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, AliasChoices
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role
from routers.usuarios import parse_course_list
from routers.monitoreo import verificar_cruce_virtual, obtener_cursos_para_validacion

router = APIRouter(prefix="/horarios", tags=["Horarios y Cursos"])


class CourseCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    asignatura: str
    area: str
    carrera: str
    docenteId: str
    docente: str
    dias: List[str]
    horaInicio: str
    horaFin: str
    meetUrl: Optional[str] = ""
    repositorio: Optional[str] = ""
    estado: str = "Activo"
    ciclovirtual: Optional[str] = Field(
        default="Semestral",
        validation_alias=AliasChoices("ciclovirtual", "cicloVirtual"),
    )


def sincronizar_cursos_asignados_docente(course_id: str, new_docente_id: Optional[str], old_docente_id: Optional[str] = None):
    """
    Sincroniza el ID del curso dentro del campo cursosAsignados del docente titular en public.users
    (y en db.json como respaldo), retirándolo del docente anterior si hubo cambio de titular.
    """
    course_id_str = str(course_id).strip()
    new_id_str = str(new_docente_id or "").strip()
    old_id_str = str(old_docente_id or "").strip()
    if not course_id_str:
        return

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # 1. Si hubo docente anterior distinto al nuevo, retirar el curso de su lista
                if old_id_str and old_id_str != new_id_str:
                    cur.execute('SELECT id, "cursosAsignados" FROM users WHERE id = %s;', (old_id_str,))
                    old_row = fetch_one_as_dict(cur)
                    if old_row:
                        current_old = parse_course_list(old_row.get("cursosAsignados"))
                        if course_id_str in current_old:
                            updated_old = [cid for cid in current_old if cid != course_id_str]
                            cur.execute(
                                'UPDATE users SET "cursosAsignados" = %s::jsonb WHERE id = %s;',
                                (json.dumps(updated_old), old_id_str),
                            )

                # 2. Agregar el curso al nuevo docente titular
                if new_id_str:
                    cur.execute('SELECT id, "cursosAsignados" FROM users WHERE id = %s;', (new_id_str,))
                    new_row = fetch_one_as_dict(cur)
                    if new_row:
                        current_new = parse_course_list(new_row.get("cursosAsignados"))
                        if course_id_str not in current_new:
                            updated_new = [*current_new, course_id_str]
                            cur.execute(
                                'UPDATE users SET "cursosAsignados" = %s::jsonb WHERE id = %s;',
                                (json.dumps(updated_new), new_id_str),
                            )
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Horarios] Advertencia al sincronizar cursosAsignados en PostgreSQL: {e}")
        finally:
            conn.close()

    # Sincronizar también en db.json local
    try:
        db = load_db()
        users = db.get("users", [])
        changed = False
        for u in users:
            uid = str(u.get("id") or "").strip()
            assigned = parse_course_list(u.get("cursosAsignados"))
            if old_id_str and uid == old_id_str and old_id_str != new_id_str:
                if course_id_str in assigned:
                    u["cursosAsignados"] = [cid for cid in assigned if cid != course_id_str]
                    changed = True
            elif new_id_str and uid == new_id_str:
                if course_id_str not in assigned:
                    u["cursosAsignados"] = [*assigned, course_id_str]
                    changed = True
        if changed:
            save_db(db)
    except Exception as e:
        print(f"[Horarios] Advertencia al sincronizar cursosAsignados en db.json: {e}")


# Listar todos los cursos (Cronograma global)
@router.get("/cursos", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_cursos():
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, asignatura, area, carrera, "docenteId", docente,
                           dias, "horaInicio", "horaFin", "meetUrl", repositorio,
                           estado, ciclovirtual, created_at
                    FROM courses
                    ORDER BY created_at DESC;
                    """
                )
                rows = fetch_all_as_dict(cur)
                if rows:
                    return rows
        except Exception as e:
            print(f"[Horarios] Fallback listar_cursos a db.json: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("courses", [])


# Obtener un curso por su ID
@router.get("/cursos/{curso_id}", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def obtener_curso(curso_id: str):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, asignatura, area, carrera, "docenteId", docente,
                           dias, "horaInicio", "horaFin", "meetUrl", repositorio,
                           estado, ciclovirtual, created_at
                    FROM courses
                    WHERE id = %s
                    LIMIT 1;
                    """,
                    (curso_id,),
                )
                row = fetch_one_as_dict(cur)
                if row:
                    return row
        except Exception as e:
            print(f"[Horarios] Fallback obtener_curso a db.json: {e}")
        finally:
            conn.close()

    db = load_db()
    curso = next((c for c in db.get("courses", []) if c.get("id") == curso_id), None)
    if curso is None:
        raise HTTPException(status_code=404, detail="Curso no encontrado")
    return curso


# Crear un curso (Solo Admin)
@router.post("/cursos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_curso(course: CourseCreate):
    course_dict = course.model_dump()
    existing_courses = obtener_cursos_para_validacion()

    if any(c.get("id") == course.id for c in existing_courses):
        print(f"[ERROR - CREAR CURSO] Ocurrió un error al crear el curso '{course.asignatura}' (ID: {course.id}). Motivo: El ID del curso ya existe.")
        raise HTTPException(status_code=400, detail="El ID del curso ya existe")

    cruce = verificar_cruce_virtual(course_dict, existing_courses)
    if cruce["status"] == "conflict":
        print(f"[ERROR - CREAR CURSO] Ocurrió un error al crear el curso '{course.asignatura}'. Motivo: {cruce['detail']}")
        raise HTTPException(status_code=400, detail=cruce["detail"])

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO courses (
                        id, asignatura, area, carrera, "docenteId", docente,
                        dias, "horaInicio", "horaFin", "meetUrl", repositorio,
                        estado, ciclovirtual, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    RETURNING *;
                    """,
                    (
                        course.id,
                        course.asignatura,
                        course.area,
                        course.carrera,
                        course.docenteId,
                        course.docente,
                        json.dumps(course.dias),
                        course.horaInicio,
                        course.horaFin,
                        course.meetUrl,
                        course.repositorio,
                        course.estado,
                        course.ciclovirtual or "Semestral",
                    ),
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Horarios] Advertencia al insertar curso en Postgres: {e}")
        finally:
            conn.close()

    db = load_db()
    courses = db.get("courses", [])
    if not any(c.get("id") == course.id for c in courses):
        courses.append(course_dict)
        db["courses"] = courses
        save_db(db)

    sincronizar_cursos_asignados_docente(course.id, course.docenteId)
    print(f"[OK - CREAR CURSO] Curso '{course.asignatura}' (ID: {course.id}, Docente: {course.docente}) creado correctamente.")

    return {"mensaje": "Curso registrado exitosamente", "id": course.id, "curso": course_dict}


# Editar un curso (Solo Admin)
@router.put("/cursos/{curso_id}", dependencies=[Depends(require_role(["admin"]))])
def editar_curso(curso_id: str, course: CourseCreate):
    if course.id != curso_id:
        print(f"[ERROR - EDITAR CURSO] Ocurrió un error al actualizar el curso '{course.asignatura}'. Motivo: El ID en la ruta ({curso_id}) no coincide con el cuerpo ({course.id}).")
        raise HTTPException(
            status_code=400,
            detail="El ID del curso en la ruta y en el cuerpo debe coincidir",
        )

    course_dict = course.model_dump()
    existing_courses = obtener_cursos_para_validacion()
    curso_previo = next((c for c in existing_courses if c.get("id") == curso_id), None)

    if curso_previo is None:
        print(f"[ERROR - EDITAR CURSO] Ocurrió un error al actualizar el curso '{course.asignatura}' (ID: {curso_id}). Motivo: Curso no encontrado.")
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    # Validar que la edición no genere cruce con otros cursos activos del docente (excluyendo curso_id)
    cruce = verificar_cruce_virtual(course_dict, existing_courses)
    if cruce["status"] == "conflict":
        print(f"[ERROR - EDITAR CURSO] Ocurrió un error al actualizar el curso '{course.asignatura}' (ID: {curso_id}). Motivo: {cruce['detail']}")
        raise HTTPException(status_code=400, detail=cruce["detail"])

    old_docente_id = curso_previo.get("docenteId")

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE courses
                    SET asignatura = %s,
                        area = %s,
                        carrera = %s,
                        "docenteId" = %s,
                        docente = %s,
                        dias = %s::jsonb,
                        "horaInicio" = %s,
                        "horaFin" = %s,
                        "meetUrl" = %s,
                        repositorio = %s,
                        estado = %s,
                        ciclovirtual = %s
                    WHERE id = %s;
                    """,
                    (
                        course.asignatura,
                        course.area,
                        course.carrera,
                        course.docenteId,
                        course.docente,
                        json.dumps(course.dias),
                        course.horaInicio,
                        course.horaFin,
                        course.meetUrl,
                        course.repositorio,
                        course.estado,
                        course.ciclovirtual or "Semestral",
                        curso_id,
                    ),
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Horarios] Advertencia al actualizar curso en Postgres: {e}")
        finally:
            conn.close()

    db = load_db()
    courses = db.get("courses", [])
    curso_index = next(
        (index for index, current_course in enumerate(courses) if current_course.get("id") == curso_id),
        None,
    )
    if curso_index is not None:
        courses[curso_index] = course_dict
        db["courses"] = courses
        save_db(db)

    sincronizar_cursos_asignados_docente(curso_id, course.docenteId, old_docente_id)
    print(f"[OK - EDITAR CURSO] Curso '{course.asignatura}' (ID: {curso_id}, Docente: {course.docente}) actualizado correctamente.")

    return {"mensaje": "Curso actualizado exitosamente", "id": curso_id, "curso": course_dict}


# Eliminar lógicamente un curso (Solo Admin)
@router.delete("/cursos/{curso_id}", dependencies=[Depends(require_role(["admin"]))])
def eliminar_curso(curso_id: str):
    found = False
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE courses SET estado = 'Inactivo' WHERE id = %s RETURNING id;", (curso_id,))
                if cur.fetchone():
                    found = True
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Horarios] Advertencia al desactivar curso en Postgres: {e}")
        finally:
            conn.close()

    db = load_db()
    courses = db.get("courses", [])
    curso = next((current_course for current_course in courses if current_course.get("id") == curso_id), None)

    if curso is not None:
        found = True
        curso["estado"] = "Inactivo"
        db["courses"] = courses
        save_db(db)

    if not found:
        print(f"[ERROR - ELIMINAR CURSO] Ocurrió un error al eliminar el curso '{curso_id}'. Motivo: Curso no encontrado.")
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    print(f"[OK - ELIMINAR CURSO] Curso '{curso_id}' eliminado lógicamente (Inactivo) correctamente.")
    return {"mensaje": "Curso eliminado lógicamente", "id": curso_id}


# API del Alumno / Docente: Filtrar clases por día exacto (ej: /horarios/hoy/Lunes)
@router.get("/hoy/{dia}", dependencies=[Depends(require_role(["estudiante", "admin", "docente"]))])
def clases_por_dia(dia: str):
    courses = obtener_cursos_para_validacion()

    clases_hoy = [
        c
        for c in courses
        if any(str(d).lower() == dia.lower() for d in (c.get("dias") or []))
    ]

    return {
        "dia_consultado": dia,
        "total_clases": len(clases_hoy),
        "clases": clases_hoy,
    }
