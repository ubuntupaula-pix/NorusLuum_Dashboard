# -*- coding: utf-8 -*-
"""Genera la infografía de herramientas del dashboard (texto exacto, sin IA)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ANCHO, ALTO = 2400, 1350
FONDO = "#0B1626"
TARJETA = "#12233A"
BORDE = "#1E3A5F"
TEXTO = "#E6EDF3"
MUTED = "#9FB3C8"
TURQUESA = "#2DD4BF"
LIMA = "#A3E635"
AZUL = "#60A5FA"

FUENTES = Path("C:/Windows/Fonts")
BOLD = FUENTES / "segoeuib.ttf"
REG = FUENTES / "segoeui.ttf"


def fuente(ruta: Path, tamano: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ruta), tamano)


TITULO = fuente(BOLD, 66)
SUBTITULO = fuente(REG, 30)
CABECERA = fuente(BOLD, 36)
NOMBRE = fuente(BOLD, 24)
DESC = fuente(REG, 24)
PIE = fuente(REG, 22)

TARJETAS = [
    (
        "01", "BACKEND Y API", TURQUESA,
        [
            ("Python 3.12", "lógica del servidor y ETL"),
            ("FastAPI", "API REST y validación"),
            ("SQLAlchemy 2", "ORM del data warehouse"),
            ("Alembic", "migraciones del esquema"),
            ("Pydantic", "esquemas de entrada y salida"),
            ("Uvicorn", "servidor ASGI local"),
        ],
    ),
    (
        "02", "SEGURIDAD", LIMA,
        [
            ("PyJWT", "sesiones y tokens de embed"),
            ("bcrypt", "hash de contraseñas"),
            ("Fernet", "cifrado de datos personales"),
            ("CSRF y HttpOnly", "protección de sesión"),
            ("TOTP y OIDC", "MFA y acceso SSO"),
            ("Prometheus", "métricas HTTP y ETL"),
        ],
    ),
    (
        "03", "FRONTEND", AZUL,
        [
            ("HTML, CSS y JS", "interfaz del dashboard"),
            ("Chart.js", "gráficos de KPI"),
            ("PapaParse", "lectura de CSV y TSV"),
            ("SheetJS", "lectura de XLSX"),
            ("Web Crypto SHA-256", "integridad de archivos"),
            ("Fetch y postMessage", "API, fragmentos y embed"),
        ],
    ),
    (
        "04", "DATOS Y CALIDAD", TURQUESA,
        [
            ("openpyxl", "lectura de Excel en servidor"),
            ("python-dateutil", "parseo flexible de fechas"),
            ("python-multipart", "subida de archivos"),
            ("pytest", "65 pruebas automatizadas"),
            ("GitHub Actions", "integración continua"),
            ("python-docx", "guías Word para gerencia"),
        ],
    ),
    (
        "05", "INFRAESTRUCTURA Y FUENTES", LIMA,
        [
            ("Vercel", "hosting serverless y dominio"),
            ("Neon PostgreSQL", "base de datos en producción"),
            ("Docker Compose", "entorno local en contenedores"),
            ("Caddy", "HTTPS automático"),
            ("SQLite", "desarrollo y pruebas"),
            ("APIs públicas", "FX, SERNATUR, EA Pulse y Zürich"),
        ],
    ),
]


def dibujar_tarjeta(draw: ImageDraw.ImageDraw, x: int, y: int, ancho: int, alto: int,
                    numero: str, titulo: str, color: str, items: list[tuple[str, str]]) -> None:
    draw.rounded_rectangle(
        (x, y, x + ancho, y + alto), radius=22, fill=TARJETA, outline=BORDE, width=2
    )
    draw.rounded_rectangle((x, y, x + ancho, y + 9), radius=4, fill=color)

    draw.rounded_rectangle((x + 28, y + 32, x + 96, y + 84), radius=12, fill=color)
    bbox = draw.textbbox((0, 0), numero, font=CABECERA)
    draw.text(
        (x + 62 - (bbox[2] - bbox[0]) / 2, y + 43 - (bbox[3] - bbox[1]) / 2),
        numero, font=CABECERA, fill=FONDO,
    )
    draw.text((x + 116, y + 42), titulo, font=CABECERA, fill=TEXTO)

    y_item = y + 112
    for nombre, descripcion in items:
        draw.ellipse((x + 32, y_item + 9, x + 44, y_item + 21), fill=color)
        nx = x + 62
        draw.text((nx, y_item), nombre, font=NOMBRE, fill=color)
        ancho_nombre = draw.textlength(nombre, font=NOMBRE)
        draw.text((nx + ancho_nombre + 12, y_item + 1), "— " + descripcion, font=DESC, fill=MUTED)
        y_item += 63


def main() -> None:
    imagen = Image.new("RGB", (ANCHO, ALTO), FONDO)
    draw = ImageDraw.Draw(imagen)

    draw.rectangle((0, 0, ANCHO, 14), fill=TURQUESA)
    draw.text((72, 58), "NORUS · HERRAMIENTAS DEL DASHBOARD", font=TITULO, fill=TEXTO)
    draw.text((74, 145), "Qué hace cada una  ·  Stack real del proyecto", font=SUBTITULO, fill=MUTED)
    draw.line((72, 205, ANCHO - 72, 205), fill=BORDE, width=2)

    margen, separacion = 72, 40
    ancho_tarjeta = (ANCHO - 2 * margen - 2 * separacion) // 3
    alto_tarjeta = 500
    y_superior, y_inferior = 250, 790

    for indice, (numero, titulo, color, items) in enumerate(TARJETAS):
        if indice < 3:
            x = margen + indice * (ancho_tarjeta + separacion)
            y = y_superior
        else:
            x = (ANCHO - (2 * ancho_tarjeta + separacion)) // 2 + (indice - 3) * (ancho_tarjeta + separacion)
            y = y_inferior
        dibujar_tarjeta(draw, x, y, ancho_tarjeta, alto_tarjeta, numero, titulo, color, items)

    draw.text(
        (72, ALTO - 62),
        "Backend y seguridad  ·  Frontend y lectura de archivos  ·  Calidad, infraestructura y datos públicos",
        font=PIE, fill=MUTED,
    )

    salida = Path(__file__).resolve().parents[2] / "docs" / "HERRAMIENTAS_DASHBOARD.png"
    salida.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(salida, "PNG", optimize=True)
    print(salida)


if __name__ == "__main__":
    main()
