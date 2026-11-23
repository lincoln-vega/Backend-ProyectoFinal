from datetime import datetime, timedelta
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

class MarcacionEstudiante(BaseModel):
    estudianteId: str
    estudiante: str
    horaRegistro: str  # Formato "HH:MM" (24 horas)

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
        print(f"[ERROR - CREAR SESIÓN ASISTENCIA] Ocurrió un error al registrar la sesión '{session.id}' del curso '{session.asignatura}'. Motivo: El ID de la sesión ya existe.")
        raise HTTPException(status_code=400, detail="El ID de la sesión de asistencia ya existe")
        
    sessions.append(session.model_dump() if hasattr(session, "model_dump") else session.dict())
    db["attendanceSessions"] = sessions
    save_db(db)
    print(f"[OK - CREAR SESIÓN ASISTENCIA] Sesión de asistencia '{session.id}' del curso '{session.asignatura}' (Docente: {session.docente}) registrada correctamente.")
    
    return {"mensaje": "Sesión de asistencia registrada correctamente", "id": session.id}

# Registrar marcación automática de asistencia (Puntual o Tardanza)
@router.post("/sesiones/{sesion_id}/marcar", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def registrar_marcacion_automatica(sesion_id: str, marcacion: MarcacionEstudiante):
    db = load_db()
    sessions = db.get("attendanceSessions", [])
    
    sesion_encontrada = None
    for s in sessions:
        if s["id"] == sesion_id:
            sesion_encontrada = s
            break
            
    if not sesion_encontrada:
        print(f"[ERROR - MARCAR ASISTENCIA] Ocurrió un error al marcar asistencia del estudiante '{marcacion.estudiante}'. Motivo: Sesión '{sesion_id}' no encontrada.")
        raise HTTPException(status_code=404, detail="Sesión de asistencia no encontrada")
        
    try:
        fmt = "%H:%M"
        hora_apertura = datetime.strptime(sesion_encontrada["horaApertura"].strip(), fmt)
        hora_registro = datetime.strptime(marcacion.horaRegistro.strip(), fmt)
    except ValueError as e:
        print(f"[ERROR - MARCAR ASISTENCIA] Ocurrió un error al marcar asistencia de '{marcacion.estudiante}'. Motivo: Formato de hora inválido ({marcacion.horaRegistro}). Detalle: {e}")
        raise HTTPException(status_code=400, detail="Formato de hora inválido. Use HH:MM en formato de 24 horas.")
        
    # Margen de tolerancia de 10 minutos para considerar puntualidad
    tolerancia_minutos = 10
    limite_puntualidad = hora_apertura + timedelta(minutes=tolerancia_minutos)
    
    datos_estudiante = {
        "estudianteId": marcacion.estudianteId,
        "estudiante": marcacion.estudiante,
        "horaRegistro": marcacion.horaRegistro
    }
    
    # Motor de decisión para clasificar automáticamente
    if hora_registro <= limite_puntualidad:
        if not any(e["estudianteId"] == marcacion.estudianteId for e in sesion_encontrada.get("asistentesPuntuales", [])):
            sesion_encontrada.setdefault("asistentesPuntuales", []).append(datos_estudiante)
        estado_resultado = "Puntual"
    else:
        if not any(e["estudianteId"] == marcacion.estudianteId for e in sesion_encontrada.get("tardanzas", [])):
            sesion_encontrada.setdefault("tardanzas", []).append(datos_estudiante)
        estado_resultado = "Tardanza"
        
    save_db(db)
    print(f"[OK - MARCAR ASISTENCIA] Asistencia del estudiante '{marcacion.estudiante}' en la sesión '{sesion_id}' registrada correctamente como '{estado_resultado}'.")
    
    return {
        "mensaje": f"Marcación procesada exitosamente como: {estado_resultado}",
        "estado": estado_resultado,
        "estudiante": marcacion.estudiante
    }