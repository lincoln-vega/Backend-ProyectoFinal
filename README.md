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

### 👥 Cuentas de Acceso y Credenciales de Prueba (InsForge Auth)

Las cuentas oficiales registradas y sincronizadas entre InsForge Auth y la base de datos PostgreSQL son:

| Rol | Correo Electrónico | Contraseña | Usuario Registrado | Carrera / Asignaturas | Panel de Redirección |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Administrador** | `admin@gmail.com` | `admin123` | Roberto Carlos Morales Salazar | Gestión Institucional | `/admin/usuarios` |
| **Docente (Titular)** | `chavez@gmail.com` | `Chavez123` | Prof. Pedro Chavez | Física Fundamental, Trigonometría, Historia | `/docente/panel` |
| **Docente** | `vicky@gmail.com` | `vicky123` | Prof. Victoria Raggi | Álgebra y Funciones, Química | `/docente/panel` |
| **Estudiante 1** | `esteban@gmail.com` | `Esteban123` | Esteban Torres | **Medicina Humana** (Física, Trigonometría) | `/estudiante/panel` |
| **Estudiante 2** | `estudiante@gmail.com` | `estudiante123` | Juan Carlos Flores Mendoza | **Ingeniería de Sistemas** (Álgebra, Física) | `/estudiante/panel` |
| **Estudiante 3** | `anapaula@gmail.com` | `AnaPaula123!` | Ana Paula Vargas Chavez | **Derecho y Ciencias Políticas** (Historia) | `/estudiante/panel` |

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

### Imagen Docker para InsForge Compute

El workflow `.github/workflows/deploy.yml` publica en GHCR al hacer push a
`main`. La imagen se etiqueta como
`ghcr.io/lincoln-vega/backend-proyectofinal:main` y también con el SHA del
commit. En pull requests solo se construye, no se publica. `db.json`, `.env` y
`.insforge` se excluyen del contexto/imagen; configura `DATABASE_URL` e
`INSFORGE_HOST` en el servicio de Compute.

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
