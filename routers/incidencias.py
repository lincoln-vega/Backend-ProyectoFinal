from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from typing import List, Optional
from database import load_db, save_db
from auth import require_role

router = APIRouter(prefix="/incidencias", tags=["Incidencias y Permutas"])

# --- MODELOS PYDANTIC ---
class IncidenciaCreate(BaseModel):
    id: Optional[str] = None
    docenteId: Optional[str] = None
    docente: str
    dni: Optional[str] = None
    correo: Optional[str] = None
    tipo: str  # Tardanza, Inasistencia Inesperada, Solicitud de Permuta / Reemplazo
    materia: str
    cursoId: Optional[str] = None
    horario: str
    motivo: str
    archivoNombre: Optional[str] = "constancia_o_solicitud.pdf"

class EstadoTransitionUpdate(BaseModel):
    nuevoEstado: str  # "Aprobado" o "Rechazado"
    docenteReemplazo: Optional[str] = None
    docenteReemplazoId: Optional[str] = None
    horarioActualizado: Optional[str] = None
    observaciones: Optional[str] = None

class ParametrosNomina(BaseModel):
    periodo: Optional[str] = "2026-10"
    tarifaHoraDefault: Optional[float] = 50.0
    docenteId: Optional[str] = None

# --- REGLAS DE LA MÁQUINA DE ESTADOS (SCRM-104) ---
ESTADOS_VALIDOS = {"Pendiente", "Aprobado", "Rechazado"}

TRANSICIONES_PERMITIDAS = {
    "Pendiente": {"Aprobado", "Rechazado"},
    "Aprobado": set(),    # Estado terminal
    "Rechazado": set()    # Estado terminal
}

def validar_transicion_estado(estado_actual: str, nuevo_estado: str):
    if nuevo_estado not in ESTADOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado destino '{nuevo_estado}' no es válido. Estados permitidos: {list(ESTADOS_VALIDOS)}"
        )
    
    if estado_actual == nuevo_estado:
        return True

    permitidos = TRANSICIONES_PERMITIDAS.get(estado_actual, set())
    if nuevo_estado not in permitidos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transición prohibida por la Máquina de Estados: no se puede pasar de '{estado_actual}' a '{nuevo_estado}'."
        )
    return True

# --- ENDPOINTS ---

# 1. Listar incidencias con filtros opcionales
@router.get("", dependencies=[Depends(require_role(["admin", "docente"]))])
def listar_incidencias(
    tipo: Optional[str] = Query(None, description="Filtrar por tipo de incidencia o permuta"),
    estado: Optional[str] = Query(None, description="Filtrar por estado (Pendiente, Aprobado, Rechazado)"),
    docenteId: Optional[str] = Query(None, description="Filtrar por ID de docente")
):
    db = load_db()
    incidencias = db.get("incidents", [])
    
    resultado = incidencias
    if tipo:
        resultado = [i for i in resultado if tipo.lower() in i.get("tipo", "").lower()]
    if estado:
        resultado = [i for i in resultado if i.get("estado", "").lower() == estado.lower()]
    if docenteId:
        resultado = [i for i in resultado if i.get("docenteId") == docenteId]
        
    return resultado

# 2. Obtener incidencia por ID
@router.get("/{incidencia_id}", dependencies=[Depends(require_role(["admin", "docente"]))])
def obtener_incidencia(incidencia_id: str):
    db = load_db()
    for inc in db.get("incidents", []):
        if inc.get("id") == incidencia_id:
            return inc
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incidencia {incidencia_id} no encontrada")

