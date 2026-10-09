from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Tokens reales proporcionados
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbkBnbWFpbC5jb20iLCJyb2wiOiJhZG1pbiJ9.S0DpfXtHFEKdsb5ouUNpTm9jn_xmjHDfnmgKWOJUxBY"
ESTUDIANTE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlc3R1ZGlhbnRlQGdtYWlsLmNvbSIsInJvbCI6ImVzdHVkaWFudGUifQ.DSEoBuA4UEKShHwvd51aga3YRh88Kj3tbW3ihpR8ByQ"

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
        "horaInicio": "10:00",
        "horaFin": "12:00",
        "meetUrl": "https://meet.google.com/test",
        "repositorio": "test"
    }
    
    response = client.post("/horarios/cursos", json=nuevo_curso_fake, headers=ESTUDIANTE_HEADERS)
    
    # Verificamos que devuelva un código de acceso denegado controlado (401 o 403)
    assert response.status_code in [401, 403], f"Se esperaba un bloqueo de seguridad, pero devolvió el código {response.status_code}"

def test_motor_reglas_cruce_docente():
    """
    Valida que el motor de reglas detecte el cruce si se intenta asignar
    un horario superpuesto para el mismo docente (Dra. Elena, USR-004, Lunes de 15:00 a 17:00).
    """
    curso_con_cruce = {
        "docenteId": "USR-004",
        "dias": ["Lunes"],
        "horaInicio": "15:30",
        "horaFin": "16:30"
    }
    response = client.post("/monitoreo/validar-curso", json=curso_con_cruce, headers=ADMIN_HEADERS)
    assert response.status_code == 400
    assert "Conflicto" in response.json().get("detail", "")

def test_listar_sesiones_asistencia():
    """Valida que se puedan listar las sesiones de asistencia."""
    response = client.get("/asistencia/sesiones", headers=ESTUDIANTE_HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_marcar_asistencia_puntual():
    """Valida que un estudiante que marca dentro de la tolerancia sea registrado como puntual."""
    # Primero creamos una sesión de prueba con hora de apertura a las 08:00
    sesion_test = {
        "id": "SES-TEST-01",
        "cursoId": "CRS-001",
        "asignatura": "Álgebra y Funciones",
        "docenteId": "USR-002",
        "docente": "Prof. Carmen Rosa Herrera",
        "fecha": "2026-10-09",
        "horaApertura": "08:00",
        "modalidad": "Virtual",
        "asistentesPuntuales": [],
        "tardanzas": [],
        "ausentes": []
    }
    client.post("/asistencia/sesiones", json=sesion_test, headers=ADMIN_HEADERS)

    # Marcación a las 08:05 (dentro de los 10 min de tolerancia -> Puntual)
    marcacion = {
        "estudianteId": "EST-001",
        "estudiante": "Juan Pérez",
        "horaRegistro": "08:05"
    }
    response = client.post("/asistencia/sesiones/SES-TEST-01/marcar", json=marcacion, headers=ESTUDIANTE_HEADERS)
    
    assert response.status_code == 200
    assert response.json().get("estado") == "Puntual"

def test_marcar_asistencia_tardanza():
    """Valida que un estudiante que marca tarde sea registrado automáticamente como tardanza."""
    sesion_test = {
        "id": "SES-TEST-02",
        "cursoId": "CRS-001",
        "asignatura": "Álgebra y Funciones",
        "docenteId": "USR-002",
        "docente": "Prof. Carmen Rosa Herrera",
        "fecha": "2026-10-09",
        "horaApertura": "08:00",
        "modalidad": "Virtual",
        "asistentesPuntuales": [],
        "tardanzas": [],
        "ausentes": []
    }
    client.post("/asistencia/sesiones", json=sesion_test, headers=ADMIN_HEADERS)

    # Marcación a las 08:15 (fuera de los 10 min de tolerancia -> Tardanza)
    marcacion = {
        "estudianteId": "EST-002",
        "estudiante": "María Gómez",
        "horaRegistro": "08:15"
    }
    response = client.post("/asistencia/sesiones/SES-TEST-02/marcar", json=marcacion, headers=ESTUDIANTE_HEADERS)
    
    assert response.status_code == 200
    assert response.json().get("estado") == "Tardanza"

def test_marcar_asistencia_sesion_no_existe():
    """Valida el manejo de error (404) si se intenta marcar en una sesión de asistencia que no existe."""
    marcacion = {
        "estudianteId": "EST-001",
        "estudiante": "Juan Pérez",
        "horaRegistro": "08:05"
    }
    # Intentamos marcar en una sesión ID inventada
    response = client.post("/asistencia/sesiones/SES-INEXISTENTE-999/marcar", json=marcacion, headers=ESTUDIANTE_HEADERS)
    
    assert response.status_code == 404
    assert "Sesión de asistencia no encontrada" in response.json().get("detail", "")