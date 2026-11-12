"""
intializes menu & calls from buttons for parts & tools in q plugin
"""
import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QToolButton, QMenu, QAction
import processing

from .sfm_part0 import LidarOrgMatchToCsv
from .sfm_part1 import Setup
from .sfm_part2 import HexConfig
from .sfm_part3A import NullInfill #3A
from .sfm_part3B import FullDEM #3B
from .sfm_part4A import BoundaryCirc   # Part 4A
from .sfm_part4B import FindExtent # Part 4B
from .sfm_part4C import ExportExtentCsv # Part 4C
from .sfm_part5 import FlowModel
# from .sfm_part6 import Vectors
from .tool_addsubtract import AddSubtract
from .tool_hextometa import writehextometa  
from .tool_AddDSM import AddDSM

class SFMPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.tool_button = None
        self.menu = None
        self.add_btn = None
        self.remove_btn = None
        self.hex_meta_btn = None  
        self.add_dsm_btn = None  
        self.add_dialog = None
        self.remove_dialog = None
        self.part4b_dialog = None

    def initGui(self):
        plugin_dir = os.path.dirname(__file__)
        icons_dir = os.path.join(plugin_dir, "icons")
        self.toolbar = self.iface.pluginToolBar()

        # init dropdown parts toolbar
        self.tool_button = QToolButton()
        self.tool_button.setText("SFM")
        self.tool_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.tool_button.setPopupMode(QToolButton.InstantPopup)
        self.tool_button.setStyleSheet("""
            QToolButton {
                background-color: #ADD8E6;
                color: #0B283B;
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                font-size: 10.5pt;
                font-weight: 300;
                border-radius: 4px;
                padding: 1px 1px;
                border: 1px solid #7EBDE6;
            }
            QToolButton:hover {
                background-color: #7EBDE6;
            }
            QToolButton::menu-indicator {
                image: none;
            }
        """)
        
        self.menu = QMenu(self.iface.mainWindow())
        self.menu.setStyleSheet("""
            QMenu {
                background-color: #f0f4fc;
                color: #1a2a40;
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                font-size: 10pt;
                border: 1px solid #7EBDE6;
                border-radius: 1px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                background-color: transparent;
            }
            QMenu::item:selected {
                background-color: #ADD8E6;
                color: #0B283B;
                border-radius: 1px;
            }
            QMenu::icon {
                background-color: transparent;
                border: none;
                width: 16px;
                padding-left: 1px;
            }
        """)

        #SFM MENU
        
        icon_p0 = QIcon(os.path.join(icons_dir, "part0_icon.png"))
        icon_p1 = QIcon(os.path.join(icons_dir, "part1_icon.png"))
        icon_p2 = QIcon(os.path.join(icons_dir, "part2_icon.png"))
        icon_p3 = QIcon(os.path.join(icons_dir, "part3_icon.png"))
        icon_p4 = QIcon(os.path.join(icons_dir, "part4_icon.png"))
        icon_p5 = QIcon(os.path.join(icons_dir, "part5_icon.png"))
        # icon_p6 = QIcon(os.path.join(icons_dir, "part6_icon.png"))
        icon_dsm = QIcon(os.path.join(icons_dir, "dsm_icon.png"))

        self.action_part0 = QAction(icon_p0, "Part 0 - Available LiDAR CSV", self.iface.mainWindow())
        self.action_part1 = QAction(icon_p1, "Part 1 - Setup", self.iface.mainWindow())
        self.action_part2 = QAction(icon_p2, "Part 2 - HexConfig", self.iface.mainWindow())

        # Create Part 3 Submenu 
        self.menu_part3 = QMenu("Part 3 - DEM Correction", self.iface.mainWindow())
        self.menu_part3.setIcon(icon_p3)
        self.menu_part3.setStyleSheet(self.menu.styleSheet())
        # Submenu Action item for Part 3A
        self.action_part3a = QAction("Part 3A - Null Infill", self.iface.mainWindow())
        self.action_part3a.triggered.connect(lambda: self.run_algorithm(NullInfill))
        self.menu_part3.addAction(self.action_part3a)
        # Submenu Action item for Part 3B
        self.action_part3b = QAction("Part 3B - 4m Full DEM", self.iface.mainWindow())
        self.action_part3b.triggered.connect(lambda: self.run_algorithm(FullDEM))
        self.menu_part3.addAction(self.action_part3b)
        
        # Create Part 4 Submenu 
        self.menu_part4 = QMenu("Part 4 - Boundary", self.iface.mainWindow())
        self.menu_part4.setIcon(icon_p4)
        self.menu_part4.setStyleSheet(self.menu.styleSheet())
        # Submenu Action item for Part 4A
        self.action_part4a = QAction("Part 4A - Boundary Buffer", self.iface.mainWindow())
        self.action_part4a.triggered.connect(lambda: self.run_algorithm(BoundaryCirc))
        self.menu_part4.addAction(self.action_part4a)
        # Submenu Action item for Part 4B - Direct Launch (Bypasses empty config window)
        self.action_part4b = QAction("Part 4B - Create Extent", self.iface.mainWindow())
        self.action_part4b.triggered.connect(self.run_part4b_direct)
        self.menu_part4.addAction(self.action_part4b)
        # Submenu Action item for Part 4C
        self.action_part4c = QAction("Part 4C - Export Extent CSV", self.iface.mainWindow())
        self.action_part4c.triggered.connect(lambda: self.run_algorithm(ExportExtentCsv))
        self.menu_part4.addAction(self.action_part4c)

        self.action_part5 = QAction(icon_p5, "Part 5 - Surface Flow Model", self.iface.mainWindow())
        #self.action_part6 = QAction(icon_p6, "Part 6 - Vector Outputs", self.iface.mainWindow())
        self.action_add_dsm = QAction(icon_dsm, "Add DSM Tool", self.iface.mainWindow())

        # Connect standard parts
        self.action_part0.triggered.connect(lambda: self.run_algorithm(LidarOrgMatchToCsv))
        self.action_part1.triggered.connect(lambda: self.run_algorithm(Setup))
        self.action_part2.triggered.connect(lambda: self.run_algorithm(HexConfig))
        self.action_part5.triggered.connect(lambda: self.run_algorithm(FlowModel))
        #self.action_part6.triggered.connect(lambda: self.run_algorithm(Vectors))
        self.action_add_dsm.triggered.connect(lambda: self.run_tool_algorithm(AddDSM))

        # Build main dropdown structure
        self.menu.addAction(self.action_part0)
        self.menu.addAction(self.action_part1)
        self.menu.addAction(self.action_part2)
        self.menu.addMenu(self.menu_part3)
        self.menu.addMenu(self.menu_part4)
        self.menu.addAction(self.action_part5)  
        #self.menu.addAction(self.action_part6)  
        #non toolbar tools on bottom
        self.menu.addSeparator()
        self.menu.addAction(self.action_add_dsm)
        self.tool_button.setMenu(self.menu)
        self.toolbar.addWidget(self.tool_button)


        # TOOLBAR TOOLS

        # Add to Extent
        icon_add = QIcon(os.path.join(icons_dir, "add_icon.png"))
        self.action_add = QAction(icon_add, "Add to Extent", self.iface.mainWindow())
        self.action_add.triggered.connect(lambda: self.run_add_subtract(add_mode=True))
        self.add_btn = QToolButton()
        self.add_btn.setDefaultAction(self.action_add)
        self.add_btn.setStyleSheet(self.get_toolbar_btn_style())
        self.toolbar.addWidget(self.add_btn)

        # Remove from Extent
        icon_remove = QIcon(os.path.join(icons_dir, "remove_extent_icon.png"))
        self.action_remove = QAction(icon_remove, "Remove from Extent", self.iface.mainWindow())
        self.action_remove.triggered.connect(lambda: self.run_add_subtract(add_mode=False))
        self.remove_btn = QToolButton()
        self.remove_btn.setDefaultAction(self.action_remove)
        self.remove_btn.setStyleSheet(self.get_toolbar_btn_style())
        self.toolbar.addWidget(self.remove_btn)

        # Write HexIDs to Meta
        icon_hextometa = QIcon(os.path.join(icons_dir, "hextometa_icon.png"))
        self.action_hextometa = QAction(icon_hextometa, "Write HexIDs to Meta", self.iface.mainWindow())
        self.action_hextometa.triggered.connect(lambda: self.run_hextometa(writehextometa))
        self.hex_meta_btn = QToolButton()
        self.hex_meta_btn.setDefaultAction(self.action_hextometa)
        self.hex_meta_btn.setStyleSheet(self.get_toolbar_btn_style())
        self.toolbar.addWidget(self.hex_meta_btn)

    def unload(self):
        for btn in [self.tool_button, self.menu, self.menu_part4, self.add_btn, self.remove_btn, self.hex_meta_btn]:
            if btn:
                btn.deleteLater()

    def get_toolbar_btn_style(self):
        return """
            QToolButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 4px;
            }
            QToolButton:hover {
                background-color: rgba(0, 0, 0, 20);
            }
        """

    def get_parts_dialog_stylesheet(self):
        return """
            QDialog, QDialog QWidget {
                background-color: #ADD8E6 !important;
            }
        """

    def get_tool_dialog_stylesheet(self):
        return """
            QDialog, QDialog QWidget {
                background-color: #BEE3BA !important;
            }
        """
    
    def run_algorithm(self, algorithm_class):
        algorithm = algorithm_class()
        dlg = processing.createAlgorithmDialog(algorithm, {})
        dlg.setStyleSheet(self.get_parts_dialog_stylesheet())
        dlg.exec_()

    def run_part4b_direct(self):
        if self.part4b_dialog and self.part4b_dialog.isVisible():
            self.part4b_dialog.raise_()
            self.part4b_dialog.activateWindow()
            return
        self.part4b_dialog = FindExtent(self.iface.mainWindow())
        self.part4b_dialog.setStyleSheet(self.get_parts_dialog_stylesheet())
        self.part4b_dialog.exec_()

    def run_tool_algorithm(self, algorithm_class):
        algorithm = algorithm_class()
        dlg = processing.createAlgorithmDialog(algorithm, {})
        dlg.setStyleSheet(self.get_tool_dialog_stylesheet())
        dlg.exec_()

    def run_hextometa(self, algorithm_class):
        algorithm = algorithm_class()
        dlg = processing.createAlgorithmDialog(algorithm, {})
        dlg.setStyleSheet(self.get_tool_dialog_stylesheet())
        dlg.exec_()

    def run_add_subtract(self, add_mode=True):
        if add_mode:
            if self.add_dialog and self.add_dialog.isVisible():
                self.add_dialog.raise_()
                self.add_dialog.activateWindow()
                return
            self.add_dialog = AddSubtract(self.iface, is_add_mode=True)
            self.add_dialog.show()
        else:
            if self.remove_dialog and self.remove_dialog.isVisible():
                self.remove_dialog.raise_()
                self.remove_dialog.activateWindow()
                return
            self.remove_dialog = AddSubtract(self.iface, is_add_mode=False)
            self.remove_dialog.show()