import json
import re
from typing import Optional, List, Dict, Any, Union
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, AliasChoices, field_validator, model_validator
from database import load_db, save_db, get_pg_connection, fetch_all_as_dict, fetch_one_as_dict
from auth import (
    authenticate_with_supabase,
    create_supabase_auth_user,
    delete_supabase_auth_user,
    get_profile_by_auth_id,
    require_role,
)

# Router para gestión de usuarios
router = APIRouter(prefix="/usuarios", tags=["Usuarios"])

# --- FUNCIONES DE NORMALIZACIÓN Y VALIDACIÓN ESTÁNDAR (CAPA 2) ---

def parse_course_list(value: Any) -> List[str]:
    """
    Normaliza cursosInscritos y cursosAsignados desde texto separado por comas ("CRS-001,CRS-002"),
    literales de array Postgres ("{CRS-001,CRS-002}"), JSON array ('["CRS-001"]') o listas Python
    a list[str] limpios.
    """
    if not value:
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                val = item.get("id")
            else:
                val = item
            if val is not None and str(val).strip():
                result.append(str(val).strip())
        return result
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return []
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, list):
                    return parse_course_list(parsed)
            except Exception:
                pass
        clean_str = re.sub(r"^\{|\}$|^\[|\]$", "", trimmed)
        return [
            re.sub(r'^["\']|["\']$', "", s.strip())
            for s in clean_str.split(",")
            if re.sub(r'^["\']|["\']$', "", s.strip())
        ]
    return []


def validar_formato_documento(tipo: str, doc: str) -> str:
    tipo_norm = (tipo or "DNI").strip().upper()
    doc_clean = str(doc or "").strip().upper()
    if not doc_clean:
        raise ValueError("El número de documento de identidad es obligatorio.")

    if tipo_norm == "DNI":
        if not re.match(r"^\d{8}$", doc_clean):
            raise ValueError("El DNI peruano debe contener exactamente 8 dígitos numéricos y no permite letras.")
    elif tipo_norm in ["CE", "CARNET DE EXTRANJERÍA", "CARNET DE EXTRANJERIA"]:
        if not re.match(r"^[A-Z0-9]{9,12}$", doc_clean):
            raise ValueError("El Carnet de Extranjería (CE) debe contener entre 9 y 12 caracteres alfanuméricos.")
    elif tipo_norm in ["PASAPORTE", "PAS"]:
        if not re.match(r"^[A-Z0-9]{6,12}$", doc_clean):
            raise ValueError("El Pasaporte debe contener entre 6 y 12 caracteres alfanuméricos.")
    else:
        raise ValueError(f"Tipo de documento '{tipo}' no reconocido. Seleccione DNI, CE o Pasaporte.")

    return doc_clean


def validar_formato_celular(cel: Optional[str]) -> Optional[str]:
    if cel is None or str(cel).strip() == "":
        return None
    cel_clean = str(cel).strip()
    if not (re.match(r"^9\d{8}$", cel_clean) or re.match(r"^\+\d{8,15}$", cel_clean)):
        raise ValueError("El número telefónico debe contener 9 dígitos numéricos iniciando en 9 (Perú) o formato internacional con prefijo '+' y entre 8 y 15 dígitos.")
    return cel_clean


def normalizar_rol(rol: Optional[str]) -> str:
    r = str(rol or "estudiante").strip().lower()
    if r.startswith("admin"):
        return "admin"
    if r.startswith("doc") or r.startswith("prof"):
        return "docente"
    if r.startswith("est") or r.startswith("alum"):
        return "estudiante"
    raise ValueError("El rol debe ser admin, docente o estudiante.")


# --- MODELOS PYDANTIC ---

class UserLogin(BaseModel):
    correo: str
    password: str


