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

# Crear un curso (Solo Admin)
@router.post("/cursos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_curso(course: CourseCreate):
    db = load_db()
    courses = db.get("courses", [])
    
    if any(c["id"] == course.id for c in courses):
        raise HTTPException(status_code=400, detail="El ID del curso ya existe")
        
    courses.append(course.dict())
    db["courses"] = courses
    save_db(db)
    
    return {"mensaje": "Curso registrado exitosamente", "id": course.id}

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