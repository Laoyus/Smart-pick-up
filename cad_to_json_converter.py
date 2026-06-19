"""
CAD → JSON Variant Converter
Smart Assistive Part Pick System | Team Decepticons@123
Caterpillar Tech Challenge 2026

Converts STEP / IGES / STL assembly files into variant JSON configs
compatible with the Smart Part Pick sequence engine.

Usage: python cad_to_json_converter.py
"""

import sys
import os
import re
import json
from pathlib import Path
from collections import Counter
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QSpinBox, QComboBox, QTextEdit, QFrame,
    QSplitter, QMessageBox, QProgressBar, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMimeData, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QDragEnterEvent, QDropEvent, QPalette,
    QLinearGradient, QPainter, QBrush
)

# ─────────────────────────────────────────────
#  COLOUR PALETTE  (Caterpillar industrial)
# ─────────────────────────────────────────────
CAT_YELLOW   = "#F4C430"
CAT_BLACK    = "#1A1A1A"
CAT_DARK     = "#242424"
CAT_PANEL    = "#2E2E2E"
CAT_BORDER   = "#3A3A3A"
CAT_TEXT     = "#F0F0F0"
CAT_MUTED    = "#888888"
CAT_GREEN    = "#4CAF50"
CAT_RED      = "#E53935"
CAT_BLUE     = "#1E88E5"

BIN_TYPES    = ["small", "large"]
PART_COLORS  = ["red", "brown", "blue", "green", "orange", "purple"]


# ─────────────────────────────────────────────
#  CAD PARSER  (STEP / IGES / STL)
# ─────────────────────────────────────────────