class ValidationCheckRequest(BaseModel):
    tipoDocumento: Optional[str] = "DNI"
    dni: Optional[str] = None
    celular: Optional[str] = None
    correo: Optional[str] = None


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    nombres: str = ""
    apellidos: str = ""
    tipoDocumento: str = "DNI"
    dni: Optional[str] = ""
    fechaNacimiento: Optional[str] = ""
    correo: str
    celular: Optional[str] = ""
    carreraObjetivo: Optional[str] = ""
    areaAcademica: Optional[str] = ""
    rol: str = "estudiante"
    estado: str = "Activo"
    ciclovirtual: str = Field(
        default="Semestral",
        validation_alias=AliasChoices("ciclovirtual", "cicloVirtual", "ciclo_virtual"),
    )
    cicloVirtual: str = "Semestral"
    salario: Optional[float] = 0.0
    especialidad: Optional[str] = ""
    cursosAsignados: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cursosAsignados", "cursos_asignados", "CursosAsignados"),
    )
    cursosInscritos: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cursosInscritos", "cursos_inscritos", "CursosInscritos"),
    )
    auth_user_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("auth_user_id", "authUserId"),
    )
    created_at: Optional[Union[str, datetime]] = None

    @field_validator("cursosAsignados", "cursosInscritos", mode="before")
    @classmethod
    def normalizar_listas_cursos(cls, v: Any) -> List[str]:
        return parse_course_list(v)

    @field_validator("auth_user_id", mode="before")
    @classmethod
    def normalizar_auth_id(cls, v: Any) -> Optional[str]:
        return str(v) if v is not None else None

    @field_validator("created_at", mode="before")
    @classmethod
    def normalizar_created_at(cls, v: Any) -> Optional[str]:
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v) if v is not None else None

    @model_validator(mode="after")
    def sincronizar_aliases(self):
        ciclo = self.ciclovirtual or self.cicloVirtual or "Semestral"
        self.ciclovirtual = ciclo
        self.cicloVirtual = ciclo
        if not self.especialidad:
            self.especialidad = self.areaAcademica or self.carreraObjetivo or ""
        return self


