"""
Generador y cargador de documentos PDF académicos y administrativos reales de prueba
para sustentación (ReportLab -> Supabase Storage -> PostgreSQL).
Puebla archivos binarios reales en:
  - public.incidents      (bucket: justificantes-docentes)
  - public.tareas         (bucket: materiales-clase)
  - public.tarea_entregas (bucket: tareas-entregas)
  - public.anuncios       (bucket: materiales-clase)
"""
from io import BytesIO
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape

import httpx
import psycopg2
from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

NAVY = colors.HexColor("#1E3A8A")
DARK = colors.HexColor("#0F172A")
GOLD = colors.HexColor("#F59E0B")
BG_SOFT = colors.HexColor("#F8FAFC")
BORDER = colors.HexColor("#CBD5E1")


def build_demo_pdf(
    *,
    header_org: str,
    header_sub: str,
    doc_code: str,
    title: str,
    meta_pairs: List[Tuple[str, str]],
    sections: List[Tuple[str, List[str]]],
    footer_signer: str,
) -> bytes:
    """Construye un PDF multipropósito limpio y realista con membrete institucional."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    usable_w = A4[0] - 32 * mm
    styles = getSampleStyleSheet()

    st_org = ParagraphStyle("Org", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=colors.white)
    st_code = ParagraphStyle("Code", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, alignment=TA_RIGHT, textColor=colors.white)
    st_title = ParagraphStyle("TitleDoc", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=DARK)
    st_sec = ParagraphStyle("Sec", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=NAVY)
    st_body = ParagraphStyle("Body", parent=styles["Normal"], fontName="Helvetica", fontSize=9.2, leading=13.5, textColor=DARK)
    st_sig = ParagraphStyle("Sig", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, alignment=TA_CENTER, textColor=DARK)

    story = []

    banner = Table(
        [[
            Paragraph(f"{escape(header_org)}<br/><font size='8' color='#DBEAFE'>{escape(header_sub)}</font>", st_org),
            Paragraph(f"DOCUMENTO DIGITAL<br/><font size='8' color='#FDE68A'>{escape(doc_code)}</font>", st_code),
        ]],
        colWidths=[usable_w * 0.72, usable_w * 0.28],
    )
    banner.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("LINEBELOW", (0, 0), (-1, -1), 2.2, GOLD),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(banner)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(escape(title), st_title))
    story.append(Spacer(1, 3 * mm))

    meta_cells = [
        Paragraph(f"<b><font color='#1E3A8A'>{escape(k)}:</font></b> {escape(v)}", st_body)
        for k, v in meta_pairs
    ]
    if len(meta_cells) % 2 == 1:
        meta_cells.append(Paragraph("", st_body))
    meta_rows = [meta_cells[i:i + 2] for i in range(0, len(meta_cells), 2)]
    meta_tbl = Table(meta_rows, colWidths=[usable_w * 0.5, usable_w * 0.5])
    meta_tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_SOFT),
            ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(meta_tbl)
    story.append(Spacer(1, 5 * mm))

    for sec_title, paragraphs in sections:
        story.append(Paragraph(escape(sec_title), st_sec))
        story.append(Spacer(1, 1.5 * mm))
        for p in paragraphs:
            story.append(Paragraph(escape(p), st_body))
            story.append(Spacer(1, 2 * mm))
        story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 10 * mm))
    sig_tbl = Table(
        [[
            Paragraph(
                f"________________________________________<br/><b>{escape(footer_signer)}</b><br/>"
                "<font size='7.5' color='#64748B'>Documento verificado en el Campus Virtual Academia Pre</font>",
                st_sig,
            )
        ]],
        colWidths=[usable_w],
    )
    story.append(sig_tbl)

    doc.build(story)
    return buf.getvalue()


def upload_and_sign_pdf(
    client: httpx.Client,
    supabase_url: str,
    service_key: str,
    bucket: str,
    object_path: str,
    pdf_bytes: bytes,
) -> Dict[str, str]:
    up_resp = client.post(
        f"{supabase_url}/storage/v1/object/{bucket}/{object_path}",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/pdf",
            "x-upsert": "true",
        },
        content=pdf_bytes,
    )
    if not up_resp.is_success:
        raise RuntimeError(f"Error subiendo {bucket}/{object_path}: {up_resp.status_code} {up_resp.text}")

    signed_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{object_path}"
    sign_resp = client.post(
        f"{supabase_url}/storage/v1/object/sign/{bucket}/{object_path}",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        },
        json={"expiresIn": 604800},
    )
    if sign_resp.is_success:
        suffix = sign_resp.json().get("signedURL") or sign_resp.json().get("signedUrl")
        if suffix:
            signed_url = f"{supabase_url}/storage/v1{suffix}" if suffix.startswith("/") else suffix

    return {
        "path": object_path,
        "url": signed_url,
        "size": len(pdf_bytes),
    }


def seed_demo_pdfs() -> None:
    db_url = os.getenv("DATABASE_URL", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not db_url or not supabase_url or not service_key:
        print("⚠️ Variables DATABASE_URL / SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY ausentes; omitiendo seed de PDFs.")
        return

    conn = psycopg2.connect(db_url)
    with httpx.Client(timeout=30.0) as client, conn.cursor() as cur:
        # 1. SUSTENTOS DE INCIDENCIAS DOCENTES (justificantes-docentes)
        incidents_seed = [
            {
                "id": "INC-101",
                "docenteid": "USR-002",
                "docente": "Carmen Rosa Herrera Villanueva",
                "dni": "45678912",
                "correo": "c.herrera@academia.edu.pe",
                "cursoid": "CRS-003",
                "materia": "Biología y Anatomía Humana",
                "horario": "Martes y Jueves 10:15 - 12:15",
                "tipo": "Inasistencia por Salud / Médica",
                "fechaPresentacion": "2026-11-24 08:40",
                "motivo": "Atención médica aguda en Policlínico EsSalud Jesús María con indicación de reposo de 24 horas.",
                "archivoNombre": "Certificado_Medico_EsSalud_Herrera.pdf",
                "objectPath": "demo/Certificado_Medico_EsSalud_Herrera.pdf",
                "estado": "Aprobado",
                "docentepropuesto": "Victoria Angelis Raggi Ruiz",
                "docentereemplazo": "Victoria Angelis Raggi Ruiz",
                "horarioactualizado": "Jueves 10:15 - 12:15",
                "observaciones": "Descanso médico verificado por Dirección Académica (sin descuento en planilla).",
                "minutos_tardanza": 0,
                "pdf": build_demo_pdf(
                    header_org="ESSALUD - RED PRESTACIONAL REBAGLIATI",
                    header_sub="CERTIFICADO DE INCAPACIDAD TEMPORAL PARA EL TRABAJO (CITT)",
                    doc_code="CITT-2026-88412",
                    title="CONSTANCIA DE ATENCIÓN MÉDICA Y DESCANSO DE 24 HORAS",
                    meta_pairs=[
                        ("Paciente", "Carmen Rosa Herrera Villanueva"),
                        ("DNI", "45678912"),
                        ("Centro Asistencial", "Policlínico Pablo Bermúdez - Lima"),
                        ("Fecha de Atención", "24/11/2026 - 07:50 hrs"),
                        ("Periodo de Reposo", "24/11/2026 (01 día lectivo)"),
                        ("Especialidad", "Medicina Interna"),
                    ],
                    sections=[
                        (
                            "1. Resumen Clínico y Prescripción",
                            [
                                "Se certifica que la paciente acudió por cuadro respiratorio agudo febril, indicándose tratamiento ambulatorio y reposo vocal/físico absoluto por veinticuatro (24) horas.",
                                "Se expide el presente documento digital para los fines de justificación laboral ante la Dirección Académica de la Academia Preuniversitaria.",
                            ],
                        )
                    ],
                    footer_signer="Dr. Luis Alberto Mendoza • CMP 48291 • Médico Tratante",
                ),
            },
            {
                "id": "INC-102",
                "docenteid": "USR-107",
                "docente": "Pedro Martlon Chavez Quispe",
                "dni": "45892317",
                "correo": "chavez@gmail.com",
                "cursoid": "CRS-001",
                "materia": "Álgebra Preuniversitaria",
                "horario": "Lunes y Miércoles 08:00 - 10:00",
                "tipo": "Solicitud de Permuta / Reemplazo",
                "fechaPresentacion": "2026-11-27 09:15",
                "motivo": "Participación oficial como ponente invitado en el Coloquio Anual de Matemática Aplicada en la UNI.",
                "archivoNombre": "Solicitud_Permuta_Coloquio_UNI_Chavez.pdf",
                "objectPath": "demo/Solicitud_Permuta_Coloquio_UNI_Chavez.pdf",
                "estado": "Pendiente",
                "docentepropuesto": "Prof. Miguel Ángel Paredes Ruiz",
                "docentereemplazo": None,
                "horarioactualizado": None,
                "observaciones": None,
                "minutos_tardanza": 0,
                "pdf": build_demo_pdf(
                    header_org="UNIVERSIDAD NACIONAL DE INGENIERÍA - FACULTAD DE CIENCIAS",
                    header_sub="COMITÉ ORGANIZADOR DEL COLOQUIO DE MATEMÁTICA PURA Y APLICADA",
                    doc_code="OFICIO-FC-UNI-2026-319",
                    title="CARTA DE INVITACIÓN A PONENCIA MAGISTRAL Y SOLICITUD DE PERMUTA",
                    meta_pairs=[
                        ("Docente Invitado", "Mag. Pedro Martlon Chavez Quispe"),
                        ("DNI", "45892317"),
                        ("Asignatura en Academia", "Álgebra Preuniversitaria (CRS-001)"),
                        ("Bloque Solicitado", "Miércoles 08:00 - 10:00 hrs"),
                        ("Docente Propuesto", "Ing. Miguel Ángel Paredes Ruiz"),
                        ("Estado de Trámite", "Pendiente de Visto Bueno"),
                    ],
                    sections=[
                        (
                            "1. Sustento Académico",
                            [
                                "Por medio de la presente se acredita la participación del docente Pedro Martlon Chavez Quispe como expositor en la sesión matutina del Coloquio de Matemática UNI.",
                                "El docente titular deja cargada la separata oficial de Polinomios Especiales y propone el reemplazo coordinado con el Ing. Miguel Ángel Paredes Ruiz para no afectar el avance silábico.",
                            ],
                        )
                    ],
                    footer_signer="Mag. Pedro Martlon Chavez Quispe • Cátedra de Álgebra",
                ),
            },
            {
                "id": "INC-103",
                "docenteid": "USR-003",
                "docente": "Miguel Ángel Paredes Ruiz",
                "dni": "48923104",
                "correo": "m.paredes@academia.edu.pe",
                "cursoid": "CRS-002",
                "materia": "Física General",
                "horario": "Martes y Viernes 08:00 - 10:00",
                "tipo": "Tardanza (18 min)",
                "fechaPresentacion": "2026-11-26 08:25",
                "motivo": "Corte fortuito de energía y caída de nodo de fibra óptica reportado a la operadora antes del inicio de sesión.",
                "archivoNombre": "Reporte_Tecnico_Conectividad_Paredes.pdf",
                "objectPath": "demo/Reporte_Tecnico_Conectividad_Paredes.pdf",
                "estado": "Pendiente",
                "docentepropuesto": None,
                "docentereemplazo": None,
                "horarioactualizado": None,
                "observaciones": None,
                "minutos_tardanza": 18,
                "pdf": build_demo_pdf(
                    header_org="REPORTE TÉCNICO DE INCIDENCIA DE SERVICIO FTTH",
                    header_sub="CENTRO DE ATENCIÓN DE AVERÍAS RESIDENCIALES - LIMA NORTE",
                    doc_code="TICKET-FTTH-992041",
                    title="CONSTANCIA DE INTERRUPCIÓN TEMPORAL DE SERVICIO DE INTERNET",
                    meta_pairs=[
                        ("Titular / Docente", "Miguel Ángel Paredes Ruiz"),
                        ("DNI", "48923104"),
                        ("Curso Afectado", "Física General (CRS-002)"),
                        ("Ventana de Corte", "26/11/2026 de 07:45 a 08:17 hrs"),
                        ("Minutos de Retraso", "18 minutos (conexión vía red móvil)"),
                        ("Código de Ticket", "INC-ISP-992041"),
                    ],
                    sections=[
                        (
                            "1. Detalle de la Incidencia Técnica",
                            [
                                "Se deja constancia de mantenimiento correctivo de emergencia en troncal óptica zonal que afectó la conectividad fija del abonado hasta las 08:17 hrs.",
                                "El docente ingresó a la sala Google Meet mediante enlace de respaldo móvil y completó el dictado íntegro de los ejercicios programados de Estática y Dinámica.",
                            ],
                        )
                    ],
                    footer_signer="Ing. Miguel Ángel Paredes Ruiz • Cátedra de Física",
                ),
            },
        ]

        # Limpiar cualquier fila de prueba antigua y sembrar las 3 incidencias oficiales con PDF real
        cur.execute("DELETE FROM incidents WHERE id LIKE 'INC-TEST%' OR id LIKE 'INC-ALIAS%';")
        for inc in incidents_seed:
            up = upload_and_sign_pdf(
                client, supabase_url, service_key,
                "justificantes-docentes", inc["objectPath"], inc["pdf"]
            )
            cur.execute(
                """
                INSERT INTO incidents (
                    id, docente, dni, correo, tipo, "fechaPresentacion",
                    materia, horario, motivo, "archivoNombre", archivo_url, archivo_path,
                    estado, docenteid, cursoid, docentepropuesto, docentereemplazo,
                    horarioactualizado, observaciones, minutos_tardanza
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    "archivoNombre" = EXCLUDED."archivoNombre",
                    archivo_url = EXCLUDED.archivo_url,
                    archivo_path = EXCLUDED.archivo_path,
                    motivo = EXCLUDED.motivo;
                """,
                (
                    inc["id"], inc["docente"], inc["dni"], inc["correo"], inc["tipo"],
                    inc["fechaPresentacion"], inc["materia"], inc["horario"], inc["motivo"],
                    inc["archivoNombre"], up["url"], up["path"], inc["estado"],
                    inc["docenteid"], inc["cursoid"], inc["docentepropuesto"],
                    inc["docentereemplazo"], inc["horarioactualizado"],
                    inc["observaciones"], inc["minutos_tardanza"],
                ),
            )

        # 2. GUÍAS PDF EN TAREAS (materiales-clase -> public.tareas)
        tareas_pdfs = [
            (
                "TSK-101",
                "Guia_Practica_06_Polinomios_DECO.pdf",
                "CRS-001/Guia_Practica_06_Polinomios_DECO.pdf",
                "GUÍA DE PRÁCTICA DIRIGIDA N.° 06: POLINOMIOS ESPECIALES Y PRODUCTOS NOTABLES",
                "Álgebra Preuniversitaria • Semestral San Marcos 2026-II",
                "Pedro Martlon Chavez Quispe",
                [
                    "Problema 1 (Tipo DECO UNMSM): Si P(x, y) = (a-2)x^(a+1)y^b + (b+3)x^5y^(2a-1) es un polinomio homogéneo de grado 9, calcule la suma de sus coeficientes.",
                    "Problema 2: Sabiendo que a + b + c = 0 y abc = 6, determine el valor reducido de E = (a^3 + b^3 + c^3) / 9.",
                    "Problema 3: Dado un polinomio completo y ordenado en forma decreciente de 12 términos, halle el término independiente y sustente cada propiedad empleada.",
                    "Indicaciones de entrega: Subir un único archivo PDF legible con procedimiento paso a paso antes de la fecha límite.",
                ],
            ),
            (
                "TSK-102",
                "Guia_Fisiologia_Cardiovascular_UNMSM.pdf",
                "CRS-003/Guia_Fisiologia_Cardiovascular_UNMSM.pdf",
                "TALLER DE BIOLOGÍA Y ANATOMÍA: FISIOLOGÍA CARDIOVASCULAR Y CICLO CARDÍACO",
                "Biología y Anatomía Humana • Área A (Ciencias de la Salud)",
                "Carmen Rosa Herrera Villanueva",
                [
                    "1. Elabore un cuadro comparativo entre circulación menor (pulmonar) y circulación mayor (sistémica), indicando vasos aferentes, eferentes y válvulas auriculoventriculares.",
                    "2. Explique las 4 fases del ciclo cardíaco (llenado ventricular, contracción isovolumétrica, eyección y relajación isovolumétrica) e identifique el origen del primer y segundo ruido cardíaco.",
                    "3. Resuelva las 20 preguntas tipo admisión UNMSM indicando la clave sustentada.",
                ],
            ),
            (
                "TSK-103",
                "Taller_Balance_Redox_Estequiometria.pdf",
                "CRS-006/Taller_Balance_Redox_Estequiometria.pdf",
                "TALLER CALIFICADO: BALANCE DE REACCIONES REDOX Y ESTEQUIOMETRÍA",
                "Química General y Orgánica • Semestral San Marcos 2026-II",
                "Carmen Rosa Herrera Villanueva",
                [
                    "1. Balancee por el método del ion-electrón en medio ácido: KMnO4 + FeSO4 + H2SO4 -> MnSO4 + Fe2(SO4)3 + K2SO4 + H2O e indique la relación molar oxidante/reductor.",
                    "2. Calcule el volumen de O2 en condiciones normales necesario para la combustión completa de 110 g de propano (C3H8) con 85% de pureza.",
                ],
            ),
            (
                "TSK-104",
                "Practica_UNI_Arco_Multiple.pdf",
                "CRS-004/Practica_UNI_Arco_Multiple.pdf",
                "PRÁCTICA CALIFICADA NIVEL CEPRE-UNI: IDENTIDADES DE ARCO MÚLTIPLE",
                "Trigonometría y Geometría Analítica • Anual UNI",
                "Pedro Martlon Chavez Quispe",
                [
                    "1. Reduzca la expresión K = cot(x) - tan(x) - 2tan(2x) - 4tan(4x) en términos de cot(8x).",
                    "2. Demuestre que sen(3x)/sen(x) - cos(3x)/cos(x) = 2 para todo x admisible y determine el rango de la función asociada.",
                ],
            ),
            (
                "TSK-V01",
                "Practica_Final_Verano_Logaritmos.pdf",
                "CRS-V01/Practica_Final_Verano_Logaritmos.pdf",
                "EVALUACIÓN FINAL VERANO 2026: ECUACIONES EXPONENCIALES Y LOGARÍTMICAS",
                "Repaso Intensivo de Álgebra DECO • Ciclo Verano 2026",
                "Pedro Martlon Chavez Quispe",
                [
                    "1. Resuelva log_2(x - 1) + log_2(x + 1) = 3 e indique el conjunto solución admisible.",
                    "2. Si 3^(2x) - 10(3^x) + 9 = 0, calcule la suma de los cuadrados de las raíces reales.",
                ],
            ),
        ]

        for tid, fname, obj_path, title, sub, docente, probs in tareas_pdfs:
            pdf_b = build_demo_pdf(
                header_org="ACADEMIA PREUNIVERSITARIA ALTO RENDIMIENTO",
                header_sub=sub,
                doc_code=f"GUIA-{tid}",
                title=title,
                meta_pairs=[
                    ("Código de Evaluación", tid),
                    ("Docente Responsable", docente),
                    ("Escala de Calificación", "0 a 20 puntos (Vigesimal)"),
                    ("Modalidad", "Entrega individual en PDF"),
                ],
                sections=[("Enunciado Oficial de Ejercicios Propuestos", probs)],
                footer_signer=f"{docente} • Coordinación Académica Pre-U",
            )
            up = upload_and_sign_pdf(client, supabase_url, service_key, "materiales-clase", obj_path, pdf_b)
            cur.execute(
                """
                UPDATE tareas
                SET archivo_nombre = %s, archivo_path = %s, archivo_url = %s
                WHERE id = %s;
                """,
                (fname, up["path"], up["url"], tid),
            )

        # 3. SEPARATAS Y FORMULARIOS EN ANUNCIOS (materiales-clase -> public.anuncios)
        anuncios_pdfs = [
            (
                "ANN-101",
                "Formulario_Division_Algebraica_y_Cocientes_Notables.pdf",
                "CRS-001/Formulario_Division_Algebraica_y_Cocientes_Notables.pdf",
                "SEPARATA TEÓRICA: MÉTODO DE HORNER, RUFFINI, TEOREMA DEL RESTO Y COCIENTES NOTABLES",
                "Álgebra Preuniversitaria • Material Oficial de Clase",
                "Pedro Martlon Chavez Quispe",
                [
                    "1. Identidad Fundamental de la División: D(x) = d(x) · q(x) + R(x), donde grad(R) < grad(d).",
                    "2. Teorema del Resto (Descartes): El residuo de dividir P(x) entre (ax + b) es R = P(-b/a).",
                    "3. Término k-ésimo de un Cociente Notable (x^n - y^n)/(x - y): t_k = x^(n-k) · y^(k-1) para 1 <= k <= n.",
                ],
            ),
            (
                "ANN-102",
                "Banco_Preguntas_Sistema_Circulatorio_UNMSM.pdf",
                "CRS-003/Banco_Preguntas_Sistema_Circulatorio_UNMSM.pdf",
                "BANCO DE PREGUNTAS DECO UNMSM: SISTEMA CARDIOVASCULAR Y SANGRE",
                "Biología y Anatomía Humana • Seminario Ciencias Médicas",
                "Carmen Rosa Herrera Villanueva",
                [
                    "1. El marcapasos primario del corazón humano se localiza en la aurícula derecha y corresponde al nodo sinusal (Keith-Flack).",
                    "2. Las arterias coronarias nacen en los senos de Valsalva de la arteria aorta ascendente e irrigan el miocardio durante la diástole.",
                    "3. La válvula mitral o bicúspide comunica la aurícula izquierda con el ventrículo izquierdo evitando el reflujo sistólico.",
                ],
            ),
            (
                "ANN-103",
                "Tabla_Masas_Atomicas_Oficiales_Prospecto.pdf",
                "CRS-006/Tabla_Masas_Atomicas_Oficiales_Prospecto.pdf",
                "VALORES OFICIALES DE MASAS ATÓMICAS Y ESTADOS DE OXIDACIÓN FRECUENTES",
                "Química General y Orgánica • Guía de Consulta Rápida",
                "Carmen Rosa Herrera Villanueva",
                [
                    "Masas atómicas fundamentales (uma): H = 1; C = 12; N = 14; O = 16; Na = 23; Mg = 24; Al = 27; S = 32; Cl = 35.5; K = 39; Ca = 40; Fe = 56; Cu = 63.5; Ag = 108.",
                    "Constante universal de los gases ideales: R = 0.082 atm·L/(mol·K) = 62.4 mmHg·L/(mol·K). Volumen molar a C.N. = 22.4 L/mol.",
                ],
            ),
        ]

        for aid, fname, obj_path, title, sub, docente, notes in anuncios_pdfs:
            pdf_b = build_demo_pdf(
                header_org="ACADEMIA PREUNIVERSITARIA ALTO RENDIMIENTO",
                header_sub=sub,
                doc_code=f"MAT-{aid}",
                title=title,
                meta_pairs=[
                    ("Comunicado", aid),
                    ("Cátedra", docente),
                    ("Acceso", "Disponible para alumnos matriculados"),
                    ("Formato", "Separata Oficial PDF"),
                ],
                sections=[("Resumen Teórico y Fórmulas Clave", notes)],
                footer_signer=f"{docente} • Departamento Académico",
            )
            up = upload_and_sign_pdf(client, supabase_url, service_key, "materiales-clase", obj_path, pdf_b)
            cur.execute(
                """
                UPDATE anuncios
                SET archivo_nombre = %s, archivo_path = %s, archivo_url = %s
                WHERE id = %s;
                """,
                (fname, up["path"], up["url"], aid),
            )

        # 4. ENTREGAS REALES DE ESTUDIANTES (tareas-entregas -> public.tarea_entregas)
        entregas_seed = [
            {
                "id": "ENT-V01",
                "tarea_id": "TSK-V01",
                "curso_id": "CRS-V01",
                "estudiante_id": "USR-108",
                "estudiante_nombre": "Esteban Torres Mendoza",
                "estudiante_correo": "esteban@gmail.com",
                "archivo_nombre": "solucionario_verano_esteban.pdf",
                "object_path": "CRS-V01/USR-108_solucionario_verano_esteban.pdf",
                "comentario_alumno": "Adjunto desarrollo completo de ecuaciones exponenciales y logarítmicas.",
                "estado": "Calificado",
                "nota": 18.50,
                "comentario_docente": "Excelente manejo de propiedades de logaritmos. Revisa solo el signo en la pregunta 9.",
                "lineas": [
                    "Ejercicio 1: Aplicando propiedad log_2((x-1)(x+1)) = 3 => x^2 - 1 = 8 => x^2 = 9. Por existencia x > 1, por tanto C.S. = {3}.",
                    "Ejercicio 2: Cambio de variable u = 3^x => u^2 - 10u + 9 = 0 => (u-9)(u-1) = 0 => 3^x = 9 (x=2) o 3^x = 1 (x=0). Suma de cuadrados = 4.",
                ],
            },
            {
                "id": "ENT-101",
                "tarea_id": "TSK-101",
                "curso_id": "CRS-001",
                "estudiante_id": "USR-006",
                "estudiante_nombre": "Valeria Sofía Quispe Alarcón",
                "estudiante_correo": "v.quispe@academia.edu.pe",
                "archivo_nombre": "Desarrollo_Polinomios_Valeria_Quispe.pdf",
                "object_path": "CRS-001/USR-006_Desarrollo_Polinomios_Valeria_Quispe.pdf",
                "comentario_alumno": "Profesor Chávez, envío el desarrollo paso a paso de los 15 problemas de la semana 6.",
                "estado": "Entregado",
                "nota": None,
                "comentario_docente": "",
                "lineas": [
                    "Problema 1 (Polinomio Homogéneo): Igualando grados absolutos: a + 1 + b = 5 + 2a - 1 = 9 => 2a + 4 = 9. Sustituyendo coeficientes se obtiene P(1,1) = 14.",
                    "Problema 2 (Identidad Condicional de Gauss): Como a + b + c = 0, se cumple a^3 + b^3 + c^3 = 3abc = 3(6) = 18. Por lo tanto E = 18 / 9 = 2.",
                ],
            },
            {
                "id": "ENT-102",
                "tarea_id": "TSK-101",
                "curso_id": "CRS-001",
                "estudiante_id": "USR-007",
                "estudiante_nombre": "Ana Paula Vargas Chavez",
                "estudiante_correo": "anapaula@gmail.com",
                "archivo_nombre": "Solucion_Practica6_Ana_Paula_Vargas.pdf",
                "object_path": "CRS-001/USR-007_Solucion_Practica6_Ana_Paula_Vargas.pdf",
                "comentario_alumno": "Adjunto resolución completa en limpio.",
                "estado": "Calificado",
                "nota": 17.00,
                "comentario_docente": "Muy buen orden algebraico e identidades condicionales bien aplicadas.",
                "lineas": [
                    "Desarrollo completo de Polinomios Especiales: Homogeneidad verificada término a término y aplicación directa de identidad de Legendre y Cauchy.",
                ],
            },
            {
                "id": "ENT-103",
                "tarea_id": "TSK-102",
                "curso_id": "CRS-003",
                "estudiante_id": "USR-108",
                "estudiante_nombre": "Esteban Torres Mendoza",
                "estudiante_correo": "esteban@gmail.com",
                "archivo_nombre": "Esquema_Ciclo_Cardiaco_Esteban_Torres.pdf",
                "object_path": "CRS-003/USR-108_Esquema_Ciclo_Cardiaco_Esteban_Torres.pdf",
                "comentario_alumno": "Doctora Herrera, adjunto el esquema comparativo y las 20 claves sustentadas.",
                "estado": "Calificado",
                "nota": 19.00,
                "comentario_docente": "Excelente precisión anatómica en válvulas sigmoideas y fases isovolumétricas.",
                "lineas": [
                    "1. Circulación Menor: Ventrículo derecho -> Arteria pulmonar -> Capilares alveolares (hematosis) -> 4 Venas pulmonares -> Aurícula izquierda.",
                    "2. Ruidos Cardíacos: 1.er ruido por cierre de válvulas AV (mitral y tricúspide); 2.do ruido por cierre de válvulas sigmoideas (aórtica y pulmonar).",
                ],
            },
        ]

        for ent in entregas_seed:
            pdf_b = build_demo_pdf(
                header_org="ENTREGA ACADÉMICA DE ESTUDIANTE - CAMPUS VIRTUAL",
                header_sub=f"Curso: {ent['curso_id']} • Tarea: {ent['tarea_id']}",
                doc_code=ent["id"],
                title=f"RESOLUCIÓN DE PRÁCTICA — {ent['estudiante_nombre'].upper()}",
                meta_pairs=[
                    ("Estudiante", ent["estudiante_nombre"]),
                    ("Correo Institucional", ent["estudiante_correo"]),
                    ("Archivo Original", ent["archivo_nombre"]),
                    ("Comentario Alumno", ent["comentario_alumno"]),
                ],
                sections=[("Desarrollo y Sustentación de Ejercicios", ent["lineas"])],
                footer_signer=f"Alumno: {ent['estudiante_nombre']}",
            )
            up = upload_and_sign_pdf(
                client, supabase_url, service_key, "tareas-entregas", ent["object_path"], pdf_b
            )
            cur.execute(
                """
                INSERT INTO tarea_entregas (
                    id, tarea_id, curso_id, estudiante_id, estudiante_nombre, estudiante_correo,
                    archivo_path, archivo_url, archivo_nombre, tamano_bytes,
                    comentario_alumno, estado, nota, comentario_docente,
                    entregado_en, calificado_en
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    NOW(), CASE WHEN %s = 'Calificado' THEN NOW() ELSE NULL END
                )
                ON CONFLICT (tarea_id, estudiante_id) DO UPDATE SET
                    archivo_path = EXCLUDED.archivo_path,
                    archivo_url = EXCLUDED.archivo_url,
                    archivo_nombre = EXCLUDED.archivo_nombre,
                    tamano_bytes = EXCLUDED.tamano_bytes,
                    comentario_alumno = EXCLUDED.comentario_alumno,
                    estado = EXCLUDED.estado,
                    nota = EXCLUDED.nota,
                    comentario_docente = EXCLUDED.comentario_docente;
                """,
                (
                    ent["id"], ent["tarea_id"], ent["curso_id"], ent["estudiante_id"],
                    ent["estudiante_nombre"], ent["estudiante_correo"],
                    up["path"], up["url"], ent["archivo_nombre"], up["size"],
                    ent["comentario_alumno"], ent["estado"], ent["nota"], ent["comentario_docente"],
                    ent["estado"],
                ),
            )

        conn.commit()
    conn.close()
    print("✅ 15 PDFs reales generados, subidos a Supabase Storage y enlazados en PostgreSQL.")


if __name__ == "__main__":
    seed_demo_pdfs()
