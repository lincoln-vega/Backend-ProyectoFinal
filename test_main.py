from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Tokens reales proporcionados
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbkBnbWFpbC5jb20iLCJyb2wiOiJhZG1pbiIsImV4cCI6MTc5MTM5ODU3NH0.xDI4h28W6USfsaU57a5RwyidvqkEu7jbZH4a-SAn9t8"
ESTUDIANTE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlc3R1ZGlhbnRlQGdtYWlsLmNvbSIsInJvbCI6ImVzdHVkaWFudGUiLCJleHAiOjE3OTEzOTg3MTh9.BS7aXZgV5MGETDxcho8AbhBS6oDFyYEhSu3Eo1j1a50"

ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
ESTUDIANTE_HEADERS = {"Authorization": f"Bearer {ESTUDIANTE_TOKEN}"}

def test_read_root():
    """Valida que la raíz del API responda correctamente sin importar el rol."""
    response = client.get("/")
    assert response.status_code == 200
    assert "mensaje" in response.json()

def test_cursos_como_admin():
    """Valida que el admin pueda listar los cursos."""
    response = client.get("/horarios/cursos", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_cursos_como_estudiante():
    """Valida que el estudiante también tenga permiso de ver los cursos del portal."""
    response = client.get("/horarios/cursos", headers=ESTUDIANTE_HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_clases_por_dia_como_estudiante():
    """Valida que el estudiante pueda consultar su cronograma del día."""
    response = client.get("/horarios/hoy/Lunes", headers=ESTUDIANTE_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["dia_consultado"] == "Lunes"

def test_crear_curso_prohibido_para_estudiante():
    """
    Valida que si un estudiante intenta realizar una acción de Administrador 
    (como crear un curso), el sistema le niegue el acceso de forma controlada 
    esperando un código de error de permisos (401 o 403) en lugar de romper la prueba.
    """
    nuevo_curso_fake = {
        "id": "CRS-999",
        "asignatura": "Curso Hack",
        "area": "Pruebas",
        "carrera": "Sistemas",
        "docenteId": "USR-002",
        "docente": "Prof. Carmen",
        "dias": ["Viernes"],
        "horaInicio": "10:00 AM",
        "horaFin": "12:00 PM",
        "meetUrl": "https://meet.google.com/test",
        "repositorio": "test"
    }
    
    response = client.post("/horarios/cursos", json=nuevo_curso_fake, headers=ESTUDIANTE_HEADERS)
    
    # Verificamos que devuelva un código de acceso denegado controlado (401 o 403)
    assert response.status_code in [401, 403], f"Se esperaba un bloqueo de seguridad, pero devolvió el código {response.status_code}"