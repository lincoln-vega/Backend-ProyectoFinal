import json
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role
from routers.usuarios import parse_course_list

router = APIRouter(prefix="/matriculas", tags=["Matriculas y Pagos"])


# --- MODELOS PYDANTIC ---
class MatriculaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    estudianteId: Optional[str] = None
    estudiante: str
    dni: Optional[str] = None
    correo: Optional[str] = None
    celular: Optional[str] = None
    programa: str
    ciclo: str
    turno: str
    fechaMatricula: str
    modalidadPago: str
    montoTotal: float
    montoPagado: float = 0
    saldoPendiente: float = 0
    estado: str = "Al día"
    cursosInscritos: Optional[List[str]] = Field(default=None)

    @field_validator("cursosInscritos", mode="before")
    @classmethod
    def normalizar_cursos(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        return parse_course_list(v)


class PagoCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

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


def sincronizar_cursos_estudiante(
    estudiante_id: Optional[str],
    dni: Optional[str],
    correo: Optional[str],
    nuevos_cursos: Optional[List[str]],
) -> List[str]:
    """
    Actualiza automáticamente la columna cursosInscritos del estudiante en public.users
    (y en db.json) haciendo unión sin duplicados con los cursos que ya tuviera inscritos.
    """
    if not nuevos_cursos:
        return []

    clean_new = parse_course_list(nuevos_cursos)
    if not clean_new:
        return []

    merged: List[str] = list(dict.fromkeys(clean_new))

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = 'SELECT id, "cursosInscritos" FROM users WHERE '
                clauses = []
                params = []
                if estudiante_id:
                    clauses.append("id = %s")
                    params.append(estudiante_id)
                if dni:
                    clauses.append("dni = %s")
                    params.append(dni)
                if correo:
                    clauses.append("LOWER(correo) = LOWER(%s)")
                    params.append(correo)

                if clauses:
                    query += " OR ".join(clauses) + " LIMIT 1;"
                    cur.execute(query, tuple(params))
                    row = fetch_one_as_dict(cur)
                    if row:
                        existing = parse_course_list(row.get("cursosInscritos"))
                        merged = list(dict.fromkeys([*existing, *clean_new]))
                        cur.execute(
                            'UPDATE users SET "cursosInscritos" = %s::jsonb WHERE id = %s;',
                            (json.dumps(merged), row["id"]),
                        )
                        conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Matriculas] Advertencia al sincronizar cursosInscritos en Postgres: {e}")
        finally:
            conn.close()

    # Sincronizar también en db.json
    try:
        db = load_db()
        users = db.get("users", [])
        for u in users:
            match = (
                (estudiante_id and u.get("id") == estudiante_id)
                or (dni and u.get("dni") == dni)
                or (correo and str(u.get("correo", "")).lower() == str(correo).lower())
            )
            if match:
                existing = parse_course_list(u.get("cursosInscritos"))
                merged = list(dict.fromkeys([*existing, *clean_new]))
                u["cursosInscritos"] = merged
                save_db(db)
                break
    except Exception as e:
        print(f"[Matriculas] Advertencia al sincronizar cursosInscritos en db.json: {e}")

    return merged


# --- ENDPOINTS MATRÍCULAS ---

