import re
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import (
    authenticate_with_insforge,
    get_profile_by_auth_id,
    require_role,
)

# Router para gestión de usuarios
router = APIRouter(prefix="/usuarios", tags=["Usuarios"])

# --- FUNCIONES DE VALIDACIÓN ESTÁNDAR (CAPA 2) ---

def validar_formato_documento(tipo: str, doc: str) -> str:
    tipo = (tipo or "DNI").strip().upper()
    doc_clean = str(doc or "").strip().upper()
    if not doc_clean:
        raise ValueError("El número de documento de identidad es obligatorio.")

    if tipo == "DNI":
        if not re.match(r"^\d{8}$", doc_clean):
            raise ValueError("El DNI peruano debe contener exactamente 8 dígitos numéricos y no permite letras.")
    elif tipo in ["CE", "CARNET DE EXTRANJERÍA", "CARNET DE EXTRANJERIA"]:
        if not re.match(r"^[A-Z0-9]{9,12}$", doc_clean):
            raise ValueError("El Carnet de Extranjería (CE) debe contener entre 9 y 12 caracteres alfanuméricos.")
    elif tipo in ["PASAPORTE", "PAS"]:
        if not re.match(r"^[A-Z0-9]{6,12}$", doc_clean):
            raise ValueError("El Pasaporte debe contener entre 6 y 12 caracteres alfanuméricos.")
    else:
        raise ValueError(f"Tipo de documento '{tipo}' no reconocido. Seleccione DNI, CE o Pasaporte.")
    
    return doc_clean

def validar_formato_celular(cel: Optional[str]) -> Optional[str]:
    if cel is None or str(cel).strip() == "":
        return None
    cel_clean = str(cel).strip()
    # Admite nacional de Perú (9 dígitos iniciando en 9) o Internacional con + (ej. +573001234567)
    if not (re.match(r"^9\d{8}$", cel_clean) or re.match(r"^\+\d{8,15}$", cel_clean)):
        raise ValueError("El número telefónico debe contener 9 dígitos numéricos iniciando en 9 (Perú) o formato internacional con prefijo '+' y entre 8 y 15 dígitos.")
    return cel_clean

# --- MODELOS PYDANTIC ---

class UserLogin(BaseModel):
    correo: str
    password: str

class ValidationCheckRequest(BaseModel):
    tipoDocumento: Optional[str] = "DNI"
    dni: Optional[str] = None
    celular: Optional[str] = None
    correo: Optional[str] = None

class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authUserId: UUID
    nombres: str = Field(..., min_length=2, max_length=100, description="Nombres del usuario")
    apellidos: str = Field(..., min_length=2, max_length=100, description="Apellidos del usuario")
    correo: str = Field(..., description="Correo institucional o personal")
    rol: Literal["admin", "docente", "estudiante"] = Field(
        "estudiante",
        description="Rol: admin, docente, estudiante",
    )
    estado: str = Field("Activo", description="Estado: Activo o Inactivo")
    tipoDocumento: str = Field("DNI", description="Tipo de documento: DNI, CE, Pasaporte")
    dni: str = Field(..., description="Número de documento")
    celular: Optional[str] = Field(None, description="Celular nacional (9 dígitos) o internacional con +")
    fechaNacimiento: Optional[str] = None
    carreraObjetivo: Optional[str] = None
    cicloVirtual: Optional[str] = "Semestral"
    areaAcademica: Optional[str] = None

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, v: str) -> str:
        if not v or "@" not in str(v):
            raise ValueError("El formato del correo electrónico es inválido.")
        return str(v).strip().lower()

    @model_validator(mode="after")
    def validar_documento_y_telefono(self):
        self.dni = validar_formato_documento(self.tipoDocumento, self.dni)
        self.celular = validar_formato_celular(self.celular)
        return self

class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombres: Optional[str] = None
    apellidos: Optional[str] = None
    correo: Optional[str] = None
    rol: Optional[str] = None
    estado: Optional[str] = None
    tipoDocumento: Optional[str] = None
    dni: Optional[str] = None
    celular: Optional[str] = None
    fechaNacimiento: Optional[str] = None
    carreraObjetivo: Optional[str] = None
    cicloVirtual: Optional[str] = None
    areaAcademica: Optional[str] = None

    @model_validator(mode="after")
    def validar_update(self):
        if self.dni is not None and str(self.dni).strip():
            tipo = self.tipoDocumento or "DNI"
            self.dni = validar_formato_documento(tipo, self.dni)
        if self.celular is not None and str(self.celular).strip():
            self.celular = validar_formato_celular(self.celular)
        return self

