from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from database import get_pg_connection, load_db, save_db, fetch_all_as_dict, fetch_one_as_dict
from auth import require_role

router = APIRouter(prefix="/academicos", tags=["Catálogo Académico (Ciclos y Carreras)"])

# ==============================================================================
# MODELOS PYDANTIC
# ==============================================================================

class AreaAcademicaCreate(BaseModel):
    id: str = Field(..., description="ID único (ej. AREA-EXA)")
    codigo: str = Field(..., description="Código identificador (ej. CIENCIAS-EXACTAS)")
    nombre: str = Field(..., description="Nombre del área académica")
    descripcion: Optional[str] = None
    color: Optional[str] = "#2563EB"
    estado: str = Field("Activo", description="'Activo' o 'Inactivo'")

class CarreraCreate(BaseModel):
    id: str = Field(..., description="ID único (ej. CAR-001)")
    codigo: str = Field(..., description="Código de carrera (ej. MED-HUM)")
    nombre: str = Field(..., description="Nombre de la carrera")
    area_id: str = Field(..., description="ID del área académica")
    descripcion: Optional[str] = None
    duracion_anios: int = Field(5, ge=1, le=10)
    grado_academico: Optional[str] = "Bachiller / Título Profesional"
    universidades_objetivo: List[str] = []
    estado: str = Field("Activo", description="'Activo' o 'Inactivo'")

class TipoCicloCreate(BaseModel):
    id: str = Field(..., description="ID único (ej. TCIC-SEM)")
    codigo: str = Field(..., description="Código (ej. SEMESTRAL)")
    nombre: str = Field(..., description="Nombre (ej. Semestral, Anual)")
    duracion_semanas: int = Field(..., ge=1)
    modalidad: str = Field("Virtual", description="'Virtual', 'Presencial', 'Híbrido'")
    descripcion: Optional[str] = None
    estado: str = Field("Activo", description="'Activo' o 'Inactivo'")

class CicloAcademicoCreate(BaseModel):
    id: str = Field(..., description="ID único (ej. CIC-2026-SEM-1)")
    codigo: str = Field(..., description="Código (ej. 2026-SEM-I)")
    nombre: str = Field(..., description="Nombre (ej. Semestral 2026-I)")
    tipo_ciclo_id: str = Field(..., description="ID del tipo de ciclo base")
    anio: int = Field(2026, ge=2020)
    periodo: str = Field("I", description="Periodo (I, II, Anual, Verano)")
    fecha_inicio: str = Field(..., description="YYYY-MM-DD")
    fecha_fin: str = Field(..., description="YYYY-MM-DD")
    costo_matricula: float = 0.0
    costo_pension_mensual: float = 0.0
    costo_total_contado: float = 0.0
    es_vigente: bool = False
    estado: str = Field("En Curso", description="'Planificado', 'En Convocatoria', 'En Curso', 'Finalizado', 'Cerrado'")

class TurnoCreate(BaseModel):
    id: str = Field(..., description="ID único (ej. TUR-MAN)")
    codigo: str = Field(..., description="Código (ej. MANANA)")
    nombre: str = Field(..., description="Nombre ('Mañana', 'Tarde', 'Noche')")
    hora_inicio: str = Field(..., description="HH:MM")
    hora_fin: str = Field(..., description="HH:MM")
    estado: str = Field("Activo", description="'Activo' o 'Inactivo'")

# ==============================================================================
# ENDPOINTS: ÁREAS ACADÉMICAS
# ==============================================================================

