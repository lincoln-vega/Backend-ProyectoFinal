import json
import re
from typing import List, Optional, Any, Iterable
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, AliasChoices, field_validator
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role
from routers.usuarios import parse_course_list

router = APIRouter(prefix="/matriculas", tags=["Matriculas y Pagos"])


def _next_sequential_code(
    existing_values: Iterable[Optional[str]],
    prefix: str,
    pad_digits: int = 3,
    base_start: int = 1,
) -> str:
    """Calcula el siguiente código correlativo libre (MAX + 1) evitando cualquier colisión de llave primaria."""
    seen = {str(v).strip() for v in existing_values if v}
    max_num = base_start - 1
    pattern = re.compile(r"^" + re.escape(prefix) + r"(\d+)$", re.IGNORECASE)
    for val in seen:
        m = pattern.match(val)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    candidate_num = max_num + 1
    while True:
        candidate = f"{prefix}{str(candidate_num).zfill(pad_digits)}"
        if candidate not in seen:
            return candidate
        candidate_num += 1


# --- MODELOS PYDANTIC ---
class MatriculaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = ""
    estudianteId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("estudianteId", "estudiante_id"),
    )
    cicloId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("cicloId", "ciclo_id"),
    )
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

    id: Optional[str] = ""
    matriculaId: str
    estudiante: str
    concepto: str
    monto: float
    metodo: str
    operacion: str
    comprobante: Optional[str] = ""
    fecha: str
    estado: str = "Aprobado"


def sincronizar_cursos_estudiante(
    estudiante_id: Optional[str],
    dni: Optional[str],
    correo: Optional[str],
    nuevos_cursos: Optional[List[str]],
    external_conn=None,
) -> List[str]:
    """
    Actualiza automáticamente la columna cursosInscritos del estudiante en public.users
    (y en db.json) haciendo unión sin duplicados con los cursos que ya tuviera inscritos.
    Soporta recibir una conexión externa activa para transacciones atómicas.
    """
    if not nuevos_cursos:
        return []

    clean_new = parse_course_list(nuevos_cursos)
    if not clean_new:
        return []

    merged: List[str] = list(dict.fromkeys(clean_new))

    should_close = False
    conn = external_conn
    if conn is None:
        conn = get_pg_connection()
        should_close = True

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
                        if should_close:
                            conn.commit()
        except Exception as e:
            if should_close:
                conn.rollback()
            print(f"[Matriculas] Advertencia al sincronizar cursosInscritos en Postgres: {e}")
            if not should_close:
                raise
        finally:
            if should_close:
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
def listar_matriculas(ciclo_id: Optional[str] = None, correo: Optional[str] = None):
    """Lista las matrículas registradas incluyendo metadatos del ciclo académico (En Curso / Finalizado)."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT m.id, m.estudiante_id AS "estudianteId", m.ciclo_id AS "cicloId",
                           m.estudiante, m.dni, m.correo, m.celular, m.programa, m.ciclo,
                           m.turno, m."fechaMatricula", m."modalidadPago", m."montoTotal",
                           m."montoPagado", m."saldoPendiente", m."cursosInscritos", m.estado,
                           COALESCE(ca.estado, 'En Curso') AS "cicloEstado",
                           COALESCE(ca.es_vigente, true) AS "cicloVigente",
                           ca.fecha_inicio::text AS "cicloFechaInicio",
                           ca.fecha_fin::text AS "cicloFechaFin",
                           m.created_at
                    FROM matriculas m
                    LEFT JOIN ciclos_academicos ca ON ca.id = m.ciclo_id
                    WHERE 1=1
                """
                params = []
                if ciclo_id:
                    query += " AND m.ciclo_id = %s"
                    params.append(ciclo_id)
                if correo:
                    query += " AND LOWER(m.correo) = LOWER(%s)"
                    params.append(correo.strip())
                query += " ORDER BY m.created_at DESC;"
                cur.execute(query, tuple(params))
                rows = fetch_all_as_dict(cur)
                if rows is not None and (len(rows) > 0 or ciclo_id or correo):
                    return rows
        except Exception as e:
            print(f"[Matriculas] Fallback listar_matriculas a db.json: {e}")
        finally:
            conn.close()

    db = load_db()
    mats = db.get("matriculas", [])
    if ciclo_id:
        mats = [m for m in mats if (m.get("cicloId") or m.get("ciclo_id")) == ciclo_id]
    if correo:
        c_low = correo.strip().lower()
        mats = [m for m in mats if str(m.get("correo") or "").lower() == c_low]
    return mats


