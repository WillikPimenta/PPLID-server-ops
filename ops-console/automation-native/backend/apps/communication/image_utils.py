import io
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB raw upload
MAX_STORED_BYTES = 500 * 1024  # ~500 KB after processing
MAX_WIDTH = 1920
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _validate_extension(name: str) -> None:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Formato não suportado. Use JPEG, PNG ou WebP."
        )


def process_uploaded_image(uploaded_file) -> ContentFile:
    """Validate, resize and compress an uploaded image for storage."""
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"Imagem excede o limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    _validate_extension(uploaded_file.name)

    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("Arquivo de imagem inválido.") from exc

    if image.format not in ALLOWED_FORMATS:
        raise ValidationError(
            "Formato não suportado. Use JPEG, PNG ou WebP."
        )

    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    elif image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    if width > MAX_WIDTH:
        new_height = int(height * (MAX_WIDTH / width))
        image = image.resize((MAX_WIDTH, new_height), Image.Resampling.LANCZOS)

    quality = 85
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", optimize=True, quality=quality)
    while buffer.tell() > MAX_STORED_BYTES and quality > 40:
        quality -= 10
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", optimize=True, quality=quality)

    buffer.seek(0)
    original_name = Path(uploaded_file.name).stem
    filename = f"{original_name[:80]}.jpg"
    return ContentFile(buffer.read(), name=filename)
