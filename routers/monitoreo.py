from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from database import load_db
from auth import require_role

router = APIRouter(prefix="/monitoreo", tags=["Monitoreo y Reglas"])

def verificar_cruce_virtual(nuevo_curso: dict, cursos_existentes: list):
    docente_nuevo = nuevo_curso.get("docenteId")
    dias_nuevos = nuevo_curso.get("dias", [])
    
    # Parseamos las horas directamente asumiendo formato de 24h (ej. "15:00") o intentando limpiar espacios
    try:
        inicio_nuevo = datetime.strptime(str(nuevo_curso.get("horaInicio")).strip(), "%H:%M").time()
        fin_nuevo = datetime.strptime(str(nuevo_curso.get("horaFin")).strip(), "%H:%M").time()
    except ValueError:
        # Por si viene en formato am/pm u otro, intentamos un fallback o lo dejamos pasar
        inicio_nuevo = datetime.strptime(str(nuevo_curso.get("horaInicio")).strip(), "%I:%M %p").time()
        fin_nuevo = datetime.strptime(str(nuevo_curso.get("horaFin")).strip(), "%I:%M %p").time()

    for c in cursos_existentes:
        if c.get("docenteId") == docente_nuevo:
            dias_existentes = c.get("dias", [])
            dias_en_comun = set(dias_nuevos).intersection(set(dias_existentes))
            
            if dias_en_comun:
                try:
                    inicio_ex = datetime.strptime(str(c.get("horaInicio")).strip(), "%H:%M").time()
                    fin_ex = datetime.strptime(str(c.get("horaFin")).strip(), "%H:%M").time()
                except ValueError:
                    inicio_ex = datetime.strptime(str(c.get("horaInicio")).strip(), "%I:%M %p").time()
                    fin_ex = datetime.strptime(str(c.get("horaFin")).strip(), "%I:%M %p").time()
                
                # Condición de solapamiento de intervalos
                if inicio_nuevo < fin_ex and fin_nuevo > inicio_ex:
                    return {
                        "status": "conflict",
                        "detail": f"Conflicto: El docente ya tiene dictando el curso '{c.get('asignatura')}' en el mismo horario los días {list(dias_en_comun)}."
                    }
                    
    return {
        "status": "success",
        "mensaje": "Horario disponible. Sin cruces para el docente."
    }

@router.post("/validar-curso")
def evaluar_nuevo_curso(curso: dict, current_user: dict = Depends(require_role(["admin"]))):
    db = load_db()
    cursos_existentes = db.get("courses", [])
    
    resultado = verificar_cruce_virtual(curso, cursos_existentes)
    
    if resultado["status"] == "conflict":
        print(f"[BLOQUEADO] El curso '{curso.get('asignatura', 'Nuevo')}' no se puede registrar porque está en el rango de horario de otro docente.")
        raise HTTPException(status_code=400, detail=resultado["detail"])
        
    print(f"[EXITO] Curso registrado correctamente. Sin cruces de horario.")
    return {
        "mensaje": resultado["mensaje"],
        "estado": "Aprobado"
    }