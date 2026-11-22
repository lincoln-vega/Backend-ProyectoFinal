from fastapi import Request
from fastapi.testclient import TestClient
from main import app
from auth import get_current_identity, get_current_user, require_role
import routers.usuarios as usuarios_router

client = TestClient(app)

# Tokens reales proporcionados
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbkBnbWFpbC5jb20iLCJyb2wiOiJhZG1pbiJ9.S0DpfXtHFEKdsb5ouUNpTm9jn_xmjHDfnmgKWOJUxBY"
ESTUDIANTE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlc3R1ZGlhbnRlQGdtYWlsLmNvbSIsInJvbCI6ImVzdHVkaWFudGUifQ.DSEoBuA4UEKShHwvd51aga3YRh88Kj3tbW3ihpR8ByQ"
DOCENTE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjLmhlcnJlcmFAYWNhZGVtaWEuZWR1LnBlIiwicm9sIjoiZG9jZW50ZSJ9.lo1tIIBxTmod551oi4kr1nxt9qn1CntsA3hor3VasSk"

ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
ESTUDIANTE_HEADERS = {"Authorization": f"Bearer {ESTUDIANTE_TOKEN}"}
DOCENTE_HEADERS = {"Authorization": f"Bearer {DOCENTE_TOKEN}"}

_TEST_USERS = {
    ADMIN_TOKEN: {"correo": "admin@gmail.com", "rol": "admin", "nombres": "Admin", "apellidos": "Test"},
    ESTUDIANTE_TOKEN: {
        "correo": "estudiante@gmail.com",
        "rol": "estudiante",
        "nombres": "Estudiante",
        "apellidos": "Test",
    },
    DOCENTE_TOKEN: {
        "correo": "c.herrera@academia.edu.pe",
        "rol": "docente",
        "nombres": "Docente",
        "apellidos": "Test",
    },
}


def _test_current_user(request: Request):
    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ")
    return _TEST_USERS[token]


app.dependency_overrides[get_current_user] = _test_current_user
app.dependency_overrides[get_current_identity] = lambda: {
    "id": "00000000-0000-4000-8000-000000000001",
    "email": "demo@example.com",
}


def test_role_guard_accepts_capitalized_database_role():
    current_user = {"rol": "Admin"}

    assert require_role(["admin"])(current_user) is current_user


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

