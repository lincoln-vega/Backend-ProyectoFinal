import re
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import verify_password, hash_password, create_access_token, require_role

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
    nombres: str = Field(..., min_length=2, max_length=100, description="Nombres del usuario")
    apellidos: str = Field(..., min_length=2, max_length=100, description="Apellidos del usuario")
    correo: str = Field(..., description="Correo institucional o personal")
    password: Optional[str] = Field("123456", description="Contraseña de acceso")
    rol: str = Field("estudiante", description="Rol: admin, docente, estudiante")
    estado: str = Field("Activo", description="Estado: Activo o Inactivo")
    tipoDocumento: str = Field("DNI", description="Tipo de documento: DNI, CE, Pasaporte")
    dni: str = Field(..., description="Número de documento")
    celular: Optional[str] = Field(None, description="Celular nacional (9 dígitos) o internacional con +")
    fechaNacimiento: Optional[str] = None
    carreraObjetivo: Optional[str] = None
    cicloVirtual: Optional[str] = "Semestral"
    universidad: Optional[str] = None
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
    universidad: Optional[str] = None
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
    conn = get_pg_connection()
    user = None
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE correo = %s LIMIT 1;", (form_data.correo.strip().lower(),))
                user = fetch_one_as_dict(cur)
        except Exception as e:
            print(f"[Login] Error buscando en Postgres: {e}")
        finally:
            conn.close()

    if not user:
        db = load_db()
        users_db = db.get("users", [])
        user = next((u for u in users_db if u["correo"] == form_data.correo), None)

    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos"
        )
    
    if user.get("estado", "Activo") != "Activo":
        raise HTTPException(status_code=403, detail="Cuenta inactiva")
    
    access_token = create_access_token(data={"sub": user["correo"], "rol": user["rol"]})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["rol"],
        "name": f"{user['nombres']} {user['apellidos']}"
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
                           universidad, "carreraObjetivo", "areaAcademica", rol, estado,
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
    return [{k: v for k, v in u.items() if k != "password"} for u in db.get("users", [])]

@router.post("/", status_code=status.HTTP_201_CREATED)
def crear_usuario(user_data: UserCreate):
    """
    Crea un usuario aplicando validación Pydantic previa (Capa 2) y persistiendo
    en PostgreSQL (Capa 3) con captura de errores de restricción SQL.
    """
    user_dict = user_data.model_dump()
    raw_password = user_dict.get("password") or "123456"
    user_dict["password"] = hash_password(raw_password)

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # 1. Validar unicidad de correo y DNI
                cur.execute("SELECT id FROM users WHERE correo = %s OR dni = %s LIMIT 1;", 
                            (user_dict["correo"], user_dict["dni"]))
                existing = fetch_one_as_dict(cur)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Ya existe un usuario registrado con el mismo correo electrónico o documento"
                    )

                # Generar ID
                cur.execute("SELECT COUNT(*) FROM users;")
                count_row = fetch_one_as_dict(cur)
                count = count_row["count"] if count_row else 0
                user_id = f"USR-{count + 101:03d}"
                user_dict["id"] = user_id

                cur.execute("""
                    INSERT INTO users (
                        id, nombres, apellidos, "tipoDocumento", dni, "fechaNacimiento", correo, password,
                        celular, universidad, "carreraObjetivo", "areaAcademica", rol,
                        estado, ciclovirtual, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, NOW()
                    ) RETURNING id, nombres, apellidos, "tipoDocumento", dni, correo, celular, rol, estado;
                """, (
                    user_dict["id"], user_dict["nombres"], user_dict["apellidos"], user_dict.get("tipoDocumento") or "DNI",
                    user_dict["dni"], user_dict.get("fechaNacimiento"), user_dict["correo"], user_dict["password"],
                    user_dict.get("celular"), user_dict.get("universidad"), user_dict.get("carreraObjetivo"),
                    user_dict.get("areaAcademica"), user_dict["rol"], user_dict["estado"],
                    user_dict.get("cicloVirtual") or "Semestral"
                ))
                created = cur.fetchone()
                conn.commit()
                
                # Sincronizar en db.json para mantener consistencia de pruebas locales
                try:
                    db = load_db()
                    users = db.get("users", [])
                    users.append(user_dict)
                    db["users"] = users
                    save_db(db)
                except Exception:
                    pass

                return {
                    "mensaje": "Usuario creado y validado exitosamente en las 3 capas",
                    "usuario": created
                }
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            err_msg = str(e)
            if "check_users_dni" in err_msg:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="La base de datos rechazó el documento por no cumplir el formato legal del tipo seleccionado (DNI 8 dígitos, CE 9-12 alfanuméricos, Pasaporte 6-12 alfanuméricos)."
                )
            if "check_users_celular" in err_msg:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="La base de datos rechazó el número telefónico por no cumplir el formato peruano (9 dígitos iniciando en 9) o internacional (+ seguido de 8 a 15 dígitos)."
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error en persistencia de base de datos: {err_msg}"
            )
        finally:
            conn.close()

    # Fallback sin postgres
    db = load_db()
    users = db.get("users", [])
    if any(u["correo"] == user_data.correo or u.get("dni") == user_data.dni for u in users):
        raise HTTPException(status_code=400, detail="El correo o documento ya está registrado")
    
    users.append(user_dict)
    db["users"] = users
    save_db(db)
    return {"mensaje": "Usuario creado exitosamente (modo local)", "correo": user_data.correo}

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