def sanitizar_usuario_por_rol(user_dict: Dict[str, Any], requester_role: str) -> Dict[str, Any]:
    """
    Convierte una fila de base de datos o dict al esquema UsuarioResponse
    y oculta campos financieros sensibles (salario) si quien consulta es Estudiante.
    Nunca expone password ni tokens.
    """
    clean_input = {k: v for k, v in user_dict.items() if k not in {"password", "hashed_password", "token", "service_token", "universidad"}}
    validated = UsuarioResponse.model_validate(clean_input).model_dump()
    if str(requester_role or "").strip().lower() == "estudiante":
        validated["salario"] = None
    return validated


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: Optional[str] = None
    authUserId: UUID = Field(
        ...,
        validation_alias=AliasChoices("authUserId", "auth_user_id"),
    )
    nombres: str = Field(..., min_length=2, max_length=100, description="Nombres del usuario")
    apellidos: str = Field(..., min_length=2, max_length=100, description="Apellidos del usuario")
    correo: str = Field(..., description="Correo institucional o personal")
    rol: str = Field("estudiante", description="Rol: admin, docente, estudiante")
    estado: str = Field("Activo", description="Estado: Activo o Inactivo")
    tipoDocumento: str = Field("DNI", description="Tipo de documento: DNI, CE, Pasaporte")
    dni: str = Field(..., description="Número de documento")
    celular: Optional[str] = Field(None, description="Celular nacional (9 dígitos) o internacional con +")
    fechaNacimiento: Optional[str] = None
    carreraObjetivo: Optional[str] = None
    cicloVirtual: Optional[str] = Field(
        default="Semestral",
        validation_alias=AliasChoices("cicloVirtual", "ciclovirtual"),
    )
    areaAcademica: Optional[str] = None
    salario: Optional[float] = None
    especialidad: Optional[str] = None
    cursosAsignados: List[str] = Field(default_factory=list)
    cursosInscritos: List[str] = Field(default_factory=list)

    @field_validator("rol", mode="before")
    @classmethod
    def validar_rol_campo(cls, v: str) -> str:
        return normalizar_rol(v)

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, v: str) -> str:
        if not v or "@" not in str(v):
            raise ValueError("El formato del correo electrónico es inválido.")
        return str(v).strip().lower()

    @field_validator("cursosAsignados", "cursosInscritos", mode="before")
    @classmethod
    def validar_listas_cursos(cls, v: Any) -> List[str]:
        return parse_course_list(v)

    @model_validator(mode="after")
    def validar_documento_y_telefono(self):
        self.dni = validar_formato_documento(self.tipoDocumento, self.dni)
        self.celular = validar_formato_celular(self.celular)
        if self.salario is None:
            self.salario = 3200.0 if self.rol == "docente" else 0.0
        if not self.especialidad:
            self.especialidad = self.areaAcademica or self.carreraObjetivo or ""
        return self


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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
    cicloVirtual: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("cicloVirtual", "ciclovirtual"),
    )
    areaAcademica: Optional[str] = None
    salario: Optional[float] = None
    especialidad: Optional[str] = None
    cursosAsignados: Optional[List[str]] = None
    cursosInscritos: Optional[List[str]] = None
    authUserId: Optional[UUID] = Field(
        default=None,
        validation_alias=AliasChoices("authUserId", "auth_user_id"),
    )

    @field_validator("rol", mode="before")
    @classmethod
    def validar_rol_opcional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return normalizar_rol(v)

    @field_validator("cursosAsignados", "cursosInscritos", mode="before")
    @classmethod
    def validar_listas_opcional(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        return parse_course_list(v)

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
        motivo = "; ".join([f"{k}: {v}" for k, v in errores.items()])
        print(f"[ERROR - VALIDACIÓN USUARIO] Ocurrió un error al validar los datos del usuario (DNI: {req.dni}, Correo: {req.correo}). Motivo: {motivo}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"mensaje": "Error de validación en campos del usuario", "errores": errores},
        )

    print(f"[OK - VALIDACIÓN USUARIO] Datos de usuario validados correctamente (Documento: {req.tipoDocumento or 'DNI'} {req.dni}, Celular: {req.celular or 'N/A'}).")
    return {"valido": True, "mensaje": "Validación exitosa: documento y número telefónico conformes"}


# --- ENDPOINT DE LOGIN ---

class AuthAccountCreateRequest(BaseModel):
    correo: str
    password: str = Field(..., min_length=6)
    nombre: Optional[str] = ""
    rol: str = "estudiante"


@router.post("/auth-account", status_code=status.HTTP_201_CREATED)
def crear_cuenta_auth_supabase(
    req: AuthAccountCreateRequest,
    admin: Dict[str, Any] = Depends(require_role(["admin"])),
):
    if str(admin.get("estado", "Activo")).strip().lower() != "activo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta administradora está inactiva.",
        )
    return create_supabase_auth_user(
        email=req.correo.strip().lower(),
        password=req.password,
        full_name=(req.nombre or "").strip(),
        role=normalizar_rol(req.rol),
    )


@router.post("/login", tags=["Autenticación"])
def login(form_data: UserLogin):
    correo_clean = form_data.correo.strip().lower()
    try:
        auth_session = authenticate_with_supabase(
            correo_clean,
            form_data.password,
        )
    except HTTPException as exc:
        print(f"[ERROR - LOGIN] Ocurrió un error al iniciar sesión para '{correo_clean}'. Motivo: {exc.detail}")
        raise

    auth_user = auth_session.get("user")
    access_token = auth_session.get("accessToken")
    if not isinstance(auth_user, dict) or not access_token or not auth_user.get("id"):
        print(f"[ERROR - LOGIN] Supabase Auth no devolvió una sesión válida para '{correo_clean}'.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase Auth no devolvió una sesión válida.",
        )

    user = get_profile_by_auth_id(str(auth_user["id"]), correo_clean)
    if user is None:
        print(f"[ERROR - LOGIN] La cuenta '{correo_clean}' (auth_id={auth_user['id']}) no tiene perfil en public.users.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta de Auth no tiene un perfil registrado en users.",
        )

    if str(user.get("estado", "Activo")).strip().lower() != "activo":
        print(f"[ERROR - LOGIN] Acceso denegado para '{correo_clean}'. Motivo: La cuenta se encuentra inactiva.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta se encuentra inactiva",
        )

    full_name = f"{user['nombres']} {user['apellidos']}".strip()
    print(f"[OK - LOGIN] Usuario '{full_name}' ({correo_clean}, Rol: {user['rol']}) inició sesión correctamente.")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["rol"],
        "name": full_name,
        "userId": user["id"],
        "id": user["id"],
        "email": user.get("correo"),
    }