@router.get("/areas")
def listar_areas():
    """Retorna todas las áreas académicas registradas."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM areas_academicas ORDER BY nombre ASC;")
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Academicos] Error consultando áreas: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("areas_academicas", [])

@router.post("/areas", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_area(area: AreaAcademicaCreate):
    """Permite al Administrador registrar una nueva área académica."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO areas_academicas (id, codigo, nombre, descripcion, color, estado)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *;
                """, (area.id, area.codigo, area.nombre, area.descripcion, area.color, area.estado))
                nuevo = fetch_one_as_dict(cur)
                conn.commit()
                print(f"[OK - CREAR ÁREA] Área académica '{area.nombre}' (ID: {area.id}) creada correctamente.")
                return {"mensaje": "Área académica creada exitosamente", "area": nuevo}
        except Exception as e:
            conn.rollback()
            print(f"[ERROR - CREAR ÁREA] Ocurrió un error al crear el área académica '{area.nombre}'. Motivo: {e}")
            raise HTTPException(status_code=400, detail="No se pudo registrar el área académica. Verifique que el ID o código no estén duplicados.")
        finally:
            conn.close()

    db = load_db()
    areas = db.get("areas_academicas", [])
    if any(a["id"] == area.id or a["codigo"] == area.codigo for a in areas):
        print(f"[ERROR - CREAR ÁREA] Ocurrió un error al crear el área académica '{area.nombre}'. Motivo: El ID ({area.id}) o código ({area.codigo}) ya existe.")
        raise HTTPException(status_code=400, detail="El ID o código ya existe")
    areas.append(area.model_dump())
    db["areas_academicas"] = areas
    save_db(db)
    print(f"[OK - CREAR ÁREA] Área académica '{area.nombre}' (ID: {area.id}) creada correctamente (modo local).")
    return {"mensaje": "Área académica creada exitosamente", "area": area}

# ==============================================================================
# ENDPOINTS: CARRERAS
# ==============================================================================

@router.get("/carreras")
def listar_carreras(area_id: Optional[str] = None):
    """Retorna las carreras profesionales, opcionalmente filtradas por área."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                if area_id:
                    cur.execute("""
                        SELECT c.*, a.nombre AS area_nombre, a.color AS area_color 
                        FROM carreras c
                        JOIN areas_academicas a ON c.area_id = a.id
                        WHERE c.area_id = %s
                        ORDER BY c.nombre ASC;
                    """, (area_id,))
                else:
                    cur.execute("""
                        SELECT c.*, a.nombre AS area_nombre, a.color AS area_color 
                        FROM carreras c
                        JOIN areas_academicas a ON c.area_id = a.id
                        ORDER BY c.nombre ASC;
                    """)
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Academicos] Error consultando carreras: {e}")
        finally:
            conn.close()

    db = load_db()
    carreras = db.get("carreras", [])
    if area_id:
        carreras = [c for c in carreras if c.get("area_id") == area_id]
    return carreras

@router.post("/carreras", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_carrera(carrera: CarreraCreate):
    """Permite al Administrador registrar una nueva carrera profesional."""
    import json
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO carreras (id, codigo, nombre, area_id, descripcion, duracion_anios, grado_academico, universidades_objetivo, estado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                """, (
                    carrera.id, carrera.codigo, carrera.nombre, carrera.area_id, carrera.descripcion,
                    carrera.duracion_anios, carrera.grado_academico, json.dumps(carrera.universidades_objetivo), carrera.estado
                ))
                nuevo = fetch_one_as_dict(cur)
                conn.commit()
                print(f"[OK - CREAR CARRERA] Carrera profesional '{carrera.nombre}' (ID: {carrera.id}) registrada correctamente.")
                return {"mensaje": "Carrera registrada exitosamente", "carrera": nuevo}
        except Exception as e:
            conn.rollback()
            print(f"[ERROR - CREAR CARRERA] Ocurrió un error al registrar la carrera '{carrera.nombre}'. Motivo: {e}")
            raise HTTPException(status_code=400, detail="No se pudo registrar la carrera profesional. Verifique que el ID, código y área asociada sean válidos.")
        finally:
            conn.close()

    db = load_db()
    carreras = db.get("carreras", [])
    if any(c["id"] == carrera.id or c["codigo"] == carrera.codigo for c in carreras):
        print(f"[ERROR - CREAR CARRERA] Ocurrió un error al registrar la carrera '{carrera.nombre}'. Motivo: El ID ({carrera.id}) o código ({carrera.codigo}) ya existe.")
        raise HTTPException(status_code=400, detail="El ID o código de carrera ya existe")
    carreras.append(carrera.model_dump())
    db["carreras"] = carreras
    save_db(db)
    print(f"[OK - CREAR CARRERA] Carrera profesional '{carrera.nombre}' (ID: {carrera.id}) registrada correctamente (modo local).")
    return {"mensaje": "Carrera registrada exitosamente", "carrera": carrera}

# ==============================================================================
# ENDPOINTS: TIPOS DE CICLO (MODALIDADES: SEMESTRAL, ANUAL, INTENSIVO)
# ==============================================================================

@router.get("/tipos-ciclo")
def listar_tipos_ciclo():
    """Retorna los tipos/modalidades de ciclo disponibles (Semestral, Anual, etc.)."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM tipos_ciclo ORDER BY duracion_semanas DESC;")
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Academicos] Error consultando tipos de ciclo: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("tipos_ciclo", [])