# --- ENDPOINT DE VALIDACIÓN PREVIA (CAPA 2) ---

@router.post("/validar", tags=["Validación"])
def validar_datos_usuario(req: ValidationCheckRequest):
    """
    Endpoint para verificación instantánea y asíncrona de campos críticos (DNI/CE/Pasaporte, Celular, Correo).
    Si algún dato no cumple la regla de validación de la institución, responde HTTP 422.
    """
    errores = {}
    if req.dni is not None and str(req.dni).strip():
        try:
            validar_formato_documento(req.tipoDocumento or "DNI", req.dni)
        except ValueError as e:
            errores["dni"] = str(e)
    
    if req.celular is not None and str(req.celular).strip() != "":
        try:
            validar_formato_celular(req.celular)
        except ValueError as e:
            errores["celular"] = str(e)
            
    if req.correo is not None and str(req.correo).strip() != "":
        correo_val = str(req.correo).strip()
        if "@" not in correo_val:
            errores["correo"] = "El correo electrónico no es válido."

    if errores:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"mensaje": "Error de validación en campos del usuario", "errores": errores}
        )

    return {"valido": True, "mensaje": "Validación exitosa: documento y número telefónico conformes"}

# --- ENDPOINT DE LOGIN ---

@router.post("/login", tags=["Autenticación"])
def login(form_data: UserLogin):
    auth_session = authenticate_with_insforge(
        form_data.correo.strip().lower(),
        form_data.password,
    )
    auth_user = auth_session.get("user")
    access_token = auth_session.get("accessToken")
    if not isinstance(auth_user, dict) or not access_token or not auth_user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="InsForge Auth no devolvió una sesión válida.",
        )

    user = get_profile_by_auth_id(str(auth_user["id"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta de Auth no tiene un perfil registrado en users.",
        )
    
    if user.get("estado", "Activo") != "Activo":
        raise HTTPException(status_code=403, detail="Cuenta inactiva")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["rol"],
        "name": f"{user['nombres']} {user['apellidos']}",
        "userId": user["id"],
        "id": user["id"],
        "email": user.get("correo")
    }

# --- CRUD DE USUARIOS ---

@router.get("/", dependencies=[Depends(require_role(["admin", "docente"]))])
def listar_usuarios():
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, nombres, apellidos, "tipoDocumento", dni, "fechaNacimiento", correo, celular,
                           "carreraObjetivo", "areaAcademica", rol, estado,
                           ciclovirtual, salario, created_at
                    FROM users
                    ORDER BY created_at DESC;
                """)
                return fetch_all_as_dict(cur)
        except Exception as e:
            print(f"[Listar Usuarios] Error en Postgres: {e}")
        finally:
            conn.close()

    db = load_db()
    return [
        {k: v for k, v in user.items() if k not in {"password", "universidad"}}
        for user in db.get("users", [])
    ]

@router.post("/", status_code=status.HTTP_201_CREATED)
def crear_usuario(
    user_data: UserCreate,
    admin: Dict[str, Any] = Depends(require_role(["admin"])),
):
    """
    Crea el perfil asociado a una cuenta ya creada en InsForge Auth.
    Solo un administrador puede asignar el rol y guardar el perfil.
    """
    if admin.get("estado", "Activo") != "Activo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta administradora está inactiva.",
        )

    auth_user_id = str(user_data.authUserId)

    conn = get_pg_connection()
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo conectar con PostgreSQL para guardar el perfil.",
        )

    user_dict = user_data.model_dump(exclude={"authUserId"})
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM users
                WHERE correo = %s OR dni = %s OR auth_user_id = %s::uuid
                LIMIT 1;
                """,
                (user_dict["correo"], user_dict["dni"], auth_user_id),
            )
            if fetch_one_as_dict(cur):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ya existe un perfil con ese correo, documento o cuenta de Auth.",
                )

            cur.execute("SELECT id FROM users WHERE id ~ '^USR-[0-9]+$';")
            existing_ids = fetch_all_as_dict(cur)
            existing_numbers = set()
            for r in existing_ids:
                try:
                    existing_numbers.add(int(r["id"].split("-")[1]))
                except (ValueError, IndexError):
                    pass

            next_candidate = 101
            while next_candidate in existing_numbers:
                next_candidate += 1

            user_dict["id"] = f"USR-{next_candidate:03d}"

            cur.execute(
                """
                INSERT INTO users (
                    id, auth_user_id, nombres, apellidos, "tipoDocumento", dni,
                    "fechaNacimiento", correo, celular, "carreraObjetivo",
                    "areaAcademica", rol, estado, ciclovirtual, created_at
                ) VALUES (
                    %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                )
                RETURNING id, auth_user_id, nombres, apellidos, "tipoDocumento",
                          dni, "fechaNacimiento", correo, celular, "carreraObjetivo",
                          "areaAcademica", rol, estado;
                """,
                (
                    user_dict["id"],
                    auth_user_id,
                    user_dict["nombres"],
                    user_dict["apellidos"],
                    user_dict.get("tipoDocumento") or "DNI",
                    user_dict["dni"],
                    user_dict.get("fechaNacimiento"),
                    user_dict["correo"],
                    user_dict.get("celular"),
                    user_dict.get("carreraObjetivo"),
                    user_dict.get("areaAcademica"),
                    user_dict["rol"],
                    user_dict["estado"],
                    user_dict.get("cicloVirtual") or "Semestral",
                ),
            )
            created = fetch_one_as_dict(cur)
            conn.commit()
            return {
                "mensaje": "Perfil de usuario creado correctamente.",
                "usuario": created,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        err_msg = str(exc)
        print(f"[Usuarios] Error guardando perfil: {err_msg}")
        if "check_users_dni" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La base de datos rechazó el documento por no cumplir el formato legal seleccionado.",
            ) from exc
        if "check_users_celular" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La base de datos rechazó el número telefónico por no cumplir el formato permitido.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo guardar el perfil en PostgreSQL.",
        ) from exc
    finally:
        conn.close()

