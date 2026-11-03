"""Generate crisp vector-rendered brand icons (`icon.ico`, `icon.png`), SVG icons & README hero banner."""

from __future__ import annotations

import os
from pathlib import Path
import sys

if sys.platform.startswith("win") and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "videodownloader" / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
DOCS_ASSETS_DIR = ROOT / "docs" / "assets"


def render_brand_master_qimage(size: int = 1024) -> QImage:
    """Render the Aurora Squircle brand icon at `size x size` with hardware-quality supersampling."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    s = float(size)
    pad = s * 0.062
    rect = QRectF(pad, pad, s - 2 * pad, s - 2 * pad)
    radius = rect.width() * 0.235

    # 1. Outer soft ambient glow
    glow_path = QPainterPath()
    glow_path.addRoundedRect(rect.adjusted(-s * 0.012, s * 0.016, s * 0.012, s * 0.024), radius, radius)
    p.fillPath(glow_path, QColor(56, 189, 248, 38))

    # 2. Main squircle body: refreshing Ice-Cyan -> Royal Indigo -> Aurora Violet gradient
    squircle = QPainterPath()
    squircle.addRoundedRect(rect, radius, radius)

    bg_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg_grad.setColorAt(0.00, QColor("#22D3EE"))  # Ice Cyan
    bg_grad.setColorAt(0.46, QColor("#3B82F6"))  # Crisp Sky Blue
    bg_grad.setColorAt(1.00, QColor("#7C3AED"))  # Soft Aurora Violet
    p.fillPath(squircle, bg_grad)

    # 3. Luminous top-left radial sheen (gives macOS/Fluent glass depth)
    p.save()
    p.setClipPath(squircle)
    sheen = QRadialGradient(QPointF(s * 0.28, s * 0.18), s * 0.65)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 92))
    sheen.setColorAt(0.55, QColor(255, 255, 255, 18))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(rect, sheen)

    # Subtle bottom-right cyan rim reflection
    rim = QRadialGradient(QPointF(s * 0.82, s * 0.88), s * 0.48)
    rim.setColorAt(0.0, QColor(56, 189, 248, 85))
    rim.setColorAt(1.0, QColor(56, 189, 248, 0))
    p.fillRect(rect, rim)
    p.restore()

    # 4. Crisp 1.5px glass border highlight
    border_pen = QPen(QColor(255, 255, 255, 105), max(2.0, s * 0.014))
    p.setPen(border_pen)
    p.drawPath(squircle)

    # 5. Inner translucent frosted media card silhouette (tilted subtle backdrop)
    inner_card = QRectF(s * 0.23, s * 0.22, s * 0.54, s * 0.40)
    card_path = QPainterPath()
    card_path.addRoundedRect(inner_card, s * 0.075, s * 0.075)
    p.fillPath(card_path, QColor(255, 255, 255, 42))
    p.setPen(QPen(QColor(255, 255, 255, 85), max(1.5, s * 0.009)))
    p.drawPath(card_path)

    # Small rounded play triangle inside top media frame
    play_cx = s * 0.50
    play_cy = s * 0.415
    pr = s * 0.088
    play_poly = QPolygonF(
        [
            QPointF(play_cx - pr * 0.62, play_cy - pr * 0.78),
            QPointF(play_cx + pr * 0.88, play_cy),
            QPointF(play_cx - pr * 0.62, play_cy + pr * 0.78),
        ]
    )
    play_path = QPainterPath()
    play_path.addPolygon(play_poly)
    play_path.closeSubpath()
    p.setPen(QPen(QColor(255, 255, 255, 235), s * 0.022, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.fillPath(play_path, QColor(255, 255, 255, 235))
    p.drawPath(play_path)

    # 6. Bold, ultra-clean white download arrow & tray bar in foreground (crystal clear even at 16x16!)
    white = QColor(255, 255, 255, 255)
    arrow_pen = QPen(white, s * 0.076, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(arrow_pen)
    p.drawLine(QPointF(s * 0.50, s * 0.535), QPointF(s * 0.50, s * 0.745))

    chevron = QPainterPath()
    chevron.moveTo(s * 0.37, s * 0.63)
    chevron.lineTo(s * 0.50, s * 0.755)
    chevron.lineTo(s * 0.63, s * 0.63)
    p.drawPath(chevron)

    # Sleek rounded catch-tray bar at bottom
    tray_pen = QPen(QColor(224, 242, 254, 235), s * 0.056, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(tray_pen)
    p.drawLine(QPointF(s * 0.31, s * 0.835), QPointF(s * 0.69, s * 0.835))

    # Subtle 4-point aurora star accent at top-right (nod to anime/yutto crispness)
    star_cx, star_cy, sr = s * 0.77, s * 0.24, s * 0.042
    star = QPainterPath()
    star.moveTo(star_cx, star_cy - sr)
    star.quadTo(star_cx, star_cy, star_cx + sr, star_cy)
    star.quadTo(star_cx, star_cy, star_cx, star_cy + sr)
    star.quadTo(star_cx, star_cy, star_cx - sr, star_cy)
    star.quadTo(star_cx, star_cy, star_cx, star_cy - sr)
    p.setPen(Qt.PenStyle.NoPen)
    p.fillPath(star, QColor(255, 255, 255, 225))

    p.end()
    return img


def qimage_to_pil(qimg: QImage) -> Image.Image:
    rgba = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = rgba.bits()
    ptr.setsize(rgba.sizeInBytes())
    return Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(ptr), "raw", "RGBA", 0, 1)


def render_readme_banner(master_icon: QImage) -> QImage:
    """Render a crisp 1280x340 README hero banner with airy gradient, icon, and pure `VideoDownloader` title."""
    w, h = 1280, 340
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    card = QRectF(4, 4, w - 8, h - 8)
    path = QPainterPath()
    path.addRoundedRect(card, 28, 28)

    bg = QLinearGradient(0, 0, w, h)
    bg.setColorAt(0.0, QColor("#F0F9FF"))
    bg.setColorAt(0.5, QColor("#EEF2FF"))
    bg.setColorAt(1.0, QColor("#F5F3FF"))
    p.fillPath(path, bg)

    # Decorative soft aurora orbs
    p.save()
    p.setClipPath(path)
    orb1 = QRadialGradient(QPointF(w * 0.16, h * 0.15), 290)
    orb1.setColorAt(0.0, QColor(56, 189, 248, 52))
    orb1.setColorAt(1.0, QColor(56, 189, 248, 0))
    p.fillRect(card, orb1)

    orb2 = QRadialGradient(QPointF(w * 0.86, h * 0.85), 320)
    orb2.setColorAt(0.0, QColor(139, 92, 246, 46))
    orb2.setColorAt(1.0, QColor(139, 92, 246, 0))
    p.fillRect(card, orb2)
    p.restore()

    p.setPen(QPen(QColor("#DBEAFE"), 2))
    p.drawPath(path)

    # Draw master icon at 188x188 on left
    icon_scaled = master_icon.scaled(
        188, 188, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    p.drawImage(96, (h - 188) // 2, icon_scaled)

    # Brand Title: pure `VideoDownloader` (no version suffix!)
    title_font = QFont("Segoe UI", 42, QFont.Weight.Bold)
    p.setFont(title_font)
    p.setPen(QColor("#0F172A"))
    p.drawText(QRectF(324, 72, 860, 74), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "VideoDownloader")

    # Tagline
    sub_font = QFont("Microsoft YaHei UI", 16, QFont.Weight.DemiBold)
    p.setFont(sub_font)
    p.setPen(QColor("#334155"))
    p.drawText(
        QRectF(326, 152, 880, 42),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "可解释的四级多引擎媒体解析与桌面极速下载器",
    )

    # Feature pills
    pills = [
        ("Ctrl+Shift+D 全局秒捕", "#0284C7", "#E0F2FE"),
        ("四级降级解析轨迹 Trace", "#4F46E5", "#E0E7FF"),
        ("1800+ 站点 H.264 直出", "#7C3AED", "#EDE9FE"),
        ("零打扰剪贴板预探测", "#0D9488", "#CCFBF1"),
    ]
    pill_font = QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold)
    p.setFont(pill_font)
    fm = p.fontMetrics()
    x = 326.0
    y = 216.0
    for text, fg_hex, bg_hex in pills:
        tw = fm.horizontalAdvance(text) + 30
        pr = QRectF(x, y, tw, 36)
        pp = QPainterPath()
        pp.addRoundedRect(pr, 18, 18)
        p.fillPath(pp, QColor(bg_hex))
        p.setPen(QPen(QColor(fg_hex), 1))
        p.drawPath(pp)
        p.setPen(QColor(fg_hex))
        p.drawText(pr, Qt.AlignmentFlag.AlignCenter, text)
        x += tw + 14

    p.end()
    return img


SVG_ICONS: dict[str, str] = {
    "bolt.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#0EA5E9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
    ),
    "trace.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#6366F1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="12" r="3"/><circle cx="6" cy="18" r="3"/>'
        '<path d="M9 6h4a2 2 0 0 1 2 2v1"/><path d="M9 18h4a2 2 0 0 0 2-2v-1"/></svg>'
    ),
    "check_circle.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    ),
    "alert_circle.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#EF4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/>'
        '<line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
    ),
}


def main() -> None:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    master = render_brand_master_qimage(1024)
    pil_master = qimage_to_pil(master)

    # 1. Save 512x512 PNG for window icon & macOS/Linux bundles
    icon_png = ASSETS_DIR / "icon.png"
    pil_master.resize((512, 512), Image.Resampling.LANCZOS).save(icon_png, format="PNG")

    # 2. Save multi-resolution Windows `.ico` (16, 24, 32, 48, 64, 128, 256)
    icon_ico = ASSETS_DIR / "icon.ico"
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    pil_master.save(icon_ico, format="ICO", sizes=ico_sizes)

    # 3. Save README hero banner
    banner_qimg = render_readme_banner(master)
    banner_png = DOCS_ASSETS_DIR / "banner.png"
    qimage_to_pil(banner_qimg).save(banner_png, format="PNG")

    # 4. Save clean vector SVGs
    for name, content in SVG_ICONS.items():
        (ICONS_DIR / name).write_text(content, encoding="utf-8")

    print(f"Generated {icon_png} ({icon_png.stat().st_size} bytes)")
    print(f"Generated {icon_ico} ({icon_ico.stat().st_size} bytes, sizes={ico_sizes})")
    print(f"Generated {banner_png} ({banner_png.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