@router.post("/tipos-ciclo", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_tipo_ciclo(tipo: TipoCicloCreate):
    """Permite al Administrador registrar una nueva modalidad de ciclo."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tipos_ciclo (id, codigo, nombre, duracion_semanas, modalidad, descripcion, estado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                """, (tipo.id, tipo.codigo, tipo.nombre, tipo.duracion_semanas, tipo.modalidad, tipo.descripcion, tipo.estado))
                nuevo = fetch_one_as_dict(cur)
                conn.commit()
                print(f"[OK - CREAR TIPO CICLO] Tipo de ciclo '{tipo.nombre}' (ID: {tipo.id}) creado correctamente.")
                return {"mensaje": "Tipo de ciclo creado exitosamente", "tipo_ciclo": nuevo}
        except Exception as e:
            conn.rollback()
            print(f"[ERROR - CREAR TIPO CICLO] Ocurrió un error al crear el tipo de ciclo '{tipo.nombre}'. Motivo: {e}")
            raise HTTPException(status_code=400, detail="No se pudo registrar el tipo de ciclo. Verifique que el ID o código no estén duplicados.")
        finally:
            conn.close()

    db = load_db()
    tipos = db.get("tipos_ciclo", [])
    if any(t["id"] == tipo.id or t["codigo"] == tipo.codigo for t in tipos):
        print(f"[ERROR - CREAR TIPO CICLO] Ocurrió un error al crear el tipo de ciclo '{tipo.nombre}'. Motivo: El ID ({tipo.id}) o código ({tipo.codigo}) ya existe.")
        raise HTTPException(status_code=400, detail="El ID o código ya existe")
    tipos.append(tipo.model_dump())
    db["tipos_ciclo"] = tipos
    save_db(db)
    print(f"[OK - CREAR TIPO CICLO] Tipo de ciclo '{tipo.nombre}' (ID: {tipo.id}) creado correctamente (modo local).")
    return {"mensaje": "Tipo de ciclo creado exitosamente", "tipo_ciclo": tipo}

# ==============================================================================
# ENDPOINTS: CICLOS ACADÉMICOS (PERIODOS VIGENTES / CONVOCATORIAS)
# ==============================================================================

@router.get("/ciclos")
def listar_ciclos(vigente: Optional[bool] = None, estado: Optional[str] = None):
    """Retorna los ciclos académicos institucionales con su tipo asociado."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT ca.*, tc.nombre AS tipo_ciclo_nombre, tc.modalidad AS tipo_ciclo_modalidad
                    FROM ciclos_academicos ca
                    JOIN tipos_ciclo tc ON ca.tipo_ciclo_id = tc.id
                    WHERE 1=1
                """
                params = []
                if vigente is not None:
                    query += " AND ca.es_vigente = %s"
                    params.append(vigente)
                if estado:
                    query += " AND ca.estado = %s"
                    params.append(estado)
                query += " ORDER BY ca.anio DESC, ca.fecha_inicio DESC;"
                cur.execute(query, tuple(params))
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Academicos] Error consultando ciclos: {e}")
        finally:
            conn.close()

    db = load_db()
    ciclos = db.get("ciclos_academicos", [])
    if vigente is not None:
        ciclos = [c for c in ciclos if c.get("es_vigente") == vigente]
    if estado:
        ciclos = [c for c in ciclos if c.get("estado") == estado]
    return ciclos

