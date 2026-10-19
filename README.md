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
deben subirse a GitHub ni enviarse al frontend. La creación de espacios Meet,
Pub/Sub y la auditoría de `participantSessions` se implementarán después de
validar esta vinculación.

Con la cuenta vinculada, el docente asignado a un curso puede crear su espacio
de Meet mediante `POST /meet/cursos/{curso_id}/espacio`. La respuesta contiene
`meetUrl`; el backend guarda también `meetSpaceName` y
`googleOrganizerEmail` dentro del curso. Si se amplían los permisos OAuth, el
docente debe desconectar y volver a conectar su cuenta para obtener un token
con los permisos actualizados.

Para recibir entradas y salidas reales, configura en `.env` el Project ID de
Google Cloud y un topic Pub/Sub:

```text
GOOGLE_CLOUD_PROJECT_ID=tu-project-id
GOOGLE_PUBSUB_TOPIC=meet-attendance-events
```

El topic debe existir en Google Cloud y Pub/Sub debe poder entregar eventos a
`POST /meet/events/pubsub` mediante HTTPS. Después de crear el espacio, el
docente ejecuta `POST /meet/cursos/{curso_id}/suscripcion`. La auditoría se
consulta con `GET /meet/cursos/{curso_id}/auditoria`. En desarrollo local, el
endpoint de Pub/Sub requiere un túnel HTTPS público.
