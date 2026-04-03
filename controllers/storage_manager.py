import io
import os
import uuid

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

try:
    import boto3
    from botocore.config import Config as BotoConfig

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "3e8202354c98be490ac6e0897cd0b332")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "320d7f80a78ce643773debbcd62bbe8d")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "c00f94b3757d4d268ad09843cd46ce9a01030d7245616a33b52038f8cd77f6ab")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "bluesparkmz")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "https://storage.bluesparkmz.com")
R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

R2_CONFIGURED = all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME])

SKYPDV_PREFIX = "skypdv"
SKYPDV_PRODUCT_FOLDER = f"{SKYPDV_PREFIX}/products"


def _public_url(key: str) -> str:
    base = R2_PUBLIC_URL.rstrip("/")
    clean_key = (key or "").lstrip("/")
    return f"{base}/{clean_key}"


class StorageManager:
    def __init__(self, bucket_name: str | None = None):
        if not BOTO3_AVAILABLE:
            raise RuntimeError("boto3 is not installed.")
        if not R2_CONFIGURED:
            raise RuntimeError("Cloudflare R2 is not configured properly.")

        self.bucket_name = bucket_name or R2_BUCKET_NAME
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )

    def _sanitize_image_bytes(self, data: bytes) -> tuple[bytes, str, str]:
        if not data:
            raise HTTPException(status_code=400, detail="Imagem vazia.")
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Imagem muito grande. Maximo permitido: 12MB.")

        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
            img = Image.open(io.BytesIO(data))
            img.load()
        except (UnidentifiedImageError, OSError):
            raise HTTPException(status_code=400, detail="Arquivo de imagem invalido ou corrompido.")

        fmt = (img.format or "").upper()
        if fmt not in {"JPEG", "JPG", "PNG", "WEBP", "GIF"}:
            raise HTTPException(status_code=400, detail="Formato de imagem nao permitido.")

        ext_map = {"JPEG": "jpg", "JPG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}
        ct_map = {
            "JPEG": "image/jpeg",
            "JPG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
            "GIF": "image/gif",
        }
        return data, ext_map[fmt], ct_map[fmt]

    def upload_file(
        self,
        file: UploadFile,
        destination_folder: str,
        custom_filename: str | None = None,
    ) -> str:
        if not file:
            raise HTTPException(status_code=400, detail="No file sent")

        data = file.file.read()
        data, sanitized_ext, content_type = self._sanitize_image_bytes(data)
        filename = custom_filename or f"{uuid.uuid4().hex}.{sanitized_ext}"
        folder = (destination_folder or "").strip("/")
        key = f"{folder}/{filename}" if folder else filename

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            return _public_url(key)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Erro ao salvar arquivo: {str(exc)}") from exc