class CADParser:
    """
    Extracts part names, quantities, and infers assembly sequence
    from STEP AP214/AP242, IGES, and STL files.

    Sequence heuristic (industry-standard assembly order):
      0 – structural frames / housings / bases
      1 – subassemblies / brackets / plates
      2 – moving parts / shafts / gears
      3 – fasteners (bolts, screws, studs, pins)
      4 – hardware (washers, nuts, clips, seals)
    """

    STRUCTURAL_KEYWORDS  = ['frame','housing','body','base','cover','case',
                             'block','mount','support','chassis']
    BRACKET_KEYWORDS     = ['bracket','plate','flange','panel','shield',
                             'guard','rail','arm']
    MOVING_KEYWORDS      = ['shaft','gear','bearing','sleeve','piston',
                             'rod','cam','pulley','sprocket','link']
    FASTENER_KEYWORDS    = ['bolt','screw','stud','pin','rivet','key']
    HARDWARE_KEYWORDS    = ['washer','nut','clip','ring','seal','gasket',
                             'snap','retainer','circlip','spring']

    def parse(self, filepath: str) -> list[dict]:
        ext = Path(filepath).suffix.lower()
        if ext in ('.stp', '.step'):
            parts = self._parse_step(filepath)
        elif ext in ('.igs', '.iges'):
            parts = self._parse_iges(filepath)
        elif ext == '.stl':
            parts = self._parse_stl(filepath)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        parts = self._deduplicate_and_count(parts)
        parts = self._apply_sequence(parts)
        return parts

    # ── STEP parser ──────────────────────────────────────────────────────
    def _parse_step(self, filepath: str) -> list[str]:
        with open(filepath, 'r', errors='replace') as f:
            content = f.read()

        # Primary: PRODUCT entities carry the part name
        # Format: PRODUCT('name','description','long_desc',(contexts))
        names = re.findall(r"PRODUCT\s*\(\s*'([^']+)'", content)

        # Filter out PRODUCT_CONTEXT / APPLICATION_CONTEXT false matches
        # (those come from PRODUCT_CONTEXT entities, not PRODUCT)
        # Real part names rarely contain spaces like "core data for..."
        names = [n for n in names if len(n) < 80 and
                 not any(k in n.lower() for k in
                         ['core data', 'context', 'design process',
                          'mechanical design', 'automotive'])]

        # Fallback: NEXT_ASSEMBLY_USAGE_OCCURENCE quantity field
        if not names:
            # Try PART_DEFINITION names
            names = re.findall(r"PRODUCT_DEFINITION\s*\(\s*'([^']+)'", content)

        return names if names else ['UnknownPart']

    # ── IGES parser ──────────────────────────────────────────────────────
    def _parse_iges(self, filepath: str) -> list[str]:
        with open(filepath, 'r', errors='replace') as f:
            content = f.read()

        names = []

        # Entity 408 = Singular Subfigure Instance (assembly reference)
        # Entity 308 = Subfigure Definition (has name)
        # Extract from Section D (Directory Entry) — column 75-80 is entity name
        # IGES line format: fixed 80 columns
        lines = content.splitlines()
        dir_entries = [l for l in lines if len(l) >= 73 and
                       l[72:73] in ('D',)]

        for line in dir_entries:
            name_field = line[8:16].strip()
            if name_field and name_field not in ('', '0'):
                names.append(name_field)

        # Fallback: Global Section has filename at minimum
        if not names:
            global_match = re.search(r'1H,.*?,(.*?),', content[:500])
            if global_match:
                names = [global_match.group(1).strip().strip(';')]

        # Also grab any 8H or similar Hollerith strings that look like part names
        hollerith = re.findall(r'\d+H([A-Za-z][A-Za-z0-9_\-]{2,})', content)
        names.extend(hollerith[:20])  # cap at 20 to avoid garbage

        return names if names else ['UnknownPart']

    # ── STL parser ───────────────────────────────────────────────────────
    def _parse_stl(self, filepath: str) -> list[str]:
        """
        STL is a single mesh — no assembly hierarchy.
        Extract named solids from ASCII STL (binary has no names).
        Returns single part or named shells.
        """
        try:
            with open(filepath, 'r', errors='replace') as f:
                content = f.read(50000)  # read first 50KB
        except Exception:
            return ['STL_Part']

        # ASCII STL: 'solid name'
        solids = re.findall(r'^\s*solid\s+(.+)$', content, re.MULTILINE)
        solids = [s.strip() for s in solids
                  if s.strip() and s.strip().lower() != 'ascii']

        return solids if solids else [Path(filepath).stem]

    # ── Post-processing ───────────────────────────────────────────────────
    def _deduplicate_and_count(self, names: list[str]) -> list[dict]:
        """Count occurrences → quantity. Return list of {name, qty} dicts."""
        # Normalise: strip whitespace, collapse underscores/hyphens to spaces
        cleaned = []
        for n in names:
            n = n.strip().replace('_', ' ').replace('-', ' ')
            n = re.sub(r'\s+', ' ', n)
            if n:
                cleaned.append(n)

        counts = Counter(cleaned)
        # Preserve first-seen order
        seen = {}
        for n in cleaned:
            if n not in seen:
                seen[n] = counts[n]
        return [{'name': k, 'qty': v} for k, v in seen.items()]

    def _sequence_priority(self, name: str) -> int:
        n = name.lower()
        if any(k in n for k in self.STRUCTURAL_KEYWORDS):  return 0
        if any(k in n for k in self.BRACKET_KEYWORDS):     return 1
        if any(k in n for k in self.MOVING_KEYWORDS):      return 2
        if any(k in n for k in self.FASTENER_KEYWORDS):    return 3
        if any(k in n for k in self.HARDWARE_KEYWORDS):    return 4
        return 1  # default: treat as bracket/subassembly

    def _apply_sequence(self, parts: list[dict]) -> list[dict]:
        """Sort by heuristic priority, then assign step numbers."""
        parts.sort(key=lambda p: self._sequence_priority(p['name']))
        for i, p in enumerate(parts):
            p['step']        = i + 1
            p['bin_type']    = 'small' if p['qty'] >= 3 else 'large'
            p['bin_index']   = i        # user can reorder in GUI
            p['part_number'] = f"CAT-{(hash(p['name']) % 90000) + 10000}"
        return parts


