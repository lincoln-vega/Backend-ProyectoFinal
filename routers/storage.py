import os
import re
import time
from typing import Dict
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from auth import get_current_user

router = APIRouter(prefix="/storage", tags=["Supabase Storage"])

# Límites estrictos alineados al plan gratuito de Supabase (1 GB total / 50 MB máx. por archivo)
BUCKET_MAX_BYTES: Dict[str, int] = {
    "tareas-entregas": 15 * 1024 * 1024,        # 15 MB máx por entrega académica
    "materiales-clase": 15 * 1024 * 1024,       # 15 MB máx por separata/pizarra/guía docente
    "vouchers-pago": 5 * 1024 * 1024,           # 5 MB máx por comprobante Yape/Plin/Banco
    "justificantes-docentes": 5 * 1024 * 1024,  # 5 MB máx por sustento médico/laboral
}

ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".zip", ".png", ".jpg", ".jpeg", ".webp",
}


def _sanitize_filename(filename: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "archivo")
    return clean[-80:] if len(clean) > 80 else clean


def _storage_config():
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase Storage requiere SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en el backend.",
        )
    return supabase_url, service_key


class SignedUrlRequest(BaseModel):
    bucket: str
    path: str
    expiresIn: int = 3600


@router.post("/upload/{bucket}", status_code=status.HTTP_201_CREATED)
async def subir_archivo_storage(
    bucket: str,
    file: UploadFile = File(...),
    folder: str = Form(default="general"),
    current_user: dict = Depends(get_current_user),
):
    if bucket not in BUCKET_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bucket '{bucket}' no permitido.",
        )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato '{ext}' no permitido. Usa PDF, DOCX, ZIP, PNG o JPG.",
        )

    content = await file.read()
    max_bytes = BUCKET_MAX_BYTES[bucket]
    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío.")
    if len(content) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el límite permitido de {max_mb} MB para '{bucket}'.",
        )

    supabase_url, service_key = _storage_config()
    safe_folder = re.sub(r"[^A-Za-z0-9/_-]", "", folder).strip("/") or "general"
    user_id = re.sub(r"[^A-Za-z0-9_-]", "", str(current_user.get("id") or "USR"))
    safe_name = _sanitize_filename(file.filename or f"archivo{ext}")
    object_path = f"{safe_folder}/{user_id}_{int(time.time())}_{safe_name}"

    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": file.content_type or "application/octet-stream",
        "x-upsert": "true",
    }

    try:
        resp = httpx.post(
            f"{supabase_url}/storage/v1/object/{bucket}/{object_path}",
            headers=headers,
            content=content,
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo conectar con Supabase Storage.",
        ) from exc

    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supabase Storage rechazó la subida ({resp.status_code}): {resp.text}",
        )

    # Generar URL firmada de larga duración (7 días) o URL autenticada
    signed_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{object_path}"
    try:
        sign_resp = httpx.post(
            f"{supabase_url}/storage/v1/object/sign/{bucket}/{object_path}",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
            json={"expiresIn": 604800},
            timeout=10.0,
        )
        if sign_resp.is_success:
            signed_suffix = sign_resp.json().get("signedURL") or sign_resp.json().get("signedUrl")
            if signed_suffix:
                signed_url = f"{supabase_url}/storage/v1{signed_suffix}" if signed_suffix.startswith("/") else signed_suffix
    except httpx.RequestError:
        pass

    return {
        "bucket": bucket,
        "path": object_path,
        "url": signed_url,
        "fileName": file.filename,
        "sizeBytes": len(content),
        "mimeType": file.content_type,
    }


@router.post("/signed-url")
def obtener_url_firmada(
    req: SignedUrlRequest,
    _user: dict = Depends(get_current_user),
):
    if req.bucket not in BUCKET_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bucket no válido.")

    supabase_url, service_key = _storage_config()
    try:
        resp = httpx.post(
            f"{supabase_url}/storage/v1/object/sign/{req.bucket}/{req.path.lstrip('/')}",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
            json={"expiresIn": max(60, min(req.expiresIn, 604800))},
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo contactar con Supabase Storage.",
        ) from exc

    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró el archivo solicitado en Supabase Storage.",
        )

    suffix = resp.json().get("signedURL") or resp.json().get("signedUrl") or ""
    full_url = f"{supabase_url}/storage/v1{suffix}" if suffix.startswith("/") else suffix
    return {"url": full_url, "bucket": req.bucket, "path": req.path}
