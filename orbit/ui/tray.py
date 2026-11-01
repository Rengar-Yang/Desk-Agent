"""System tray icon in the notification area, QQ-style."""

from PyQt5.QtWidgets import QSystemTrayIcon

from orbit.ui.chrome import make_context_menu
from orbit.ui.mascot_icon import app_icon


class OrbitTray:
    """Keep Orbit reachable from the notification area while the mascot floats."""

    def __init__(self, pet):
        self.pet = pet
        self.icon = QSystemTrayIcon(app_icon(), pet)
        self.icon.setToolTip("Orbit")
        self.menu = make_context_menu(pet)
        self.menu.aboutToShow.connect(self._fill_menu)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()

    def _fill_menu(self):
        self.menu.clear()
        self.pet.fill_orbit_menu(self.menu)

    def _activated(self, reason):
        # Right-click uses the context menu. Left/double-click brings Orbit forward.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
            self.pet.reveal_from_tray()

    def hide(self):
        self.icon.hide()
