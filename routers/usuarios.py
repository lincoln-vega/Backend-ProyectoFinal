from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from database import load_db, save_db
from auth import verify_password, hash_password, create_access_token, require_role

# Creamos el router para usuarios
router = APIRouter(prefix="/usuarios", tags=["Usuarios"])

# --- MODELOS PYDANTIC ---
class UserLogin(BaseModel):
    correo: str
    password: str

class UserCreate(BaseModel):
    nombres: str
    apellidos: str
    correo: str
    password: str
    rol: str  # admin, docente, estudiante
    estado: str = "Activo"

class UserUpdate(BaseModel):
    nombres: str | None = None
    apellidos: str | None = None
    correo: str | None = None
    rol: str | None = None
    estado: str | None = None

# --- ENDPOINT DE LOGIN (Puede estar aquí o en auth, lo ponemos aquí por orden) ---
@router.post("/login", tags=["Autenticación"])
def login(form_data: UserLogin):
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
    db = load_db()
    return [{k: v for k, v in u.items() if k != "password"} for u in db.get("users", [])]

@router.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def crear_usuario(user_data: UserCreate):
    db = load_db()
    users = db.get("users", [])
    
    if any(u["correo"] == user_data.correo for u in users):
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    
    nuevo_usuario = user_data.dict()
    nuevo_usuario["password"] = hash_password(user_data.password)
    
    users.append(nuevo_usuario)
    db["users"] = users
    save_db(db)
    
    return {"mensaje": "Usuario creado exitosamente", "correo": user_data.correo}

@router.put("/{correo}", dependencies=[Depends(require_role(["admin"]))])
def actualizar_usuario(correo: str, user_data: UserUpdate):
    db = load_db()
    users = db.get("users", [])
    
    user = next((u for u in users if u["correo"] == correo), None)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    update_data = user_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        user[key] = value
        
    db["users"] = users
    save_db(db)
    
    return {"mensaje": "Usuario actualizado correctamente"}

@router.delete("/{correo}", dependencies=[Depends(require_role(["admin"]))])
def eliminar_usuario(correo: str):
    db = load_db()
    users = db.get("users", [])
    
    user_to_delete = next((u for u in users if u["correo"] == correo), None)
    if not user_to_delete:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    db["users"] = [u for u in users if u["correo"] != correo]
    save_db(db)
    
    return {"mensaje": "Usuario eliminado correctamente"}