"""Draw the round mascot used for the app icon and the chat mark."""

import struct

from PyQt5.QtCore import QBuffer, QIODevice, Qt, QRectF
from PyQt5.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPixmap


def render_mascot(size):
    """Return a transparent image of the black round mascot."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(size / 2, size / 2 + size * 0.04)
    painter.scale(size / 128, size / 128)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor("#2d2d31")))
    for sign in (-1, 1):
        painter.save()
        painter.translate(sign * 22, -30)
        painter.rotate(sign * 28)
        painter.drawRoundedRect(QRectF(-10, -18, 20, 34), 9, 9)
        painter.restore()
    painter.drawEllipse(QRectF(-36, -36, 72, 72))
    painter.setBrush(QBrush(QColor("white")))
    painter.drawEllipse(QRectF(-16, -4, 9, 12))
    painter.drawEllipse(QRectF(7, -4, 9, 12))
    painter.end()
    return image


def app_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render_mascot(size)))
    return icon


def write_ico(path):
    """Pack PNG images into a Windows .ico that shortcuts and the exe can use."""
    blobs = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        render_mascot(size).save(buffer, "PNG")
        blobs.append((size, bytes(buffer.data())))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    body = b""
    for size, blob in blobs:
        edge = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        body += blob
    path.write_bytes(header + entries + body)
