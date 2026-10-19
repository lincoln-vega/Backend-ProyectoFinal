# Herramientas de desarrollo

```
python -m venv venv
venv\Scripts\Activate
pip install fastapi uvicorn

-- Levanta el servidor
uvicorn main:app --reload

--Encriptador
pip install "python-jose[cryptography]"
pip install bcrypt


--Realizar pruebas manuales
!el servidor debe estar corriendo con pip install fastapi uvicorn¡
http://localhost:8000/docs

-- Realizar pruebas automatizadas
pip install pytest httpx
pytest -v
```

### Registro manual de asistencia docente

Para el flujo `feature/SCRM-15-asistencia-interfaz`, la interfaz registra la
sesión explícitamente:

```text
POST /meet/cursos/{curso_id}/entrada?docente_email=...
POST /meet/cursos/{curso_id}/salida?docente_email=...
```

La entrada devuelve `meet_url` y crea una sesión `EN_PROGRESO`. La salida
finaliza esa misma sesión y calcula `minutos_efectivos`. El docente debe ser el
usuario autenticado y estar asignado al curso.
