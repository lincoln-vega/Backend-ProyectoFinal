-- ==============================================================================
-- SCHEMA DE BASE DE DATOS NORMALIZADO (POSTGRESQL - INSFORGE BAAS)
-- Proyecto Final: Academia SCRM - Portal Académico y Gestión
-- ==============================================================================

-- 1. TABLA: users (Usuarios del sistema, roles RBAC y salarios)
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
    ciclovirtual TEXT DEFAULT 'Semestral',
    salario NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. TABLA: courses (Cursos y programación horaria institucional sin cruces)
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    asignatura TEXT NOT NULL,
    area TEXT,
    carrera TEXT,
    "docenteId" TEXT REFERENCES users(id),
    docente TEXT,
    dias JSONB DEFAULT '[]'::jsonb, -- e.g. ["Lunes", "Miércoles"]
    "horaInicio" TEXT NOT NULL, -- e.g. "08:00"
    "horaFin" TEXT NOT NULL, -- e.g. "10:00"
    "meetUrl" TEXT,
    repositorio TEXT,
    estado TEXT DEFAULT 'Activo',
    ciclovirtual TEXT DEFAULT 'Semestral',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. TABLA: attendance_sessions (Sesiones de asistencia con cálculo de puntualidad)
CREATE TABLE IF NOT EXISTS attendance_sessions (
    id TEXT PRIMARY KEY,
    "cursoId" TEXT REFERENCES courses(id),
    asignatura TEXT NOT NULL,
    "docenteId" TEXT REFERENCES users(id),
    docente TEXT,
    fecha TEXT NOT NULL, -- e.g. "2026-10-21"
    "horaApertura" TEXT NOT NULL, -- e.g. "08:00"
    modalidad TEXT DEFAULT 'Virtual en vivo',
    "asistentesPuntuales" JSONB DEFAULT '[]'::jsonb,
    tardanzas JSONB DEFAULT '[]'::jsonb,
    ausentes JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. TABLA: incidents (Incidencias laborales, justificaciones y reemplazos)
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    docente TEXT NOT NULL,
    dni TEXT,
    correo TEXT,
    tipo TEXT NOT NULL, -- 'Inasistencia', 'Tardanza', 'Cambio Horario'
    "fechaPresentacion" TEXT,
    materia TEXT,
    horario TEXT,
    motivo TEXT,
    "archivoNombre" TEXT,
    estado TEXT DEFAULT 'Pendiente', -- 'Pendiente', 'Aprobado', 'Rechazado'
    docenteid TEXT REFERENCES users(id),
    cursoid TEXT REFERENCES courses(id),
    docentepropuesto TEXT,
    docentereemplazo TEXT,
    horarioactualizado TEXT,
    observaciones TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. TABLA: matriculas (Registro oficial de matrículas y saldos de estudiantes)
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
    "modalidadPago" TEXT, -- 'Al Contado', 'Cuotas'
    "montoTotal" NUMERIC DEFAULT 0,
    "montoPagado" NUMERIC DEFAULT 0,
    "saldoPendiente" NUMERIC DEFAULT 0,
    estado TEXT DEFAULT 'Al día', -- 'Al día', 'Pagado Total', 'Cuota Vencida', 'Pendiente'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. TABLA: pagos (Comprobantes y recibos de pago sincronizados)
CREATE TABLE IF NOT EXISTS pagos (
    id TEXT PRIMARY KEY, -- e.g. "PAG-001"
    "matriculaId" TEXT REFERENCES matriculas(id),
    estudiante TEXT,
    concepto TEXT,
    monto NUMERIC DEFAULT 0,
    metodo TEXT, -- 'Yape / Plin', 'Transferencia BCP', 'Tarjeta Débito/Crédito', etc.
    operacion TEXT,
    comprobante TEXT, -- e.g. "B001-00382"
    fecha TEXT,
    estado TEXT DEFAULT 'Aprobado',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. TABLA: configuracion (Parámetros globales de la institución)
CREATE TABLE IF NOT EXISTS configuracion (
    id TEXT PRIMARY KEY DEFAULT 'general',
    institucion_nombre TEXT NOT NULL,
    institucion_ruc TEXT,
    institucion_direccion TEXT,
    institucion_telefono TEXT,
    institucion_correo TEXT,
    ciclo_vigente TEXT DEFAULT 'Semestral 2026-I',
    ciclo_inicio TEXT,
    ciclo_fin TEXT,
    ciclo_estado TEXT DEFAULT 'En Curso',
    tarifa_hora_docente NUMERIC DEFAULT 50.00,
    tolerancia_tardanza_min INTEGER DEFAULT 15,
    horas_descuento_inasistencia NUMERIC DEFAULT 2.00,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. TABLA: meet_attendance (Auditoría de conexión de clases virtuales Google Meet)
CREATE TABLE IF NOT EXISTS meet_attendance (
    id BIGSERIAL PRIMARY KEY,
    docente_id TEXT REFERENCES users(id),
    docente_email TEXT,
    curso_id TEXT REFERENCES courses(id),
    evento TEXT NOT NULL, -- 'ENTRADA', 'SALIDA'
    hora_entrada TIMESTAMPTZ DEFAULT NOW(),
    hora_salida TIMESTAMPTZ,
    estado_sesion TEXT DEFAULT 'EN_PROGRESO',
    minutos_efectivos NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==============================================================================
-- SEMILLA MAESTRA INSTITUCIONAL (DATOS LIMPIOS Y NORMALIZADOS)
-- ==============================================================================

-- A. USUARIOS
INSERT INTO users (
  id, apellidos, nombres, dni, "fechaNacimiento", correo, password, celular,
  universidad, "carreraObjetivo", "areaAcademica", rol, estado,
  "cursosAsignados", "cursosInscritos", ciclovirtual, salario
) VALUES
('USR-001', 'Morales Salazar', 'Roberto Carlos', '41235678', '1985-04-12', 'admin@gmail.com', 'admin123', '987654321', 'UNMSM', 'Dirección Académica', 'Gestión Institucional', 'admin', 'Activo', '[]'::jsonb, '[]'::jsonb, 'Semestral', 4500.00),
('USR-002', 'Herrera Villanueva', 'Carmen Rosa', '45678912', '1988-06-20', 'c.herrera@academia.edu.pe', 'docente123', '987111222', 'UNMSM', 'Cátedra de Álgebra y Aritmética', 'Ciencias Exactas', 'docente', 'Activo', '["CRS-001", "CRS-006"]'::jsonb, '[]'::jsonb, 'Semestral', 3200.00),
('USR-003', 'Paredes Ruiz', 'Miguel Ángel', '48923104', '1990-09-15', 'm.paredes@academia.edu.pe', 'docente123', '987222333', 'UNI', 'Cátedra de Física e Historia', 'Ciencias Exactas', 'docente', 'Activo', '["CRS-002", "CRS-008"]'::jsonb, '[]'::jsonb, 'Semestral', 3000.00),
('USR-004', 'Torres Meléndez', 'Elena Sofía', '43219876', '1987-03-25', 'e.torres@academia.edu.pe', 'docente123', '987333444', 'UPCH', 'Cátedra de Biología y Anatomía', 'Ciencias Médicas', 'docente', 'Activo', '["CRS-003", "CRS-007"]'::jsonb, '[]'::jsonb, 'Semestral', 3500.00),
('USR-008', 'Torres Sánchez', 'Jesús Héctor', '47891234', '1992-11-08', 'j.torres@academia.edu.pe', 'docente123', '987444555', 'UNI', 'Cátedra de Programación (Python)', 'Programación', 'docente', 'Activo', '["CRS-004"]'::jsonb, '[]'::jsonb, 'Semestral', 3200.00),
('USR-009', 'Pérez Ramos', 'Juan Gabriel', '46781290', '1989-07-19', 'j.perez@academia.edu.pe', 'docente123', '987555666', 'UNI', 'Cátedra de Matemática Avanzada', 'Ciencias Exactas', 'docente', 'Activo', '["CRS-005"]'::jsonb, '[]'::jsonb, 'Semestral', 3400.00),
('USR-005', 'Flores Mendoza', 'Juan Carlos', '74839201', '2005-02-18', 'estudiante@gmail.com', 'estudiante123', '955112233', 'UNMSM', 'Medicina Humana', 'Ciencias Médicas', 'estudiante', 'Activo', '[]'::jsonb, '["CRS-001", "CRS-002", "CRS-003"]'::jsonb, 'Semestral', 0.00),
('USR-006', 'Quispe Alarcón', 'Valeria Sofía', '76543210', '2004-10-14', 'v.quispe@academia.edu.pe', 'estudiante123', '955223344', 'UNI', 'Ingeniería de Sistemas', 'Ciencias Exactas', 'estudiante', 'Activo', '[]'::jsonb, '["CRS-001", "CRS-004", "CRS-005"]'::jsonb, 'Semestral', 0.00),
('USR-007', 'Gómez Pérez', 'Lucía Fernanda', '73214569', '2005-05-30', 'l.gomez@academia.edu.pe', 'estudiante123', '955334455', 'PUCP', 'Derecho', 'Humanidades y Letras', 'estudiante', 'Activo', '[]'::jsonb, '["CRS-001", "CRS-007", "CRS-008"]'::jsonb, 'Semestral', 0.00),
('USR-010', 'Flores Mendoza', 'Diego', '75849302', '2004-08-22', 'd.flores@academia.edu.pe', 'estudiante123', '955445566', 'UNI', 'Ingeniería Industrial', 'Ciencias Exactas', 'estudiante', 'Activo', '[]'::jsonb, '["CRS-001", "CRS-002", "CRS-006"]'::jsonb, 'Intensivo', 0.00),
('USR-011', 'Morales Ruiz', 'Elena', '71928374', '2005-01-09', 'e.morales@academia.edu.pe', 'estudiante123', '955556677', 'UNMSM', 'Medicina Humana', 'Ciencias Médicas', 'estudiante', 'Activo', '[]'::jsonb, '["CRS-001", "CRS-003", "CRS-007"]'::jsonb, 'Semestral', 0.00)
ON CONFLICT (id) DO UPDATE SET
  apellidos = EXCLUDED.apellidos, nombres = EXCLUDED.nombres, dni = EXCLUDED.dni, "fechaNacimiento" = EXCLUDED."fechaNacimiento",
  correo = EXCLUDED.correo, password = EXCLUDED.password, celular = EXCLUDED.celular, universidad = EXCLUDED.universidad,
  "carreraObjetivo" = EXCLUDED."carreraObjetivo", "areaAcademica" = EXCLUDED."areaAcademica", rol = EXCLUDED.rol,
  estado = EXCLUDED.estado, "cursosAsignados" = EXCLUDED."cursosAsignados", "cursosInscritos" = EXCLUDED."cursosInscritos",
  ciclovirtual = EXCLUDED.ciclovirtual, salario = EXCLUDED.salario;

-- B. CURSOS SIN CRUCES DE HORARIOS
INSERT INTO courses (
  id, asignatura, area, carrera, "docenteId", docente, dias,
  "horaInicio", "horaFin", "meetUrl", repositorio, estado, ciclovirtual
) VALUES
('CRS-001', 'Álgebra y Funciones', 'Ciencias Exactas', 'Ingeniería de Sistemas', 'USR-002', 'Carmen Rosa Herrera Villanueva', '["Lunes", "Miércoles"]'::jsonb, '08:00', '10:00', 'https://meet.google.com/alg-fun-live', 'drive.google.com/algebra101', 'Activo', 'Semestral'),
('CRS-002', 'Física Fundamental', 'Ciencias Exactas', 'Ingeniería de Sistemas', 'USR-003', 'Miguel Ángel Paredes Ruiz', '["Martes", "Jueves"]'::jsonb, '08:00', '10:00', 'https://meet.google.com/fis-fun-live', 'drive.google.com/fisica101', 'Activo', 'Semestral'),
('CRS-003', 'Biología y Anatomía', 'Ciencias Médicas', 'Medicina Humana', 'USR-004', 'Elena Sofía Torres Meléndez', '["Lunes", "Viernes"]'::jsonb, '15:00', '17:00', 'https://meet.google.com/bio-ana-live', 'drive.google.com/biologia101', 'Activo', 'Semestral'),
('CRS-004', 'Python para Ingenieros', 'Programación', 'Ingeniería de Sistemas', 'USR-008', 'Jesús Héctor Torres Sánchez', '["Lunes", "Miércoles"]'::jsonb, '10:00', '12:00', 'https://meet.google.com/py-ing-live', 'drive.google.com/python101', 'Activo', 'Semestral'),
('CRS-005', 'Matemática II (Cálculo)', 'Ciencias Exactas', 'Ingeniería de Sistemas', 'USR-009', 'Juan Gabriel Pérez Ramos', '["Martes", "Jueves"]'::jsonb, '10:00', '12:00', 'https://meet.google.com/mat-dos-live', 'drive.google.com/mat201', 'Activo', 'Semestral'),
('CRS-006', 'Aritmética Preuniversitaria', 'Ciencias Exactas', 'Ingeniería de Sistemas', 'USR-002', 'Carmen Rosa Herrera Villanueva', '["Viernes"]'::jsonb, '10:00', '13:00', 'https://meet.google.com/ari-pre-live', 'drive.google.com/aritmetica101', 'Activo', 'Semestral'),
('CRS-007', 'Historia del Perú', 'Humanidades y Letras', 'Derecho', 'USR-004', 'Elena Sofía Torres Meléndez', '["Viernes"]'::jsonb, '08:00', '11:00', 'https://meet.google.com/his-per-live', 'drive.google.com/historia-peru', 'Activo', 'Semestral'),
('CRS-008', 'Historia Universal', 'Humanidades y Letras', 'Derecho', 'USR-003', 'Miguel Ángel Paredes Ruiz', '["Sábado"]'::jsonb, '09:00', '12:00', 'https://meet.google.com/his-uni-live', 'drive.google.com/historia-universal', 'Activo', 'Semestral')
ON CONFLICT (id) DO UPDATE SET
  asignatura = EXCLUDED.asignatura, area = EXCLUDED.area, carrera = EXCLUDED.carrera,
  "docenteId" = EXCLUDED."docenteId", docente = EXCLUDED.docente, dias = EXCLUDED.dias,
  "horaInicio" = EXCLUDED."horaInicio", "horaFin" = EXCLUDED."horaFin", "meetUrl" = EXCLUDED."meetUrl",
  repositorio = EXCLUDED.repositorio, estado = EXCLUDED.estado, ciclovirtual = EXCLUDED.ciclovirtual;

-- C. MATRÍCULAS
INSERT INTO matriculas (
  id, estudiante, dni, correo, celular, programa, ciclo, turno,
  "fechaMatricula", "modalidadPago", "montoTotal", "montoPagado", "saldoPendiente", estado
) VALUES
('MAT-2026-001', 'Juan Carlos Flores Mendoza', '74839201', 'estudiante@gmail.com', '955112233', 'Medicina Humana (UNMSM)', 'Semestral 2026-I', 'Mañana', '2026-03-01', 'Cuotas', 1200.00, 800.00, 400.00, 'Al día'),
('MAT-2026-002', 'Valeria Sofía Quispe Alarcón', '76543210', 'v.quispe@academia.edu.pe', '955223344', 'Ingeniería de Sistemas (UNI)', 'Semestral 2026-I', 'Mañana', '2026-03-02', 'Al Contado', 1800.00, 1800.00, 0.00, 'Pagado Total'),
('MAT-2026-003', 'Lucía Fernanda Gómez Pérez', '73214569', 'l.gomez@academia.edu.pe', '955334455', 'Derecho y Ciencias Políticas (PUCP)', 'Semestral 2026-I', 'Tarde', '2026-03-03', 'Cuotas', 1400.00, 700.00, 700.00, 'Cuota Vencida'),
('MAT-2026-004', 'Diego Flores Mendoza', '75849302', 'd.flores@academia.edu.pe', '955445566', 'Ingeniería Industrial (UNI)', 'Intensivo 2026-I', 'Mañana', '2026-03-04', 'Al Contado', 850.00, 850.00, 0.00, 'Pagado Total'),
('MAT-2026-005', 'Elena Morales Ruiz', '71928374', 'e.morales@academia.edu.pe', '955556677', 'Medicina Humana (UNMSM)', 'Semestral 2026-I', 'Tarde', '2026-03-05', 'Cuotas', 1200.00, 400.00, 800.00, 'Pendiente')
ON CONFLICT (id) DO UPDATE SET
  estudiante = EXCLUDED.estudiante, dni = EXCLUDED.dni, correo = EXCLUDED.correo, celular = EXCLUDED.celular,
  programa = EXCLUDED.programa, ciclo = EXCLUDED.ciclo, turno = EXCLUDED.turno, "fechaMatricula" = EXCLUDED."fechaMatricula",
  modalidadPago = EXCLUDED."modalidadPago", montoTotal = EXCLUDED.montoTotal, montoPagado = EXCLUDED.montoPagado,
  saldoPendiente = EXCLUDED.saldoPendiente, estado = EXCLUDED.estado;

-- D. PAGOS
INSERT INTO pagos (
  id, "matriculaId", estudiante, concepto, monto, metodo, operacion, comprobante, fecha, estado
) VALUES
('PAG-001', 'MAT-2026-001', 'Juan Carlos Flores Mendoza', 'Matrícula + Cuota 1 (Enero)', 400.00, 'Yape / Plin', 'OP-9823412', 'B001-00382', '15/01/2026 10:30 AM', 'Aprobado'),
('PAG-002', 'MAT-2026-001', 'Juan Carlos Flores Mendoza', 'Cuota 2 (Febrero)', 400.00, 'Transferencia BCP', 'BCP-5544321', 'B001-00412', '14/02/2026 04:15 PM', 'Aprobado'),
('PAG-003', 'MAT-2026-002', 'Valeria Sofía Quispe Alarcón', 'Pago Único Contado Anual UNI (10% Dcto)', 1800.00, 'Tarjeta Débito/Crédito', 'VISA-44021', 'B001-00395', '18/01/2026 11:20 AM', 'Aprobado'),
('PAG-004', 'MAT-2026-003', 'Lucía Fernanda Gómez Pérez', 'Matrícula + Cuota 1 (Enero)', 350.00, 'Transferencia BBVA', 'BBVA-88219', 'B001-00401', '20/01/2026 09:40 AM', 'Aprobado'),
('PAG-005', 'MAT-2026-003', 'Lucía Fernanda Gómez Pérez', 'Cuota 2 (Febrero)', 350.00, 'Yape / Plin', 'OP-7731209', 'B001-00450', '22/02/2026 03:50 PM', 'Aprobado'),
('PAG-006', 'MAT-2026-004', 'Diego Flores Mendoza', 'Pago Único Intensivo Verano', 850.00, 'Transferencia BCP', 'BCP-9912301', 'B001-00422', '25/01/2026 12:10 PM', 'Aprobado'),
('PAG-007', 'MAT-2026-005', 'Elena Morales Ruiz', 'Matrícula + Cuota 1 (Febrero)', 400.00, 'Efectivo en Caja', 'CAJA-019', 'B001-00465', '02/02/2026 10:00 AM', 'Aprobado')
ON CONFLICT (id) DO UPDATE SET
  "matriculaId" = EXCLUDED."matriculaId", estudiante = EXCLUDED.estudiante, concepto = EXCLUDED.concepto,
  monto = EXCLUDED.monto, metodo = EXCLUDED.metodo, operacion = EXCLUDED.operacion,
  comprobante = EXCLUDED.comprobante, fecha = EXCLUDED.fecha, estado = EXCLUDED.estado;

-- E. INCIDENCIAS
INSERT INTO incidents (
  id, docente, dni, correo, tipo, "fechaPresentacion", materia,
  horario, motivo, "archivoNombre", estado, docenteid, cursoid,
  docentepropuesto, docentereemplazo, horarioactualizado, observaciones
) VALUES
('INC-D01', 'Carmen Rosa Herrera Villanueva', '45678912', 'c.herrera@academia.edu.pe', 'Inasistencia', '2026-10-18', 'Álgebra y Funciones', 'Lun / Mié 08:00 - 10:00', 'Atención por emergencia médica justificada con descanso médico Essalud.', 'Certificado_Medico_Herrera.pdf', 'Aprobado', 'USR-002', 'CRS-001', 'Juan Gabriel Pérez Ramos', 'Juan Gabriel Pérez Ramos', 'Miércoles 08:00 - 10:00', 'Reemplazo cubierto y validado por Dirección Académica.'),
('INC-D02', 'Miguel Ángel Paredes Ruiz', '48923104', 'm.paredes@academia.edu.pe', 'Tardanza', '2026-10-19', 'Física Fundamental', 'Mar / Jue 08:00 - 10:00', 'Retraso de 25 minutos por congestión en transporte público en Av. Universitaria.', NULL, 'Rechazado', 'USR-003', 'CRS-002', NULL, NULL, NULL, 'Excedió tolerancia reglamentaria (15 min). Descuento de 1h aplicado en nómina.'),
('INC-D03', 'Elena Sofía Torres Meléndez', '43219876', 'e.torres@academia.edu.pe', 'Inasistencia', '2026-10-23', 'Biología y Anatomía', 'Lun / Vie 15:00 - 17:00', 'Participación programada en congreso nacional de Ciencias Médicas.', 'Invitacion_Congreso_Medico.pdf', 'Pendiente', 'USR-004', 'CRS-003', 'Carmen Rosa Herrera Villanueva', NULL, 'Viernes 15:00 - 17:00', 'En evaluación para aprobación de reemplazo.')
ON CONFLICT (id) DO UPDATE SET
  docente = EXCLUDED.docente, dni = EXCLUDED.dni, correo = EXCLUDED.correo, tipo = EXCLUDED.tipo,
  "fechaPresentacion" = EXCLUDED."fechaPresentacion", materia = EXCLUDED.materia, horario = EXCLUDED.horario,
  motivo = EXCLUDED.motivo, "archivoNombre" = EXCLUDED."archivoNombre", estado = EXCLUDED.estado,
  docenteid = EXCLUDED.docenteid, cursoid = EXCLUDED.cursoid, docentepropuesto = EXCLUDED.docentepropuesto,
  docentereemplazo = EXCLUDED.docentereemplazo, horarioactualizado = EXCLUDED.horarioactualizado,
  observaciones = EXCLUDED.observaciones;

-- F. SESIONES DE ASISTENCIA
INSERT INTO attendance_sessions (
  id, "cursoId", asignatura, "docenteId", docente, fecha, "horaApertura", modalidad,
  "asistentesPuntuales", tardanzas, ausentes
) VALUES
('ATT-SES-001', 'CRS-001', 'Álgebra y Funciones', 'USR-002', 'Carmen Rosa Herrera Villanueva', '2026-10-21', '08:00', 'Virtual en vivo', '[{"id": "USR-005", "nombre": "Juan Carlos Flores Mendoza", "hora": "08:02"}, {"id": "USR-006", "nombre": "Valeria Sofía Quispe Alarcón", "hora": "08:04"}]'::jsonb, '[{"id": "USR-007", "nombre": "Lucía Fernanda Gómez Pérez", "hora": "08:14"}]'::jsonb, '[{"id": "USR-010", "nombre": "Diego Flores Mendoza"}]'::jsonb),
('ATT-SES-002', 'CRS-002', 'Física Fundamental', 'USR-003', 'Miguel Ángel Paredes Ruiz', '2026-10-22', '08:00', 'Virtual en vivo', '[{"id": "USR-005", "nombre": "Juan Carlos Flores Mendoza", "hora": "08:01"}, {"id": "USR-010", "nombre": "Diego Flores Mendoza", "hora": "08:05"}]'::jsonb, '[]'::jsonb, '[]'::jsonb),
('ATT-SES-003', 'CRS-003', 'Biología y Anatomía', 'USR-004', 'Elena Sofía Torres Meléndez', '2026-10-23', '15:00', 'Virtual en vivo', '[{"id": "USR-005", "nombre": "Juan Carlos Flores Mendoza", "hora": "15:01"}, {"id": "USR-011", "nombre": "Elena Morales Ruiz", "hora": "15:03"}]'::jsonb, '[]'::jsonb, '[{"id": "USR-007", "nombre": "Lucía Fernanda Gómez Pérez"}]'::jsonb)
ON CONFLICT (id) DO UPDATE SET
  "cursoId" = EXCLUDED."cursoId", asignatura = EXCLUDED.asignatura, "docenteId" = EXCLUDED."docenteId",
  docente = EXCLUDED.docente, fecha = EXCLUDED.fecha, "horaApertura" = EXCLUDED."horaApertura",
  modalidad = EXCLUDED.modalidad, "asistentesPuntuales" = EXCLUDED."asistentesPuntuales",
  tardanzas = EXCLUDED.tardanzas, ausentes = EXCLUDED.ausentes;

-- G. CONFIGURACIÓN
INSERT INTO configuracion (
  id, institucion_nombre, institucion_ruc, institucion_direccion, institucion_telefono,
  institucion_correo, ciclo_vigente, ciclo_inicio, ciclo_fin, ciclo_estado,
  tarifa_hora_docente, tolerancia_tardanza_min, horas_descuento_inasistencia
) VALUES (
  'general', 'Academia Preuniversitaria S.A.C.', '20608912345', 'Av. Universitaria 1234, Lima, Perú',
  '+51 987 654 321', 'contacto@academia.edu.pe', 'Semestral 2026-I', '2026-01-15', '2026-07-31',
  'En Curso', 50.00, 15, 2.00
) ON CONFLICT (id) DO NOTHING;