@router.post("/", dependencies=[Depends(require_role(["admin"]))], status_code=status.HTTP_201_CREATED)
def crear_matricula(matricula: MatriculaCreate):
    """Permite al Administrador registrar una nueva matrícula y sincronizar cursosInscritos del estudiante de forma atómica."""
    db = load_db()
    if "matriculas" not in db:
        db["matriculas"] = []

    existing_mat_ids = {str(m.get("id")).strip() for m in db["matriculas"] if m.get("id")}
    requested_mat_id = (matricula.id or "").strip()

    conn = get_pg_connection()
    cursos_actualizados = []
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM matriculas;")
                for r in fetch_all_as_dict(cur) or []:
                    if r.get("id"):
                        existing_mat_ids.add(str(r["id"]).strip())

                final_mat_id = (
                    requested_mat_id
                    if (requested_mat_id and requested_mat_id not in existing_mat_ids)
                    else _next_sequential_code(existing_mat_ids, "MAT-2026-", 3, 1)
                )

                resolved_ciclo_id = matricula.cicloId
                if not resolved_ciclo_id and matricula.ciclo:
                    cur.execute(
                        "SELECT id FROM ciclos_academicos WHERE LOWER(nombre) = LOWER(%s) OR LOWER(codigo) = LOWER(%s) LIMIT 1;",
                        (matricula.ciclo.strip(), matricula.ciclo.strip()),
                    )
                    c_row = fetch_one_as_dict(cur)
                    if c_row:
                        resolved_ciclo_id = c_row["id"]

                cur.execute(
                    """
                    INSERT INTO matriculas (
                        id, estudiante_id, ciclo_id, estudiante, dni, correo, celular,
                        programa, ciclo, turno, "fechaMatricula", "modalidadPago",
                        "montoTotal", "montoPagado", "saldoPendiente", "cursosInscritos",
                        estado, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW()
                    );
                    """,
                    (
                        final_mat_id,
                        matricula.estudianteId,
                        resolved_ciclo_id,
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
                        json.dumps(matricula.cursosInscritos or []),
                        matricula.estado,
                    ),
                )

                # Transacción atómica: sincronizar cursos del alumno en la misma conexión
                if matricula.cursosInscritos is not None:
                    cursos_actualizados = sincronizar_cursos_estudiante(
                        matricula.estudianteId,
                        matricula.dni,
                        matricula.correo,
                        matricula.cursosInscritos,
                        external_conn=conn,
                    )

                conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Matriculas] Error al insertar matrícula atómica en Postgres: {e}")
            raise HTTPException(status_code=500, detail=f"Error en base de datos al registrar matrícula: {e}")
        finally:
            conn.close()
    else:
        final_mat_id = (
            requested_mat_id
            if (requested_mat_id and requested_mat_id not in existing_mat_ids)
            else _next_sequential_code(existing_mat_ids, "MAT-2026-", 3, 1)
        )
        # Fallback cuando PostgreSQL no está disponible
        if matricula.cursosInscritos is not None:
            cursos_actualizados = sincronizar_cursos_estudiante(
                matricula.estudianteId,
                matricula.dni,
                matricula.correo,
                matricula.cursosInscritos,
            )

    mat_dict = matricula.model_dump()
    mat_dict["id"] = final_mat_id
    if cursos_actualizados:
        mat_dict["cursosInscritos"] = cursos_actualizados
    db["matriculas"].append(mat_dict)
    save_db(db)

    print(f"[OK - CREAR MATRÍCULA] Matrícula '{final_mat_id}' del estudiante '{matricula.estudiante}' (Programa: {matricula.programa}) registrada correctamente.")
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
                if rows is not None and len(rows) > 0:
                    return rows
        except Exception as e:
            print(f"[Pagos] Fallback listar_pagos a db.json: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("pagos", [])


@router.post("/pagos", dependencies=[Depends(require_role(["admin"]))], status_code=status.HTTP_201_CREATED)
def registrar_pago(pago: PagoCreate):
    """Registra un nuevo pago auto-resolviendo correlativos sin colisiones y actualiza el saldo de la matrícula."""
    db = load_db()
    if "pagos" not in db:
        db["pagos"] = []
    if "matriculas" not in db:
        db["matriculas"] = []

    existing_pago_ids = {str(p.get("id")).strip() for p in db["pagos"] if p.get("id")}
    existing_comprobantes = {str(p.get("comprobante")).strip() for p in db["pagos"] if p.get("comprobante")}
    requested_pago_id = (pago.id or "").strip()
    requested_comprobante = (pago.comprobante or "").strip()

    matricula = next((m for m in db["matriculas"] if m["id"] == pago.matriculaId), None)

    conn = get_pg_connection()
    pg_mat = None
    if conn:
        try:
            with conn.cursor() as cur:
                # Bloqueo a nivel de fila (FOR UPDATE) para prevenir condiciones de carrera
                cur.execute('SELECT * FROM matriculas WHERE id = %s FOR UPDATE;', (pago.matriculaId,))
                pg_mat = fetch_one_as_dict(cur)
                if not pg_mat and not matricula:
                    print(f"[ERROR - REGISTRAR PAGO] Ocurrió un error al registrar el pago de '{pago.estudiante}'. Motivo: Matrícula asociada '{pago.matriculaId}' no encontrada.")
                    raise HTTPException(status_code=404, detail="Matrícula asociada no encontrada")

                cur.execute("SELECT id, comprobante FROM pagos;")
                for r in fetch_all_as_dict(cur) or []:
                    if r.get("id"):
                        existing_pago_ids.add(str(r["id"]).strip())
                    if r.get("comprobante"):
                        existing_comprobantes.add(str(r["comprobante"]).strip())

                final_pago_id = (
                    requested_pago_id
                    if (requested_pago_id and requested_pago_id not in existing_pago_ids)
                    else _next_sequential_code(existing_pago_ids, "PAG-", 3, 1)
                )
                final_comprobante = (
                    requested_comprobante
                    if (requested_comprobante and requested_comprobante not in existing_comprobantes)
                    else _next_sequential_code(existing_comprobantes, "B001-", 5, 451)
                )

                if pg_mat:
                    nuevo_pagado = float(pg_mat.get("montoPagado") or 0) + pago.monto
                    nuevo_saldo = max(0.0, float(pg_mat.get("montoTotal") or 0) - nuevo_pagado)
                    nuevo_estado = "Pagado Total" if nuevo_saldo == 0 else "Al día"
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
                            final_pago_id,
                            pago.matriculaId,
                            pago.estudiante,
                            pago.concepto,
                            pago.monto,
                            pago.metodo,
                            pago.operacion,
                            final_comprobante,
                            pago.fecha,
                            pago.estado,
                        ),
                    )
                    conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            print(f"[Pagos] Error al registrar pago atómico en Postgres: {e}")
            raise HTTPException(status_code=500, detail=f"Error en base de datos al registrar pago: {e}")
        finally:
            conn.close()
    else:
        final_pago_id = (
            requested_pago_id
            if (requested_pago_id and requested_pago_id not in existing_pago_ids)
            else _next_sequential_code(existing_pago_ids, "PAG-", 3, 1)
        )
        final_comprobante = (
            requested_comprobante
            if (requested_comprobante and requested_comprobante not in existing_comprobantes)
            else _next_sequential_code(existing_comprobantes, "B001-", 5, 451)
        )

    if not matricula and not pg_mat:
        print(f"[ERROR - REGISTRAR PAGO] Ocurrió un error al registrar el pago de '{pago.estudiante}'. Motivo: Matrícula asociada '{pago.matriculaId}' no encontrada.")
        raise HTTPException(status_code=404, detail="Matrícula asociada no encontrada")

    if matricula:
        matricula["montoPagado"] = float(matricula.get("montoPagado", 0)) + pago.monto
        matricula["saldoPendiente"] = max(0.0, float(matricula.get("montoTotal", 0)) - matricula["montoPagado"])
        matricula["estado"] = "Pagado Total" if matricula["saldoPendiente"] == 0 else "Al día"

    pago_dict = pago.model_dump()
    pago_dict["id"] = final_pago_id
    pago_dict["comprobante"] = final_comprobante

    db["pagos"].insert(0, pago_dict)
    save_db(db)
    print(f"[OK - REGISTRAR PAGO] Pago '{final_pago_id}' (Comprobante: {final_comprobante}, Monto: S/ {pago.monto}) del estudiante '{pago.estudiante}' registrado correctamente.")
    return {"mensaje": "Pago registrado y saldo actualizado", "pago": pago_dict}


