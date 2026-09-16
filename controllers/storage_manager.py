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
SKYPDV_INVOICE_FOLDER = f"{SKYPDV_PREFIX}/invoices"

# WebP conversion settings
WEBP_QUALITY = 82           # 0-100 — good balance between quality and size
WEBP_MAX_DIMENSION = 1920   # max width or height in pixels
MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # 12 MB raw input limit


def _public_url(key: str) -> str:
    base = R2_PUBLIC_URL.rstrip("/")
    clean_key = (key or "").lstrip("/")
    return f"{base}/{clean_key}"


def _convert_to_webp(data: bytes) -> bytes:
    """
    Validate the image, optionally downscale it so neither dimension
    exceeds WEBP_MAX_DIMENSION, then re-encode as WebP at WEBP_QUALITY.
    Returns the resulting WebP bytes — always smaller than PNG/JPEG of
    the same visual quality.
    """
    if not data:
        raise HTTPException(status_code=400, detail="Imagem vazia.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Imagem muito grande. Maximo permitido: 12MB.")

    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        # verify() closes the file; reopen for actual use
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Arquivo de imagem invalido ou corrompido.")

    fmt = (img.format or "").upper()
    if fmt not in {"JPEG", "JPG", "PNG", "WEBP", "GIF", "BMP"}:
        raise HTTPException(
            status_code=400,
            detail="Formato de imagem nao permitido. Use JPEG, PNG, WebP ou GIF.",
        )

    # Normalise colour mode — WebP needs RGB or RGBA
    if img.mode in ("P", "LA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        # Flatten transparency on white background
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Downscale if needed (preserves aspect ratio)
    w, h = img.size
    if w > WEBP_MAX_DIMENSION or h > WEBP_MAX_DIMENSION:
        img.thumbnail((WEBP_MAX_DIMENSION, WEBP_MAX_DIMENSION), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
    return out.getvalue()


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

    def upload_file(
        self,
        file: UploadFile,
        destination_folder: str,
        custom_filename: str | None = None,
    ) -> str:
        """
        Read the uploaded file, convert it to WebP, and store it in
        Cloudflare R2 under destination_folder/. Returns the public URL.
        """
        if not file:
            raise HTTPException(status_code=400, detail="No file sent")

        raw = file.file.read()

        # Convert to WebP (validates + resizes + re-encodes)
        webp_data = _convert_to_webp(raw)

        # Always store as .webp
        base_name = custom_filename or uuid.uuid4().hex
        # Strip any existing extension and force .webp
        if "." in base_name:
            base_name = base_name.rsplit(".", 1)[0]
        filename = f"{base_name}.webp"

        folder = (destination_folder or "").strip("/")
        key = f"{folder}/{filename}" if folder else filename

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=webp_data,
                ContentType="image/webp",
            )
            return _public_url(key)
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=500, detail=f"Erro ao salvar arquivo: {str(exc)}") from exc
