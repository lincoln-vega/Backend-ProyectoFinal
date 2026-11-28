import re
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, AliasChoices
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role
from routers.usuarios import parse_course_list
from routers.horarios import sincronizar_cursos_asignados_docente

router = APIRouter(prefix="/incidencias", tags=["Incidencias y Permutas"])


# --- MODELOS PYDANTIC ---

class IncidenciaCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = None
    docenteId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docenteId", "docenteid"),
    )
    docente: str
    dni: Optional[str] = None
    correo: Optional[str] = None
    tipo: str  # Tardanza, Inasistencia Inesperada, Solicitud de Permuta / Reemplazo
    materia: str
    cursoId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("cursoId", "cursoid"),
    )
    horario: str
    motivo: str
    archivoNombre: Optional[str] = Field(
        default="constancia_o_solicitud.pdf",
        validation_alias=AliasChoices("archivoNombre", "archivonombre", "archivo_nombre"),
    )
    archivoUrl: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("archivoUrl", "archivo_url"),
    )
    archivoPath: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("archivoPath", "archivo_path"),
    )
    fechaPresentacion: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("fechaPresentacion", "fechapresentacion"),
    )
    docentePropuesto: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docentePropuesto", "docentepropuesto"),
    )
    docenteReemplazo: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docenteReemplazo", "docentereemplazo"),
    )
    horarioActualizado: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("horarioActualizado", "horarioactualizado"),
    )
    observaciones: Optional[str] = None


class EstadoTransitionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    nuevoEstado: str  # "Aprobado" o "Rechazado"
    docenteReemplazo: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docenteReemplazo", "docentereemplazo"),
    )
    docenteReemplazoId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docenteReemplazoId", "docentereemplazoid"),
    )
    docentePropuesto: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docentePropuesto", "docentepropuesto"),
    )
    horarioActualizado: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("horarioActualizado", "horarioactualizado"),
    )
    observaciones: Optional[str] = None


class ParametrosNomina(BaseModel):
    periodo: Optional[str] = "2026-10"
    tarifaHoraDefault: Optional[float] = 50.0
    docenteId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("docenteId", "docenteid"),
    )


def normalizar_incidencia(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Asegura que cada incidencia devuelta incluya simultáneamente las claves camelCase
    esperadas por el frontend y las claves en minúsculas de PostgreSQL.
    """
    if not row:
        return row
    docente_id = row.get("docenteId") or row.get("docenteid")
    curso_id = row.get("cursoId") or row.get("cursoid")
    docente_propuesto = row.get("docentePropuesto") or row.get("docentepropuesto")
    docente_reemplazo = row.get("docenteReemplazo") or row.get("docentereemplazo")
    horario_actualizado = row.get("horarioActualizado") or row.get("horarioactualizado")
    fecha_presentacion = row.get("fechaPresentacion") or row.get("fechapresentacion")
    archivo_nombre = row.get("archivoNombre") or row.get("archivonombre")
    archivo_url = row.get("archivoUrl") or row.get("archivo_url")
    archivo_path = row.get("archivoPath") or row.get("archivo_path")
    observaciones = row.get("observaciones") or row.get("observacionesResolucion")

    return {
        **row,
        "docenteId": docente_id,
        "docenteid": docente_id,
        "cursoId": curso_id,
        "cursoid": curso_id,
        "docentePropuesto": docente_propuesto,
        "docentepropuesto": docente_propuesto,
        "docenteReemplazo": docente_reemplazo,
        "docentereemplazo": docente_reemplazo,
        "horarioActualizado": horario_actualizado,
        "horarioactualizado": horario_actualizado,
        "fechaPresentacion": fecha_presentacion,
        "archivoNombre": archivo_nombre,
        "archivoUrl": archivo_url,
        "archivo_url": archivo_url,
        "archivoPath": archivo_path,
        "archivo_path": archivo_path,
        "observaciones": observaciones,
    }


# --- REGLAS DE LA MÁQUINA DE ESTADOS (SCRM-104) ---
ESTADOS_VALIDOS = {"Pendiente", "Aprobado", "Rechazado"}

TRANSICIONES_PERMITIDAS = {
    "Pendiente": {"Aprobado", "Rechazado"},
    "Aprobado": set(),    # Estado terminal
    "Rechazado": set(),   # Estado terminal
}


def validar_transicion_estado(estado_actual: str, nuevo_estado: str):
    if nuevo_estado not in ESTADOS_VALIDOS:
        print(f"[ERROR - INCIDENCIA] Ocurrió un error al cambiar estado a '{nuevo_estado}'. Motivo: Estado destino inválido (permitidos: {list(ESTADOS_VALIDOS)}).")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado destino '{nuevo_estado}' no es válido. Estados permitidos: {list(ESTADOS_VALIDOS)}",
        )

    if estado_actual == nuevo_estado:
        return True

    permitidos = TRANSICIONES_PERMITIDAS.get(estado_actual, set())
    if nuevo_estado not in permitidos:
        print(f"[ERROR - INCIDENCIA] Ocurrió un error al cambiar estado de '{estado_actual}' a '{nuevo_estado}'. Motivo: Transición prohibida por la Máquina de Estados.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transición prohibida por la Máquina de Estados: no se puede pasar de '{estado_actual}' a '{nuevo_estado}'.",
        )
    return True


def _cargar_incidencias() -> List[Dict[str, Any]]:
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM incidents
                    ORDER BY created_at DESC;
                    """
                )
                rows = fetch_all_as_dict(cur)
                if rows is not None:
                    return [normalizar_incidencia(r) for r in rows]
        except Exception as e:
            print(f"[Incidencias] Fallback listar a db.json: {e}")
        finally:
            conn.close()
    db = load_db()
    return [normalizar_incidencia(i) for i in db.get("incidents", [])]


