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

## Vinculación de Google Meet (docentes)

La primera fase de la integración permite que un docente vincule su cuenta Google
desde la sección Perfil/Configuración. El correo institucional (`correo`) y el
correo Google (`googleEmail`) son independientes; `googleEmail` solo se guarda
después de una autorización OAuth válida.

1. Guarda el JSON OAuth descargado de Google Cloud fuera del repositorio, por
   por ejemplo en una carpeta privada fuera del repositorio, como
   `C:\ruta\privada\client_secret.json`.
2. Copia `.env.example` como `.env` y ajusta las rutas locales:

   ```text
   GOOGLE_CLIENT_SECRETS_FILE=C:\ruta\privada\client_secret.json
   GOOGLE_TOKEN_STORE=C:\ruta\privada\google-oauth-tokens.json
   GOOGLE_REDIRECT_URI=http://localhost:8000/google/callback
   ```

3. Exporta esas variables en la terminal antes de iniciar FastAPI. En
   PowerShell:

   ```powershell
   $env:GOOGLE_CLIENT_SECRETS_FILE="C:\ruta\privada\client_secret.json"
   $env:GOOGLE_TOKEN_STORE="C:\ruta\privada\google-oauth-tokens.json"
   $env:GOOGLE_REDIRECT_URI="http://localhost:8000/google/callback"
   ```

4. Inicia el servidor con `uvicorn main:app --reload`, inicia sesión como
   docente y solicita `GET /google/authorize` con el token del portal. El
   backend devolverá `authorizationUrl`; el frontend debe redirigir el navegador
   a esa URL.
5. Google regresará a `/google/callback`. El backend verificará la cuenta,
   almacenará los tokens en `GOOGLE_TOKEN_STORE` y actualizará `googleEmail`.

El archivo OAuth y el almacén de tokens están excluidos por `.gitignore`. No
deben subirse a GitHub ni enviarse al frontend.

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
