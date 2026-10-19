-- ==============================================================================
-- SCHEMA DE BASE DE DATOS (POSTGRESQL - INSFORGE BAAS)
-- Proyecto Final: Academia SCRM - Portal Académico y Gestión
-- ==============================================================================

-- 1. TABLA: users (Usuarios del sistema y roles RBAC)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    apellidos TEXT NOT NULL,
    nombres TEXT NOT NULL,
    dni TEXT,
    "fechaNacimiento" TEXT,
    correo TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    celular TEXT,
    universidad TEXT,
    "carreraObjetivo" TEXT,
    "areaAcademica" TEXT,
    rol TEXT NOT NULL DEFAULT 'estudiante', -- 'admin', 'docente', 'estudiante'
    estado TEXT NOT NULL DEFAULT 'Activo', -- 'Activo', 'Inactivo'
    "cursosAsignados" JSONB DEFAULT '[]'::jsonb,
    "cursosInscritos" JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. TABLA: courses (Cursos y programación horaria)
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    asignatura TEXT NOT NULL,
    area TEXT,
    carrera TEXT,
    "docenteId" TEXT REFERENCES users(id),
    docente TEXT,
    dias JSONB DEFAULT '[]'::jsonb, -- e.g. ["Lunes", "Miercoles"]
    "horaInicio" TEXT NOT NULL, -- e.g. "08:00"
    "horaFin" TEXT NOT NULL, -- e.g. "10:00"
    "meetUrl" TEXT,
    repositorio TEXT,
    estado TEXT DEFAULT 'Activo',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. TABLA: attendance_sessions (Sesiones de asistencia con cálculo de tolerancia)
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id TEXT PRIMARY KEY,
    "cursoId" TEXT REFERENCES courses(id),
    asignatura TEXT NOT NULL,
    "docenteId" TEXT REFERENCES users(id),
    docente TEXT,
    fecha TEXT NOT NULL, -- e.g. "2026-10-09"
    "horaApertura" TEXT NOT NULL, -- e.g. "08:00"
    modalidad TEXT DEFAULT 'Virtual',
    "asistentesPuntuales" JSONB DEFAULT '[]'::jsonb,
    tardanzas JSONB DEFAULT '[]'::jsonb,
    ausentes JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. TABLA: incidents (Incidencias y solicitudes de docentes)
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    docente TEXT NOT NULL,
    dni TEXT,
    correo TEXT,
    tipo TEXT NOT NULL, -- 'Inasistencia', 'Cambio Horario', etc.
    "fechaPresentacion" TEXT,
    materia TEXT,
    horario TEXT,
    motivo TEXT,
    "archivoNombre" TEXT,
    estado TEXT DEFAULT 'Pendiente', -- 'Pendiente', 'Aprobado', 'Rechazado'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. TABLA: matriculas (Registro de matrículas y modalidades de pago)
CREATE TABLE IF NOT EXISTS matriculas (
    id TEXT PRIMARY KEY, -- e.g. "MAT-2026-001"
    estudiante TEXT NOT NULL,
    dni TEXT,
    correo TEXT,
    celular TEXT,
    programa TEXT,
    ciclo TEXT,
    turno TEXT,
    "fechaMatricula" TEXT,
    "modalidadPago" TEXT, -- 'Contado', 'Cuotas (3 cuotas)', etc.
    "montoTotal" NUMERIC DEFAULT 0,
    "montoPagado" NUMERIC DEFAULT 0,
    "saldoPendiente" NUMERIC DEFAULT 0,
    estado TEXT DEFAULT 'Al día', -- 'Al día', 'Pagado Total', 'Cuota Vencida', 'Pendiente'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. TABLA: pagos (Recibos y comprobantes de pago de cuotas)
CREATE TABLE IF NOT EXISTS pagos (
    id TEXT PRIMARY KEY, -- e.g. "PAG-001"
    "matriculaId" TEXT REFERENCES matriculas(id),
    estudiante TEXT,
    concepto TEXT,
    monto NUMERIC DEFAULT 0,
    metodo TEXT, -- 'Yape / Plin', 'Transferencia BCP', 'Tarjeta', etc.
    operacion TEXT,
    comprobante TEXT, -- e.g. "B001-00382"
    fecha TEXT,
    estado TEXT DEFAULT 'Aprobado',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. TABLA: meet_attendance_audit (Auditoría del docente organizador)
CREATE TABLE IF NOT EXISTS meet_attendance_audit (
    id TEXT PRIMARY KEY,
    "cursoId" TEXT NOT NULL REFERENCES courses(id),
    "docenteId" TEXT NOT NULL REFERENCES users(id),
    "meetingSpace" TEXT NOT NULL,
    "participantSession" TEXT NOT NULL,
    "eventType" TEXT NOT NULL,
    "participantEmail" TEXT NOT NULL,
    "receivedAt" TIMESTAMPTZ NOT NULL,
    UNIQUE ("meetingSpace", "participantSession", "eventType")
);