@router.get("/", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_matriculas():
    """Lista todas las matrículas registradas."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, estudiante, dni, correo, celular, programa, ciclo, turno,
                           "fechaMatricula", "modalidadPago", "montoTotal", "montoPagado",
                           "saldoPendiente", estado, created_at
                    FROM matriculas
                    ORDER BY created_at DESC;
                    """
                )
                rows = fetch_all_as_dict(cur)
                if rows:
                    return rows
        except Exception as e:
            print(f"[Matriculas] Fallback listar_matriculas a db.json: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("matriculas", [])


@router.post("/", dependencies=[Depends(require_role(["admin"]))], status_code=status.HTTP_201_CREATED)
def crear_matricula(matricula: MatriculaCreate):
    """Permite al Administrador registrar una nueva matrícula y sincronizar cursosInscritos del estudiante."""
    db = load_db()
    if "matriculas" not in db:
        db["matriculas"] = []

    if any(m["id"] == matricula.id for m in db["matriculas"]):
        print(f"[ERROR - CREAR MATRÍCULA] Ocurrió un error al registrar la matrícula '{matricula.id}' para '{matricula.estudiante}'. Motivo: El ID de matrícula ya existe.")
        raise HTTPException(status_code=400, detail="El ID de matrícula ya existe")

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM matriculas WHERE id = %s LIMIT 1;", (matricula.id,))
                if fetch_one_as_dict(cur):
                    print(f"[ERROR - CREAR MATRÍCULA] Ocurrió un error al registrar la matrícula '{matricula.id}' para '{matricula.estudiante}'. Motivo: El ID de matrícula ya existe en PostgreSQL.")
                    raise HTTPException(status_code=400, detail="El ID de matrícula ya existe")

                cur.execute(
                    """
                    INSERT INTO matriculas (
                        id, estudiante, dni, correo, celular, programa, ciclo, turno,
                        "fechaMatricula", "modalidadPago", "montoTotal", "montoPagado",
                        "saldoPendiente", estado, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    );
                    """,
                    (
                        matricula.id,
                        matricula.estudiante,
                        matricula.dni,
                        matricula.correo,
                        matricula.celular,
                        matricula.programa,
                        matricula.ciclo,
                        matricula.turno,
                        matricula.fechaMatricula,
                        matricula.modalidadPago,
                        matricula.montoTotal,
                        matricula.montoPagado,
                        matricula.saldoPendiente,
                        matricula.estado,
                    ),
                )
                conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Matriculas] Advertencia al insertar matrícula en Postgres: {e}")
        finally:
            conn.close()

    mat_dict = matricula.model_dump()
    db["matriculas"].append(mat_dict)
    save_db(db)

    cursos_actualizados = []
    if matricula.cursosInscritos is not None:
        cursos_actualizados = sincronizar_cursos_estudiante(
            matricula.estudianteId,
            matricula.dni,
            matricula.correo,
            matricula.cursosInscritos,
        )
        mat_dict["cursosInscritos"] = cursos_actualizados

    print(f"[OK - CREAR MATRÍCULA] Matrícula '{matricula.id}' del estudiante '{matricula.estudiante}' (Programa: {matricula.programa}) registrada correctamente.")
    return {
        "mensaje": "Matrícula registrada exitosamente",
        "matricula": mat_dict,
        "cursosInscritos": cursos_actualizados,
    }


# --- ENDPOINTS PAGOS ---

@router.get("/pagos", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def listar_pagos():
    """Lista el historial de recibos y pagos."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, "matriculaId", estudiante, concepto, monto, metodo,
                           operacion, comprobante, fecha, estado, created_at
                    FROM pagos
                    ORDER BY created_at DESC;
                    """
                )
                rows = fetch_all_as_dict(cur)
                if rows:
                    return rows
        except Exception as e:
            print(f"[Pagos] Fallback listar_pagos a db.json: {e}")
        finally:
            conn.close()

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

    conn = get_pg_connection()
    pg_mat = None
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM matriculas WHERE id = %s LIMIT 1;', (pago.matriculaId,))
                pg_mat = fetch_one_as_dict(cur)
                if pg_mat:
                    nuevo_pagado = float(pg_mat.get("montoPagado") or 0) + pago.monto
                    nuevo_saldo = max(0.0, float(pg_mat.get("montoTotal") or 0) - nuevo_pagado)
                    nuevo_estado = "Pagado Total" if nuevo_saldo == 0 else pg_mat.get("estado", "Al día")
                    cur.execute(
                        """
                        UPDATE matriculas
                        SET "montoPagado" = %s, "saldoPendiente" = %s, estado = %s
                        WHERE id = %s;
                        """,
                        (nuevo_pagado, nuevo_saldo, nuevo_estado, pago.matriculaId),
                    )
                    cur.execute(
                        """
                        INSERT INTO pagos (
                            id, "matriculaId", estudiante, concepto, monto,
                            metodo, operacion, comprobante, fecha, estado, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                        );
                        """,
                        (
                            pago.id,
                            pago.matriculaId,
                            pago.estudiante,
                            pago.concepto,
                            pago.monto,
                            pago.metodo,
                            pago.operacion,
                            pago.comprobante,
                            pago.fecha,
                            pago.estado,
                        ),
                    )
                    conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Pagos] Advertencia al registrar pago en Postgres: {e}")
        finally:
            conn.close()

    if not matricula and not pg_mat:
        print(f"[ERROR - REGISTRAR PAGO] Ocurrió un error al registrar el pago '{pago.id}' de '{pago.estudiante}'. Motivo: Matrícula asociada '{pago.matriculaId}' no encontrada.")
        raise HTTPException(status_code=404, detail="Matrícula asociada no encontrada")

    if matricula:
        matricula["montoPagado"] = float(matricula.get("montoPagado", 0)) + pago.monto
        matricula["saldoPendiente"] = max(0.0, float(matricula.get("montoTotal", 0)) - matricula["montoPagado"])
        if matricula["saldoPendiente"] == 0:
            matricula["estado"] = "Pagado Total"

    db["pagos"].insert(0, pago.model_dump())
    save_db(db)
    print(f"[OK - REGISTRAR PAGO] Pago '{pago.id}' (Comprobante: {pago.comprobante}, Monto: S/ {pago.monto}) del estudiante '{pago.estudiante}' registrado correctamente.")
    return {"mensaje": "Pago registrado y saldo actualizado", "pago": pago}
