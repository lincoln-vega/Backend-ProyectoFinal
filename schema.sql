-- ==============================================================================
-- SCHEMA V2.0 — SUPABASE POSTGRESQL (ACADEMIA PREUNIVERSITARIA VIRTUAL)
-- Proyecto Supabase: hgasvotbwatoznnizmzr
-- ==============================================================================

-- 1. ÁREAS ACADÉMICAS (Bloques de postulación: Ciencias/Ingenierías, Salud, Gestión, Letras)
CREATE TABLE IF NOT EXISTS areas_academicas (
    id TEXT PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    descripcion TEXT,
    color TEXT DEFAULT '#2563EB',
    estado TEXT NOT NULL DEFAULT 'Activo' CHECK (estado IN ('Activo', 'Inactivo')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. CARRERAS OBJETIVO (Programas universitarios a los que postulan los alumnos)
CREATE TABLE IF NOT EXISTS carreras (
    id TEXT PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    area_id TEXT NOT NULL REFERENCES areas_academicas(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    descripcion TEXT,
    duracion_anios INTEGER DEFAULT 5 CHECK (duracion_anios > 0),
    grado_academico TEXT DEFAULT 'Bachiller / Título Profesional',
    universidades_objetivo JSONB DEFAULT '[]'::jsonb,
    estado TEXT NOT NULL DEFAULT 'Activo' CHECK (estado IN ('Activo', 'Inactivo')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. TIPOS DE CICLO (Anual, Semestral, Intensivo/Verano, Repaso)
CREATE TABLE IF NOT EXISTS tipos_ciclo (
    id TEXT PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    duracion_semanas INTEGER NOT NULL CHECK (duracion_semanas > 0),
    modalidad TEXT NOT NULL DEFAULT 'Virtual' CHECK (modalidad IN ('Virtual', 'Presencial', 'Híbrido')),
    descripcion TEXT,
    estado TEXT NOT NULL DEFAULT 'Activo' CHECK (estado IN ('Activo', 'Inactivo')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. CICLOS ACADÉMICOS (Convocatorias concretas activas en paralelo o finalizadas)
CREATE TABLE IF NOT EXISTS ciclos_academicos (
    id TEXT PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    tipo_ciclo_id TEXT NOT NULL REFERENCES tipos_ciclo(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    anio INTEGER NOT NULL CHECK (anio >= 2020),
    periodo TEXT NOT NULL DEFAULT 'I',
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NOT NULL,
    costo_matricula NUMERIC(10, 2) DEFAULT 0.00 CHECK (costo_matricula >= 0),
    costo_pension_mensual NUMERIC(10, 2) DEFAULT 0.00 CHECK (costo_pension_mensual >= 0),
    costo_total_contado NUMERIC(10, 2) DEFAULT 0.00 CHECK (costo_total_contado >= 0),
    es_vigente BOOLEAN DEFAULT false,
    estado TEXT NOT NULL DEFAULT 'En Curso' CHECK (estado IN ('Planificado', 'En Convocatoria', 'En Curso', 'Finalizado', 'Cerrado')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT check_ciclo_fechas CHECK (fecha_fin >= fecha_inicio)
);

-- 5. TURNOS DE ESTUDIO
CREATE TABLE IF NOT EXISTS turnos (
    id TEXT PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    hora_inicio TEXT NOT NULL,
    hora_fin TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'Activo' CHECK (estado IN ('Activo', 'Inactivo')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. OFERTA DE VACANTES POR CICLO Y CARRERA
CREATE TABLE IF NOT EXISTS ciclo_carreras (
    id TEXT PRIMARY KEY,
    ciclo_id TEXT NOT NULL REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE CASCADE,
    carrera_id TEXT NOT NULL REFERENCES carreras(id) ON UPDATE CASCADE ON DELETE CASCADE,
    vacantes_totales INTEGER DEFAULT 40 CHECK (vacantes_totales >= 0),
    vacantes_ocupadas INTEGER DEFAULT 0 CHECK (vacantes_ocupadas >= 0),
    estado TEXT NOT NULL DEFAULT 'Disponible' CHECK (estado IN ('Disponible', 'Agotado', 'Cerrado')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_ciclo_carrera UNIQUE (ciclo_id, carrera_id),
    CONSTRAINT check_vacantes CHECK (vacantes_ocupadas <= vacantes_totales)
);

-- 7. USUARIOS DEL SISTEMA (Vinculados 1:1 con auth.users de Supabase)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    auth_user_id UUID UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL,
    apellidos TEXT NOT NULL,
    nombres TEXT NOT NULL,
    "tipoDocumento" TEXT DEFAULT 'DNI',
    dni TEXT,
    "fechaNacimiento" TEXT,
    correo TEXT UNIQUE NOT NULL,
    celular TEXT,
    "carreraObjetivo" TEXT,
    "areaAcademica" TEXT,
    rol TEXT NOT NULL DEFAULT 'estudiante',
    estado TEXT NOT NULL DEFAULT 'Activo',
    "cursosAsignados" JSONB DEFAULT '[]'::jsonb,
    "cursosInscritos" JSONB DEFAULT '[]'::jsonb,
    ciclovirtual TEXT DEFAULT 'Semestral',
    salario NUMERIC DEFAULT 0,
    especialidad TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT check_users_dni CHECK (
        dni IS NULL OR dni = '' OR (
            (COALESCE("tipoDocumento", 'DNI') = 'DNI' AND dni ~ '^[0-9]{8}$') OR
            (COALESCE("tipoDocumento", 'DNI') = 'CE' AND dni ~ '^[A-Za-z0-9]{9,12}$') OR
            (COALESCE("tipoDocumento", 'DNI') = 'Pasaporte' AND dni ~ '^[A-Za-z0-9]{6,12}$')
        )
    ),
    CONSTRAINT check_users_celular CHECK (
        celular IS NULL OR celular = '' OR
        celular ~ '^9[0-9]{8}$' OR
        celular ~ '^\+[0-9]{8,15}$'
    )
);

-- 8. CURSOS PREUNIVERSITARIOS Y HORARIOS
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    asignatura TEXT NOT NULL,
    area TEXT,
    carrera TEXT,
    ciclo_id TEXT REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE SET NULL,
    "docenteId" TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente TEXT,
    dias JSONB DEFAULT '[]'::jsonb,
    "horaInicio" TEXT NOT NULL,
    "horaFin" TEXT NOT NULL,
    "meetUrl" TEXT,
    repositorio TEXT,
    estado TEXT DEFAULT 'Activo',
    ciclovirtual TEXT DEFAULT 'Semestral',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 9. MATRÍCULAS POR CICLO ACADÉMICO
CREATE TABLE IF NOT EXISTS matriculas (
    id TEXT PRIMARY KEY,
    estudiante_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    ciclo_id TEXT REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE SET NULL,
    "tipoDocumento" TEXT DEFAULT 'DNI',
    estudiante TEXT NOT NULL,
    dni TEXT,
    correo TEXT,
    celular TEXT,
    programa TEXT,
    ciclo TEXT,
    turno TEXT,
    "fechaMatricula" TEXT,
    "modalidadPago" TEXT,
    "montoTotal" NUMERIC DEFAULT 0,
    "montoPagado" NUMERIC DEFAULT 0,
    "saldoPendiente" NUMERIC DEFAULT 0,
    "cursosInscritos" JSONB DEFAULT '[]'::jsonb,
    estado TEXT DEFAULT 'Al día',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT check_matriculas_dni CHECK (
        dni IS NULL OR dni = '' OR (
            (COALESCE("tipoDocumento", 'DNI') = 'DNI' AND dni ~ '^[0-9]{8}$') OR
            (COALESCE("tipoDocumento", 'DNI') = 'CE' AND dni ~ '^[A-Za-z0-9]{9,12}$') OR
            (COALESCE("tipoDocumento", 'DNI') = 'Pasaporte' AND dni ~ '^[A-Za-z0-9]{6,12}$')
        )
    ),
    CONSTRAINT check_matriculas_celular CHECK (
        celular IS NULL OR celular = '' OR
        celular ~ '^9[0-9]{8}$' OR
        celular ~ '^\+[0-9]{8,15}$'
    )
);

-- 10. PAGOS Y VOUCHERS (Con soporte de validación por Admin)
CREATE TABLE IF NOT EXISTS pagos (
    id TEXT PRIMARY KEY,
    "matriculaId" TEXT REFERENCES matriculas(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    estudiante TEXT,
    concepto TEXT,
    monto NUMERIC DEFAULT 0,
    metodo TEXT,
    operacion TEXT,
    comprobante TEXT,
    fecha TEXT,
    voucher_url TEXT,
    voucher_path TEXT,
    motivo_rechazo TEXT,
    validado_por TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    validado_en TIMESTAMPTZ,
    estado TEXT DEFAULT 'Aprobado', -- 'Pendiente', 'Aprobado', 'Rechazado'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 11. INCIDENCIAS DOCENTES, PERMUTAS Y JUSTIFICANTES
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    docente TEXT NOT NULL,
    "tipoDocumento" TEXT DEFAULT 'DNI',
    dni TEXT,
    correo TEXT,
    tipo TEXT NOT NULL,
    "fechaPresentacion" TEXT,
    materia TEXT,
    horario TEXT,
    motivo TEXT,
    "archivoNombre" TEXT,
    archivo_url TEXT,
    archivo_path TEXT,
    estado TEXT DEFAULT 'Pendiente',
    docenteid TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    cursoid TEXT REFERENCES courses(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docentepropuesto TEXT,
    docentereemplazo TEXT,
    horarioactualizado TEXT,
    observaciones TEXT,
    minutos_tardanza INTEGER DEFAULT 0,
    sesion_clase_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT check_incidents_dni CHECK (
        dni IS NULL OR dni = '' OR (
            (COALESCE("tipoDocumento", 'DNI') = 'DNI' AND dni ~ '^[0-9]{8}$') OR
            (COALESCE("tipoDocumento", 'DNI') = 'CE' AND dni ~ '^[A-Za-z0-9]{9,12}$') OR
            (COALESCE("tipoDocumento", 'DNI') = 'Pasaporte' AND dni ~ '^[A-Za-z0-9]{6,12}$')
        )
    )
);

-- 12. CONFIGURACIÓN INSTITUCIONAL GLOBAL
CREATE TABLE IF NOT EXISTS configuracion (
    id TEXT PRIMARY KEY DEFAULT 'general',
    institucion_nombre TEXT NOT NULL,
    institucion_ruc TEXT,
    institucion_direccion TEXT,
    institucion_telefono TEXT,
    institucion_correo TEXT,
    ciclo_vigente TEXT DEFAULT 'Semestral San Marcos 2026-II',
    ciclo_inicio TEXT,
    ciclo_fin TEXT,
    ciclo_estado TEXT DEFAULT 'En Curso',
    tarifa_hora_docente NUMERIC DEFAULT 50.00,
    tolerancia_tardanza_min INTEGER DEFAULT 10,
    horas_descuento_inasistencia NUMERIC DEFAULT 2.00,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 13. SESIONES DE ASISTENCIA LEGACY & AUDITORÍA MEET
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id TEXT PRIMARY KEY,
    "cursoId" TEXT REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    asignatura TEXT NOT NULL,
    "docenteId" TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente TEXT,
    fecha TEXT NOT NULL,
    "horaApertura" TEXT NOT NULL,
    modalidad TEXT DEFAULT 'Virtual en vivo',
    "asistentesPuntuales" JSONB DEFAULT '[]'::jsonb,
    tardanzas JSONB DEFAULT '[]'::jsonb,
    ausentes JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meet_attendance (
    id BIGSERIAL PRIMARY KEY,
    docente_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente_email TEXT,
    curso_id TEXT REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    evento TEXT NOT NULL,
    hora_entrada TIMESTAMPTZ DEFAULT NOW(),
    hora_salida TIMESTAMPTZ,
    estado_sesion TEXT DEFAULT 'EN_PROGRESO',
    minutos_efectivos NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 14. CLASES EN VIVO CON CRONÓMETRO, RECESOS Y ASISTENCIA HÍBRIDA (Fase 3)
CREATE TABLE IF NOT EXISTS sesiones_clase (
    id TEXT PRIMARY KEY,
    curso_id TEXT NOT NULL REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    ciclo_id TEXT REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente_id TEXT NOT NULL REFERENCES users(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    docente_nombre TEXT,
    fecha DATE NOT NULL DEFAULT CURRENT_DATE,
    hora_programada_inicio TEXT NOT NULL,
    hora_programada_fin TEXT NOT NULL,
    hora_real_inicio TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hora_real_fin TIMESTAMPTZ,
    segundos_receso_acumulados INTEGER NOT NULL DEFAULT 0,
    minutos_reales_brutos NUMERIC(8, 2) NOT NULL DEFAULT 0,
    minutos_bloque_programado INTEGER NOT NULL DEFAULT 120,
    minutos_efectivos_dictados NUMERIC(8, 2) DEFAULT 0,
    minutos_tardanza_docente INTEGER NOT NULL DEFAULT 0,
    cierre_automatico BOOLEAN NOT NULL DEFAULT FALSE,
    estado TEXT NOT NULL DEFAULT 'EN_CLASE' CHECK (estado IN ('EN_CLASE', 'EN_RECESO', 'FINALIZADA')),
    meet_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recesos_clase (
    id BIGSERIAL PRIMARY KEY,
    sesion_id TEXT NOT NULL REFERENCES sesiones_clase(id) ON UPDATE CASCADE ON DELETE CASCADE,
    inicio TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fin TIMESTAMPTZ,
    segundos_duracion INTEGER DEFAULT 0,
    motivo TEXT DEFAULT 'Receso académico'
);

CREATE TABLE IF NOT EXISTS asistencia_alumnos (
    id BIGSERIAL PRIMARY KEY,
    sesion_id TEXT NOT NULL REFERENCES sesiones_clase(id) ON UPDATE CASCADE ON DELETE CASCADE,
    curso_id TEXT NOT NULL REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_id TEXT NOT NULL REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_nombre TEXT,
    estado TEXT NOT NULL DEFAULT 'Presente' CHECK (estado IN ('Presente', 'Tardanza', 'Ausente', 'Justificado')),
    origen TEXT NOT NULL DEFAULT 'AUTO_MEET' CHECK (origen IN ('AUTO_MEET', 'PASE_LISTA')),
    hora_registro TIMESTAMPTZ DEFAULT NOW(),
    minutos_permanencia INTEGER NOT NULL DEFAULT 0,
    ultimo_latido TIMESTAMPTZ,
    ventana_abierta BOOLEAN NOT NULL DEFAULT FALSE,
    observacion TEXT,
    CONSTRAINT uq_sesion_estudiante UNIQUE (sesion_id, estudiante_id)
);

-- 15. TAREAS ACADÉMICAS Y ENTREGAS INDIVIDUALES POR ESTUDIANTE (Fase 4)
CREATE TABLE IF NOT EXISTS tareas (
    id TEXT PRIMARY KEY,
    curso_id TEXT NOT NULL REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    ciclo_id TEXT REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE SET NULL,
    curso TEXT NOT NULL,
    tipo TEXT DEFAULT 'Tarea Calificada',
    titulo TEXT NOT NULL,
    descripcion TEXT DEFAULT '',
    fecha_limite TEXT NOT NULL,
    puntos TEXT DEFAULT '20 pts',
    docente_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente TEXT NOT NULL,
    estado TEXT DEFAULT 'Por entregar',
    archivo_url TEXT,
    archivo_path TEXT,
    archivo_nombre TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tarea_entregas (
    id TEXT PRIMARY KEY,
    tarea_id TEXT NOT NULL REFERENCES tareas(id) ON UPDATE CASCADE ON DELETE CASCADE,
    curso_id TEXT NOT NULL REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_id TEXT NOT NULL REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_nombre TEXT NOT NULL,
    estudiante_correo TEXT,
    archivo_path TEXT,
    archivo_url TEXT,
    archivo_nombre TEXT,
    tamano_bytes BIGINT DEFAULT 0,
    comentario_alumno TEXT DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'Entregado' CHECK (estado IN ('Entregado', 'Calificado', 'Observado')),
    nota NUMERIC(4, 2) CHECK (nota IS NULL OR (nota >= 0 AND nota <= 20)),
    comentario_docente TEXT DEFAULT '',
    entregado_en TIMESTAMPTZ DEFAULT NOW(),
    calificado_en TIMESTAMPTZ,
    CONSTRAINT uq_tarea_estudiante UNIQUE (tarea_id, estudiante_id)
);

-- 16. ANUNCIOS ACADÉMICOS Y COMUNICADOS
CREATE TABLE IF NOT EXISTS anuncios (
    id TEXT PRIMARY KEY,
    curso_id TEXT NOT NULL REFERENCES courses(id) ON UPDATE CASCADE ON DELETE CASCADE,
    ciclo_id TEXT REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE SET NULL,
    curso TEXT NOT NULL,
    titulo TEXT NOT NULL,
    contenido TEXT NOT NULL,
    prioridad TEXT DEFAULT 'Normal',
    docente_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente TEXT NOT NULL,
    archivo_url TEXT,
    archivo_path TEXT,
    archivo_nombre TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 17. MENSAJERÍA Y TUTORÍA BIDIRECCIONAL
CREATE TABLE IF NOT EXISTS mensajes (
    id TEXT PRIMARY KEY,
    estudiante_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    estudiante_nombre TEXT NOT NULL,
    estudiante_correo TEXT NOT NULL,
    estudiante_carrera TEXT DEFAULT '',
    docente_id TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    docente_nombre TEXT NOT NULL,
    curso_id TEXT REFERENCES courses(id) ON UPDATE CASCADE ON DELETE SET NULL,
    curso TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    fecha TEXT,
    estado TEXT DEFAULT 'Pendiente',
    respuesta TEXT,
    fecha_respuesta TEXT,
    destinatario_tipo TEXT DEFAULT 'docente',
    destinatario_id TEXT,
    destinatario_nombre TEXT,
    destinatario_correo TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 18. SIMULACROS TIPO EXAMEN DE ADMISIÓN Y RANKING (Fase 6)
CREATE TABLE IF NOT EXISTS simulacros (
    id TEXT PRIMARY KEY,
    titulo TEXT NOT NULL,
    descripcion TEXT DEFAULT '',
    ciclo_id TEXT REFERENCES ciclos_academicos(id) ON UPDATE CASCADE ON DELETE SET NULL,
    area_id TEXT REFERENCES areas_academicas(id) ON UPDATE CASCADE ON DELETE SET NULL,
    duracion_minutos INTEGER NOT NULL DEFAULT 120 CHECK (duracion_minutos > 0),
    pts_correcta NUMERIC(6, 3) NOT NULL DEFAULT 20.000,
    pts_incorrecta NUMERIC(6, 3) NOT NULL DEFAULT -1.125,
    pts_blanco NUMERIC(6, 3) NOT NULL DEFAULT 0.000,
    fecha_inicio TIMESTAMPTZ DEFAULT NOW(),
    fecha_fin TIMESTAMPTZ,
    estado TEXT NOT NULL DEFAULT 'Publicado' CHECK (estado IN ('Borrador', 'Publicado', 'Cerrado')),
    creado_por TEXT REFERENCES users(id) ON UPDATE CASCADE ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS simulacro_preguntas (
    id TEXT PRIMARY KEY,
    simulacro_id TEXT NOT NULL REFERENCES simulacros(id) ON UPDATE CASCADE ON DELETE CASCADE,
    orden INTEGER NOT NULL DEFAULT 1,
    asignatura TEXT NOT NULL,
    enunciado TEXT NOT NULL,
    opciones JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{"clave":"A","texto":"..."}, ...]
    respuesta_correcta TEXT NOT NULL CHECK (respuesta_correcta IN ('A', 'B', 'C', 'D', 'E')),
    explicacion TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS simulacro_intentos (
    id TEXT PRIMARY KEY,
    simulacro_id TEXT NOT NULL REFERENCES simulacros(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_id TEXT NOT NULL REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    estudiante_nombre TEXT NOT NULL,
    carrera_objetivo TEXT DEFAULT '',
    inicio_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entregado_en TIMESTAMPTZ,
    estado TEXT NOT NULL DEFAULT 'EN_PROGRESO' CHECK (estado IN ('EN_PROGRESO', 'ENTREGADO')),
    respuestas JSONB NOT NULL DEFAULT '{}'::jsonb,
    correctas INTEGER DEFAULT 0,
    incorrectas INTEGER DEFAULT 0,
    blanco INTEGER DEFAULT 0,
    puntaje_total NUMERIC(8, 3) DEFAULT 0,
    CONSTRAINT uq_simulacro_estudiante UNIQUE (simulacro_id, estudiante_id)
);

-- ==============================================================================
-- ÍNDICES DE RENDIMIENTO EN CLAVES FORÁNEAS Y FILTROS FRECUENTES
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_users_correo ON users (LOWER(correo));
CREATE INDEX IF NOT EXISTS idx_users_rol ON users (rol);
CREATE INDEX IF NOT EXISTS idx_courses_docente ON courses ("docenteId");
CREATE INDEX IF NOT EXISTS idx_courses_ciclo ON courses (ciclo_id);
CREATE INDEX IF NOT EXISTS idx_matriculas_estudiante ON matriculas (estudiante_id);
CREATE INDEX IF NOT EXISTS idx_matriculas_ciclo ON matriculas (ciclo_id);
CREATE INDEX IF NOT EXISTS idx_matriculas_correo ON matriculas (LOWER(correo));
CREATE INDEX IF NOT EXISTS idx_pagos_matricula ON pagos ("matriculaId");
CREATE INDEX IF NOT EXISTS idx_pagos_estado ON pagos (estado);
CREATE INDEX IF NOT EXISTS idx_incidents_docente ON incidents (docenteid);
CREATE INDEX IF NOT EXISTS idx_sesiones_clase_curso ON sesiones_clase (curso_id, estado);
CREATE INDEX IF NOT EXISTS idx_sesiones_clase_docente ON sesiones_clase (docente_id, estado);
CREATE INDEX IF NOT EXISTS idx_asistencia_alumnos_est ON asistencia_alumnos (estudiante_id);
CREATE INDEX IF NOT EXISTS idx_tareas_curso ON tareas (curso_id);
CREATE INDEX IF NOT EXISTS idx_tareas_ciclo ON tareas (ciclo_id);
CREATE INDEX IF NOT EXISTS idx_tarea_entregas_tarea ON tarea_entregas (tarea_id);
CREATE INDEX IF NOT EXISTS idx_tarea_entregas_est ON tarea_entregas (estudiante_id);
CREATE INDEX IF NOT EXISTS idx_tarea_entregas_curso ON tarea_entregas (curso_id);
CREATE INDEX IF NOT EXISTS idx_anuncios_curso ON anuncios (curso_id);
CREATE INDEX IF NOT EXISTS idx_mensajes_docente ON mensajes (docente_id);
CREATE INDEX IF NOT EXISTS idx_mensajes_estudiante ON mensajes (LOWER(estudiante_correo));
CREATE INDEX IF NOT EXISTS idx_simulacro_intentos_ranking ON simulacro_intentos (simulacro_id, puntaje_total DESC);

-- ==============================================================================
-- SEGURIDAD SUPABASE: HABILITAR ROW LEVEL SECURITY EN TODAS LAS TABLAS PÚBLICAS
-- (El backend FastAPI opera autenticado como rol privilegiado tras validar RBAC;
--  esto bloquea accesos anónimos directos por PostgREST sin autorización).
-- ==============================================================================
ALTER TABLE areas_academicas ENABLE ROW LEVEL SECURITY;
ALTER TABLE carreras ENABLE ROW LEVEL SECURITY;
ALTER TABLE tipos_ciclo ENABLE ROW LEVEL SECURITY;
ALTER TABLE ciclos_academicos ENABLE ROW LEVEL SECURITY;
ALTER TABLE turnos ENABLE ROW LEVEL SECURITY;
ALTER TABLE ciclo_carreras ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE courses ENABLE ROW LEVEL SECURITY;
ALTER TABLE matriculas ENABLE ROW LEVEL SECURITY;
ALTER TABLE pagos ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuracion ENABLE ROW LEVEL SECURITY;
ALTER TABLE attendance_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE meet_attendance ENABLE ROW LEVEL SECURITY;
ALTER TABLE sesiones_clase ENABLE ROW LEVEL SECURITY;
ALTER TABLE recesos_clase ENABLE ROW LEVEL SECURITY;
ALTER TABLE asistencia_alumnos ENABLE ROW LEVEL SECURITY;
ALTER TABLE tareas ENABLE ROW LEVEL SECURITY;
ALTER TABLE tarea_entregas ENABLE ROW LEVEL SECURITY;
ALTER TABLE anuncios ENABLE ROW LEVEL SECURITY;
ALTER TABLE mensajes ENABLE ROW LEVEL SECURITY;
ALTER TABLE simulacros ENABLE ROW LEVEL SECURITY;
ALTER TABLE simulacro_preguntas ENABLE ROW LEVEL SECURITY;
ALTER TABLE simulacro_intentos ENABLE ROW LEVEL SECURITY;
