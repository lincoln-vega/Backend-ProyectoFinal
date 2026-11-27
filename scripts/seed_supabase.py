#!/usr/bin/env python3
"""
Script idempotente de aprovisionamiento y semilla para Supabase (Academia Preuniversitaria Virtual).
1. Aplica schema.sql v2 completo en PostgreSQL.
2. Crea los 3 buckets privados de Storage (tareas-entregas, vouchers-pago, justificantes-docentes).
3. Registra / actualiza las cuentas reales en Supabase Auth y las vincula en public.users.
4. Inserta el catálogo académico UNMSM/UNI, 3 ciclos reales (2 activos + 1 finalizado para historial),
   cursos preuniversitarios sin cruces horarios, matrículas, pagos, tareas, anuncios, mensajes y simulacros.

Uso:
    cd back && python scripts/seed_supabase.py
"""
import json
import os
import sys
from pathlib import Path

import httpx
import psycopg2
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

BUCKETS = [
    {"id": "tareas-entregas", "name": "tareas-entregas", "public": False, "file_size_limit": 15 * 1024 * 1024},
    {"id": "materiales-clase", "name": "materiales-clase", "public": False, "file_size_limit": 15 * 1024 * 1024},
    {"id": "vouchers-pago", "name": "vouchers-pago", "public": False, "file_size_limit": 5 * 1024 * 1024},
    {"id": "justificantes-docentes", "name": "justificantes-docentes", "public": False, "file_size_limit": 5 * 1024 * 1024},
]