# --- ENDPOINTS ---

# 1. Listar incidencias con filtros opcionales
@router.get("", dependencies=[Depends(require_role(["admin", "docente"]))])
def listar_incidencias(
    tipo: Optional[str] = Query(None, description="Filtrar por tipo de incidencia o permuta"),
    estado: Optional[str] = Query(None, description="Filtrar por estado (Pendiente, Aprobado, Rechazado)"),
    docenteId: Optional[str] = Query(None, description="Filtrar por ID de docente"),
):
    resultado = _cargar_incidencias()
    if tipo:
        resultado = [i for i in resultado if tipo.lower() in str(i.get("tipo", "")).lower()]
    if estado:
        resultado = [i for i in resultado if str(i.get("estado", "")).lower() == estado.lower()]
    if docenteId:
        resultado = [i for i in resultado if (i.get("docenteId") or i.get("docenteid")) == docenteId]

    return resultado


# 2. Obtener incidencia por ID
@router.get("/{incidencia_id}", dependencies=[Depends(require_role(["admin", "docente"]))])
def obtener_incidencia(incidencia_id: str):
    for inc in _cargar_incidencias():
        if inc.get("id") == incidencia_id:
            return inc
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incidencia {incidencia_id} no encontrada")


# 3. Registrar una nueva incidencia o solicitud de permuta (Docente o Admin)
@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin", "docente"]))])
def registrar_incidencia(datos: IncidenciaCreate):
    db = load_db()
    incidencias = db.setdefault("incidents", [])

    existing_ids = {str(inc.get("id")).strip() for inc in incidencias if inc.get("id")}
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM incidents;")
                for r in fetch_all_as_dict(cur) or []:
                    if r.get("id"):
                        existing_ids.add(str(r["id"]).strip())
        except Exception:
            pass

    requested_id = (datos.id or "").strip()
    is_sequential_inc_id = bool(re.match(r"^INC-D?\d+$", requested_id, re.IGNORECASE))
    if not requested_id or (is_sequential_inc_id and requested_id in existing_ids):
        max_num = 0
        for eid in existing_ids:
            if re.match(r"^INC-D?\d+$", eid, re.IGNORECASE):
                digits = "".join(ch for ch in eid if ch.isdigit())
                if digits:
                    max_num = max(max_num, int(digits))
        cand_num = max_num + 1
        while f"INC-{str(cand_num).zfill(3)}" in existing_ids or f"INC-D{str(cand_num).zfill(2)}" in existing_ids:
            cand_num += 1
        nuevo_id = f"INC-{str(cand_num).zfill(3)}"
    else:
        nuevo_id = requested_id

    fecha_pres = datos.fechaPresentacion or datetime.now().strftime("%Y-%m-%d %H:%M")

    nueva_incidencia = normalizar_incidencia(
        {
            **datos.model_dump(),
            "id": nuevo_id,
            "estado": "Pendiente",
            "fechaPresentacion": fecha_pres,
        }
    )

    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO incidents (
                        id, docente, dni, correo, tipo, "fechaPresentacion",
                        materia, horario, motivo, "archivoNombre", archivo_url, archivo_path,
                        estado, docenteid, cursoid, docentepropuesto, docentereemplazo,
                        horarioactualizado, observaciones, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'Pendiente', %s, %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        docente = EXCLUDED.docente,
                        tipo = EXCLUDED.tipo,
                        materia = EXCLUDED.materia,
                        horario = EXCLUDED.horario,
                        motivo = EXCLUDED.motivo,
                        "archivoNombre" = COALESCE(EXCLUDED."archivoNombre", incidents."archivoNombre"),
                        archivo_url = COALESCE(EXCLUDED.archivo_url, incidents.archivo_url),
                        archivo_path = COALESCE(EXCLUDED.archivo_path, incidents.archivo_path),
                        estado = 'Pendiente'
                    RETURNING *;
                    """,
                    (
                        nuevo_id,
                        datos.docente,
                        datos.dni,
                        datos.correo,
                        datos.tipo,
                        fecha_pres,
                        datos.materia,
                        datos.horario,
                        datos.motivo,
                        datos.archivoNombre,
                        datos.archivoUrl,
                        datos.archivoPath,
                        datos.docenteId,
                        datos.cursoId,
                        datos.docentePropuesto,
                        datos.docenteReemplazo,
                        datos.horarioActualizado,
                        datos.observaciones,
                    ),
                )
                row = fetch_one_as_dict(cur)
                conn.commit()
                if row:
                    nueva_incidencia = normalizar_incidencia(row)
        except Exception as e:
            conn.rollback()
            print(f"[Incidencias] Advertencia al guardar en Postgres: {e}")
        finally:
            conn.close()

    incidencias.insert(0, nueva_incidencia)
    save_db(db)
    print(f"[OK - CREAR INCIDENCIA] Incidencia '{nuevo_id}' ({datos.tipo} - Docente: '{datos.docente}', Curso: '{datos.materia}') registrada correctamente en estado 'Pendiente'.")

    return {
        "mensaje": "Solicitud / Incidencia registrada correctamente en estado 'Pendiente'",
        "incidencia": nueva_incidencia,
    }


# 4. Transicionar estado según Máquina de Estados (SCRM-104)
@router.patch("/{incidencia_id}/estado", dependencies=[Depends(require_role(["admin"]))])
def cambiar_estado_incidencia(incidencia_id: str, transicion: EstadoTransitionUpdate):
    db = load_db()
    incidencias = db.get("incidents", [])

    incidencia = next((inc for inc in incidencias if inc.get("id") == incidencia_id), None)

    conn = get_pg_connection()
    pg_incidencia = None
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM incidents WHERE id = %s LIMIT 1;", (incidencia_id,))
                pg_incidencia = fetch_one_as_dict(cur)
        except Exception as e:
            print(f"[Incidencias] Error consultando incidencia en Postgres: {e}")
        finally:
            conn.close()

    target = pg_incidencia or incidencia
    if not target:
        print(f"[ERROR - INCIDENCIA] Ocurrió un error al actualizar la incidencia '{incidencia_id}'. Motivo: Incidencia no encontrada.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incidencia {incidencia_id} no encontrada")

    estado_actual = target.get("estado", "Pendiente")
    nuevo_estado = transicion.nuevoEstado

    # Validar con la Máquina de Estados
    validar_transicion_estado(estado_actual, nuevo_estado)

    fecha_res = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target["estado"] = nuevo_estado
    target["fechaResolucion"] = fecha_res

    if transicion.observaciones:
        target["observacionesResolucion"] = transicion.observaciones
        target["observaciones"] = transicion.observaciones

    if transicion.docenteReemplazo:
        target["docenteReemplazo"] = transicion.docenteReemplazo
        target["docentereemplazo"] = transicion.docenteReemplazo
    if transicion.docenteReemplazoId:
        target["docenteReemplazoId"] = transicion.docenteReemplazoId
    if transicion.docentePropuesto:
        target["docentePropuesto"] = transicion.docentePropuesto
        target["docentepropuesto"] = transicion.docentePropuesto
    if transicion.horarioActualizado:
        target["horarioActualizado"] = transicion.horarioActualizado
        target["horarioactualizado"] = transicion.horarioActualizado

    curso_id = target.get("cursoId") or target.get("cursoid")
    old_docente_id = target.get("docenteId") or target.get("docenteid")

    # Actualizar en PostgreSQL si está disponible
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET estado = %s,
                        docentereemplazo = COALESCE(%s, docentereemplazo),
                        horarioactualizado = COALESCE(%s, horarioactualizado),
                        observaciones = COALESCE(%s, observaciones)
                    WHERE id = %s;
                    """,
                    (
                        nuevo_estado,
                        transicion.docenteReemplazo,
                        transicion.horarioActualizado,
                        transicion.observaciones,
                        incidencia_id,
                    ),
                )
                if nuevo_estado == "Aprobado" and curso_id and transicion.docenteReemplazo:
                    if transicion.docenteReemplazoId:
                        cur.execute(
                            'UPDATE courses SET docente = %s, "docenteId" = %s WHERE id = %s;',
                            (transicion.docenteReemplazo, transicion.docenteReemplazoId, curso_id),
                        )
                    else:
                        cur.execute(
                            "UPDATE courses SET docente = %s WHERE id = %s;",
                            (transicion.docenteReemplazo, curso_id),
                        )
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[Incidencias] Advertencia al actualizar estado en Postgres: {e}")
        finally:
            conn.close()

    # Actualizar también en db.json
    if incidencia:
        incidencia.update(target)
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
        if transicion.docenteReemplazoId:
            sincronizar_cursos_asignados_docente(curso_id, transicion.docenteReemplazoId, old_docente_id)

    save_db(db)

    normalized = normalizar_incidencia(target)
    print(f"[OK - ESTADO INCIDENCIA] Incidencia '{incidencia_id}' (Docente: '{normalized.get('docente')}') actualizada de '{estado_actual}' a '{nuevo_estado}' correctamente.")
    return {
        "mensaje": f"Incidencia {incidencia_id} transicionada exitosamente de '{estado_actual}' a '{nuevo_estado}'.",
        "incidencia": normalized,
    }


