# Contribuyendo a Backend - Proyecto Final

## Flujo de trabajo de ramas
- **feature-<nombre>** – rama de desarrollo individual (ejemplo: `feature-lincoln_modulo-cursos`).
- **Dev** (o `dev`) – rama de integración donde se agrupan los features del equipo y se prueban errores.
- **main** – rama de producción. Cada merge de `Dev` a `main` genera una versión semántica (`vMAJOR.MINOR.PATCH`).

## Flujo de Pull Request
1. Crea tu rama personal desde `Dev`: `git checkout -b feature-<tu-nombre>_<modulo>`.
2. Realiza commits siguiendo **Conventional Commits** (ejemplo: `feat: agregar endpoint de asistencia`, `fix: corregir validacion de cruce`).
3. Abre un PR hacia la rama `Dev` para revisión por el equipo.
4. Al verificar que las pruebas pasan, se aprueba e integra en `Dev`.
5. Al consolidar una versión funcional, se abre PR de `Dev` hacia `main` con su respectiva versión semántica.

## Herramientas y Ejecución
- **Python 3.12+**
- Instalar dependencias: `pip install -r requirements.txt`
- Correr pruebas: `pytest`
- Ejecutar servidor: `uvicorn main:app --reload`