# ─────────────────────────────────────────────
#  PARSE THREAD  (keeps GUI responsive)
# ─────────────────────────────────────────────

class ParseThread(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            parser = CADParser()
            parts = parser.parse(self.filepath)
            self.finished.emit(parts)
        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────
#  DROP ZONE WIDGET
# ─────────────────────────────────────────────

class DropZone(QFrame):
    file_dropped = pyqtSignal(str)

    ACCEPTED_EXT = {'.stp', '.step', '.igs', '.iges', '.stl'}

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        self._active = False
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet(f"""
            QFrame {{
                border: 2px dashed {CAT_BORDER};
                border-radius: 12px;
                background: {CAT_PANEL};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("⬇")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet(f"font-size: 36px; color: {CAT_YELLOW}; border: none;")

        self.text_label = QLabel("Drop STEP / IGES / STL file here")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setStyleSheet(f"font-size: 14px; color: {CAT_TEXT}; font-weight: 600; border: none;")

        self.sub_label = QLabel(".stp  .step  .igs  .iges  .stl")
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setStyleSheet(f"font-size: 11px; color: {CAT_MUTED}; border: none;")

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.sub_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and Path(urls[0].toLocalFile()).suffix.lower() in self.ACCEPTED_EXT:
                event.acceptProposedAction()
                self._set_active(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._set_active(False)

    def dropEvent(self, event: QDropEvent):
        self._set_active(False)
        urls = event.mimeData().urls()
        if urls:
            filepath = urls[0].toLocalFile()
            if Path(filepath).suffix.lower() in self.ACCEPTED_EXT:
                self.file_dropped.emit(filepath)

    def _set_active(self, active: bool):
        self._active = active
        if active:
            self.setStyleSheet(f"""
                QFrame {{
                    border: 2px dashed {CAT_YELLOW};
                    border-radius: 12px;
                    background: #332800;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame {{
                    border: 2px dashed {CAT_BORDER};
                    border-radius: 12px;
                    background: {CAT_PANEL};
                }}
            """)

    def mousePressEvent(self, event):
        # Click to browse
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open CAD File", "",
            "CAD Files (*.stp *.step *.igs *.iges *.stl)"
        )
        if filepath:
            self.file_dropped.emit(filepath)


# ─────────────────────────────────────────────
#  REORDERABLE PARTS TABLE
# ─────────────────────────────────────────────

class PartsTable(QTableWidget):
    """Drag-reorderable table. Each row = one assembly step."""

    COLS = ['Step', 'Part Name', 'Part Number', 'Qty', 'Bin Type', 'Bin Index']

    def __init__(self):
        super().__init__()
        self.setColumnCount(len(self.COLS))
        self.setHorizontalHeaderLabels(self.COLS)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 55)
        self.setColumnWidth(2, 110)
        self.setColumnWidth(3, 55)
        self.setColumnWidth(4, 90)
        self.setColumnWidth(5, 80)
        self.setStyleSheet(f"""
            QTableWidget {{
                background: {CAT_DARK};
                alternate-background-color: {CAT_PANEL};
                color: {CAT_TEXT};
                border: 1px solid {CAT_BORDER};
                border-radius: 8px;
                gridline-color: transparent;
                font-size: 13px;
            }}
            QHeaderView::section {{
                background: {CAT_BLACK};
                color: {CAT_YELLOW};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 6px 8px;
                border: none;
                border-bottom: 2px solid {CAT_YELLOW};
            }}
            QTableWidget::item:selected {{
                background: #3A2E00;
                color: {CAT_YELLOW};
            }}
        """)

        # Renumber steps after any row move
        self.model().rowsMoved.connect(self._renumber_steps)

    def load_parts(self, parts: list[dict]):
        self.setRowCount(0)
        for p in parts:
            self._add_row(p)

    def _add_row(self, p: dict):
        row = self.rowCount()
        self.insertRow(row)

        # Step (read-only, auto-numbered)
        step_item = QTableWidgetItem(str(row + 1))
        step_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        step_item.setFlags(step_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        step_item.setForeground(QColor(CAT_YELLOW))
        self.setItem(row, 0, step_item)

        # Part Name (editable)
        self.setItem(row, 1, QTableWidgetItem(p.get('name', '')))

        # Part Number (editable)
        self.setItem(row, 2, QTableWidgetItem(p.get('part_number', '')))

        # Qty (spin box)
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 999)
        qty_spin.setValue(p.get('qty', 1))
        qty_spin.setStyleSheet(f"""
            QSpinBox {{
                background: {CAT_PANEL};
                color: {CAT_TEXT};
                border: 1px solid {CAT_BORDER};
                border-radius: 4px;
                padding: 2px 4px;
            }}
        """)
        self.setCellWidget(row, 3, qty_spin)

        # Bin Type (dropdown)
        bin_type_combo = QComboBox()
        bin_type_combo.addItems(BIN_TYPES)
        idx = BIN_TYPES.index(p.get('bin_type', 'small')) if p.get('bin_type', 'small') in BIN_TYPES else 0
        bin_type_combo.setCurrentIndex(idx)
        bin_type_combo.setStyleSheet(f"""
            QComboBox {{
                background: {CAT_PANEL};
                color: {CAT_TEXT};
                border: 1px solid {CAT_BORDER};
                border-radius: 4px;
                padding: 2px 6px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {CAT_PANEL};
                color: {CAT_TEXT};
                selection-background-color: {CAT_YELLOW};
                selection-color: {CAT_BLACK};
            }}
        """)
        self.setCellWidget(row, 4, bin_type_combo)

        # Bin Index (editable number)
        bin_idx_item = QTableWidgetItem(str(p.get('bin_index', row)))
        bin_idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setItem(row, 5, bin_idx_item)

        self.setRowHeight(row, 40)

    def _renumber_steps(self):
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item:
                item.setText(str(row + 1))

    def get_parts(self) -> list[dict]:
        """Extract current table state as list of dicts."""
        parts = []
        for row in range(self.rowCount()):
            name_item = self.item(row, 1)
            pn_item   = self.item(row, 2)
            qty_spin  = self.cellWidget(row, 3)
            bt_combo  = self.cellWidget(row, 4)
            bi_item   = self.item(row, 5)

            name = name_item.text().strip() if name_item else ''
            if not name:
                continue

            parts.append({
                'step':        row + 1,
                'name':        name,
                'part_number': pn_item.text().strip() if pn_item else '',
                'qty':         qty_spin.value() if qty_spin else 1,
                'bin_type':    bt_combo.currentText() if bt_combo else 'small',
                'bin_index':   int(bi_item.text()) if bi_item and bi_item.text().isdigit() else row,
            })
        return parts


# ─────────────────────────────────────────────
#  JSON PREVIEW
# ─────────────────────────────────────────────

def build_variant_json(variant_name: str, parts: list[dict]) -> dict:
    """
    Converts table rows into the exact JSON format expected by
    the Smart Part Pick sequence engine.

    Engine requires:
      bins[]          — one entry per physical bin (bin_id is 1-indexed)
      pick_sequence[] — ordered steps referencing bin_id
    unit_weight_g is set to 0.0 — fill it in after weighing one real part.
    """
    bins          = []
    pick_sequence = []

    for p in parts:
        bin_id = p['bin_index'] + 1          # engine uses 1-based bin_id

        bins.append({
            "bin_id":             bin_id,
            "part_number":        p['part_number'],
            "part_name":          p['name'],
            "unit_weight_g":      0.0,        # ← weigh one part and fill this in
            "weight_tolerance_g": 5.0,
            "initial_qty":        max(p['qty'] * 5, 20),
            "led_position":       p['bin_index'],
        })

        pick_sequence.append({
            "step":        p['step'],
            "bin_id":      bin_id,
            "qty":         p['qty'],
            "instruction": f"Pick {p['qty']}× {p['name']}",
        })

    return {
        "variant_id":            variant_name.replace(' ', '_').upper(),
        "variant_name":          variant_name,
        "description":           f"Auto-generated from CAD file — {datetime.now().strftime('%Y-%m-%d')}",
        "bins":                  bins,
        "pick_sequence":         pick_sequence,
        "cycle_time_target_sec": len(parts) * 30,
    }


# ─────────────────────────────────────────────
#  MAIN WINDOW
# ─────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAD → JSON Converter  |  Smart Part Pick System")
        self.setMinimumSize(1100, 740)
        self._current_file = None
        self._parse_thread = None
        self._setup_ui()
        self._apply_global_style()

    # ── UI construction ───────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(16)

        # Left panel: drop zone + controls
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_drop_section())
        left.addWidget(self._build_variant_section())
        left.addWidget(self._build_action_buttons())
        left.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(280)

        # Right panel: table + JSON preview
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self._build_table_section())
        right.addWidget(self._build_preview_section())
        right.setSizes([420, 220])

        body.addWidget(left_widget)
        body.addWidget(right, stretch=1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        root.addWidget(body_widget, stretch=1)

        root.addWidget(self._build_status_bar())

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setFixedHeight(64)
        header.setStyleSheet(f"background: {CAT_BLACK}; border-bottom: 3px solid {CAT_YELLOW};")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)

        cat_label = QLabel("CAT")
        cat_label.setStyleSheet(f"""
            color: {CAT_YELLOW};
            font-size: 22px;
            font-weight: 900;
            letter-spacing: 3px;
            font-family: 'Arial Black', Arial, sans-serif;
        """)

        title_label = QLabel("CAD → JSON Variant Converter")
        title_label.setStyleSheet(f"""
            color: {CAT_TEXT};
            font-size: 15px;
            font-weight: 600;
        """)

        sub_label = QLabel("Smart Assistive Part Pick System  ·  Decepticons@123")
        sub_label.setStyleSheet(f"color: {CAT_MUTED}; font-size: 11px;")
        sub_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(cat_label)
        layout.addSpacing(16)
        layout.addWidget(title_label)
        layout.addStretch()
        layout.addWidget(sub_label)
        return header

    def _build_drop_section(self) -> QWidget:
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background: {CAT_PANEL};
                border: 1px solid {CAT_BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        section_label = QLabel("① IMPORT CAD FILE")
        section_label.setStyleSheet(f"color: {CAT_YELLOW}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; border: none;")

        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self._on_file_dropped)

        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet(f"color: {CAT_MUTED}; font-size: 10px; border: none;")
        self.file_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: {CAT_BORDER}; border-radius: 2px; border: none; }}
            QProgressBar::chunk {{ background: {CAT_YELLOW}; border-radius: 2px; }}
        """)

        layout.addWidget(section_label)
        layout.addWidget(self.drop_zone)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.file_label)
        return container

    def _build_variant_section(self) -> QWidget:
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background: {CAT_PANEL};
                border: 1px solid {CAT_BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        section_label = QLabel("② VARIANT DETAILS")
        section_label.setStyleSheet(f"color: {CAT_YELLOW}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; border: none;")

        name_label = QLabel("Variant Name")
        name_label.setStyleSheet(f"color: {CAT_MUTED}; font-size: 11px; border: none;")
        self.variant_name_edit = QLineEdit("Model A")
        self.variant_name_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {CAT_DARK};
                color: {CAT_TEXT};
                border: 1px solid {CAT_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {CAT_YELLOW}; }}
        """)
        self.variant_name_edit.textChanged.connect(self._refresh_preview)

        layout.addWidget(section_label)
        layout.addWidget(name_label)
        layout.addWidget(self.variant_name_edit)

        # Hint about sequence
        hint = QLabel("💡 Drag rows in the table to reorder assembly sequence")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {CAT_MUTED}; font-size: 10px; border: none; margin-top: 6px;")
        layout.addWidget(hint)

        return container

    def _build_action_buttons(self) -> QWidget:
        container = QFrame()
        container.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        section_label = QLabel("③ EXPORT")
        section_label.setStyleSheet(f"color: {CAT_YELLOW}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; border: none;")

        self.export_btn = QPushButton("⬇  Export JSON")
        self.export_btn.setFixedHeight(44)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_json)
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CAT_YELLOW};
                color: {CAT_BLACK};
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{ background: #FFD740; }}
            QPushButton:disabled {{ background: {CAT_BORDER}; color: {CAT_MUTED}; }}
        """)

        self.clear_btn = QPushButton("✕  Clear")
        self.clear_btn.setFixedHeight(36)
        self.clear_btn.clicked.connect(self._clear)
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CAT_MUTED};
                border: 1px solid {CAT_BORDER};
                border-radius: 8px;
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {CAT_TEXT}; border-color: {CAT_TEXT}; }}
        """)

        layout.addWidget(section_label)
        layout.addWidget(self.export_btn)
        layout.addWidget(self.clear_btn)
        return container

    def _build_table_section(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        table_label = QLabel("ASSEMBLY STEPS  —  drag rows to reorder sequence")
        table_label.setStyleSheet(f"color: {CAT_YELLOW}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")

        add_row_btn = QPushButton("+ Add Part")
        add_row_btn.setFixedHeight(28)
        add_row_btn.clicked.connect(self._add_empty_row)
        add_row_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CAT_YELLOW};
                border: 1px solid {CAT_YELLOW};
                border-radius: 6px;
                font-size: 11px;
                padding: 0 10px;
            }}
            QPushButton:hover {{ background: #3A2E00; }}
        """)

        del_row_btn = QPushButton("− Remove")
        del_row_btn.setFixedHeight(28)
        del_row_btn.clicked.connect(self._remove_selected_row)
        del_row_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CAT_RED};
                border: 1px solid {CAT_RED};
                border-radius: 6px;
                font-size: 11px;
                padding: 0 10px;
            }}
            QPushButton:hover {{ background: #2A0000; }}
        """)

        header_row.addWidget(table_label)
        header_row.addStretch()
        header_row.addWidget(add_row_btn)
        header_row.addWidget(del_row_btn)

        self.parts_table = PartsTable()
        self.parts_table.model().dataChanged.connect(self._refresh_preview)
        self.parts_table.model().rowsMoved.connect(self._refresh_preview)

        layout.addLayout(header_row)
        layout.addWidget(self.parts_table)
        return container

    def _build_preview_section(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        preview_label = QLabel("JSON PREVIEW  —  live output")
        preview_label.setStyleSheet(f"color: {CAT_YELLOW}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setFont(QFont("Courier New", 10))
        self.preview_text.setStyleSheet(f"""
            QTextEdit {{
                background: {CAT_BLACK};
                color: #A8FF78;
                border: 1px solid {CAT_BORDER};
                border-radius: 8px;
                padding: 10px;
            }}
        """)

        layout.addWidget(preview_label)
        layout.addWidget(self.preview_text)
        return container

    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(32)
        bar.setStyleSheet(f"background: {CAT_BLACK}; border-top: 1px solid {CAT_BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)

        self.status_label = QLabel("Ready — drop a CAD file to begin")
        self.status_label.setStyleSheet(f"color: {CAT_MUTED}; font-size: 11px;")

        self.parts_count_label = QLabel("")
        self.parts_count_label.setStyleSheet(f"color: {CAT_YELLOW}; font-size: 11px; font-weight: 600;")

        layout.addWidget(self.status_label)
        layout.addStretch()
        layout.addWidget(self.parts_count_label)
        return bar

    def _apply_global_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {CAT_DARK};
                color: {CAT_TEXT};
                font-family: 'Segoe UI', 'SF Pro Display', Arial, sans-serif;
            }}
            QSplitter::handle {{
                background: {CAT_BORDER};
                height: 2px;
            }}
            QScrollBar:vertical {{
                background: {CAT_DARK};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {CAT_BORDER};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {CAT_MUTED}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_file_dropped(self, filepath: str):
        self._current_file = filepath
        filename = Path(filepath).name
        self.file_label.setText(f"📄 {filename}")
        self.status_label.setText(f"Parsing {filename}…")
        self.progress_bar.setVisible(True)
        self.export_btn.setEnabled(False)

        self._parse_thread = ParseThread(filepath)
        self._parse_thread.finished.connect(self._on_parse_done)
        self._parse_thread.error.connect(self._on_parse_error)
        self._parse_thread.start()

    def _on_parse_done(self, parts: list[dict]):
        self.progress_bar.setVisible(False)
        self.parts_table.load_parts(parts)
        n = len(parts)
        self.status_label.setText(
            f"✅  Parsed successfully — {n} part{'s' if n != 1 else ''} extracted. "
            f"Sequence inferred by assembly heuristics — drag rows to adjust."
        )
        self.parts_count_label.setText(f"{n} parts  ·  {self.rowCount_str(n)} steps")
        self.export_btn.setEnabled(True)
        self._refresh_preview()

    def _on_parse_error(self, error_msg: str):
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"❌  Parse error: {error_msg}")
        QMessageBox.critical(self, "Parse Error",
            f"Could not parse the CAD file:\n\n{error_msg}\n\n"
            "Tip: Try re-exporting as STEP AP214 from SolidWorks (File → Save As → STEP AP214).")

    def _refresh_preview(self):
        parts = self.parts_table.get_parts()
        if not parts:
            self.preview_text.setPlainText("// Load a CAD file to see the JSON output here")
            return
        variant_name = self.variant_name_edit.text() or "Unnamed Variant"
        data = build_variant_json(variant_name, parts)
        self.preview_text.setPlainText(json.dumps(data, indent=2))
        n = len(parts)
        self.parts_count_label.setText(f"{n} parts  ·  {n} steps")

    def _export_json(self):
        parts = self.parts_table.get_parts()
        if not parts:
            QMessageBox.warning(self, "Nothing to Export", "Add parts before exporting.")
            return

        variant_name = self.variant_name_edit.text() or "variant"
        default_name = variant_name.replace(' ', '_').lower() + ".json"

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Variant JSON", default_name, "JSON Files (*.json)"
        )
        if not filepath:
            return

        data = build_variant_json(variant_name, parts)
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        self.status_label.setText(f"✅  Exported to {Path(filepath).name}")
        QMessageBox.information(self, "Export Successful",
            f"Variant JSON saved to:\n{filepath}\n\n"
            "Before uploading to the Manager dashboard:\n"
            "  • Open the file and fill in unit_weight_g for each bin\n"
            "    (weigh one real part on a scale — value in grams).\n\n"
            "Then upload via Manager → Upload & Push Custom Variant.")

    def _add_empty_row(self):
        row_idx = self.parts_table.rowCount()
        self.parts_table._add_row({
            'name': 'New Part', 'part_number': 'CAT-00000',
            'qty': 1, 'bin_type': 'small', 'bin_index': row_idx
        })
        self.export_btn.setEnabled(True)
        self._refresh_preview()

    def _remove_selected_row(self):
        rows = sorted(set(i.row() for i in self.parts_table.selectedItems()), reverse=True)
        for row in rows:
            self.parts_table.removeRow(row)
        self.parts_table._renumber_steps()
        self._refresh_preview()

    def _clear(self):
        self.parts_table.setRowCount(0)
        self.preview_text.setPlainText("")
        self.file_label.setText("No file loaded")
        self.status_label.setText("Ready — drop a CAD file to begin")
        self.parts_count_label.setText("")
        self.export_btn.setEnabled(False)
        self._current_file = None

    @staticmethod
    def rowCount_str(n: int) -> str:
        return str(n)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName("CAD → JSON Converter")

    # High-DPI support
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
