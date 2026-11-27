from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from database import get_pg_connection, fetch_one_as_dict, load_db, save_db
from auth import require_role

router = APIRouter(prefix="/configuracion", tags=["Configuración Institucional"])

DEFAULT_CONFIG: Dict[str, Any] = {
    "id": "general",
    "institucion_nombre": "Academia Preuniversitaria Alto Rendimiento",
    "institucion_ruc": "20609876543",
    "institucion_direccion": "Av. Universitaria 1801, Lima",
    "institucion_telefono": "(01) 480-1234",
    "institucion_correo": "informes@academiapreu.edu.pe",
    "ciclo_vigente": "Semestral San Marcos 2026-II",
    "ciclo_inicio": "2026-08-10",
    "ciclo_fin": "2026-12-20",
    "ciclo_estado": "En Curso",
    "tarifa_hora_docente": 50.0,
    "tolerancia_tardanza_min": 10,
    "horas_descuento_inasistencia": 2.0,
}


class ConfiguracionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    institucion_nombre: str = DEFAULT_CONFIG["institucion_nombre"]
    institucion_ruc: Optional[str] = DEFAULT_CONFIG["institucion_ruc"]
    institucion_direccion: Optional[str] = DEFAULT_CONFIG["institucion_direccion"]
    institucion_telefono: Optional[str] = DEFAULT_CONFIG["institucion_telefono"]
    institucion_correo: Optional[str] = DEFAULT_CONFIG["institucion_correo"]
    ciclo_vigente: Optional[str] = DEFAULT_CONFIG["ciclo_vigente"]
    ciclo_inicio: Optional[str] = DEFAULT_CONFIG["ciclo_inicio"]
    ciclo_fin: Optional[str] = DEFAULT_CONFIG["ciclo_fin"]
    ciclo_estado: Optional[str] = DEFAULT_CONFIG["ciclo_estado"]
    tarifa_hora_docente: float = 50.0
    tolerancia_tardanza_min: int = 10
    horas_descuento_inasistencia: float = 2.0


@router.get("", dependencies=[Depends(require_role(["admin", "docente", "estudiante"]))])
def obtener_configuracion():
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM configuracion WHERE id = 'general' LIMIT 1;")
                row = fetch_one_as_dict(cur)
                if row:
                    return row
        except Exception as e:
            print(f"[Configuración] Error consultando PostgreSQL: {e}")
        finally:
            conn.close()

    db = load_db()
    return db.get("configuracion") or DEFAULT_CONFIG


@router.put("", dependencies=[Depends(require_role(["admin"]))])
def actualizar_configuracion(payload: ConfiguracionUpdate):
    data = payload.model_dump()
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO configuracion (
                        id, institucion_nombre, institucion_ruc, institucion_direccion,
                        institucion_telefono, institucion_correo, ciclo_vigente,
                        ciclo_inicio, ciclo_fin, ciclo_estado, tarifa_hora_docente,
                        tolerancia_tardanza_min, horas_descuento_inasistencia, updated_at
                    ) VALUES (
                        'general', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        institucion_nombre = EXCLUDED.institucion_nombre,
                        institucion_ruc = EXCLUDED.institucion_ruc,
                        institucion_direccion = EXCLUDED.institucion_direccion,
                        institucion_telefono = EXCLUDED.institucion_telefono,
                        institucion_correo = EXCLUDED.institucion_correo,
                        ciclo_vigente = EXCLUDED.ciclo_vigente,
                        ciclo_inicio = EXCLUDED.ciclo_inicio,
                        ciclo_fin = EXCLUDED.ciclo_fin,
                        ciclo_estado = EXCLUDED.ciclo_estado,
                        tarifa_hora_docente = EXCLUDED.tarifa_hora_docente,
                        tolerancia_tardanza_min = EXCLUDED.tolerancia_tardanza_min,
                        horas_descuento_inasistencia = EXCLUDED.horas_descuento_inasistencia,
                        updated_at = NOW()
                    RETURNING *;
                    """,
                    (
                        data["institucion_nombre"],
                        data["institucion_ruc"],
                        data["institucion_direccion"],
                        data["institucion_telefono"],
                        data["institucion_correo"],
                        data["ciclo_vigente"],
                        data["ciclo_inicio"],
                        data["ciclo_fin"],
                        data["ciclo_estado"],
                        data["tarifa_hora_docente"],
                        data["tolerancia_tardanza_min"],
                        data["horas_descuento_inasistencia"],
                    ),
                )
                saved = fetch_one_as_dict(cur)
                conn.commit()
                return saved or {"id": "general", **data}
        except Exception as e:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"No se pudo guardar la configuración en PostgreSQL: {e}",
            )
        finally:
            conn.close()

    db = load_db()
    db["configuracion"] = {"id": "general", **data}
    save_db(db)
    return db["configuracion"]