SEED_USERS = [
    {
        "id": "USR-001",
        "nombres": "Roberto Carlos",
        "apellidos": "Morales Salazar",
        "dni": "41235678",
        "fechaNacimiento": "1985-04-12",
        "correo": "admin@gmail.com",
        "password": "admin123",
        "celular": "987654321",
        "carreraObjetivo": "Dirección Académica",
        "areaAcademica": "Gestión Institucional y Empresarial",
        "rol": "admin",
        "ciclovirtual": "Semestral San Marcos 2026-II",
        "salario": 4500.00,
        "especialidad": "Dirección Académica Preuniversitaria",
        "cursosAsignados": [],
        "cursosInscritos": [],
    },
    {
        "id": "USR-107",
        "nombres": "Pedro Martlon",
        "apellidos": "Chavez Quispe",
        "dni": "45892317",
        "fechaNacimiento": "1989-05-14",
        "correo": "chavez@gmail.com",
        "password": "Chavez123",
        "celular": "987107107",
        "carreraObjetivo": "Cátedra de Álgebra y Trigonometría",
        "areaAcademica": "Ciencias Exactas e Ingenierías",
        "rol": "docente",
        "ciclovirtual": "Semestral San Marcos 2026-II",
        "salario": 3400.00,
        "especialidad": "Álgebra y Trigonometría Preuniversitaria",
        "cursosAsignados": ["CRS-001", "CRS-004", "CRS-V01"],
        "cursosInscritos": [],
    },
    {
        "id": "USR-002",
        "nombres": "Carmen Rosa",
        "apellidos": "Herrera Villanueva",
        "dni": "45678912",
        "fechaNacimiento": "1988-06-20",
        "correo": "c.herrera@academia.edu.pe",
        "password": "Herrera123",
        "celular": "987111222",
        "carreraObjetivo": "Cátedra de Biología y Química",
        "areaAcademica": "Ciencias Médicas y de la Salud",
        "rol": "docente",
        "ciclovirtual": "Semestral San Marcos 2026-II",
        "salario": 3500.00,
        "especialidad": "Biología, Anatomía y Química DECO",
        "cursosAsignados": ["CRS-003", "CRS-006", "CRS-V02"],
        "cursosInscritos": [],
    },
    {
        "id": "USR-003",
        "nombres": "Miguel Ángel",
        "apellidos": "Paredes Ruiz",
        "dni": "48923104",
        "fechaNacimiento": "1990-09-15",
        "correo": "m.paredes@academia.edu.pe",
        "password": "Paredes123",
        "celular": "987222333",
        "carreraObjetivo": "Cátedra de Física y Razonamiento Matemático",
        "areaAcademica": "Ciencias Exactas e Ingenierías",
        "rol": "docente",
        "ciclovirtual": "Anual UNI 2026",
        "salario": 3300.00,
        "especialidad": "Física General y Aptitud Matemática UNI",
        "cursosAsignados": ["CRS-002", "CRS-005"],
        "cursosInscritos": [],
    },
    {
        "id": "USR-109",
        "nombres": "Victoria Angelis",
        "apellidos": "Raggi Ruiz",
        "dni": "46123987",
        "fechaNacimiento": "1991-02-11",
        "correo": "v.raggi@academia.edu.pe",
        "password": "Victoria123",
        "celular": "987333444",
        "carreraObjetivo": "Cátedra de Historia y Razonamiento Verbal",
        "areaAcademica": "Humanidades y Ciencias Sociales",
        "rol": "docente",
        "ciclovirtual": "Semestral San Marcos 2026-II",
        "salario": 3200.00,
        "especialidad": "Historia del Perú, Universal y Comprensión Lectora",
        "cursosAsignados": ["CRS-007", "CRS-008"],
        "cursosInscritos": [],
    },
    {
        "id": "USR-108",
        "nombres": "Esteban",
        "apellidos": "Torres Mendoza",
        "dni": "74839201",
        "fechaNacimiento": "2007-02-18",
        "correo": "esteban@gmail.com",
        "password": "Esteban123",
        "celular": "955112233",
        "carreraObjetivo": "Medicina Humana",
        "areaAcademica": "Ciencias Médicas y de la Salud",
        "rol": "estudiante",
        "ciclovirtual": "Semestral San Marcos 2026-II",
        "salario": 0.00,
        "especialidad": "Postulante Medicina UNMSM",
        "cursosAsignados": [],
        "cursosInscritos": ["CRS-001", "CRS-003", "CRS-006", "CRS-007", "CRS-V01", "CRS-V02"],
    },
    {
        "id": "USR-006",
        "nombres": "Valeria Sofía",
        "apellidos": "Quispe Alarcón",
        "dni": "76543210",
        "fechaNacimiento": "2007-10-14",
        "correo": "v.quispe@academia.edu.pe",
        "password": "Valeria123",
        "celular": "955223344",
        "carreraObjetivo": "Ingeniería de Sistemas",
        "areaAcademica": "Ciencias Exactas e Ingenierías",
        "rol": "estudiante",
        "ciclovirtual": "Anual UNI 2026",
        "salario": 0.00,
        "especialidad": "Postulante Ingeniería UNI",
        "cursosAsignados": [],
        "cursosInscritos": ["CRS-001", "CRS-002", "CRS-004", "CRS-005"],
    },
    {
        "id": "USR-007",
        "nombres": "Ana Paula",
        "apellidos": "Vargas Chavez",
        "dni": "73214569",
        "fechaNacimiento": "2007-05-30",
        "correo": "anapaula@gmail.com",
        "password": "AnaPaula123",
        "celular": "955334455",
        "carreraObjetivo": "Derecho y Ciencias Políticas",
        "areaAcademica": "Humanidades y Ciencias Sociales",
        "rol": "estudiante",
        "ciclovirtual": "Semestral San Marcos 2026-II",
        "salario": 0.00,
        "especialidad": "Postulante Derecho UNMSM",
        "cursosAsignados": [],
        "cursosInscritos": ["CRS-001", "CRS-007", "CRS-008"],
    },
]


def ensure_buckets():
    if not SUPABASE_URL or not SERVICE_KEY:
        print("⚠️  Omitiendo creación de buckets Storage (falta SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY).")
        return
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    for b in BUCKETS:
        resp = httpx.post(
            f"{SUPABASE_URL}/storage/v1/bucket",
            headers=headers,
            json=b,
            timeout=15.0,
        )
        if resp.is_success or resp.status_code == 409 or "already exists" in resp.text.lower():
            print(f"✅ Bucket Storage listo: {b['id']}")
        else:
            print(f"⚠️  Advertencia creando bucket {b['id']}: {resp.status_code} {resp.text}")


