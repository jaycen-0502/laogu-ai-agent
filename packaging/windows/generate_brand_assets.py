from __future__ import annotations

from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = PROJECT_ROOT / "desktop" / "assets"
SVG_PATH = ASSET_DIR / "laogu-control-center-logo.svg"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_image(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Unable to encode PNG icon data")
    return bytes(data)


def write_ico(images: list[tuple[int, bytes]], path: Path) -> None:
    header_size = 6 + (16 * len(images))
    offset = header_size
    entries: list[bytes] = []
    payloads: list[bytes] = []
    for size, payload in images:
        dimension = 0 if size >= 256 else size
        entries.append(
            struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(payload), offset)
        )
        payloads.append(payload)
        offset += len(payload)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(payloads))


def main() -> None:
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG logo: {SVG_PATH}")

    rendered = [(size, render_image(renderer, size)) for size in ICON_SIZES]
    rendered[-1][1].save(str(ASSET_DIR / "laogu-control-center-256.png"), "PNG")
    rendered[2][1].save(str(ASSET_DIR / "laogu-control-center-32.png"), "PNG")
    write_ico(
        [(size, png_bytes(image)) for size, image in rendered],
        ASSET_DIR / "laogu-control-center.ico",
    )


if __name__ == "__main__":
    main()