@router.put("/{matricula_id}", dependencies=[Depends(require_role(["admin"]))])
def actualizar_matricula(matricula_id: str, matricula: MatriculaCreate):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE matriculas
                    SET estudiante = %s, dni = %s, correo = %s, celular = %s,
                        programa = %s, ciclo = %s, turno = %s, "fechaMatricula" = %s,
                        "modalidadPago" = %s, "montoTotal" = %s, "montoPagado" = %s,
                        "saldoPendiente" = %s, estado = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (
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
                        matricula_id,
                    ),
                )
                updated = fetch_one_as_dict(cur)
                if not updated:
                    raise HTTPException(status_code=404, detail="Matrícula no encontrada")
                if matricula.cursosInscritos is not None:
                    sincronizar_cursos_estudiante(
                        matricula.estudianteId,
                        matricula.dni,
                        matricula.correo,
                        matricula.cursosInscritos,
                        external_conn=conn,
                    )
                conn.commit()
                return updated
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al actualizar matrícula: {e}")
        finally:
            conn.close()

    db = load_db()
    mats = db.get("matriculas", [])
    for idx, m in enumerate(mats):
        if m.get("id") == matricula_id:
            mats[idx] = {**m, **matricula.model_dump()}
            save_db(db)
            return mats[idx]
    raise HTTPException(status_code=404, detail="Matrícula no encontrada")


@router.delete("/{matricula_id}", dependencies=[Depends(require_role(["admin"]))])
def eliminar_matricula(matricula_id: str):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pagos WHERE \"matriculaId\" = %s;", (matricula_id,))
                cur.execute("DELETE FROM matriculas WHERE id = %s RETURNING id;", (matricula_id,))
                deleted = fetch_one_as_dict(cur)
                if not deleted:
                    raise HTTPException(status_code=404, detail="Matrícula no encontrada")
                conn.commit()
                return {"mensaje": "Matrícula eliminada correctamente", "id": matricula_id}
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al eliminar matrícula: {e}")
        finally:
            conn.close()

    db = load_db()
    mats = db.get("matriculas", [])
    filtradas = [m for m in mats if m.get("id") != matricula_id]
    if len(filtradas) == len(mats):
        raise HTTPException(status_code=404, detail="Matrícula no encontrada")
    db["matriculas"] = filtradas
    save_db(db)
    return {"mensaje": "Matrícula eliminada correctamente", "id": matricula_id}