# --- CRUD DE USUARIOS ---

@router.get("/", response_model=List[UsuarioResponse])
def listar_usuarios(
    current_user: Dict[str, Any] = Depends(require_role(["admin", "docente", "estudiante"])),
):
    requester_role = str(current_user.get("rol", "estudiante"))
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM users
                    ORDER BY created_at DESC;
                    """
                )
                rows = fetch_all_as_dict(cur)
                return [sanitizar_usuario_por_rol(row, requester_role) for row in rows]
        except Exception as e:
            print(f"[Listar Usuarios] Error en Postgres: {e}")
        finally:
            conn.close()

    db = load_db()
    return [
        sanitizar_usuario_por_rol(user, requester_role)
        for user in db.get("users", [])
    ]


@router.get("/{identificador}", response_model=UsuarioResponse)
def obtener_usuario(
    identificador: str,
    current_user: Dict[str, Any] = Depends(require_role(["admin", "docente", "estudiante"])),
):
    requester_role = str(current_user.get("rol", "estudiante"))
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE id = %s OR LOWER(correo) = LOWER(%s) OR dni = %s
                    LIMIT 1;
                    """,
                    (identificador, identificador, identificador),
                )
                row = fetch_one_as_dict(cur)
                if not row:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
                return sanitizar_usuario_por_rol(row, requester_role)
        except HTTPException:
            raise
        except Exception as e:
            print(f"[Obtener Usuario] Error en Postgres: {e}")
        finally:
            conn.close()

    db = load_db()
    ident_lower = identificador.strip().lower()
    user = next(
        (
            u
            for u in db.get("users", [])
            if str(u.get("id", "")) == identificador
            or str(u.get("correo", "")).lower() == ident_lower
            or str(u.get("dni", "")) == identificador
        ),
        None,
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return sanitizar_usuario_por_rol(user, requester_role)


@router.post("/", status_code=status.HTTP_201_CREATED)
def crear_usuario(
    user_data: UserCreate,
    admin: Dict[str, Any] = Depends(require_role(["admin"])),
):
    """
    Crea el perfil asociado a una cuenta ya creada en InsForge Auth y devuelve
    el objeto completo del usuario recién insertado.
    """
    if str(admin.get("estado", "Activo")).strip().lower() != "activo":
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
                print(f"[ERROR - CREAR USUARIO] Ocurrió un error al registrar al usuario '{user_dict['nombres']} {user_dict['apellidos']}'. Motivo: Ya existe un perfil con el correo '{user_dict['correo']}', DNI '{user_dict['dni']}' o auth_user_id '{auth_user_id}'.")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ya existe un perfil con ese correo, documento o cuenta de Auth.",
                )

            if not user_dict.get("id"):
                cur.execute("SELECT id FROM users WHERE id ~ '^USR-[0-9]+$';")
                existing_ids = fetch_all_as_dict(cur)
                existing_numbers = set()
                for r in existing_ids:
                    try:
                        existing_numbers.add(int(str(r.get("id", "")).split("-")[1]))
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
            created = fetch_one_as_dict(cur) or {}

            # Actualizar campos extendidos (salario, cursosAsignados, cursosInscritos, especialidad) si existen en la tabla
            try:
                cur.execute(
                    """
                    UPDATE users
                    SET salario = %s,
                        "cursosAsignados" = %s::jsonb,
                        "cursosInscritos" = %s::jsonb
                    WHERE id = %s;
                    """,
                    (
                        user_dict.get("salario", 0.0),
                        json.dumps(user_dict.get("cursosAsignados") or []),
                        json.dumps(user_dict.get("cursosInscritos") or []),
                        user_dict["id"],
                    ),
                )
            except Exception:
                pass

            conn.commit()

            full_created = {
                **user_dict,
                **created,
                "auth_user_id": str(created.get("auth_user_id") or auth_user_id),
                "ciclovirtual": user_dict.get("cicloVirtual") or "Semestral",
                "cicloVirtual": user_dict.get("cicloVirtual") or "Semestral",
                "salario": user_dict.get("salario", 0.0),
                "especialidad": user_dict.get("especialidad") or user_dict.get("areaAcademica") or user_dict.get("carreraObjetivo") or "",
                "cursosAsignados": parse_course_list(created.get("cursosAsignados") or user_dict.get("cursosAsignados")),
                "cursosInscritos": parse_course_list(created.get("cursosInscritos") or user_dict.get("cursosInscritos")),
            }
            normalized_user = UsuarioResponse.model_validate(full_created).model_dump()
            print(f"[OK - CREAR USUARIO] Usuario '{normalized_user['nombres']} {normalized_user['apellidos']}' (ID: {normalized_user['id']}, Rol: {normalized_user['rol']}) registrado correctamente.")
            return {
                **normalized_user,
                "mensaje": "Perfil de usuario creado correctamente.",
                "usuario": normalized_user,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        err_msg = str(exc)
        print(f"[ERROR - CREAR USUARIO] Ocurrió un error al registrar al usuario '{user_dict.get('nombres')} {user_dict.get('apellidos')}'. Motivo: {err_msg}")
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
    if "cicloVirtual" in update_data:
        update_data["ciclovirtual"] = update_data.pop("cicloVirtual")
    if "authUserId" in update_data:
        update_data["auth_user_id"] = str(update_data.pop("authUserId"))

    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM users WHERE id = %s OR correo = %s LIMIT 1;",
                    (identificador, identificador),
                )
                user = fetch_one_as_dict(cur)
                if not user:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

                quoted_cols = {
                    "fechaNacimiento",
                    "carreraObjetivo",
                    "areaAcademica",
                    "tipoDocumento",
                    "cursosAsignados",
                    "cursosInscritos",
                }
                set_clauses = []
                values = []
                for k, v in update_data.items():
                    if k == "especialidad" and "especialidad" not in user:
                        continue
                    col_name = f'"{k}"' if k in quoted_cols else k
                    if k in {"cursosAsignados", "cursosInscritos"}:
                        set_clauses.append(f"{col_name} = %s::jsonb")
                        values.append(json.dumps(parse_course_list(v)))
                    else:
                        set_clauses.append(f"{col_name} = %s")
                        values.append(v)

                updated_row = user
                if set_clauses:
                    values.append(user["id"])
                    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s RETURNING *;"
                    cur.execute(query, tuple(values))
                    updated_row = fetch_one_as_dict(cur) or {**user, **update_data}
                    conn.commit()

                normalized = UsuarioResponse.model_validate({**user, **update_data, **updated_row}).model_dump()
                print(f"[OK - ACTUALIZAR USUARIO] Usuario '{normalized['nombres']} {normalized['apellidos']}' (ID: {normalized['id']}) actualizado correctamente.")
                return {
                    **normalized,
                    "mensaje": "Usuario actualizado correctamente",
                    "usuario": normalized,
                }
        except HTTPException as http_exc:
            print(f"[ERROR - ACTUALIZAR USUARIO] Ocurrió un error al actualizar el usuario '{identificador}'. Motivo: {http_exc.detail}")
            raise
        except Exception as e:
            conn.rollback()
            err_msg = str(e)
            print(f"[ERROR - ACTUALIZAR USUARIO] Ocurrió un error al actualizar el usuario '{identificador}'. Motivo: {err_msg}")
            if "check_users_dni" in err_msg:
                raise HTTPException(status_code=422, detail="Error DB: El documento no cumple el formato válido")
            if "check_users_celular" in err_msg:
                raise HTTPException(status_code=422, detail="Error DB: El celular no cumple el formato nacional o internacional")
            raise HTTPException(status_code=500, detail="No se pudo actualizar el usuario en la base de datos.")
        finally:
            conn.close()

    db = load_db()
    users = db.get("users", [])
    user = next((u for u in users if u.get("correo") == identificador or u.get("id") == identificador), None)
    if not user:
        print(f"[ERROR - ACTUALIZAR USUARIO] Ocurrió un error al actualizar el usuario '{identificador}'. Motivo: Usuario no encontrado.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    for key, value in update_data.items():
        user[key] = value
    save_db(db)
    normalized = UsuarioResponse.model_validate(user).model_dump()
    print(f"[OK - ACTUALIZAR USUARIO] Usuario '{normalized['nombres']} {normalized['apellidos']}' (ID: {normalized['id']}) actualizado correctamente (modo local).")
    return {
        **normalized,
        "mensaje": "Usuario actualizado correctamente (modo local)",
        "usuario": normalized,
    }


@router.delete("/{identificador}", dependencies=[Depends(require_role(["admin"]))])
def eliminar_usuario(identificador: str):
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # 1. Consultar id, correo y auth_user_id antes de eliminar
                cur.execute(
                    "SELECT id, correo, auth_user_id FROM users WHERE id = %s OR correo = %s;",
                    (identificador, identificador),
                )
                row = fetch_one_as_dict(cur)
                if not row:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

                user_id = row["id"]
                user_email = row["correo"]
                auth_id = row.get("auth_user_id")

                # 2. Eliminar de public.users
                cur.execute("DELETE FROM users WHERE id = %s;", (user_id,))

                # 3. Eliminar de Supabase Auth Admin API y auth.users para permitir recrear el usuario limpiamente
                if auth_id:
                    delete_supabase_auth_user(str(auth_id))
                    try:
                        cur.execute("DELETE FROM auth.users WHERE id = %s::uuid;", (auth_id,))
                    except Exception as e_auth:
                        print(f"[Eliminar Usuario] No se pudo eliminar de auth.users por id: {e_auth}")
                elif user_email:
                    try:
                        cur.execute("DELETE FROM auth.users WHERE email = %s;", (user_email,))
                    except Exception as e_auth:
                        print(f"[Eliminar Usuario] No se pudo eliminar de auth.users por email: {e_auth}")

                conn.commit()
                print(f"[OK - ELIMINAR USUARIO] Usuario '{user_email}' (ID: {user_id}) eliminado correctamente de la base de datos y de Auth.")
                return {
                    "mensaje": f"Usuario {user_id} eliminado correctamente de la base de datos y de Auth",
                    "id": user_id,
                }
        except HTTPException as http_exc:
            print(f"[ERROR - ELIMINAR USUARIO] Ocurrió un error al eliminar al usuario '{identificador}'. Motivo: {http_exc.detail}")
            raise
        except Exception as e:
            conn.rollback()
            print(f"[ERROR - ELIMINAR USUARIO] Ocurrió un error al eliminar al usuario '{identificador}'. Motivo: {e}")
            raise HTTPException(status_code=500, detail="No se pudo eliminar el usuario en la base de datos.")
        finally:
            conn.close()

    db = load_db()
    users = db.get("users", [])
    user_to_delete = next((u for u in users if u.get("correo") == identificador or u.get("id") == identificador), None)
    if not user_to_delete:
        print(f"[ERROR - ELIMINAR USUARIO] Ocurrió un error al eliminar al usuario '{identificador}'. Motivo: Usuario no encontrado.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    db["users"] = [u for u in users if u.get("correo") != identificador and u.get("id") != identificador]
    save_db(db)
    print(f"[OK - ELIMINAR USUARIO] Usuario '{user_to_delete.get('nombres', '')} {user_to_delete.get('apellidos', '')}' (ID: {user_to_delete.get('id')}) eliminado correctamente (modo local).")
    return {"mensaje": "Usuario eliminado correctamente (modo local)", "id": user_to_delete.get("id")}