def test_listar_matriculas_como_admin():
    """Valida que el admin pueda listar las matrículas."""
    response = client.get("/matriculas", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_listar_pagos_como_estudiante():
    """Valida que el estudiante pueda consultar los pagos."""
    response = client.get("/matriculas/pagos", headers=ESTUDIANTE_HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

# --- PRUEBAS SCRM-104: MÁQUINA DE ESTADOS DE SOLICITUDES / INCIDENCIAS ---

def test_listar_incidencias_como_admin():
    """Valida que el administrador pueda listar las incidencias registradas."""
    response = client.get("/incidencias", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_registrar_incidencia_docente():
    """Valida que un docente pueda registrar una solicitud de permuta o justificación en estado Pendiente."""
    nueva_solicitud = {
        "id": "INC-TEST-01",
        "docente": "Prof. Carmen Rosa Herrera",
        "tipo": "Solicitud de Permuta / Reemplazo",
        "materia": "Álgebra y Funciones",
        "cursoId": "CRS-001",
        "horario": "Lunes y Miércoles 08:00 - 10:00 AM",
        "motivo": "Sustentación de grado académico.",
        "archivoNombre": "constancia_sustentacion.pdf"
    }
    response = client.post("/incidencias", json=nueva_solicitud, headers=DOCENTE_HEADERS)
    assert response.status_code == 201
    data = response.json()
    assert data["incidencia"]["estado"] == "Pendiente"
    assert data["incidencia"]["id"] == "INC-TEST-01"

def test_maquina_estados_transicion_valida_y_reemplazo():
    """Valida la transición permitida Pendiente -> Aprobado con docente de reemplazo (SCRM-104)."""
    # Creamos una incidencia base
    incidencia_id = "INC-TEST-TRANS"
    client.post("/incidencias", json={
        "id": incidencia_id,
        "docente": "Prof. Carmen Rosa Herrera",
        "tipo": "Inasistencia Inesperada",
        "materia": "Álgebra y Funciones",
        "cursoId": "CRS-001",
        "horario": "Lunes y Miércoles 08:00 - 10:00 AM",
        "motivo": "Cita médica programada."
    }, headers=DOCENTE_HEADERS)

    # El Administrador aprueba y asigna reemplazo
    transicion = {
        "nuevoEstado": "Aprobado",
        "docenteReemplazo": "Prof. Miguel Ángel Paredes",
        "docenteReemplazoId": "USR-003",
        "horarioActualizado": "Lunes y Miércoles 08:00 - 10:00 AM",
        "observaciones": "Reemplazo aprobado por dirección académica."
    }
    response = client.patch(f"/incidencias/{incidencia_id}/estado", json=transicion, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["incidencia"]["estado"] == "Aprobado"
    assert res_data["incidencia"]["docenteReemplazo"] == "Prof. Miguel Ángel Paredes"

def test_maquina_estados_transicion_prohibida_terminal():
    """Valida que una incidencia Aprobada no pueda revertirse a Pendiente (Máquina de estados estricta)."""
    incidencia_id = "INC-TEST-FORBIDDEN"
    client.post("/incidencias", json={
        "id": incidencia_id,
        "docente": "Prof. Carmen Rosa Herrera",
        "tipo": "Inasistencia Inesperada",
        "materia": "Álgebra y Funciones",
        "cursoId": "CRS-001",
        "horario": "Lunes y Miércoles 08:00 - 10:00 AM",
        "motivo": "Cita médica."
    }, headers=DOCENTE_HEADERS)

    # La aprobamos primero
    client.patch(f"/incidencias/{incidencia_id}/estado", json={"nuevoEstado": "Aprobado"}, headers=ADMIN_HEADERS)

    # Intentamos revertir a Pendiente (prohibido)
    transicion_invalida = {
        "nuevoEstado": "Pendiente",
        "observaciones": "Intento de reversión no permitido."
    }
    response = client.patch(f"/incidencias/{incidencia_id}/estado", json=transicion_invalida, headers=ADMIN_HEADERS)
    assert response.status_code == 400
    assert "Transición prohibida por la Máquina de Estados" in response.json().get("detail", "")

# --- PRUEBAS SCRM-105: MOTOR DE NÓMINA Y CRUCE DE HORAS EFECTIVAS ---

def test_cruce_nomina_algoritmo():
    """Valida el cálculo del algoritmo del motor de nómina cruzando horas programadas vs incidencias (SCRM-105)."""
    payload = {
        "periodo": "2026-10",
        "tarifaHoraDefault": 50.0
    }
    response = client.post("/incidencias/nomina/cruce", json=payload, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    data = response.json()
    
    assert "resumenGeneral" in data
    assert "detalleDocentes" in data
    assert data["resumenGeneral"]["totalDocentesAuditados"] > 0
    assert data["resumenGeneral"]["totalHorasProgramadas"] > 0
    assert data["resumenGeneral"]["totalPlanillaPEN"] > 0
    
    # Validar que los campos clave de cada docente auditado existan
    primer_docente = data["detalleDocentes"][0]
    assert "horasProgramadas" in primer_docente
    assert "horasEfectivas" in primer_docente
    assert "montoNeto" in primer_docente
    assert "estadoAuditoria" in primer_docente

# --- PRUEBAS CAPA 2: VALIDACIÓN RIGUROSA DE DNI Y NÚMERO TELEFÓNICO (PERÚ) ---

def test_endpoint_validar_datos_exitoso():
    """Valida que el endpoint responda 200 cuando el DNI (8 dígitos) y el celular (9 dígitos y empieza en 9) son correctos."""
    payload = {
        "dni": "74839201",
        "celular": "987654321",
        "correo": "alumno.valido@academia.edu.pe"
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 200
    assert response.json()["valido"] is True

def test_endpoint_validar_datos_dni_con_letras_rechazado():
    """Valida que el backend rechace con 422 si el DNI contiene letras."""
    payload = {
        "dni": "7483920A",
        "celular": "987654321"
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 422
    assert "dni" in response.json()["detail"]["errores"]

def test_endpoint_validar_datos_dni_longitud_incorrecta():
    """Valida que el backend rechace con 422 si el DNI no tiene exactamente 8 dígitos."""
    payload = {
        "dni": "1234567",  # 7 dígitos
        "celular": "987654321"
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 422
    assert "dni" in response.json()["detail"]["errores"]

def test_endpoint_validar_datos_celular_invalido():
    """Valida que el backend rechace con 422 si el celular no tiene 9 dígitos o no empieza con 9."""
    payload = {
        "dni": "74839201",
        "celular": "355353535353"  # 12 dígitos, no empieza en 9
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 422
    assert "celular" in response.json()["detail"]["errores"]

def test_crear_usuario_rechazo_dni_invalido_pydantic():
    """Valida que al crear usuario el modelo Pydantic rechace DNI con letras o longitud errónea."""
    nuevo_usuario_invalido = {
        "authUserId": "00000000-0000-4000-8000-000000000001",
        "nombres": "Usuario",
        "apellidos": "Prueba",
        "correo": "usuario.invalido@academia.edu.pe",
        "rol": "estudiante",
        "dni": "8765432X",  # Letra
        "celular": "987111222"
    }
    response = client.post(
        "/usuarios/",
        json=nuevo_usuario_invalido,
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422


def test_crear_usuario_rechaza_password_en_perfil():
    payload = {
        "authUserId": "00000000-0000-4000-8000-000000000001",
        "nombres": "Usuario",
        "apellidos": "Prueba",
        "correo": "demo@example.com",
        "dni": "12345678",
        "password": "NoDebeLlegarAlPerfil123!",
    }

    response = client.post("/usuarios/", json=payload, headers=ADMIN_HEADERS)

    assert response.status_code == 422
    assert any(
        error["loc"][-1] == "password"
        for error in response.json()["detail"]
    )

def test_crear_usuario_requiere_administrador():
    payload = {
        "authUserId": "00000000-0000-4000-8000-000000000001",
        "nombres": "Usuario",
        "apellidos": "Prueba",
        "correo": "demo@example.com",
        "dni": "12345678",
        "rol": "admin",
    }

    response = client.post("/usuarios/", json=payload, headers=ESTUDIANTE_HEADERS)

    assert response.status_code == 403


def test_crear_usuario_guarda_perfil_sin_password_ni_universidad(monkeypatch):
    class FakeCursor:
        description = None
        row = None
        insert_query = ""
        insert_values = ()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, values=()):
            if "SELECT id FROM users" in query:
                self.description = [("id",)]
                self.row = None
            elif "SELECT COUNT(*) FROM users" in query:
                self.description = [("count",)]
                self.row = (0,)
            elif "INSERT INTO users" in query:
                self.insert_query = query
                self.insert_values = values
                columns = [
                    "id",
                    "auth_user_id",
                    "nombres",
                    "apellidos",
                    "tipoDocumento",
                    "dni",
                    "fechaNacimiento",
                    "correo",
                    "celular",
                    "carreraObjetivo",
                    "areaAcademica",
                    "rol",
                    "estado",
                ]
                self.description = [(column,) for column in columns]
                self.row = (
                    "USR-101",
                    "00000000-0000-4000-8000-000000000001",
                    "Demo",
                    "Usuario",
                    "DNI",
                    "12345678",
                    "2000-01-02",
                    "demo@example.com",
                    "987654321",
                    "Sistemas",
                    "Ciencias",
                    "docente",
                    "Activo",
                )
            else:
                raise AssertionError(f"Unexpected SQL in test: {query}")

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = FakeCursor()
            self.committed = False

        def cursor(self):
            return self.fake_cursor

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("A valid profile insert should not roll back")

        def close(self):
            pass

    connection = FakeConnection()
    monkeypatch.setattr(usuarios_router, "get_pg_connection", lambda: connection)
    payload = {
        "authUserId": "00000000-0000-4000-8000-000000000001",
        "nombres": "Demo",
        "apellidos": "Usuario",
        "correo": "demo@example.com",
        "dni": "12345678",
        "celular": "987654321",
        "fechaNacimiento": "2000-01-02",
        "carreraObjetivo": "Sistemas",
        "areaAcademica": "Ciencias",
        "rol": "docente",
        "estado": "Activo",
        "cicloVirtual": "Semestral",
    }

    response = client.post("/usuarios/", json=payload, headers=ADMIN_HEADERS)

    assert response.status_code == 201
    assert response.json()["usuario"]["correo"] == "demo@example.com"
    assert response.json()["usuario"]["rol"] == "docente"
    assert connection.committed
    assert "password" not in connection.fake_cursor.insert_query.lower()
    assert "universidad" not in connection.fake_cursor.insert_query.lower()
    assert "demo@example.com" in connection.fake_cursor.insert_values
    assert "docente" in connection.fake_cursor.insert_values

def test_endpoint_validar_datos_carnet_extranjeria_exitoso():
    """Valida que un Carnet de Extranjería (CE) válido de 9 a 12 caracteres alfanuméricos sea aceptado."""
    payload = {
        "tipoDocumento": "CE",
        "dni": "CE123456789",
        "celular": "+573001234567"  # Celular internacional con código +57
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 200
    assert response.json()["valido"] is True

def test_endpoint_validar_datos_pasaporte_exitoso():
    """Valida que un Pasaporte válido de 6 a 12 caracteres sea aceptado."""
    payload = {
        "tipoDocumento": "Pasaporte",
        "dni": "PAS987654",
        "celular": "+34612345678"  # Celular internacional España
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 200
    assert response.json()["valido"] is True

def test_endpoint_validar_datos_carnet_extranjeria_invalido():
    """Valida que un Carnet de Extranjería menor a 9 caracteres sea rechazado con 422."""
    payload = {
        "tipoDocumento": "CE",
        "dni": "CE1234",  # Muy corto
        "celular": "+51987654321"
    }
    response = client.post("/usuarios/validar", json=payload)
    assert response.status_code == 422
    assert "dni" in response.json()["detail"]["errores"]


# --- PRUEBAS DE AUDITORÍA: ALINEACIÓN FRONTEND / INSFORGE / RBAC ---

def test_usuarios_get_incluye_cursos_y_normaliza_listas():
    """Verifica que GET /usuarios/ incluya cursosInscritos, cursosAsignados, especialidad, auth_user_id y cicloVirtual."""
    response = client.get("/usuarios/", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    users = response.json()
    assert len(users) > 0
    first = users[0]
    for field in (
        "id", "nombres", "apellidos", "tipoDocumento", "dni", "fechaNacimiento",
        "correo", "celular", "carreraObjetivo", "areaAcademica", "rol", "estado",
        "ciclovirtual", "cicloVirtual", "salario", "especialidad",
        "cursosAsignados", "cursosInscritos", "auth_user_id", "created_at"
    ):
        assert field in first, f"Falta el campo {field} en UsuarioResponse"
    assert isinstance(first["cursosAsignados"], list)
    assert isinstance(first["cursosInscritos"], list)


def test_estudiante_no_ve_salario_de_docentes():
    """Verifica que si un Estudiante consulta /usuarios/, el campo financiero salario se oculte (None)."""
    response = client.get("/usuarios/", headers=ESTUDIANTE_HEADERS)
    assert response.status_code == 200
    users = response.json()
    assert all(u.get("salario") is None for u in users)


def test_parse_course_list_normaliza_formatos_postgres():
    """Verifica que UsuarioResponse normalice cadenas CSV, literales de array Postgres y JSON arrays."""
    u = usuarios_router.UsuarioResponse.model_validate({
        "id": "USR-999",
        "nombres": "Ana",
        "apellidos": "Test",
        "correo": "ana@test.com",
        "cursosInscritos": "{CRS-001,CRS-002}",
        "cursosAsignados": "CRS-003, CRS-004",
    })
    assert u.cursosInscritos == ["CRS-001", "CRS-002"]
    assert u.cursosAsignados == ["CRS-003", "CRS-004"]


def test_validar_curso_excluye_mismo_id_en_edicion():
    """Verifica que editar un curso existente (enviando su propio id) no genere falso positivo consigo mismo."""
    curso_editado = {
        "id": "CRS-003",
        "asignatura": "Biología y Anatomía",
        "docenteId": "USR-004",
        "docente": "Elena Sofía Torres Meléndez",
        "dias": ["Lunes", "Viernes"],
        "horaInicio": "15:00",
        "horaFin": "17:00",
        "estado": "Activo",
    }
    response = client.post("/monitoreo/validar-curso", json=curso_editado, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["valido"] is True


def test_validar_curso_permite_horarios_contiguos():
    """Verifica que clases contiguas (ej. 17:00-19:00 después de 15:00-17:00) no se marquen como cruce."""
    curso_contiguo = {
        "id": "CRS-NEW-CONT",
        "asignatura": "Anatomía II",
        "docenteId": "USR-004",
        "dias": ["Lunes"],
        "horaInicio": "17:00",
        "horaFin": "19:00",
        "estado": "Activo",
    }
    response = client.post("/monitoreo/validar-curso", json=curso_contiguo, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["valido"] is True


def test_incidencias_acepta_aliases_lowercase_y_camelcase():
    """Verifica que POST /incidencias acepte y devuelva tanto docenteId como docenteid y cursoId como cursoid."""
    payload = {
        "id": "INC-ALIAS-01",
        "docente": "Miguel Ángel Paredes Ruiz",
        "docenteid": "USR-003",
        "cursoid": "CRS-002",
        "tipo": "Tardanza",
        "materia": "Física Fundamental",
        "horario": "Martes 08:00 - 10:00",
        "motivo": "Tráfico intenso",
        "docentepropuesto": "Juan Gabriel Pérez Ramos",
    }
    response = client.post("/incidencias", json=payload, headers=DOCENTE_HEADERS)
    assert response.status_code == 201
    inc = response.json()["incidencia"]
    assert inc["docenteId"] == "USR-003"
    assert inc["docenteid"] == "USR-003"
    assert inc["cursoId"] == "CRS-002"
    assert inc["cursoid"] == "CRS-002"
    assert inc["docentePropuesto"] == "Juan Gabriel Pérez Ramos"