# 3. Registrar una nueva incidencia o solicitud de permuta (Docente o Admin)
@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def registrar_incidencia(datos: IncidenciaCreate):
    db = load_db()
    incidencias = db.setdefault("incidents", [])
    
    # Generar ID consecutivo si no se proporciona
    nuevo_id = datos.id or f"INC-D{str(len(incidencias) + 1).zfill(2)}"
    
    nueva_incidencia = datos.model_dump() if hasattr(datos, "model_dump") else datos.dict()
    nueva_incidencia["id"] = nuevo_id
    nueva_incidencia["estado"] = "Pendiente"
    nueva_incidencia["fechaPresentacion"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    incidencias.insert(0, nueva_incidencia)
    save_db(db)
    
    return {
        "mensaje": "Solicitud / Incidencia registrada correctamente en estado 'Pendiente'",
        "incidencia": nueva_incidencia
    }

# 4. Transicionar estado según Máquina de Estados (SCRM-104)
@router.patch("/{incidencia_id}/estado", dependencies=[Depends(require_role(["admin"]))])
def cambiar_estado_incidencia(incidencia_id: str, transicion: EstadoTransitionUpdate):
    db = load_db()
    incidencias = db.get("incidents", [])
    
    incidencia = None
    for inc in incidencias:
        if inc.get("id") == incidencia_id:
            incidencia = inc
            break
            
    if not incidencia:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incidencia {incidencia_id} no encontrada")
        
    estado_actual = incidencia.get("estado", "Pendiente")
    nuevo_estado = transicion.nuevoEstado
    
    # Validar con la Máquina de Estados
    validar_transicion_estado(estado_actual, nuevo_estado)
    
    # Aplicar la transición
    incidencia["estado"] = nuevo_estado
    incidencia["fechaResolucion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if transicion.observaciones:
        incidencia["observacionesResolucion"] = transicion.observaciones
        
    # Si incluye reemplazo o confirmación de horario
    if transicion.docenteReemplazo:
        incidencia["docenteReemplazo"] = transicion.docenteReemplazo
    if transicion.docenteReemplazoId:
        incidencia["docenteReemplazoId"] = transicion.docenteReemplazoId
    if transicion.horarioActualizado:
        incidencia["horarioActualizado"] = transicion.horarioActualizado
        
    # Si se aprueba con reemplazo, actualizar automáticamente el curso académico si existe cursoId
    curso_id = incidencia.get("cursoId")
    if nuevo_estado == "Aprobado" and curso_id and transicion.docenteReemplazo:
        courses = db.get("courses", [])
        for c in courses:
            if c.get("id") == curso_id:
                c["docente"] = transicion.docenteReemplazo
                if transicion.docenteReemplazoId:
                    c["docenteId"] = transicion.docenteReemplazoId
                if transicion.horarioActualizado:
                    c["horario"] = transicion.horarioActualizado
                c["reemplazoActivo"] = True
                break
        db["courses"] = courses

    save_db(db)
    
    return {
        "mensaje": f"Incidencia {incidencia_id} transicionada exitosamente de '{estado_actual}' a '{nuevo_estado}'.",
        "incidencia": incidencia
    }

# 5. Algoritmo del Motor de Nómina y Cruce de Horas (SCRM-105)
@router.post("/nomina/cruce", dependencies=[Depends(require_role(["admin"]))])
def calcular_cruce_nomina(params: ParametrosNomina):
    db = load_db()
    users = db.get("users", [])
    courses = db.get("courses", [])
    incidents = db.get("incidents", [])
    
    # Filtrar docentes
    docentes = [u for u in users if u.get("rol") in ("docente", "profesor")]
    if params.docenteId:
        docentes = [d for d in docentes if d.get("id") == params.docenteId]
        
    tarifa_base = params.tarifaHoraDefault or 50.0
    detalle_docentes = []
    total_horas_programadas_general = 0.0
    total_horas_efectivas_general = 0.0
    total_descuentos_general = 0.0
    total_monto_general = 0.0
    
    for doc in docentes:
        doc_id = doc.get("id")
        nombre_docente = f"{doc.get('nombres', '')} {doc.get('apellidos', '')}".strip() or doc.get("name", "Docente")
        
        # Cursos asignados
        doc_courses = [c for c in courses if c.get("docenteId") == doc_id or nombre_docente.lower() in (c.get("docente", "")).lower()]
        
        # Cada curso tiene 2 sesiones semanales x 4 semanas = 8 sesiones = 16 horas mensuales
        horas_programadas = len(doc_courses) * 16.0
        if horas_programadas == 0:
            horas_programadas = 16.0  # Mínimo estimado para docentes registrados
            
        # Incidencias del docente
        doc_incidents = [
            i for i in incidents 
            if i.get("docenteId") == doc_id or nombre_docente.lower() in (i.get("docente", "")).lower()
        ]
        
        horas_descontadas = 0.0
        minutos_tardanza = 0
        inasistencias_no_justificadas = 0
        justificaciones_aprobadas = 0
        
        for inc in doc_incidents:
            tipo = inc.get("tipo", "").lower()
            estado = inc.get("estado", "")
            
            if "tardanza" in tipo:
                minutos = 15
                if "+20" in tipo:
                    minutos = 20
                minutos_tardanza += minutos
                
                # Si no está aprobada, se descuenta proporcional
                if estado != "Aprobado":
                    horas_descontadas += (minutos / 60.0)
                else:
                    justificaciones_aprobadas += 1
            elif "inasistencia" in tipo:
                if estado != "Aprobado":
                    inasistencias_no_justificadas += 1
                    horas_descontadas += 2.0  # 2 horas de bloque lectivo
                else:
                    justificaciones_aprobadas += 1
            elif "permuta" in tipo or "reemplazo" in tipo:
                if estado == "Aprobado":
                    justificaciones_aprobadas += 1
                    
        horas_efectivas = max(0.0, horas_programadas - horas_descontadas)
        monto_bruto = round(horas_programadas * tarifa_base, 2)
        descuento_pen = round(horas_descontadas * tarifa_base, 2)
        monto_neto = round(horas_efectivas * tarifa_base, 2)
        
        estado_auditoria = "100% Efectivo"
        if horas_descontadas > 0:
            estado_auditoria = "Con Descuentos"
        elif len(doc_incidents) > 0 and justificaciones_aprobadas > 0:
            estado_auditoria = "Justificado / Regularizado"
            
        item = {
            "docenteId": doc_id,
            "docente": nombre_docente,
            "correo": doc.get("correo"),
            "dni": doc.get("dni"),
            "cursosAsignados": len(doc_courses),
            "horasProgramadas": horas_programadas,
            "minutosTardanzaTotal": minutos_tardanza,
            "inasistenciasNoJustificadas": inasistencias_no_justificadas,
            "justificacionesAprobadas": justificaciones_aprobadas,
            "horasDescontadas": round(horas_descontadas, 2),
            "horasEfectivas": round(horas_efectivas, 2),
            "tarifaPorHora": tarifa_base,
            "montoBruto": monto_bruto,
            "descuentoPEN": descuento_pen,
            "montoNeto": monto_neto,
            "estadoAuditoria": estado_auditoria
        }
        
        detalle_docentes.append(item)
        total_horas_programadas_general += horas_programadas
        total_horas_efectivas_general += horas_efectivas
        total_descuentos_general += descuento_pen
        total_monto_general += monto_neto
        
    return {
        "periodo": params.periodo,
        "fechaGeneracion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "resumenGeneral": {
            "totalDocentesAuditados": len(docentes),
            "totalHorasProgramadas": round(total_horas_programadas_general, 2),
            "totalHorasEfectivas": round(total_horas_efectivas_general, 2),
            "eficienciaGlobal": f"{round((total_horas_efectivas_general / total_horas_programadas_general) * 100, 1) if total_horas_programadas_general > 0 else 100}%",
            "totalDescuentosPEN": round(total_descuentos_general, 2),
            "totalPlanillaPEN": round(total_monto_general, 2)
        },
        "detalleDocentes": detalle_docentes
    }
