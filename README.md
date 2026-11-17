# Herramientas de desarrollo

```
python -m venv venv
venv\Scripts\Activate
pip install -r requirements.txt

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

### Registro de usuarios e InsForge Auth

Las cuentas se crean en InsForge Auth. Solo un administrador autenticado puede
guardar el perfil en `public.users` mediante `POST /usuarios/`, asociado por
`auth_user_id`, y asignar el rol elegido (`admin`, `docente` o `estudiante`).
El endpoint requiere el token Bearer del administrador; no el token de la
cuenta recién creada. No se deben enviar `password` ni `universidad` al backend.

Configura `DATABASE_URL` e `INSFORGE_HOST` en el entorno del backend. Para una
base existente, aplica la migración
[`20261117001500_users_auth_profile.sql`](./migrations/20261117001500_users_auth_profile.sql)
con el CLI de InsForge (`npx @insforge/cli db migrations up --all`) antes de
desplegar el backend. La migración elimina las columnas `password` y
`universidad` de `public.users`; los perfiles anteriores quedan sin vínculo de
Auth hasta que se asocien a una cuenta.

### Asistencia docente Meet en InsForge

El registro manual de asistencia docente también está desplegado en el proyecto
InsForge `Portal-backend`. No usa Google Meet API ni detecta participantes
automáticamente: el docente registra la entrada, abre el enlace del curso y
registra la salida al finalizar.

Funciones activas:

```text
meet-entrada
meet-salida
meet-auditoria
```

Las funciones reciben el token de InsForge en `Authorization: Bearer <token>`.
El cuerpo debe incluir el identificador del curso:

```json
{
  "curso_id": "CRS-003"
}
```

Ejemplo desde el frontend con `@insforge/sdk`:

```typescript
await insforge.functions.invoke("meet-entrada", {
  body: { curso_id: "CRS-003" }
});

await insforge.functions.invoke("meet-salida", {
  body: { curso_id: "CRS-003" }
});

const { data: historial } = await insforge.functions.invoke("meet-auditoria", {
  body: { curso_id: "CRS-003" }
});
```

La tabla remota es `meet_attendance`. Las funciones validan que el usuario
autenticado sea un docente activo, que sea el docente asignado al curso y que
no tenga otra sesión activa para el mismo curso.
