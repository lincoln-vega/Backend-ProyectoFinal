from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from database import load_db, save_db
from auth import require_role

router = APIRouter(prefix="/matriculas", tags=["Matriculas y Pagos"])

# --- MODELOS PYDANTIC ---
class MatriculaCreate(BaseModel):
    id: str
    estudiante: str
    dni: str | None = None
    correo: str | None = None
    celular: str | None = None
    programa: str
    ciclo: str
    turno: str
    fechaMatricula: str
    modalidadPago: str
    montoTotal: float
    montoPagado: float = 0
    saldoPendiente: float = 0
    estado: str = "Al día"

class PagoCreate(BaseModel):
    id: str
    matriculaId: str
    estudiante: str
    concepto: str
    monto: float
    metodo: str
    operacion: str
    comprobante: str
    fecha: str
    estado: str = "Aprobado"

# --- ENDPOINTS MATRÍCULAS ---

@router.get("/", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_matriculas():
    """Lista todas las matrículas registradas."""
    db = load_db()
    return db.get("matriculas", [])

@router.post("/", dependencies=[Depends(require_role(["admin"]))], status_code=status.HTTP_201_CREATED)
def crear_matricula(matricula: MatriculaCreate):
    """Permite al Administrador registrar una nueva matrícula."""
    db = load_db()
    if "matriculas" not in db:
        db["matriculas"] = []
    
    if any(m["id"] == matricula.id for m in db["matriculas"]):
        raise HTTPException(status_code=400, detail="El ID de matrícula ya existe")
    
    db["matriculas"].append(matricula.model_dump())
    save_db(db)
    return {"mensaje": "Matrícula registrada exitosamente", "matricula": matricula}

# --- ENDPOINTS PAGOS ---

@router.get("/pagos", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_pagos():
    """Lista el historial de recibos y pagos."""
    db = load_db()
    return db.get("pagos", [])

@router.post("/pagos", dependencies=[Depends(require_role(["admin"]))], status_code=status.HTTP_201_CREATED)
def registrar_pago(pago: PagoCreate):
    """Registra un nuevo pago y actualiza el saldo de la matrícula asociada."""
    db = load_db()
    if "pagos" not in db:
        db["pagos"] = []
    if "matriculas" not in db:
        db["matriculas"] = []

    matricula = next((m for m in db["matriculas"] if m["id"] == pago.matriculaId), None)
    if not matricula:
        raise HTTPException(status_code=404, detail="Matrícula asociada no encontrada")

    # Actualizar saldos
    matricula["montoPagado"] = float(matricula.get("montoPagado", 0)) + pago.monto
    matricula["saldoPendiente"] = max(0.0, float(matricula.get("montoTotal", 0)) - matricula["montoPagado"])
    if matricula["saldoPendiente"] == 0:
        matricula["estado"] = "Pagado Total"

    db["pagos"].insert(0, pago.model_dump())
    save_db(db)
    return {"mensaje": "Pago registrado y saldo actualizado", "pago": pago}
