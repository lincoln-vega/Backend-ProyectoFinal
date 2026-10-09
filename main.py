from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import usuarios, horarios, asistencia, monitoreo

app = FastAPI(
    title="API Backend - Academia SCRM", 
    version="2.0",
    description="Backend modularizado con FastAPI y control de roles (RBAC)"
)

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrar los módulos (Routers)
app.include_router(usuarios.router)
app.include_router(horarios.router)
app.include_router(asistencia.router)
app.include_router(monitoreo.router)
# Ruta raíz para verificar que el servidor enciende
@app.get("/")
def read_root():
    return {"mensaje": "Bienvenido al Backend de Academia SCRM 🚀", "docs": "/docs"}