from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List
from database import load_db, save_db
from auth import require_role

router = APIRouter(prefix="/asistencia", tags=["Asistencia"])

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

# Listar todas las sesiones de asistencia
@router.get("/sesiones", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_sesiones_asistencia():
    db = load_db()
    return db.get("attendanceSessions", [])

# Registrar una nueva sesión de asistencia (Docente o Admin)
@router.post("/sesiones", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def registrar_sesion_asistencia(session: SessionAsistenciaCreate):
    db = load_db()
    sessions = db.get("attendanceSessions", [])
    
    if any(s["id"] == session.id for s in sessions):
        raise HTTPException(status_code=400, detail="El ID de la sesión de asistencia ya existe")
        
    sessions.append(session.dict())
    db["attendanceSessions"] = sessions
    save_db(db)
    
    return {"mensaje": "Sesión de asistencia registrada correctamente", "id": session.id}