# 5. Algoritmo del Motor de Nómina y Cruce de Horas (SCRM-105)
@router.post("/nomina/cruce", dependencies=[Depends(require_role(["admin"]))])
def calcular_cruce_nomina(params: ParametrosNomina):
    users = []
    courses = []
    incidents = []

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users;")
                users = fetch_all_as_dict(cur)
                cur.execute("SELECT * FROM courses;")
                courses = fetch_all_as_dict(cur)
                cur.execute("SELECT * FROM incidents;")
                incidents = [normalizar_incidencia(r) for r in fetch_all_as_dict(cur)]
        except Exception as e:
            print(f"[Nomina] Fallback de Postgres a db.json: {e}")
        finally:
            conn.close()

    if not users:
        db = load_db()
        users = db.get("users", [])
        courses = db.get("courses", [])
        incidents = [normalizar_incidencia(i) for i in db.get("incidents", [])]

    # Filtrar docentes (case-insensitive)
    docentes = [
        u
        for u in users
        if str(u.get("rol") or "").strip().lower() in ("docente", "profesor", "profesora")
    ]
    if params.docenteId:
        docentes = [d for d in docentes if d.get("id") == params.docenteId]

    tarifa_default = float(params.tarifaHoraDefault or 50.0)
    detalle_docentes = []
    total_horas_programadas_general = 0.0
    total_horas_efectivas_general = 0.0
    total_descuentos_general = 0.0
    total_monto_general = 0.0

    for doc in docentes:
        doc_id = doc.get("id")
        apellidos = str(doc.get("apellidos") or "").strip()
        nombre_docente = f"{doc.get('nombres', '')} {apellidos}".strip() or doc.get("name", "Docente")
        assigned_ids = set(parse_course_list(doc.get("cursosAsignados")))

        # Cursos asignados (por docenteId, por lista cursosAsignados o por coincidencia de nombre/apellidos)
        doc_courses = [
            c
            for c in courses
            if c.get("docenteId") == doc_id
            or str(c.get("id")) in assigned_ids
            or (nombre_docente and nombre_docente.lower() in str(c.get("docente", "")).lower())
            or (apellidos and apellidos.lower() in str(c.get("docente", "")).lower())
        ]

        # Cada curso equivale a 16 horas mensuales programadas (mínimo 16h para docentes activos)
        horas_programadas = len(doc_courses) * 16.0
        if horas_programadas == 0:
            horas_programadas = 16.0

        # Incidencias del docente (cruce por docenteId/docenteid, DNI o nombre/apellidos)
        doc_incidents = [
            i
            for i in incidents
            if (i.get("docenteId") and str(i.get("docenteId")) == str(doc_id))
            or (i.get("dni") and doc.get("dni") and str(i.get("dni")) == str(doc.get("dni")))
            or (nombre_docente and nombre_docente.lower() in str(i.get("docente", "")).lower())
            or (apellidos and apellidos.lower() in str(i.get("docente", "")).lower())
        ]

        horas_descontadas = 0.0
        minutos_tardanza = 0
        inasistencias_no_justificadas = 0
        justificaciones_aprobadas = 0

        for inc in doc_incidents:
            tipo = str(inc.get("tipo", "")).lower()
            estado = inc.get("estado", "")

            if "tardanza" in tipo:
                minutos = 20 if "+20" in tipo else 15
                minutos_tardanza += minutos

                if estado != "Aprobado":
                    horas_descontadas += minutos / 60.0
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

        # Utilizar salario real registrado en public.users si es > 0 como monto bruto base
        try:
            salario_registrado = float(doc.get("salario") or 0.0)
        except (TypeError, ValueError):
            salario_registrado = 0.0

        if salario_registrado > 0:
            monto_bruto = round(salario_registrado, 2)
            tarifa_por_hora = round(monto_bruto / horas_programadas, 2)
        else:
            tarifa_por_hora = round(tarifa_default, 2)
            monto_bruto = round(horas_programadas * tarifa_por_hora, 2)

        descuento_pen = round(min(monto_bruto, horas_descontadas * tarifa_por_hora), 2)
        monto_neto = round(max(0.0, monto_bruto - descuento_pen), 2)

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
            "salarioBase": salario_registrado,
            "cursosAsignados": len(doc_courses),
            "horasProgramadas": horas_programadas,
            "minutosTardanzaTotal": minutos_tardanza,
            "inasistenciasNoJustificadas": inasistencias_no_justificadas,
            "justificacionesAprobadas": justificaciones_aprobadas,
            "horasDescontadas": round(horas_descontadas, 2),
            "horasEfectivas": round(horas_efectivas, 2),
            "tarifaPorHora": tarifa_por_hora,
            "montoBruto": monto_bruto,
            "descuentoPEN": descuento_pen,
            "montoNeto": monto_neto,
            "estadoAuditoria": estado_auditoria,
        }

        detalle_docentes.append(item)
        total_horas_programadas_general += horas_programadas
        total_horas_efectivas_general += horas_efectivas
        total_descuentos_general += descuento_pen
        total_monto_general += monto_neto

    print(f"[OK - CRUCE NÓMINA] Cálculo de nómina del periodo '{params.periodo}' procesado correctamente ({len(docentes)} docentes auditados, Total Planilla: S/ {round(total_monto_general, 2)}).")
    return {
        "periodo": params.periodo,
        "fechaGeneracion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "resumenGeneral": {
            "totalDocentesAuditados": len(docentes),
            "totalHorasProgramadas": round(total_horas_programadas_general, 2),
            "totalHorasEfectivas": round(total_horas_efectivas_general, 2),
            "eficienciaGlobal": f"{round((total_horas_efectivas_general / total_horas_programadas_general) * 100, 1) if total_horas_programadas_general > 0 else 100}%",
            "totalDescuentosPEN": round(total_descuentos_general, 2),
            "totalPlanillaPEN": round(total_monto_general, 2),
        },
        "detalleDocentes": detalle_docentes,
    }