def ensure_auth_users() -> dict:
    """Retorna mapa {correo: auth_user_id_uuid} creando o actualizando las cuentas en Supabase Auth."""
    auth_map = {}
    if not SUPABASE_URL or not SERVICE_KEY:
        print("⚠️  Sin SUPABASE_SERVICE_ROLE_KEY: se insertarán perfiles con auth_user_id NULL (se vincularán al primer login si existen en Auth).")
        return auth_map

    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    existing_by_email = {}
    list_resp = httpx.get(f"{SUPABASE_URL}/auth/v1/admin/users?per_page=500", headers=headers, timeout=15.0)
    if list_resp.is_success:
        for u in list_resp.json().get("users", []):
            if u.get("email") and u.get("id"):
                existing_by_email[u["email"].strip().lower()] = u["id"]

    for u in SEED_USERS:
        email = u["correo"].lower()
        full_name = f"{u['nombres']} {u['apellidos']}".strip()
        if email in existing_by_email:
            uid = existing_by_email[email]
            httpx.put(
                f"{SUPABASE_URL}/auth/v1/admin/users/{uid}",
                headers=headers,
                json={
                    "password": u["password"],
                    "email_confirm": True,
                    "user_metadata": {"full_name": full_name},
                    "app_metadata": {"role": u["rol"]},
                },
                timeout=15.0,
            )
            auth_map[email] = uid
            print(f"✅ Cuenta Auth sincronizada: {email} ({u['rol']})")
        else:
            resp = httpx.post(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers=headers,
                json={
                    "email": email,
                    "password": u["password"],
                    "email_confirm": True,
                    "user_metadata": {"full_name": full_name},
                    "app_metadata": {"role": u["rol"]},
                },
                timeout=15.0,
            )
            if resp.is_success:
                data = resp.json()
                uid = data.get("id") or (data.get("user") or {}).get("id")
                auth_map[email] = uid
                print(f"✅ Cuenta Auth creada: {email} ({u['rol']})")
            else:
                print(f"❌ Error creando Auth para {email}: {resp.status_code} {resp.text}")
    return auth_map