@router.post("/ciclos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_ciclo(ciclo: CicloAcademicoCreate):
    """Permite al Administrador registrar un ciclo académico."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ciclos_academicos (
                        id, codigo, nombre, tipo_ciclo_id, anio, periodo,
                        fecha_inicio, fecha_fin, costo_matricula, costo_pension_mensual,
                        costo_total_contado, es_vigente, estado
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                """, (
                    ciclo.id, ciclo.codigo, ciclo.nombre, ciclo.tipo_ciclo_id, ciclo.anio, ciclo.periodo,
                    ciclo.fecha_inicio, ciclo.fecha_fin, ciclo.costo_matricula, ciclo.costo_pension_mensual,
                    ciclo.costo_total_contado, ciclo.es_vigente, ciclo.estado
                ))
                nuevo = fetch_one_as_dict(cur)
                conn.commit()
                print(f"[OK - CREAR CICLO] Ciclo académico '{ciclo.nombre}' (ID: {ciclo.id}) registrado correctamente.")
                return {"mensaje": "Ciclo académico registrado exitosamente", "ciclo": nuevo}
        except Exception as e:
            conn.rollback()
            print(f"[ERROR - CREAR CICLO] Ocurrió un error al registrar el ciclo académico '{ciclo.nombre}'. Motivo: {e}")
            raise HTTPException(status_code=400, detail="No se pudo registrar el ciclo académico. Verifique los datos y fechas ingresadas.")
        finally:
            conn.close()

    db = load_db()
    ciclos = db.get("ciclos_academicos", [])
    if any(c["id"] == ciclo.id or c["codigo"] == ciclo.codigo for c in ciclos):
        print(f"[ERROR - CREAR CICLO] Ocurrió un error al registrar el ciclo académico '{ciclo.nombre}'. Motivo: El ID ({ciclo.id}) o código ({ciclo.codigo}) ya existe.")
        raise HTTPException(status_code=400, detail="El ID o código ya existe")
    ciclos.append(ciclo.model_dump())
    db["ciclos_academicos"] = ciclos
    save_db(db)
    print(f"[OK - CREAR CICLO] Ciclo académico '{ciclo.nombre}' (ID: {ciclo.id}) registrado correctamente (modo local).")
    return {"mensaje": "Ciclo académico registrado exitosamente", "ciclo": ciclo}

# ==============================================================================
# ENDPOINTS: TURNOS Y OFERTA ACADÉMICA
# ==============================================================================

@router.get("/turnos")
def listar_turnos():
    """Retorna los turnos institucionales disponibles (Mañana, Tarde, Noche)."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM turnos ORDER BY hora_inicio ASC;")
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Academicos] Error consultando turnos: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("turnos", [])

@router.post("/turnos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_turno(turno: TurnoCreate):
    """Permite al Administrador registrar un turno institucional."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO turnos (id, codigo, nombre, hora_inicio, hora_fin, estado)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *;
                """, (turno.id, turno.codigo, turno.nombre, turno.hora_inicio, turno.hora_fin, turno.estado))
                nuevo = fetch_one_as_dict(cur)
                conn.commit()
                print(f"[OK - CREAR TURNO] Turno '{turno.nombre}' (ID: {turno.id}) creado correctamente.")
                return {"mensaje": "Turno creado exitosamente", "turno": nuevo}
        except Exception as e:
            conn.rollback()
            print(f"[ERROR - CREAR TURNO] Ocurrió un error al crear el turno '{turno.nombre}'. Motivo: {e}")
            raise HTTPException(status_code=400, detail="No se pudo registrar el turno institucional. Verifique que el ID o código no estén duplicados.")
        finally:
            conn.close()

    db = load_db()
    turnos = db.get("turnos", [])
    if any(t["id"] == turno.id or t["codigo"] == turno.codigo for t in turnos):
        print(f"[ERROR - CREAR TURNO] Ocurrió un error al crear el turno '{turno.nombre}'. Motivo: El ID ({turno.id}) o código ({turno.codigo}) ya existe.")
        raise HTTPException(status_code=400, detail="El ID o código ya existe")
    turnos.append(turno.model_dump())
    db["turnos"] = turnos
    save_db(db)
    print(f"[OK - CREAR TURNO] Turno '{turno.nombre}' (ID: {turno.id}) creado correctamente (modo local).")
    return {"mensaje": "Turno creado exitosamente", "turno": turno}

@router.get("/oferta")
def listar_oferta_academica(ciclo_id: Optional[str] = None):
    """Retorna la oferta de vacantes de carreras por cada ciclo."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT oc.*, 
                           ca.nombre AS ciclo_nombre, 
                           c.nombre AS carrera_nombre,
                           c.codigo AS carrera_codigo,
                           a.nombre AS area_nombre
                    FROM ciclo_carreras oc
                    JOIN ciclos_academicos ca ON oc.ciclo_id = ca.id
                    JOIN carreras c ON oc.carrera_id = c.id
                    JOIN areas_academicas a ON c.area_id = a.id
                    WHERE 1=1
                """
                params = []
                if ciclo_id:
                    query += " AND oc.ciclo_id = %s"
                    params.append(ciclo_id)
                query += " ORDER BY ca.anio DESC, c.nombre ASC;"
                cur.execute(query, tuple(params))
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Academicos] Error consultando oferta: {e}")
        finally:
            conn.close()

    db = load_db()
    ofertas = db.get("ciclo_carreras", [])
    if ciclo_id:
        ofertas = [o for o in ofertas if o.get("ciclo_id") == ciclo_id]
    return ofertas
