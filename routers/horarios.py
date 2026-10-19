from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List
from database import load_db, save_db
from auth import require_role

router = APIRouter(prefix="/horarios", tags=["Horarios y Cursos"])

class CourseCreate(BaseModel):
    id: str
    asignatura: str
    area: str
    carrera: str
    docenteId: str
    docente: str
    dias: List[str]
    horaInicio: str
    horaFin: str
    meetUrl: str
    repositorio: str
    estado: str = "Activo"

# Listar todos los cursos (Cronograma global)
@router.get("/cursos", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_cursos():
    db = load_db()
    return db.get("courses", [])

# Obtener un curso por su ID
@router.get("/cursos/{curso_id}", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def obtener_curso(curso_id: str):
    db = load_db()
    curso = next((c for c in db.get("courses", []) if c.get("id") == curso_id), None)

    if curso is None:
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    return curso

# Crear un curso (Solo Admin)
@router.post("/cursos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_curso(course: CourseCreate):
    db = load_db()
    courses = db.get("courses", [])
    
    if any(c["id"] == course.id for c in courses):
        raise HTTPException(status_code=400, detail="El ID del curso ya existe")
        
    courses.append(course.model_dump() if hasattr(course, "model_dump") else course.dict())
    db["courses"] = courses
    save_db(db)
    
    return {"mensaje": "Curso registrado exitosamente", "id": course.id}

# Editar un curso (Solo Admin)
@router.put("/cursos/{curso_id}", dependencies=[Depends(require_role(["admin"]))])
def editar_curso(curso_id: str, course: CourseCreate):
    if course.id != curso_id:
        raise HTTPException(
            status_code=400,
            detail="El ID del curso en la ruta y en el cuerpo debe coincidir"
        )

    db = load_db()
    courses = db.get("courses", [])
    curso_index = next(
        (index for index, current_course in enumerate(courses)
         if current_course.get("id") == curso_id),
        None
    )

    if curso_index is None:
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    courses[curso_index] = course.model_dump() if hasattr(course, "model_dump") else course.dict()
    db["courses"] = courses
    save_db(db)

    return {"mensaje": "Curso actualizado exitosamente", "id": curso_id}

# Eliminar lógicamente un curso (Solo Admin)
@router.delete("/cursos/{curso_id}", dependencies=[Depends(require_role(["admin"]))])
def eliminar_curso(curso_id: str):
    db = load_db()
    courses = db.get("courses", [])
    curso = next((current_course for current_course in courses
                  if current_course.get("id") == curso_id), None)

    if curso is None:
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    curso["estado"] = "Inactivo"
    db["courses"] = courses
    save_db(db)

    return {"mensaje": "Curso eliminado lógicamente", "id": curso_id}

# API del Alumno / Docente: Filtrar clases por día exacto (ej: /horarios/hoy/Lunes)
@router.get("/hoy/{dia}", dependencies=[Depends(require_role(["estudiante", "admin", "docente"]))])
def clases_por_dia(dia: str):
    db = load_db()
    courses = db.get("courses", [])
    
    clases_hoy = [
        c for c in courses 
        if any(d.lower() == dia.lower() for d in c.get("dias", []))
    ]
    
    return {
        "dia_consultado": dia,
        "total_clases": len(clases_hoy),
        "clases": clases_hoy
    }