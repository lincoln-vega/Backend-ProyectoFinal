"""
Motor Institucional de Reportes Paramétricos PDF (estilo JasperReports) y Datos JSON/Excel.
Genera documentos PDF reales con ReportLab Platypus (encabezado corporativo, RUC, filtros,
KPIs ejecutivos, tablas paginadas con cabeceras repetidas, numeración 'Página X de Y' y firmas),
aplicando aislamiento estricto por rol (Admin global vs. Docente acotado a sus cursos/alumnos).
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import time
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from auth import require_role
from database import fetch_all_as_dict, fetch_one_as_dict, get_pg_connection, load_db
from routers.configuracion import DEFAULT_CONFIG
from routers.usuarios import parse_course_list

router = APIRouter(prefix="/reportes", tags=["Motor de Reportes Institucionales (PDF / JSON)"])

LIMA_TZ = timezone(timedelta(hours=-5))
NAVY = colors.HexColor("#1E3A8A")
NAVY_DARK = colors.HexColor("#0F172A")
GOLD = colors.HexColor("#F59E0B")
SLATE_BG = colors.HexColor("#F8FAFC")
SLATE_BORDER = colors.HexColor("#CBD5E1")
SLATE_MUTED = colors.HexColor("#64748B")
EMERALD = colors.HexColor("#059669")
ROSE = colors.HexColor("#DC2626")


def _now_lima() -> datetime:
    return datetime.now(LIMA_TZ)


def _safe_text(val: Any) -> str:
    if val is None:
        return "—"
    txt = str(val).strip()
    return escape(txt) if txt else "—"


def _normalize_name(val: Any) -> str:
    txt = str(val or "").lower().strip()
    for p in ("prof.", "dra.", "dr.", "ing.", "lic."):
        if txt.startswith(p):
            txt = txt[len(p):].strip()
    return txt


def _course_belongs_to_teacher(course: Dict[str, Any], teacher_user: Dict[str, Any]) -> bool:
    tid = str(teacher_user.get("id") or "").strip()
    doc_id = str(course.get("docenteId") or course.get("docente_id") or "").strip()
    if tid and doc_id and tid == doc_id:
        return True
    assigned = parse_course_list(teacher_user.get("cursosAsignados") or teacher_user.get("cursos_asignados"))
    cid = str(course.get("id") or "").strip()
    if cid and cid in assigned:
        return True
    full_name = _normalize_name(f"{teacher_user.get('nombres', '')} {teacher_user.get('apellidos', '')}")
    c_doc = _normalize_name(course.get("docente"))
    if full_name and c_doc and (full_name in c_doc or c_doc in full_name):
        return True
    return False


# --- CANVAS NUMERADO ESTILO JASPERREPORTS ("Página X de Y") ---

class JasperNumberedCanvas(canvas.Canvas):
    def __init__(self, *args, report_code: str = "", institution_name: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: List[Dict[str, Any]] = []
        self._report_code = report_code
        self._institution_name = institution_name

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_footer(total_pages)
            super().showPage()
        super().save()

    def _draw_page_footer(self, page_count: int):
        width, _ = self._pagesize
        self.saveState()
        self.setStrokeColor(SLATE_BORDER)
        self.setLineWidth(0.6)
        self.line(14 * mm, 13 * mm, width - 14 * mm, 13 * mm)

        self.setFont("Helvetica", 7.5)
        self.setFillColor(SLATE_MUTED)
        left_txt = (
            f"{self._institution_name} • Sistema Integrado de Gestión Académica • "
            f"Código: {self._report_code}"
        )
        self.drawString(14 * mm, 8.5 * mm, left_txt[:115])
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(NAVY_DARK)
        self.drawRightString(width - 14 * mm, 8.5 * mm, f"Página {self._pageNumber} de {page_count}")
        self.restoreState()


# --- GENERADOR PDF UNIFICADO (ESTILO JASPERREPORTS) ---

def _build_jasper_pdf(
    *,
    config: Dict[str, Any],
    report_code: str,
    titulo: str,
    subtitulo: str,
    parametros: List[Tuple[str, str]],
    emitido_por: str,
    kpis: List[Dict[str, str]],
    columnas: List[Dict[str, Any]],
    filas: List[Dict[str, Any]],
    extra_section: Optional[Dict[str, Any]] = None,
    use_landscape: bool = True,
    firmante_secundario: str = "Coordinación Académica / Cátedra",
) -> bytes:
    pagesize = landscape(A4) if use_landscape else A4
    page_w, _ = pagesize
    usable_w = page_w - 28 * mm

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=pagesize,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=18 * mm,
        title=titulo,
        author=config.get("institucion_nombre") or "Academia Preuniversitaria",
    )

    styles = getSampleStyleSheet()
    st_inst = ParagraphStyle(
        "Inst",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.white,
    )
    st_inst_sub = ParagraphStyle(
        "InstSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#DBEAFE"),
    )
    st_code = ParagraphStyle(
        "CodeRight",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        alignment=TA_RIGHT,
        textColor=colors.white,
    )
    st_title = ParagraphStyle(
        "RepTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13.5,
        leading=16,
        textColor=NAVY_DARK,
    )
    st_sub = ParagraphStyle(
        "RepSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=SLATE_MUTED,
    )
    st_th = ParagraphStyle(
        "TH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.8,
        leading=9.5,
        textColor=colors.white,
    )
    st_td = ParagraphStyle(
        "TD",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.6,
        leading=9.5,
        textColor=NAVY_DARK,
    )
    st_td_right = ParagraphStyle("TDR", parent=st_td, alignment=TA_RIGHT, fontName="Helvetica-Bold")
    st_td_center = ParagraphStyle("TDC", parent=st_td, alignment=TA_CENTER)

    story: List[Any] = []
    inst_nombre = config.get("institucion_nombre") or DEFAULT_CONFIG["institucion_nombre"]
    inst_ruc = config.get("institucion_ruc") or DEFAULT_CONFIG["institucion_ruc"]
    inst_dir = config.get("institucion_direccion") or DEFAULT_CONFIG["institucion_direccion"]
    inst_tel = config.get("institucion_telefono") or DEFAULT_CONFIG["institucion_telefono"]
    fecha_emision = _now_lima().strftime("%d/%m/%Y %H:%M:%S")

    # 1. BANDA DE MEMBRETE CORPORATIVO
    header_left = Paragraph(
        f"{_safe_text(inst_nombre.upper())}<br/>"
        f"<font size='7.5' color='#DBEAFE'>RUC: {_safe_text(inst_ruc)} • {_safe_text(inst_dir)} • Tel: {_safe_text(inst_tel)}</font>",
        st_inst,
    )
    header_right = Paragraph(
        f"REPORTE OFICIAL<br/>"
        f"<font size='7.5' color='#FDE68A'>Cód: {_safe_text(report_code)}</font><br/>"
        f"<font size='7.2' color='#DBEAFE'>Emisión: {fecha_emision}</font>",
        st_code,
    )
    banner_tbl = Table([[header_left, header_right]], colWidths=[usable_w * 0.70, usable_w * 0.30])
    banner_tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("LINEBELOW", (0, 0), (-1, -1), 2.2, GOLD),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(banner_tbl)
    story.append(Spacer(1, 4 * mm))

    # 2. TÍTULO DEL DOCUMENTO Y BANDA DE PARÁMETROS
    story.append(Paragraph(_safe_text(titulo), st_title))
    story.append(Paragraph(_safe_text(subtitulo), st_sub))
    story.append(Spacer(1, 2.5 * mm))

    param_pairs = list(parametros) + [("Emitido por", emitido_por)]
    param_cells = [
        Paragraph(
            f"<font name='Helvetica-Bold' color='#1E3A8A'>{_safe_text(k)}:</font> {_safe_text(v)}",
            st_td,
        )
        for k, v in param_pairs
    ]
    while len(param_cells) % 3 != 0:
        param_cells.append(Paragraph("", st_td))
    param_rows = [param_cells[i:i + 3] for i in range(0, len(param_cells), 3)]
    param_tbl = Table(param_rows, colWidths=[usable_w / 3.0] * 3)
    param_tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SLATE_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, SLATE_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.append(param_tbl)
    story.append(Spacer(1, 3.5 * mm))

    # 3. BANDA DE RESUMEN EJECUTIVO (KPIs)
    if kpis:
        n_kpis = len(kpis)
        kpi_cells = [
            Paragraph(
                f"<font size='7' color='#64748B'>{_safe_text(k['label']).upper()}</font><br/>"
                f"<font name='Helvetica-Bold' size='11' color='#0F172A'>{_safe_text(k['value'])}</font>",
                st_td,
            )
            for k in kpis
        ]
        kpi_tbl = Table([kpi_cells], colWidths=[usable_w / n_kpis] * n_kpis)
        kpi_tbl.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#BFDBFE")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFDBFE")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])
        )
        story.append(kpi_tbl)
        story.append(Spacer(1, 4 * mm))

    # Helper local para construir una tabla de detalle estilo Jasper
    def _make_detail_table(cols_spec: List[Dict[str, Any]], rows_data: List[Dict[str, Any]]) -> Table:
        weights = [float(c.get("weight", 1.0)) for c in cols_spec]
        total_w = sum(weights) or 1.0
        col_widths = [(w / total_w) * usable_w for w in weights]

        header_row = [Paragraph(_safe_text(c["label"]), st_th) for c in cols_spec]
        table_matrix: List[List[Any]] = [header_row]

        if not rows_data:
            empty_row = [Paragraph("No se registran datos para los filtros seleccionados.", st_td_center)]
            empty_row += [Paragraph("", st_td)] * (len(cols_spec) - 1)
            table_matrix.append(empty_row)
        else:
            for item in rows_data:
                r_cells = []
                for c in cols_spec:
                    val = item.get(c["key"])
                    align = c.get("align", "left")
                    style_use = st_td_right if align == "right" else (st_td_center if align == "center" else st_td)
                    r_cells.append(Paragraph(_safe_text(val), style_use))
                table_matrix.append(r_cells)

        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, SLATE_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ]
        if not rows_data and len(cols_spec) > 1:
            t_style.append(("SPAN", (0, 1), (-1, 1)))
        else:
            for idx in range(1, len(table_matrix)):
                if idx % 2 == 0:
                    t_style.append(("BACKGROUND", (0, idx), (-1, idx), SLATE_BG))

        tbl = Table(table_matrix, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle(t_style))
        return tbl

    # 4. TABLA PRINCIPAL DE DETALLE
    story.append(_make_detail_table(columnas, filas))

    # 5. SECCIÓN SECUNDARIA OPCIONAL (Ej. Detalle de Evaluaciones en Kardex de Alumno)
    if extra_section and extra_section.get("columnas"):
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                _safe_text(extra_section.get("titulo", "Detalle Complementario")),
                ParagraphStyle("SecTitle", parent=st_title, fontSize=10.5, leading=13),
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(_make_detail_table(extra_section["columnas"], extra_section.get("filas", [])))

    # 6. BANDA DE FIRMAS Y CONFORMIDAD INSTITUCIONAL
    story.append(Spacer(1, 10 * mm))
    sig_style = ParagraphStyle("Sig", parent=st_td, alignment=TA_CENTER, leading=10)
    sig_tbl = Table(
        [[
            Paragraph(
                "______________________________________<br/>"
                "<b>DIRECCIÓN ACADÉMICA GENERAL</b><br/>"
                f"<font size='7' color='#64748B'>{_safe_text(inst_nombre)}</font>",
                sig_style,
            ),
            Paragraph(
                "______________________________________<br/>"
                f"<b>{_safe_text(firmante_secundario.upper())}</b><br/>"
                f"<font size='7' color='#64748B'>Emitido por: {_safe_text(emitido_por)}</font>",
                sig_style,
            ),
        ]],
        colWidths=[usable_w * 0.5, usable_w * 0.5],
    )
    sig_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(KeepTogether([sig_tbl]))

    doc.build(
        story,
        canvasmaker=lambda *a, **kw: JasperNumberedCanvas(
            *a,
            report_code=report_code,
            institution_name=inst_nombre,
            **kw,
        ),
    )
    return buf.getvalue()


def _respond_report(
    *,
    formato: str,
    filename_base: str,
    config: Dict[str, Any],
    report_code: str,
    titulo: str,
    subtitulo: str,
    parametros: List[Tuple[str, str]],
    emitido_por: str,
    kpis: List[Dict[str, str]],
    columnas: List[Dict[str, Any]],
    filas: List[Dict[str, Any]],
    extra_section: Optional[Dict[str, Any]] = None,
    use_landscape: bool = True,
    firmante_secundario: str = "Coordinación Académica",
):
    if str(formato or "pdf").lower() == "json":
        return {
            "reportCode": report_code,
            "titulo": titulo,
            "subtitulo": subtitulo,
            "institucion": {
                "nombre": config.get("institucion_nombre") or DEFAULT_CONFIG["institucion_nombre"],
                "ruc": config.get("institucion_ruc") or DEFAULT_CONFIG["institucion_ruc"],
                "cicloVigente": config.get("ciclo_vigente") or DEFAULT_CONFIG["ciclo_vigente"],
            },
            "emitidoEn": _now_lima().isoformat(),
            "emitidoPor": emitido_por,
            "parametros": [{"label": k, "value": v} for k, v in parametros],
            "kpis": kpis,
            "columnas": [{"key": c["key"], "label": c["label"]} for c in columnas],
            "filas": filas,
            "extraSection": extra_section,
        }

    pdf_bytes = _build_jasper_pdf(
        config=config,
        report_code=report_code,
        titulo=titulo,
        subtitulo=subtitulo,
        parametros=parametros,
        emitido_por=emitido_por,
        kpis=kpis,
        columnas=columnas,
        filas=filas,
        extra_section=extra_section,
        use_landscape=use_landscape,
        firmante_secundario=firmante_secundario,
    )
    stamp = _now_lima().strftime("%Y%m%d_%H%M")
    headers = {
        "Content-Disposition": f'inline; filename="{filename_base}_{stamp}.pdf"',
        "Cache-Control": "no-store",
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


# --- CAPA UNIFICADA DE ACCESO A DATOS (PG + FALLBACK DB.JSON) ---

def _load_full_snapshot() -> Dict[str, Any]:
    """Obtiene en 1 sola conexión del pool los datasets requeridos para cruzar reportes reales."""
    conn = get_pg_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM configuracion WHERE id = 'general' LIMIT 1;")
                config = fetch_one_as_dict(cur) or dict(DEFAULT_CONFIG)

                cur.execute("SELECT * FROM ciclos_academicos ORDER BY es_vigente DESC, fecha_inicio DESC NULLS LAST;")
                ciclos = fetch_all_as_dict(cur) or []

                cur.execute(
                    """
                    SELECT id, nombres, apellidos, dni, correo, celular, rol, estado, salario,
                           "carreraObjetivo", "areaAcademica", ciclovirtual, "cursosInscritos", "cursosAsignados"
                    FROM users
                    ORDER BY apellidos ASC NULLS LAST, nombres ASC NULLS LAST;
                    """
                )
                users = fetch_all_as_dict(cur) or []

                cur.execute(
                    """
                    SELECT c.id, c.asignatura, c.docente, c."docenteId", c.dias,
                           c."horaInicio", c."horaFin", c.ciclo_id, c.estado,
                           ca.nombre AS ciclo_nombre
                    FROM courses c
                    LEFT JOIN ciclos_academicos ca ON ca.id = c.ciclo_id
                    ORDER BY c.asignatura ASC;
                    """
                )
                courses = fetch_all_as_dict(cur) or []

                cur.execute("SELECT * FROM tareas ORDER BY created_at DESC;")
                tareas = fetch_all_as_dict(cur) or []

                cur.execute("SELECT * FROM tarea_entregas ORDER BY entregado_en DESC NULLS LAST;")
                entregas = fetch_all_as_dict(cur) or []

                cur.execute("SELECT * FROM sesiones_clase ORDER BY hora_real_inicio DESC;")
                sesiones = fetch_all_as_dict(cur) or []

                cur.execute("SELECT * FROM asistencia_alumnos;")
                asistencias = fetch_all_as_dict(cur) or []

                cur.execute("SELECT * FROM incidents ORDER BY created_at DESC;")
                incidents = fetch_all_as_dict(cur) or []

                cur.execute("SELECT * FROM matriculas ORDER BY created_at DESC;")
                matriculas = fetch_all_as_dict(cur) or []

                return {
                    "config": config,
                    "ciclos": ciclos,
                    "users": users,
                    "courses": courses,
                    "tareas": tareas,
                    "entregas": entregas,
                    "sesiones": sesiones,
                    "asistencias": asistencias,
                    "incidents": incidents,
                    "matriculas": matriculas,
                }
        except Exception as e:
            print(f"[Reportes Snapshot PG] Error: {e}")
        finally:
            conn.close()

    db = load_db()
    return {
        "config": db.get("configuracion") or dict(DEFAULT_CONFIG),
        "ciclos": db.get("ciclos_academicos") or db.get("ciclosAcademicos") or [],
        "users": db.get("users") or [],
        "courses": db.get("courses") or [],
        "tareas": db.get("tareas") or [],
        "entregas": db.get("tareaEntregas") or [],
        "sesiones": db.get("sesionesClase") or [],
        "asistencias": db.get("asistenciaAlumnos") or [],
        "incidents": db.get("incidents") or [],
        "matriculas": db.get("matriculas") or [],
    }


def _resolve_enrolled_students_for_course(
    curso_id: str,
    users: List[Dict[str, Any]],
    matriculas: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    users_by_mail = {
        str(u.get("correo") or "").strip().lower(): u
        for u in users
        if u.get("correo")
    }
    for u in users:
        if str(u.get("rol") or "").lower() != "estudiante":
            continue
        if curso_id in parse_course_list(u.get("cursosInscritos")):
            uid = str(u["id"])
            by_id[uid] = {
                "id": uid,
                "nombre": f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip() or u.get("correo") or uid,
                "dni": u.get("dni") or "—",
                "correo": u.get("correo") or "",
                "carrera": u.get("carreraObjetivo") or "General",
            }
    for m in matriculas:
        if curso_id not in parse_course_list(m.get("cursosInscritos")):
            continue
        mail = str(m.get("correo") or "").strip().lower()
        u_ref = users_by_mail.get(mail) or {}
        uid = str(m.get("estudiante_id") or m.get("estudianteId") or u_ref.get("id") or "").strip()
        if uid and uid not in by_id:
            by_id[uid] = {
                "id": uid,
                "nombre": m.get("estudiante") or mail or uid,
                "dni": m.get("dni") or u_ref.get("dni") or "—",
                "correo": m.get("correo") or "",
                "carrera": u_ref.get("carreraObjetivo") or m.get("programa") or "General",
            }
    return sorted(by_id.values(), key=lambda x: x["nombre"].lower())


def _ciclo_label(ciclos: List[Dict[str, Any]], ciclo_id: Optional[str]) -> str:
    if not ciclo_id:
        return "Todos los Ciclos"
    found = next((c for c in ciclos if str(c.get("id")) == str(ciclo_id)), None)
    return found.get("nombre") if found else str(ciclo_id)


def _user_display(u: Dict[str, Any]) -> str:
    full = f"{u.get('nombres', '')} {u.get('apellidos', '')}".strip()
    rol = str(u.get("rol") or "").capitalize()
    return f"{full or u.get('correo') or 'Usuario'} ({rol})"


# --- 0. RESUMEN EJECUTIVO EN TIEMPO REAL PARA ADMIN LAYOUT Y DASHBOARD ---

@router.get("/resumen-ejecutivo", dependencies=[Depends(require_role(["admin"]))])
def obtener_resumen_ejecutivo(ciclo_id: Optional[str] = None):
    snap = _load_full_snapshot()
    ciclos = snap["ciclos"]
    vigente_obj = next((c for c in ciclos if c.get("es_vigente")), None) or (ciclos[0] if ciclos else None)
    ciclo_vigente_nombre = (
        (vigente_obj or {}).get("nombre")
        or snap["config"].get("ciclo_vigente")
        or "Semestral San Marcos 2026-II"
    )

    users = snap["users"]
    estudiantes = [u for u in users if str(u.get("rol") or "").lower() == "estudiante" and u.get("estado", "Activo") == "Activo"]
    docentes = [u for u in users if str(u.get("rol") or "").lower() in ("docente", "profesor") and u.get("estado", "Activo") == "Activo"]

    courses_by_id = {str(c.get("id")): c for c in snap["courses"]}
    clases_en_vivo = []
    for s in snap["sesiones"]:
        if s.get("estado") == "EN_CLASE":
            cid = str(s.get("curso_id") or s.get("cursoId") or "")
            c_info = courses_by_id.get(cid) or {}
            if ciclo_id and str(s.get("ciclo_id") or c_info.get("ciclo_id") or "") != str(ciclo_id):
                continue
            clases_en_vivo.append({
                "sesionId": s.get("id"),
                "cursoId": cid,
                "asignatura": c_info.get("asignatura") or "Curso en vivo",
                "docente": s.get("docente_nombre") or c_info.get("docente") or "Docente",
                "horaProgramada": f"{s.get('hora_programada_inicio', '')} - {s.get('hora_programada_fin', '')}",
            })

    incidencias_pend = [
        {
            "id": inc.get("id"),
            "docente": inc.get("docente"),
            "tipo": inc.get("tipo"),
            "materia": inc.get("materia") or "",
            "fecha": inc.get("fechaPresentacion") or "",
        }
        for inc in snap["incidents"]
        if str(inc.get("estado") or "") == "Pendiente"
    ]

    mats = snap["matriculas"]
    if ciclo_id:
        mats = [m for m in mats if str(m.get("ciclo_id") or m.get("cicloId") or "") == str(ciclo_id)]
    recaudado = sum(float(m.get("montoPagado") or 0) for m in mats)
    pendiente_cobro = sum(float(m.get("saldoPendiente") or 0) for m in mats)
    morosos = [
        {
            "id": m.get("id"),
            "estudiante": m.get("estudiante"),
            "saldoPendiente": float(m.get("saldoPendiente") or 0),
            "estado": m.get("estado"),
        }
        for m in mats
        if float(m.get("saldoPendiente") or 0) > 0
    ]

    return {
        "cicloVigente": ciclo_vigente_nombre,
        "cicloVigenteId": (vigente_obj or {}).get("id"),
        "ciclos": ciclos,
        "kpis": {
            "estudiantesActivos": len(estudiantes),
            "docentesActivos": len(docentes),
            "clasesEnVivoCount": len(clases_en_vivo),
            "incidenciasPendientesCount": len(incidencias_pend),
            "recaudacionTotal": round(recaudado, 2),
            "saldoPendienteTotal": round(pendiente_cobro, 2),
            "cuentasConSaldoCount": len(morosos),
        },
        "clasesEnVivo": clases_en_vivo,
        "incidenciasPendientes": incidencias_pend[:6],
        "cuentasPorCobrar": morosos[:5],
    }


# --- 1. REPORTE DE EVALUACIONES Y RÉCORD DE NOTAS ---

@router.get("/notas")
def reporte_evaluaciones_y_notas(
    ciclo_id: Optional[str] = None,
    curso_id: Optional[str] = None,
    formato: str = Query("pdf", pattern="^(pdf|json)$"),
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    snap = _load_full_snapshot()
    is_docente = str(current_user.get("rol") or "").lower() == "docente"

    allowed_courses = snap["courses"]
    if is_docente:
        allowed_courses = [c for c in allowed_courses if _course_belongs_to_teacher(c, current_user)]
        if curso_id and not any(str(c.get("id")) == str(curso_id) for c in allowed_courses):
            raise HTTPException(status_code=403, detail="No tienes autorización sobre el curso solicitado.")

    if ciclo_id:
        allowed_courses = [
            c for c in allowed_courses
            if str(c.get("ciclo_id") or c.get("cicloId") or "") == str(ciclo_id)
        ]
    if curso_id:
        allowed_courses = [c for c in allowed_courses if str(c.get("id")) == str(curso_id)]

    courses_by_id = {str(c.get("id")): c for c in allowed_courses}
    tareas_filtradas = [
        t for t in snap["tareas"]
        if str(t.get("curso_id") or t.get("cursoId") or "") in courses_by_id
    ]

    entregas_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in snap["entregas"]:
        tid = str(e.get("tarea_id") or e.get("tareaId") or "")
        eid = str(e.get("estudiante_id") or e.get("estudianteId") or "")
        if tid and eid:
            entregas_map[(tid, eid)] = e

    filas: List[Dict[str, Any]] = []
    notas_registradas: List[float] = []
    entregas_recibidas = 0

    for t in tareas_filtradas:
        tid = str(t.get("id"))
        cid = str(t.get("curso_id") or t.get("cursoId") or "")
        c_obj = courses_by_id.get(cid) or {}
        alumnos = _resolve_enrolled_students_for_course(cid, snap["users"], snap["matriculas"])

        # Incluir también entregas registradas por alumnos históricos de esa tarea
        seen_ids = {a["id"] for a in alumnos}
        for (k_tid, k_eid), ent in entregas_map.items():
            if k_tid == tid and k_eid not in seen_ids:
                alumnos.append({
                    "id": k_eid,
                    "nombre": ent.get("estudiante_nombre") or ent.get("estudianteNombre") or k_eid,
                    "dni": "—",
                    "correo": ent.get("estudiante_correo") or "",
                })

        for al in alumnos:
            ent = entregas_map.get((tid, al["id"]))
            estado = ent.get("estado") if ent else "Sin entregar"
            if ent and (ent.get("archivo_url") or ent.get("archivoUrl") or ent.get("archivo_nombre") or ent.get("archivoNombre")):
                entregas_recibidas += 1
            nota_raw = ent.get("nota") if ent else None
            nota_num = float(nota_raw) if nota_raw is not None else None
            if nota_num is not None:
                notas_registradas.append(nota_num)
            condicion = (
                "Aprobado" if (nota_num is not None and nota_num >= 11.0)
                else ("Desaprobado" if nota_num is not None else "Pendiente")
            )
            filas.append({
                "curso": c_obj.get("asignatura") or t.get("curso") or cid,
                "evaluacion": t.get("titulo") or tid,
                "estudiante": al["nombre"],
                "dni": al.get("dni") or "—",
                "estado": estado,
                "nota": f"{nota_num:.2f}" if nota_num is not None else "—",
                "condicion": condicion,
                "retroalimentacion": (ent.get("comentario_docente") or ent.get("comentarioDocente") or "—") if ent else "—",
            })

    promedio_gral = round(sum(notas_registradas) / len(notas_registradas), 2) if notas_registradas else 0.0
    aprobados = sum(1 for n in notas_registradas if n >= 11.0)
    tasa_aprob = f"{round((aprobados / len(notas_registradas)) * 100, 1)}%" if notas_registradas else "0.0%"

    curso_label = (
        courses_by_id[str(curso_id)].get("asignatura")
        if curso_id and str(curso_id) in courses_by_id
        else "Todos los Cursos Autorizados"
    )
    kpis = [
        {"label": "Evaluaciones / Tareas", "value": str(len(tareas_filtradas))},
        {"label": "Entregas Recibidas", "value": f"{entregas_recibidas} / {len(filas)}"},
        {"label": "Promedio Vigesimal", "value": f"{promedio_gral:.2f} / 20"},
        {"label": "Tasa de Aprobación", "value": tasa_aprob},
    ]
    columnas = [
        {"key": "curso", "label": "ASIGNATURA", "weight": 1.35},
        {"key": "evaluacion", "label": "EVALUACIÓN / TAREA", "weight": 1.65},
        {"key": "estudiante", "label": "ESTUDIANTE", "weight": 1.45},
        {"key": "dni", "label": "DNI", "weight": 0.70, "align": "center"},
        {"key": "estado", "label": "ESTADO", "weight": 0.85, "align": "center"},
        {"key": "nota", "label": "NOTA (0-20)", "weight": 0.75, "align": "right"},
        {"key": "condicion", "label": "CONDICIÓN", "weight": 0.85, "align": "center"},
        {"key": "retroalimentacion", "label": "OBSERVACIÓN / RETROALIMENTACIÓN", "weight": 1.80},
    ]
    report_code = f"REP-NOT-{int(time.time()) % 1000000:06d}"

    return _respond_report(
        formato=formato,
        filename_base="acta_oficial_evaluaciones_y_notas",
        config=snap["config"],
        report_code=report_code,
        titulo="ACTA CONSOLIDADA DE EVALUACIONES Y RÉCORD DE NOTAS",
        subtitulo="Registro oficial en escala vigesimal (0 a 20) de prácticas, talleres y tareas calificadas.",
        parametros=[
            ("Ciclo Académico", _ciclo_label(snap["ciclos"], ciclo_id)),
            ("Asignatura", curso_label),
        ],
        emitido_por=_user_display(current_user),
        kpis=kpis,
        columnas=columnas,
        filas=filas,
        use_landscape=True,
        firmante_secundario="Cátedra / Control de Evaluaciones",
    )


# --- 2. KARDEX / RÉCORD ACADÉMICO INDIVIDUAL DE ALUMNO ---

@router.get("/kardex-alumno/{estudiante_id}")
def reporte_kardex_alumno(
    estudiante_id: str,
    ciclo_id: Optional[str] = None,
    formato: str = Query("pdf", pattern="^(pdf|json)$"),
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    snap = _load_full_snapshot()
    student = next((u for u in snap["users"] if str(u.get("id")) == str(estudiante_id)), None)
    if not student:
        raise HTTPException(status_code=404, detail="No se encontró el estudiante solicitado.")

    is_docente = str(current_user.get("rol") or "").lower() == "docente"
    est_mail = str(student.get("correo") or "").strip().lower()
    student_course_ids = set(parse_course_list(student.get("cursosInscritos")))
    for m in snap["matriculas"]:
        if str(m.get("estudiante_id") or "") == str(estudiante_id) or (
            est_mail and str(m.get("correo") or "").strip().lower() == est_mail
        ):
            student_course_ids.update(parse_course_list(m.get("cursosInscritos")))

    # También incluir cursos donde tenga entregas o asistencias históricas registradas
    for e in snap["entregas"]:
        if str(e.get("estudiante_id") or e.get("estudianteId") or "") == str(estudiante_id):
            cid_e = str(e.get("curso_id") or e.get("cursoId") or "")
            if cid_e:
                student_course_ids.add(cid_e)

    candidate_courses = [c for c in snap["courses"] if str(c.get("id")) in student_course_ids]
    if is_docente:
        candidate_courses = [c for c in candidate_courses if _course_belongs_to_teacher(c, current_user)]
        if not candidate_courses:
            raise HTTPException(
                status_code=403,
                detail="Este estudiante no figura matriculado en ninguna de tus asignaturas.",
            )

    if ciclo_id:
        candidate_courses = [
            c for c in candidate_courses
            if str(c.get("ciclo_id") or c.get("cicloId") or "") == str(ciclo_id)
        ]

    resumen_cursos: List[Dict[str, Any]] = []
    detalle_evaluaciones: List[Dict[str, Any]] = []
    all_grades: List[float] = []
    asistencia_ratios: List[float] = []

    for c in candidate_courses:
        cid = str(c.get("id"))
        sesiones_curso = [
            s for s in snap["sesiones"]
            if str(s.get("curso_id") or s.get("cursoId") or "") == cid and s.get("estado") == "FINALIZADA"
        ]
        sesion_ids = {str(s.get("id")) for s in sesiones_curso}
        mis_asist = [
            a for a in snap["asistencias"]
            if str(a.get("sesion_id") or "") in sesion_ids
            and str(a.get("estudiante_id") or "") == str(estudiante_id)
            and a.get("estado") in ("Presente", "Tardanza", "Justificado")
        ]
        pct_asist = round((len(mis_asist) / len(sesiones_curso)) * 100, 1) if sesiones_curso else 100.0
        if sesiones_curso:
            asistencia_ratios.append(pct_asist)

        tareas_curso = [t for t in snap["tareas"] if str(t.get("curso_id") or t.get("cursoId") or "") == cid]
        mis_entregas_curso = [
            e for e in snap["entregas"]
            if str(e.get("curso_id") or e.get("cursoId") or "") == cid
            and str(e.get("estudiante_id") or e.get("estudianteId") or "") == str(estudiante_id)
        ]
        ent_by_tid = {str(e.get("tarea_id") or e.get("tareaId")): e for e in mis_entregas_curso}

        notas_curso: List[float] = []
        entregadas_count = 0
        for t in tareas_curso:
            tid = str(t.get("id"))
            ent = ent_by_tid.get(tid)
            if ent and (ent.get("archivo_url") or ent.get("archivoUrl") or ent.get("archivo_nombre") or ent.get("archivoNombre")):
                entregadas_count += 1
            n_val = float(ent["nota"]) if (ent and ent.get("nota") is not None) else None
            if n_val is not None:
                notas_curso.append(n_val)
                all_grades.append(n_val)
            detalle_evaluaciones.append({
                "asignatura": c.get("asignatura") or cid,
                "evaluacion": t.get("titulo") or tid,
                "estado": ent.get("estado") if ent else "Sin entregar",
                "nota": f"{n_val:.2f}" if n_val is not None else "—",
                "comentario": (ent.get("comentario_docente") or ent.get("comentarioDocente") or "—") if ent else "—",
            })

        prom_c = round(sum(notas_curso) / len(notas_curso), 2) if notas_curso else None
        resumen_cursos.append({
            "codigo": cid,
            "asignatura": c.get("asignatura") or cid,
            "docente": c.get("docente") or "Por asignar",
            "ciclo": c.get("ciclo_nombre") or _ciclo_label(snap["ciclos"], c.get("ciclo_id")),
            "asistencia": f"{pct_asist:.0f}% ({len(mis_asist)}/{len(sesiones_curso)})" if sesiones_curso else "Sin sesiones",
            "entregas": f"{entregadas_count} / {len(tareas_curso)}",
            "promedio": f"{prom_c:.2f}" if prom_c is not None else "S/N",
            "situacion": (
                "Destacado" if (prom_c is not None and prom_c >= 16)
                else ("Regular" if (prom_c is not None and prom_c >= 11) else ("En Riesgo" if prom_c is not None else "En Curso"))
            ),
        })

    prom_pond = round(sum(all_grades) / len(all_grades), 2) if all_grades else 0.0
    asist_global = round(sum(asistencia_ratios) / len(asistencia_ratios), 1) if asistencia_ratios else 100.0
    est_nombre = f"{student.get('nombres', '')} {student.get('apellidos', '')}".strip() or est_mail

    kpis = [
        {"label": "Cursos Evaluados", "value": str(len(resumen_cursos))},
        {"label": "Promedio General", "value": f"{prom_pond:.2f} / 20" if all_grades else "Sin notas"},
        {"label": "Asistencia Global", "value": f"{asist_global:.1f}%"},
        {"label": "Evaluaciones Calificadas", "value": str(len(all_grades))},
    ]
    columnas = [
        {"key": "codigo", "label": "CÓDIGO", "weight": 0.75, "align": "center"},
        {"key": "asignatura", "label": "ASIGNATURA", "weight": 1.70},
        {"key": "docente", "label": "DOCENTE TITULAR", "weight": 1.45},
        {"key": "ciclo", "label": "CICLO", "weight": 1.35},
        {"key": "asistencia", "label": "ASISTENCIA", "weight": 1.00, "align": "center"},
        {"key": "entregas", "label": "TAREAS", "weight": 0.75, "align": "center"},
        {"key": "promedio", "label": "PROMEDIO", "weight": 0.75, "align": "right"},
        {"key": "situacion", "label": "SITUACIÓN", "weight": 0.85, "align": "center"},
    ]
    extra = {
        "titulo": "Historial Detallado de Evaluaciones y Retroalimentación Docente",
        "columnas": [
            {"key": "asignatura", "label": "ASIGNATURA", "weight": 1.4},
            {"key": "evaluacion", "label": "PRÁCTICA / TAREA", "weight": 1.9},
            {"key": "estado", "label": "ESTADO", "weight": 0.9, "align": "center"},
            {"key": "nota", "label": "NOTA (0-20)", "weight": 0.8, "align": "right"},
            {"key": "comentario", "label": "RETROALIMENTACIÓN DEL DOCENTE", "weight": 2.4},
        ],
        "filas": detalle_evaluaciones,
    }
    report_code = f"REP-KDX-{int(time.time()) % 1000000:06d}"

    return _respond_report(
        formato=formato,
        filename_base=f"kardex_academico_{estudiante_id}",
        config=snap["config"],
        report_code=report_code,
        titulo=f"KARDEX Y RÉCORD ACADÉMICO INDIVIDUAL: {est_nombre.upper()}",
        subtitulo="Ficha oficial de rendimiento vigesimal, cumplimiento de entregas y asistencia sincrónica.",
        parametros=[
            ("Estudiante", est_nombre),
            ("DNI / Correo", f"{student.get('dni') or '—'} • {est_mail}"),
            ("Carrera Objetivo", student.get("carreraObjetivo") or "Preuniversitario"),
            ("Periodo", _ciclo_label(snap["ciclos"], ciclo_id)),
            ("Alcance", "Asignaturas del Docente" if is_docente else "Récord Institucional Completo"),
        ],
        emitido_por=_user_display(current_user),
        kpis=kpis,
        columnas=columnas,
        filas=resumen_cursos,
        extra_section=extra,
        use_landscape=True,
        firmante_secundario="Tutoría y Control Académico",
    )


# --- 3. REPORTE DE ASISTENCIA ESTUDIANTIL ---

@router.get("/asistencia")
def reporte_asistencia_estudiantil(
    ciclo_id: Optional[str] = None,
    curso_id: Optional[str] = None,
    formato: str = Query("pdf", pattern="^(pdf|json)$"),
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    snap = _load_full_snapshot()
    is_docente = str(current_user.get("rol") or "").lower() == "docente"

    allowed_courses = snap["courses"]
    if is_docente:
        allowed_courses = [c for c in allowed_courses if _course_belongs_to_teacher(c, current_user)]
        if curso_id and not any(str(c.get("id")) == str(curso_id) for c in allowed_courses):
            raise HTTPException(status_code=403, detail="No tienes autorización sobre el curso solicitado.")

    if ciclo_id:
        allowed_courses = [
            c for c in allowed_courses
            if str(c.get("ciclo_id") or c.get("cicloId") or "") == str(ciclo_id)
        ]
    if curso_id:
        allowed_courses = [c for c in allowed_courses if str(c.get("id")) == str(curso_id)]

    filas: List[Dict[str, Any]] = []
    pcts: List[float] = []
    total_sesiones_dictadas = 0
    en_riesgo = 0

    for c in allowed_courses:
        cid = str(c.get("id"))
        sesiones_c = [
            s for s in snap["sesiones"]
            if str(s.get("curso_id") or s.get("cursoId") or "") == cid and s.get("estado") == "FINALIZADA"
        ]
        total_sesiones_dictadas += len(sesiones_c)
        s_ids = {str(s.get("id")) for s in sesiones_c}
        asist_c = [a for a in snap["asistencias"] if str(a.get("sesion_id") or "") in s_ids]

        alumnos = _resolve_enrolled_students_for_course(cid, snap["users"], snap["matriculas"])
        for al in alumnos:
            mis = [a for a in asist_c if str(a.get("estudiante_id") or "") == al["id"]]
            pres = sum(1 for a in mis if a.get("estado") in ("Presente", "Justificado"))
            tard = sum(1 for a in mis if a.get("estado") == "Tardanza")
            aus = max(0, len(sesiones_c) - pres - tard) if sesiones_c else 0
            min_perm = sum(int(a.get("minutos_permanencia") or 0) for a in mis)
            pct = round(((pres + tard) / len(sesiones_c)) * 100, 1) if sesiones_c else 100.0
            if sesiones_c:
                pcts.append(pct)
                if pct < 70.0:
                    en_riesgo += 1

            filas.append({
                "curso": c.get("asignatura") or cid,
                "estudiante": al["nombre"],
                "dni": al["dni"],
                "sesiones": str(len(sesiones_c)),
                "presentes": str(pres),
                "tardanzas": str(tard),
                "inasistencias": str(aus),
                "permanenciaMin": f"{min_perm} min",
                "porcentaje": f"{pct:.1f}%" if sesiones_c else "Sin sesiones",
                "estadoAsistencia": "En Riesgo (<70%)" if (sesiones_c and pct < 70.0) else "Regular",
            })

    prom_asist = f"{round(sum(pcts) / len(pcts), 1)}%" if pcts else "100.0%"
    kpis = [
        {"label": "Cursos Analizados", "value": str(len(allowed_courses))},
        {"label": "Sesiones en Vivo Cerradas", "value": str(total_sesiones_dictadas)},
        {"label": "Asistencia Promedio", "value": prom_asist},
        {"label": "Alumnos en Riesgo (<70%)", "value": str(en_riesgo)},
    ]
    columnas = [
        {"key": "curso", "label": "ASIGNATURA", "weight": 1.60},
        {"key": "estudiante", "label": "ESTUDIANTE", "weight": 1.65},
        {"key": "dni", "label": "DNI", "weight": 0.75, "align": "center"},
        {"key": "sesiones", "label": "SESIONES", "weight": 0.70, "align": "center"},
        {"key": "presentes", "label": "ASISTIÓ", "weight": 0.70, "align": "center"},
        {"key": "tardanzas", "label": "TARDANZA", "weight": 0.75, "align": "center"},
        {"key": "inasistencias", "label": "FALTAS", "weight": 0.65, "align": "center"},
        {"key": "permanenciaMin", "label": "PERMANENCIA MEET", "weight": 0.95, "align": "right"},
        {"key": "porcentaje", "label": "% ASIST.", "weight": 0.75, "align": "right"},
        {"key": "estadoAsistencia", "label": "CONDICIÓN", "weight": 0.95, "align": "center"},
    ]
    report_code = f"REP-ASI-{int(time.time()) % 1000000:06d}"

    return _respond_report(
        formato=formato,
        filename_base="acta_asistencia_estudiantil",
        config=snap["config"],
        report_code=report_code,
        titulo="ACTA DE CONTROL DE ASISTENCIA Y PERMANENCIA VIRTUAL ESTUDIANTIL",
        subtitulo="Consolidado auditado de ingresos a Google Meet, latidos de permanencia y pase de lista híbrido.",
        parametros=[
            ("Ciclo Académico", _ciclo_label(snap["ciclos"], ciclo_id)),
            ("Curso Filtrado", curso_id or "Todos los Cursos Autorizados"),
        ],
        emitido_por=_user_display(current_user),
        kpis=kpis,
        columnas=columnas,
        filas=filas,
        use_landscape=True,
        firmante_secundario="Inspectoría y Control de Asistencia",
    )


# --- 4. RÉCORD DE DESEMPEÑO Y LIQUIDACIÓN DE PLANILLA DOCENTE ---

@router.get("/docentes")
def reporte_desempeno_y_planilla_docente(
    ciclo_id: Optional[str] = None,
    docente_id: Optional[str] = None,
    formato: str = Query("pdf", pattern="^(pdf|json)$"),
    current_user: dict = Depends(require_role(["admin", "docente"])),
):
    is_docente = str(current_user.get("rol") or "").lower() == "docente"
    if is_docente:
        my_id = str(current_user.get("id") or "")
        if docente_id and str(docente_id) != my_id:
            raise HTTPException(
                status_code=403,
                detail="Un docente solo puede consultar su propio récord de desempeño.",
            )
        docente_id = my_id

    snap = _load_full_snapshot()
    cfg = snap["config"]
    tarifa_default = float(cfg.get("tarifa_hora_docente") or 50.0)
    horas_desc_falta = float(cfg.get("horas_descuento_inasistencia") or 2.0)

    teachers = [
        u for u in snap["users"]
        if str(u.get("rol") or "").lower() in ("docente", "profesor", "profesora")
    ]
    if docente_id:
        teachers = [t for t in teachers if str(t.get("id")) == str(docente_id)]

    courses_pool = snap["courses"]
    if ciclo_id:
        courses_pool = [
            c for c in courses_pool
            if str(c.get("ciclo_id") or c.get("cicloId") or "") == str(ciclo_id)
        ]

    filas: List[Dict[str, Any]] = []
    sum_planilla_neta = 0.0
    sum_descuentos = 0.0
    sum_horas_reales = 0.0
    sum_sesiones = 0

    for t in teachers:
        tid = str(t.get("id"))
        t_name = f"{t.get('nombres', '')} {t.get('apellidos', '')}".strip() or t.get("correo") or tid
        mis_cursos = [c for c in courses_pool if _course_belongs_to_teacher(c, t)]
        if ciclo_id and not mis_cursos and not docente_id:
            continue

        horas_prog_mes = max(16.0, len(mis_cursos) * 16.0) if mis_cursos else 16.0
        salario_base = float(t.get("salario") or 0)
        monto_bruto = salario_base if salario_base > 0 else round(horas_prog_mes * tarifa_default, 2)
        tarifa_efectiva = round(monto_bruto / horas_prog_mes, 2) if horas_prog_mes > 0 else tarifa_default

        mis_sesiones = [
            s for s in snap["sesiones"]
            if str(s.get("docente_id") or "") == tid
            and s.get("estado") == "FINALIZADA"
            and (not ciclo_id or str(s.get("ciclo_id") or "") == str(ciclo_id))
        ]
        min_dictados = sum(float(s.get("minutos_efectivos_dictados") or 0) for s in mis_sesiones)
        horas_dictadas_meet = round(min_dictados / 60.0, 2)

        mis_inc = [
            inc for inc in snap["incidents"]
            if str(inc.get("docenteid") or inc.get("docenteId") or "") == tid
            or (t.get("dni") and str(inc.get("dni") or "") == str(t.get("dni")))
        ]
        min_tardanza = 0
        horas_descontadas = 0.0
        justificadas = 0
        no_justificadas = 0

        for inc in mis_inc:
            tipo_l = str(inc.get("tipo") or "").lower()
            est_inc = str(inc.get("estado") or "Pendiente")
            mins_inc = int(inc.get("minutos_tardanza") or 0)
            if mins_inc <= 0 and "tardanza" in tipo_l:
                mins_inc = 20 if "+20" in tipo_l else 15

            if est_inc == "Aprobado":
                justificadas += 1
                min_tardanza += mins_inc
            else:
                if "tardanza" in tipo_l:
                    no_justificadas += 1
                    min_tardanza += mins_inc
                    horas_descontadas += mins_inc / 60.0
                elif "inasistencia" in tipo_l:
                    no_justificadas += 1
                    horas_descontadas += horas_desc_falta

        descuento_pen = round(min(monto_bruto, horas_descontadas * tarifa_efectiva), 2)
        monto_neto = round(max(0.0, monto_bruto - descuento_pen), 2)
        estado_aud = (
            "Con Descuento" if descuento_pen > 0
            else ("Justificado (S/ 0)" if justificadas > 0 else "100% Conforme")
        )

        sum_planilla_neta += monto_neto
        sum_descuentos += descuento_pen
        sum_horas_reales += horas_dictadas_meet
        sum_sesiones += len(mis_sesiones)

        filas.append({
            "codigo": tid,
            "docente": t_name,
            "dni": t.get("dni") or "—",
            "cursos": ", ".join(c.get("asignatura") or "" for c in mis_cursos) or "Sin carga en ciclo",
            "sesionesMeet": str(len(mis_sesiones)),
            "horasMeet": f"{horas_dictadas_meet:.2f} h",
            "horasContrato": f"{horas_prog_mes:.0f} h",
            "tardanzasMin": f"{min_tardanza} min",
            "incidencias": f"{justificadas} Aprob. / {no_justificadas} Desc.",
            "montoBruto": f"S/ {monto_bruto:,.2f}",
            "descuento": f"- S/ {descuento_pen:,.2f}",
            "montoNeto": f"S/ {monto_neto:,.2f}",
            "estadoAuditoria": estado_aud,
        })

    kpis = [
        {"label": "Docentes Auditados", "value": str(len(filas))},
        {"label": "Sesiones Reales Meet", "value": f"{sum_sesiones} ({sum_horas_reales:.1f} h)"},
        {"label": "Descuentos Aplicados", "value": f"S/ {sum_descuentos:,.2f}"},
        {"label": "Planilla Neta Total", "value": f"S/ {sum_planilla_neta:,.2f}"},
    ]
    columnas = [
        {"key": "docente", "label": "DOCENTE", "weight": 1.45},
        {"key": "dni", "label": "DNI", "weight": 0.72, "align": "center"},
        {"key": "cursos", "label": "CÁTEDRAS ASIGNADAS", "weight": 1.65},
        {"key": "sesionesMeet", "label": "CLASES MEET", "weight": 0.68, "align": "center"},
        {"key": "horasMeet", "label": "HRS DICTADAS", "weight": 0.78, "align": "right"},
        {"key": "tardanzasMin", "label": "TARDANZA", "weight": 0.72, "align": "right"},
        {"key": "incidencias", "label": "INCIDENCIAS", "weight": 1.00, "align": "center"},
        {"key": "montoBruto", "label": "HABER BRUTO", "weight": 0.88, "align": "right"},
        {"key": "descuento", "label": "DESCUENTO", "weight": 0.82, "align": "right"},
        {"key": "montoNeto", "label": "NETO A PAGAR", "weight": 0.92, "align": "right"},
        {"key": "estadoAuditoria", "label": "AUDITORÍA", "weight": 0.95, "align": "center"},
    ]
    report_code = f"REP-DOC-{int(time.time()) % 1000000:06d}"

    return _respond_report(
        formato=formato,
        filename_base="liquidacion_y_record_docente",
        config=cfg,
        report_code=report_code,
        titulo="LIQUIDACIÓN DE NÓMINA Y RÉCORD DE AUDITORÍA DOCENTE",
        subtitulo="Cruce de sesiones reales dictadas en Google Meet, control de tolerancia (10 min), justificantes y haberes netos.",
        parametros=[
            ("Ciclo Académico", _ciclo_label(snap["ciclos"], ciclo_id)),
            ("Docente", filas[0]["docente"] if (docente_id and filas) else "Plana Docente Completa"),
            ("Tarifa Base / Tolerancia", f"S/ {tarifa_default:.2f}/h • {cfg.get('tolerancia_tardanza_min', 10)} min gracia"),
        ],
        emitido_por=_user_display(current_user),
        kpis=kpis,
        columnas=columnas,
        filas=filas,
        use_landscape=True,
        firmante_secundario="Oficina de Recursos Humanos y Planillas",
    )


# --- 5. REPORTE DE MATRÍCULAS Y ESTADO FINANCIERO (SOLO ADMIN) ---

@router.get("/matriculas", dependencies=[Depends(require_role(["admin"]))])
def reporte_matriculas_y_recaudacion(
    ciclo_id: Optional[str] = None,
    estado_pago: Optional[str] = None,
    formato: str = Query("pdf", pattern="^(pdf|json)$"),
    current_user: dict = Depends(require_role(["admin"])),
):
    snap = _load_full_snapshot()
    mats = list(snap["matriculas"])
    if ciclo_id:
        mats = [m for m in mats if str(m.get("ciclo_id") or m.get("cicloId") or "") == str(ciclo_id)]
    if estado_pago and estado_pago.upper() != "TODOS":
        if estado_pago == "CON_DEUDA":
            mats = [m for m in mats if float(m.get("saldoPendiente") or 0) > 0]
        else:
            mats = [m for m in mats if str(m.get("estado") or "").lower() == estado_pago.lower()]

    sum_facturado = 0.0
    sum_pagado = 0.0
    sum_pendiente = 0.0
    filas: List[Dict[str, Any]] = []

    for m in mats:
        tot = float(m.get("montoTotal") or 0)
        pag = float(m.get("montoPagado") or 0)
        pen = float(m.get("saldoPendiente") or 0)
        sum_facturado += tot
        sum_pagado += pag
        sum_pendiente += pen

        filas.append({
            "codigo": m.get("id") or "—",
            "estudiante": m.get("estudiante") or "—",
            "dni": m.get("dni") or "—",
            "programa": m.get("programa") or "Preuniversitario",
            "ciclo": m.get("ciclo") or _ciclo_label(snap["ciclos"], m.get("ciclo_id")),
            "modalidad": m.get("modalidadPago") or "Contado",
            "montoTotal": f"S/ {tot:,.2f}",
            "montoPagado": f"S/ {pag:,.2f}",
            "saldoPendiente": f"S/ {pen:,.2f}",
            "estado": m.get("estado") or "Al día",
        })

    kpis = [
        {"label": "Matrículas Registradas", "value": str(len(filas))},
        {"label": "Total Facturado", "value": f"S/ {sum_facturado:,.2f}"},
        {"label": "Total Recaudado", "value": f"S/ {sum_pagado:,.2f}"},
        {"label": "Cartera por Cobrar", "value": f"S/ {sum_pendiente:,.2f}"},
    ]
    columnas = [
        {"key": "codigo", "label": "MATRÍCULA", "weight": 0.90, "align": "center"},
        {"key": "estudiante", "label": "POSTULANTE MATRICULADO", "weight": 1.60},
        {"key": "dni", "label": "DNI", "weight": 0.72, "align": "center"},
        {"key": "programa", "label": "ÁREA / PROGRAMA", "weight": 1.55},
        {"key": "ciclo", "label": "CICLO", "weight": 1.20},
        {"key": "modalidad", "label": "MODALIDAD", "weight": 0.95},
        {"key": "montoTotal", "label": "FACTURADO", "weight": 0.85, "align": "right"},
        {"key": "montoPagado", "label": "ABONADO", "weight": 0.85, "align": "right"},
        {"key": "saldoPendiente", "label": "SALDO", "weight": 0.85, "align": "right"},
        {"key": "estado", "label": "ESTADO", "weight": 0.85, "align": "center"},
    ]
    report_code = f"REP-MAT-{int(time.time()) % 1000000:06d}"

    return _respond_report(
        formato=formato,
        filename_base="padron_matriculas_y_recaudacion",
        config=snap["config"],
        report_code=report_code,
        titulo="PADRÓN OFICIAL DE MATRÍCULAS Y ESTADO DE RECAUDACIÓN",
        subtitulo="Control financiero de pensiones, pagos acreditados y cuentas por cobrar por periodo académico.",
        parametros=[
            ("Ciclo Académico", _ciclo_label(snap["ciclos"], ciclo_id)),
            ("Filtro Financiero", estado_pago or "Todas las Matrículas"),
        ],
        emitido_por=_user_display(current_user),
        kpis=kpis,
        columnas=columnas,
        filas=filas,
        use_landscape=True,
        firmante_secundario="Tesorería y Administración Financiera",
    )
