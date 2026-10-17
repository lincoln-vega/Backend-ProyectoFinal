# Registro de Cambios (Changelog) - Backend

Todas las notas notables de cambios a este proyecto serán documentadas en este archivo siguiendo el formato de versiones semánticas (SemVer).

## [0.5.0] - 2026-10-16
### Añadido
- **Arquitectura de API:** Backend modularizado con FastAPI y routers separados (`usuarios`, `horarios`, `asistencia`, `monitoreo`).
- **Autenticación y RBAC:** Emisión y validación de tokens JWT con roles (`admin`, `docente`, `estudiante`) y hash de contraseñas con bcrypt.
- **Motor de Reglas de Horarios:** Validación contra cruces de horarios para el mismo docente o en las mismas aulas.
- **Módulo de Asistencia:** Registro de sesiones con categorización automática según tolerancia (Puntual, Tardanza, Ausente).
- **Pruebas Automatizadas:** Suite de pruebas unitarias con `pytest` y `TestClient` para endpoints y lógica de negocio.
- **DevOps y Flujo Git:** Flujo estandarizado (`feature-*`, `Dev`, `main`), suite de CI/CD y plantillas de issues.