def seed_database(auth_map: dict):
    if not DATABASE_URL or "insforge.app" in DATABASE_URL:
        print("❌ Configura DATABASE_URL apuntando a tu proyecto Supabase en back/.env antes de ejecutar el seed.")
        sys.exit(1)

    schema_sql = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            print("📦 Aplicando schema.sql v2 en Supabase PostgreSQL...")
            cur.execute(schema_sql)

            # 1. Áreas Académicas
            areas = [
                ("AREA-EXA", "CIENCIAS-EXACTAS", "Ciencias Exactas e Ingenierías", "Matemáticas, Física, Química e Ingenierías (UNI / UNMSM Área C y E)", "#2563EB"),
                ("AREA-MED", "CIENCIAS-MEDICAS", "Ciencias Médicas y de la Salud", "Medicina Humana, Enfermería, Odontología y Biología (UNMSM Área A)", "#DC2626"),
                ("AREA-GES", "GESTION", "Gestión Institucional y Empresarial", "Economía, Administración y Contabilidad (UNMSM Área D)", "#059669"),
                ("AREA-HUM", "HUMANIDADES", "Humanidades y Ciencias Sociales", "Derecho, Psicología, Literatura e Historia (UNMSM Área E)", "#7C3AED"),
            ]
            for a in areas:
                cur.execute(
                    """
                    INSERT INTO areas_academicas (id, codigo, nombre, descripcion, color, estado)
                    VALUES (%s, %s, %s, %s, %s, 'Activo')
                    ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, descripcion = EXCLUDED.descripcion, color = EXCLUDED.color;
                    """,
                    a,
                )

            # 2. Carreras Objetivo representativas
            carreras = [
                ("CAR-001", "ING-SIS", "Ingeniería de Sistemas", "AREA-EXA", '["UNI", "UNMSM", "PUCP"]'),
                ("CAR-002", "ING-CIV", "Ingeniería Civil", "AREA-EXA", '["UNI", "PUCP", "UNMSM"]'),
                ("CAR-006", "MED-HUM", "Medicina Humana", "AREA-MED", '["UNMSM", "UPCH"]'),
                ("CAR-007", "ENF-SAL", "Enfermería", "AREA-MED", '["UNMSM", "UPCH"]'),
                ("CAR-011", "ECO-GES", "Economía", "AREA-GES", '["UNMSM", "UP", "PUCP"]'),
                ("CAR-014", "DER-POL", "Derecho y Ciencias Políticas", "AREA-HUM", '["UNMSM", "PUCP"]'),
            ]
            for c in carreras:
                cur.execute(
                    """
                    INSERT INTO carreras (id, codigo, nombre, area_id, universidades_objetivo, estado)
                    VALUES (%s, %s, %s, %s, %s::jsonb, 'Activo')
                    ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, area_id = EXCLUDED.area_id;
                    """,
                    c,
                )

            # 3. Tipos de Ciclo, Turnos y 3 Ciclos Académicos (2 activos + 1 finalizado para Modo Historial)
            tipos = [
                ("TCIC-SEM", "SEMESTRAL", "Semestral", 22, "Virtual", "Preparación intensiva tipo DECO / Admisión semestral."),
                ("TCIC-ANU", "ANUAL", "Anual", 42, "Virtual", "Formación integral desde base teórica hasta nivel examen de admisión."),
                ("TCIC-INT", "INTENSIVO", "Verano / Repaso", 10, "Virtual", "Ciclo intensivo full práctica y banco de preguntas."),
            ]
            for t in tipos:
                cur.execute(
                    """
                    INSERT INTO tipos_ciclo (id, codigo, nombre, duracion_semanas, modalidad, descripcion, estado)
                    VALUES (%s, %s, %s, %s, %s, %s, 'Activo')
                    ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, duracion_semanas = EXCLUDED.duracion_semanas;
                    """,
                    t,
                )

            turnos = [
                ("TUR-MAN", "MANANA", "Mañana", "08:00", "13:00"),
                ("TUR-TAR", "TARDE", "Tarde", "14:00", "19:00"),
                ("TUR-NOC", "NOCHE", "Noche", "18:30", "22:00"),
            ]
            for tr in turnos:
                cur.execute(
                    """
                    INSERT INTO turnos (id, codigo, nombre, hora_inicio, hora_fin, estado)
                    VALUES (%s, %s, %s, %s, %s, 'Activo')
                    ON CONFLICT (id) DO UPDATE SET hora_inicio = EXCLUDED.hora_inicio, hora_fin = EXCLUDED.hora_fin;
                    """,
                    tr,
                )

            ciclos = [
                ("CIC-2026-SEM-2", "2026-SEM-II", "Semestral San Marcos 2026-II", "TCIC-SEM", 2026, "II", "2026-08-10", "2026-12-22", 120.00, 320.00, 1450.00, True, "En Curso"),
                ("CIC-2026-ANU", "2026-ANU", "Anual UNI 2026", "TCIC-ANU", 2026, "Anual", "2026-03-15", "2026-12-20", 150.00, 350.00, 2800.00, True, "En Curso"),
                ("CIC-2026-VER", "2026-VER", "Verano Intensivo 2026", "TCIC-INT", 2026, "Verano", "2026-01-05", "2026-03-10", 80.00, 280.00, 620.00, False, "Finalizado"),
            ]
            for ci in ciclos:
                cur.execute(
                    """
                    INSERT INTO ciclos_academicos (
                        id, codigo, nombre, tipo_ciclo_id, anio, periodo,
                        fecha_inicio, fecha_fin, costo_matricula, costo_pension_mensual,
                        costo_total_contado, es_vigente, estado
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        nombre = EXCLUDED.nombre, es_vigente = EXCLUDED.es_vigente, estado = EXCLUDED.estado;
                    """,
                    ci,
                )

            # 4. Configuración Institucional
            cur.execute(
                """
                INSERT INTO configuracion (
                    id, institucion_nombre, institucion_ruc, institucion_direccion,
                    institucion_telefono, institucion_correo, ciclo_vigente,
                    ciclo_inicio, ciclo_fin, ciclo_estado,
                    tarifa_hora_docente, tolerancia_tardanza_min, horas_descuento_inasistencia
                ) VALUES (
                    'general', 'Academia Preuniversitaria Alto Rendimiento', '20609876543',
                    'Av. Universitaria 1801, Lima', '(01) 480-1234', 'informes@academiapreu.edu.pe',
                    'Semestral San Marcos 2026-II', '2026-08-10', '2026-12-22', 'En Curso',
                    50.00, 10, 2.00
                )
                ON CONFLICT (id) DO UPDATE SET
                    ciclo_vigente = EXCLUDED.ciclo_vigente,
                    tolerancia_tardanza_min = 10;
                """
            )

            # 5. Usuarios vinculados a Supabase Auth
            for u in SEED_USERS:
                auth_id = auth_map.get(u["correo"].lower())
                cur.execute(
                    """
                    INSERT INTO users (
                        id, auth_user_id, apellidos, nombres, "tipoDocumento", dni,
                        "fechaNacimiento", correo, celular, "carreraObjetivo", "areaAcademica",
                        rol, estado, "cursosAsignados", "cursosInscritos", ciclovirtual, salario, especialidad
                    ) VALUES (
                        %s, %s, %s, %s, 'DNI', %s, %s, %s, %s, %s, %s, %s, 'Activo',
                        %s::jsonb, %s::jsonb, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        auth_user_id = COALESCE(EXCLUDED.auth_user_id, users.auth_user_id),
                        apellidos = EXCLUDED.apellidos,
                        nombres = EXCLUDED.nombres,
                        dni = EXCLUDED.dni,
                        correo = EXCLUDED.correo,
                        rol = EXCLUDED.rol,
                        "cursosAsignados" = EXCLUDED."cursosAsignados",
                        "cursosInscritos" = EXCLUDED."cursosInscritos",
                        ciclovirtual = EXCLUDED.ciclovirtual,
                        salario = EXCLUDED.salario,
                        especialidad = EXCLUDED.especialidad;
                    """,
                    (
                        u["id"], auth_id, u["apellidos"], u["nombres"], u["dni"],
                        u["fechaNacimiento"], u["correo"], u["celular"], u["carreraObjetivo"], u["areaAcademica"],
                        u["rol"], json.dumps(u["cursosAsignados"]), json.dumps(u["cursosInscritos"]),
                        u["ciclovirtual"], u["salario"], u["especialidad"],
                    ),
                )

            # 6. Cursos 100% Preuniversitarios sin cruces horarios
            cursos = [
                ("CRS-001", "Álgebra Preuniversitaria", "Ciencias Exactas e Ingenierías", "Ingeniería de Sistemas", "CIC-2026-SEM-2", "USR-107", "Pedro Martlon Chavez Quispe", ["Lunes", "Miércoles"], "08:00", "10:00", "https://meet.google.com/new", "Semestral San Marcos 2026-II", "Activo"),
                ("CRS-002", "Física General", "Ciencias Exactas e Ingenierías", "Ingeniería de Sistemas", "CIC-2026-ANU", "USR-003", "Miguel Ángel Paredes Ruiz", ["Martes", "Jueves"], "08:00", "10:00", "https://meet.google.com/new", "Anual UNI 2026", "Activo"),
                ("CRS-003", "Biología y Anatomía Humana", "Ciencias Médicas y de la Salud", "Medicina Humana", "CIC-2026-SEM-2", "USR-002", "Carmen Rosa Herrera Villanueva", ["Lunes", "Viernes"], "14:00", "16:00", "https://meet.google.com/new", "Semestral San Marcos 2026-II", "Activo"),
                ("CRS-004", "Trigonometría y Geometría Analítica", "Ciencias Exactas e Ingenierías", "Ingeniería de Sistemas", "CIC-2026-ANU", "USR-107", "Pedro Martlon Chavez Quispe", ["Martes", "Jueves"], "10:15", "12:15", "https://meet.google.com/new", "Anual UNI 2026", "Activo"),
                ("CRS-005", "Razonamiento Matemático", "Ciencias Exactas e Ingenierías", "Ingeniería de Sistemas", "CIC-2026-ANU", "USR-003", "Miguel Ángel Paredes Ruiz", ["Miércoles", "Viernes"], "10:15", "12:15", "https://meet.google.com/new", "Anual UNI 2026", "Activo"),
                ("CRS-006", "Química General y Orgánica", "Ciencias Médicas y de la Salud", "Medicina Humana", "CIC-2026-SEM-2", "USR-002", "Carmen Rosa Herrera Villanueva", ["Martes", "Jueves"], "15:00", "17:00", "https://meet.google.com/new", "Semestral San Marcos 2026-II", "Activo"),
                ("CRS-007", "Historia del Perú y del Mundo", "Humanidades y Ciencias Sociales", "Derecho y Ciencias Políticas", "CIC-2026-SEM-2", "USR-109", "Victoria Angelis Raggi Ruiz", ["Miércoles", "Viernes"], "16:15", "18:15", "https://meet.google.com/new", "Semestral San Marcos 2026-II", "Activo"),
                ("CRS-008", "Comprensión Lectora (RV)", "Humanidades y Ciencias Sociales", "Derecho y Ciencias Políticas", "CIC-2026-SEM-2", "USR-109", "Victoria Angelis Raggi Ruiz", ["Sábado"], "09:00", "12:00", "https://meet.google.com/new", "Semestral San Marcos 2026-II", "Activo"),
                # Cursos históricos del ciclo finalizado Verano Intensivo 2026
                ("CRS-V01", "Repaso Intensivo de Álgebra DECO", "Ciencias Exactas e Ingenierías", "Medicina Humana", "CIC-2026-VER", "USR-107", "Pedro Martlon Chavez Quispe", ["Lunes", "Miércoles"], "08:00", "10:30", "https://meet.google.com/new", "Verano Intensivo 2026", "Finalizado"),
                ("CRS-V02", "Repaso Intensivo de Biología UNMSM", "Ciencias Médicas y de la Salud", "Medicina Humana", "CIC-2026-VER", "USR-002", "Carmen Rosa Herrera Villanueva", ["Martes", "Jueves"], "08:00", "10:30", "https://meet.google.com/new", "Verano Intensivo 2026", "Finalizado"),
            ]
            for c in cursos:
                cur.execute(
                    """
                    INSERT INTO courses (
                        id, asignatura, area, carrera, ciclo_id, "docenteId", docente,
                        dias, "horaInicio", "horaFin", "meetUrl", repositorio, ciclovirtual, estado
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, 'https://drive.google.com', %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        asignatura = EXCLUDED.asignatura, ciclo_id = EXCLUDED.ciclo_id,
                        "docenteId" = EXCLUDED."docenteId", docente = EXCLUDED.docente,
                        dias = EXCLUDED.dias, "horaInicio" = EXCLUDED."horaInicio",
                        "horaFin" = EXCLUDED."horaFin", ciclovirtual = EXCLUDED.ciclovirtual,
                        estado = EXCLUDED.estado;
                    """,
                    (c[0], c[1], c[2], c[3], c[4], c[5], c[6], json.dumps(c[7]), c[8], c[9], c[10], c[11], c[12]),
                )

            # 7. Matrículas (incluye matrícula activa + matrícula histórica para Esteban)
            matriculas = [
                ("MAT-2026-101", "USR-108", "CIC-2026-SEM-2", "Esteban Torres Mendoza", "74839201", "esteban@gmail.com", "955112233", "Medicina Humana (UNMSM)", "Semestral San Marcos 2026-II", "Tarde", "2026-08-05", "Cuotas", 1400.00, 760.00, 640.00, ["CRS-001", "CRS-003", "CRS-006", "CRS-007"], "Al día"),
                ("MAT-2026-015", "USR-108", "CIC-2026-VER", "Esteban Torres Mendoza", "74839201", "esteban@gmail.com", "955112233", "Medicina Humana (UNMSM)", "Verano Intensivo 2026", "Mañana", "2026-01-03", "Al Contado", 620.00, 620.00, 0.00, ["CRS-V01", "CRS-V02"], "Pagado Total"),
                ("MAT-2026-102", "USR-006", "CIC-2026-ANU", "Valeria Sofía Quispe Alarcón", "76543210", "v.quispe@academia.edu.pe", "955223344", "Ingeniería de Sistemas (UNI)", "Anual UNI 2026", "Mañana", "2026-03-10", "Al Contado", 2800.00, 2800.00, 0.00, ["CRS-001", "CRS-002", "CRS-004", "CRS-005"], "Pagado Total"),
                ("MAT-2026-103", "USR-007", "CIC-2026-SEM-2", "Ana Paula Vargas Chavez", "73214569", "anapaula@gmail.com", "955334455", "Derecho y Ciencias Políticas (UNMSM)", "Semestral San Marcos 2026-II", "Tarde", "2026-08-08", "Cuotas", 1400.00, 440.00, 960.00, ["CRS-001", "CRS-007", "CRS-008"], "Cuota Vencida"),
            ]
            for m in matriculas:
                cur.execute(
                    """
                    INSERT INTO matriculas (
                        id, estudiante_id, ciclo_id, estudiante, dni, correo, celular,
                        programa, ciclo, turno, "fechaMatricula", "modalidadPago",
                        "montoTotal", "montoPagado", "saldoPendiente", "cursosInscritos", estado
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        estudiante_id = EXCLUDED.estudiante_id, ciclo_id = EXCLUDED.ciclo_id,
                        ciclo = EXCLUDED.ciclo, "montoPagado" = EXCLUDED."montoPagado",
                        "saldoPendiente" = EXCLUDED."saldoPendiente",
                        "cursosInscritos" = EXCLUDED."cursosInscritos", estado = EXCLUDED.estado;
                    """,
                    (*m[:15], json.dumps(m[15]), m[16]),
                )

            # 8. Pagos verificados e historial realista
            pagos = [
                ("PAG-101", "MAT-2026-101", "USR-108", "Esteban Torres Mendoza", "Matrícula + Pensión Cuota 1", 440.00, "Yape / Plin", "YP-8841203", "B001-00501", "2026-08-05 10:15", "Aprobado"),
                ("PAG-102", "MAT-2026-101", "USR-108", "Esteban Torres Mendoza", "Pensión Cuota 2", 320.00, "Transferencia BCP", "BCP-9910442", "B001-00548", "2026-09-06 16:40", "Aprobado"),
                ("PAG-015", "MAT-2026-015", "USR-108", "Esteban Torres Mendoza", "Ciclo Verano Intensivo Completo", 620.00, "Yape / Plin", "YP-1102934", "B001-00112", "2026-01-03 09:20", "Aprobado"),
                ("PAG-103", "MAT-2026-102", "USR-006", "Valeria Sofía Quispe Alarcón", "Pago Contado Anual UNI", 2800.00, "Transferencia BBVA", "BBVA-773190", "B001-00280", "2026-03-10 11:30", "Aprobado"),
                ("PAG-104", "MAT-2026-103", "USR-007", "Ana Paula Vargas Chavez", "Matrícula + Pensión Cuota 1", 440.00, "Yape / Plin", "YP-6623109", "B001-00510", "2026-08-08 14:10", "Aprobado"),
            ]
            for p in pagos:
                cur.execute(
                    """
                    INSERT INTO pagos (
                        id, "matriculaId", estudiante_id, estudiante, concepto,
                        monto, metodo, operacion, comprobante, fecha, estado
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET monto = EXCLUDED.monto, estado = EXCLUDED.estado;
                    """,
                    p,
                )

            # 9. Tareas Académicas Reales Pre-U
            tareas = [
                ("TSK-101", "CRS-001", "CIC-2026-SEM-2", "Álgebra Preuniversitaria", "Práctica Dirigida", "Polinomios Especiales y Productos Notables DECO", "Resolver los 15 problemas propuestos de la separata semana 6 adjuntando desarrollo paso a paso en PDF.", "2026-12-03", "20 pts", "USR-107", "Pedro Martlon Chavez Quispe"),
                ("TSK-102", "CRS-003", "CIC-2026-SEM-2", "Biología y Anatomía Humana", "Tarea Calificada", "Fisiología Cardiovascular y Ciclo Cardíaco", "Elaborar el esquema funcional de circulación mayor/menor y responder el banco de 20 preguntas UNMSM.", "2026-12-04", "20 pts", "USR-002", "Carmen Rosa Herrera Villanueva"),
                ("TSK-103", "CRS-006", "CIC-2026-SEM-2", "Química General y Orgánica", "Taller Calificado", "Balance de Reacciones Redox y Estequiometría", "Subir la resolución de los ejercicios pares por el método ion-electrón.", "2026-12-05", "20 pts", "USR-002", "Carmen Rosa Herrera Villanueva"),
                ("TSK-104", "CRS-004", "CIC-2026-ANU", "Trigonometría y Geometría Analítica", "Práctica UNI", "Identidades de Arco Múltiple y Transformaciones", "Desarrollar los 12 problemas nivel CEPRE-UNI de la guía semanal.", "2026-12-04", "20 pts", "USR-107", "Pedro Martlon Chavez Quispe"),
                ("TSK-V01", "CRS-V01", "CIC-2026-VER", "Repaso Intensivo de Álgebra DECO", "Tarea Histórica", "Ecuaciones Exponenciales y Logarítmicas", "Práctica final del ciclo Verano 2026.", "2026-03-01", "20 pts", "USR-107", "Pedro Martlon Chavez Quispe"),
            ]
            for t in tareas:
                cur.execute(
                    """
                    INSERT INTO tareas (
                        id, curso_id, ciclo_id, curso, tipo, titulo, descripcion,
                        fecha_limite, puntos, docente_id, docente, estado
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Por entregar')
                    ON CONFLICT (id) DO UPDATE SET titulo = EXCLUDED.titulo, ciclo_id = EXCLUDED.ciclo_id;
                    """,
                    t,
                )

            # Entrega histórica calificada de Esteban en Verano 2026
            cur.execute(
                """
                INSERT INTO tarea_entregas (
                    id, tarea_id, curso_id, estudiante_id, estudiante_nombre, estudiante_correo,
                    archivo_nombre, comentario_alumno, estado, nota, comentario_docente
                ) VALUES (
                    'ENT-V01', 'TSK-V01', 'CRS-V01', 'USR-108', 'Esteban Torres Mendoza', 'esteban@gmail.com',
                    'solucionario_verano_esteban.pdf', 'Adjunto desarrollo completo.', 'Calificado', 18.50,
                    'Excelente manejo de propiedades de logaritmos. Revisa solo el signo en la pregunta 9.'
                )
                ON CONFLICT (tarea_id, estudiante_id) DO NOTHING;
                """
            )

            # 10. Anuncios Académicos
            anuncios = [
                ("ANN-101", "CRS-001", "CIC-2026-SEM-2", "Álgebra Preuniversitaria", "Formulario Oficial de División Algebraica y Cocientes Notables", "Ya está disponible la separata teórica con ejercicios resueltos tipo admisión San Marcos.", "Importante", "USR-107", "Pedro Martlon Chavez Quispe"),
                ("ANN-102", "CRS-003", "CIC-2026-SEM-2", "Biología y Anatomía Humana", "Banco de Preguntas UNMSM: Sistema Circulatorio", "Alumnos del área de Ciencias de la Salud: revisen las 40 preguntas DECO antes del seminario del viernes.", "Alta", "USR-002", "Carmen Rosa Herrera Villanueva"),
                ("ANN-103", "CRS-006", "CIC-2026-SEM-2", "Química General y Orgánica", "Tabla Periódica Interactiva y Masas Atómicas Oficiales", "Recuerden tener a la mano los valores de masas molares que vienen en el prospecto para la práctica del jueves.", "Normal", "USR-002", "Carmen Rosa Herrera Villanueva"),
            ]
            for an in anuncios:
                cur.execute(
                    """
                    INSERT INTO anuncios (id, curso_id, ciclo_id, curso, titulo, contenido, prioridad, docente_id, docente)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET titulo = EXCLUDED.titulo, contenido = EXCLUDED.contenido;
                    """,
                    an,
                )

            # 11. Mensajes Reales de Tutoría
            mensajes = [
                ("MSG-101", "USR-108", "Esteban Torres Mendoza", "esteban@gmail.com", "Medicina Humana", "USR-107", "Pedro Martlon Chavez Quispe", "CRS-001", "Álgebra Preuniversitaria", "Profesor Chávez, buenas tardes. En el ejercicio 8 de polinomios homogéneos, ¿el grado absoluto se iguala en todos los términos antes de hallar P(1)?", "Hoy, 09:30 AM", "Pendiente", None, None),
                ("MSG-102", "USR-108", "Esteban Torres Mendoza", "esteban@gmail.com", "Medicina Humana", "USR-002", "Carmen Rosa Herrera Villanueva", "CRS-003", "Biología y Anatomía Humana", "Doctora Herrera, ¿para el prospecto UNMSM se consideran 36 o 38 ATPs netos en la respiración celular aeróbica?", "Ayer, 05:15 PM", "Respondido", "Hola Esteban. UNMSM considera 36 ATP con lanzadera glicerol-3-fosfato y 38 ATP con malato-aspartato (corazón e hígado).", "Ayer, 06:00 PM"),
            ]
            for msg in mensajes:
                cur.execute(
                    """
                    INSERT INTO mensajes (
                        id, estudiante_id, estudiante_nombre, estudiante_correo, estudiante_carrera,
                        docente_id, docente_nombre, curso_id, curso, mensaje, fecha,
                        estado, respuesta, fecha_respuesta, destinatario_tipo, destinatario_id, destinatario_nombre
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'docente', %s, %s)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (*msg, msg[5], msg[6]),
                )

            conn.commit()
            print("🎉 Base de datos Supabase poblada exitosamente con el entorno 100% Academia Preuniversitaria.")
    except Exception as exc:
        conn.rollback()
        print(f"❌ Error ejecutando seed en PostgreSQL: {exc}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    print("🚀 Iniciando aprovisionamiento de Supabase (Academia Preuniversitaria Virtual)...")
    ensure_buckets()
    users_auth = ensure_auth_users()
    seed_database(users_auth)