@router.put("/{identificador}", dependencies=[Depends(require_role(["admin"]))])
def actualizar_usuario(identificador: str, user_data: UserUpdate):
    update_data = user_data.model_dump(exclude_unset=True)
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE id = %s OR correo = %s LIMIT 1;", 
                            (identificador, identificador))
                user = fetch_one_as_dict(cur)
                if not user:
                    raise HTTPException(status_code=404, detail="Usuario no encontrado")

                set_clauses = []
                values = []
                for k, v in update_data.items():
                    col_name = f'"{k}"' if k in ["fechaNacimiento", "carreraObjetivo", "areaAcademica", "tipoDocumento"] else k
                    set_clauses.append(f"{col_name} = %s")
                    values.append(v)

                if set_clauses:
                    values.append(user["id"])
                    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s RETURNING id, nombres, apellidos, dni, celular;"
                    cur.execute(query, tuple(values))
                    conn.commit()
                return {"mensaje": "Usuario actualizado correctamente"}
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            err_msg = str(e)
            if "check_users_dni" in err_msg:
                raise HTTPException(status_code=422, detail="Error DB: El documento no cumple el formato válido")
            if "check_users_celular" in err_msg:
                raise HTTPException(status_code=422, detail="Error DB: El celular no cumple el formato nacional o internacional")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            conn.close()

    db = load_db()
    users = db.get("users", [])
    user = next((u for u in users if u.get("correo") == identificador or u.get("id") == identificador), None)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    for key, value in update_data.items():
        user[key] = value
    save_db(db)
    return {"mensaje": "Usuario actualizado correctamente (modo local)"}

@router.delete("/{identificador}", dependencies=[Depends(require_role(["admin"]))])
def eliminar_usuario(identificador: str):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s OR correo = %s RETURNING id;", 
                            (identificador, identificador))
                deleted = cur.fetchone()
                conn.commit()
                if not deleted:
                    raise HTTPException(status_code=404, detail="Usuario no encontrado")
                return {"mensaje": "Usuario eliminado correctamente"}
        finally:
            conn.close()

    db = load_db()
    users = db.get("users", [])
    user_to_delete = next((u for u in users if u.get("correo") == identificador or u.get("id") == identificador), None)
    if not user_to_delete:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db["users"] = [u for u in users if u.get("correo") != identificador and u.get("id") != identificador]
    save_db(db)
    return {"mensaje": "Usuario eliminado correctamente (modo local)"}
