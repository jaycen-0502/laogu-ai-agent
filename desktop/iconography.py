from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_PATHS = {
    "play": '<path d="M9 6l9 6-9 6z" fill="{color}" stroke="none"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2" fill="{color}" stroke="none"/>',
    "refresh": '<path d="M19 8v5h-5M5 16v-5h5M18 12a6 6 0 0 0-10.5-4M6 12a6 6 0 0 0 10.5 4"/>',
    "scan": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4M9 12h6M12 9v6"/>',
    "check": '<circle cx="12" cy="12" r="8"/><path d="m8.5 12 2.3 2.3 4.8-5"/>',
    "profile": '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M6.5 16c.7-2 4.3-2 5 0M14 9h3M14 13h3"/>',
    "timeline": '<path d="M5 6h11M5 12h8M5 18h11"/><circle cx="18" cy="12" r="2"/>',
    "search": '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4.5 4.5"/>',
    "settings": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6"/>',
    "update": '<path d="M12 4v10M8 10l4 4 4-4M5 19h14"/>',
}


@lru_cache(maxsize=64)
def line_icon(name: str, color: str = "#475569", size: int = 18) -> QIcon:
    path = _PATHS.get(name, _PATHS["settings"]).format(color=color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{path}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill("transparent")
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
