from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import check_pg_health
from routers import (
    usuarios,
    horarios,
    asistencia,
    monitoreo,
    matriculas,
    incidencias,
    academicos,
    actividades,
    mensajes,
    configuracion,
    storage,
)

app = FastAPI(
    title="API Backend - Academia Preuniversitaria Virtual",
    version="2.1.0",
    description="Backend FastAPI unificado con Supabase (PostgreSQL, Auth y Storage) y control de roles RBAC",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(usuarios.router)
app.include_router(horarios.router)
app.include_router(asistencia.router)
app.include_router(monitoreo.router)
app.include_router(matriculas.router)
app.include_router(incidencias.router)
app.include_router(academicos.router)
app.include_router(actividades.router_tareas)
app.include_router(actividades.router_anuncios)
app.include_router(mensajes.router)
app.include_router(configuracion.router)
app.include_router(storage.router)


@app.get("/")
def read_root():
    return {
        "mensaje": "Bienvenido al Backend de Academia Preuniversitaria Virtual",
        "version": "2.1.0",
        "provider": "supabase",
    }


@app.get("/health/db", tags=["Diagnóstico"])
def health_db():
    """Keep-alive SQL para evitar la pausa por inactividad de 7 días en Supabase Free."""
    return check_pg_health()
