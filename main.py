import sys
import io
import os
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGridLayout, QLabel, QComboBox, QSpinBox, QCheckBox, QPushButton,
    QPlainTextEdit, QScrollArea, QSplitter, QFileDialog, QFrame, QSizePolicy,
    QLineEdit
)
from PySide6.QtGui import QPixmap, QImage, QFont, QIcon
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import pygments
from pygments.lexers import (
    PythonLexer, JavascriptLexer, HtmlLexer, CssLexer, JsonLexer, CppLexer, JavaLexer
)
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from pygments.token import Token

# --- Configuration & Theme Maps ---

LEXERS = {
    "Python": PythonLexer,
    "JavaScript": JavascriptLexer,
    "HTML": HtmlLexer,
    "CSS": CssLexer,
    "JSON": JsonLexer,
    "C++": CppLexer,
    "Java": JavaLexer
}

THEME_COLORS = {
    "Dracula": {
        "bg": "#282a36",
        "fg": "#f8f8f2",
        "pygments": "dracula"
    },
    "Monokai": {
        "bg": "#272822",
        "fg": "#f8f8f2",
        "pygments": "monokai"
    },
    "GitHub Dark": {
        "bg": "#0d1117",
        "fg": "#c9d1d9",
        "pygments": "inkpot"
    },
    "Nord": {
        "bg": "#2e3440",
        "fg": "#d8dee9",
        "pygments": "nord"
    }
}

GRADIENT_PRESETS = {
    "Mint → Purple": ((0, 255, 170), (157, 78, 221)),
    "Blue → Pink": ((0, 198, 251), (255, 75, 145)),
    "Orange → Red": ((255, 102, 0), (241, 39, 17)),
    "Dark Gray": ((40, 40, 43), (20, 20, 22))
}

CANVAS_PRESETS = {
    "Auto": None,
    "16:9 Landscape": (1600, 900),
    "1:1 Square": (1200, 1200),
    "4:5 Portrait": (1200, 1500),
    "9:16 Story": (1080, 1920)
}

QUALITY_MAP = {
    "1× (Standard)": 1,
    "2× (High)": 2,
    "4× (Ultra)": 4,
    "8× (Maximum)": 8
}


class ForgeSnapRenderer:
    """Handles parsing of text, smart caching, and high-quality image composition."""

    _cache = {}
    _cache_limit = 32

    @staticmethod
    def hex_to_rgb(hex_str):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 3:
            hex_str = "".join(c * 2 for c in hex_str)
        return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def get_monospace_font(font_size):
        """Attempts to load a clean system monospace font, falling back safely."""
        system = sys.platform
        font_paths = []
        if system == "win32":
            font_paths = ["consola.ttf", "lucon.ttf", "cour.ttf"]
        elif system == "darwin":
            font_paths = [
                "/System/Library/Fonts/Supplemental/Courier New.ttf",
                "/System/Library/Fonts/Monaco.ttf",
                "/System/Library/Fonts/Supplemental/Andale Mono.ttf"
            ]
        else:
            font_paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
            ]

        for fp in font_paths:
            try:
                return ImageFont.truetype(fp, font_size)
            except IOError:
                continue

        for name in ["Consolas", "Courier New", "Courier", "DejaVu Sans Mono", "Monospace"]:
            try:
                return ImageFont.truetype(name, font_size)
            except IOError:
                continue

        return ImageFont.load_default()

    @staticmethod
    def get_token_color(token_type, pyg_style, theme_default_fg):
        """Looks up style color for a given Pygments token."""
        while token_type:
            style_def = pyg_style.style_for_token(token_type)
            if style_def and style_def.get('color'):
                return f"#{style_def['color']}"
            token_type = token_type.parent
        return theme_default_fg

    @staticmethod
    def split_tokens_by_line(tokens):
        """Splits flat Pygments token output into clean line-by-line collections."""
        lines = []
        current_line = []
        for token_type, value in tokens:
            if '\n' in value:
                parts = value.split('\n')
                for i, part in enumerate(parts):
                    if part:
                        current_line.append((token_type, part))
                    if i < len(parts) - 1:
                        lines.append(current_line)
                        current_line = []
            else:
                if value:
                    current_line.append((token_type, value))
        if current_line or not lines:
            lines.append(current_line)

        # Safeguard: If the editor is entirely empty, render an empty safe space
        if not lines or (len(lines) == 1 and not lines[0]):
            lines = [[(Token.Text, " ")]]
        return lines

    @staticmethod
    def get_text_width(text, font, draw=None):
        """Cross-version safe text width calculation in PIL."""
        if not text:
            return 0
        if draw and hasattr(draw, 'textlength'):
            try:
                return int(draw.textlength(text, font=font))
            except Exception:
                pass
        if hasattr(font, 'getbbox'):
            try:
                bbox = font.getbbox(text)
                return bbox[2] - bbox[0]
            except Exception:
                pass
        if hasattr(font, 'getsize'):
            try:
                return font.getsize(text)[0]
            except Exception:
                pass
        return len(text) * int(font.size * 0.6 if hasattr(font, 'size') else 8)

    @staticmethod
    def create_gradient(width, height, color1, color2):
        """Generates a smooth bilinear gradient background."""
        grad = Image.new('RGB', (2, 2))
        mid = (
            (color1[0] + color2[0]) // 2,
            (color1[1] + color2[1]) // 2,
            (color1[2] + color2[2]) // 2
        )
        grad.putpixel((0, 0), color1)
        grad.putpixel((1, 0), mid)
        grad.putpixel((0, 1), mid)
        grad.putpixel((1, 1), color2)
        return grad.resize((width, height), resample=Image.Resampling.BILINEAR)

    @classmethod
    def render(cls, code, language, theme_name, font_size, show_line_numbers,
               canvas_size_opt="Auto", gradient_name="Mint → Purple",
               enable_watermark=False, watermark_text="", scale_factor=1):
        """Calculates bounds, checks cache, adjusts canvas constraints, and renders high-res outputs."""
        
        # 0. Smart Cache Key Lookup
        cache_key = (
            hash(code), language, theme_name, font_size, show_line_numbers,
            canvas_size_opt, gradient_name, enable_watermark, watermark_text, scale_factor
        )

        if cache_key in cls._cache:
            return cls._cache[cache_key].copy()

        lexer_class = LEXERS.get(language, PythonLexer)
        lexer = lexer_class()
        tokens = list(pygments.lex(code, lexer))
        token_lines = cls.split_tokens_by_line(tokens)

        theme_info = THEME_COLORS.get(theme_name, THEME_COLORS["Monokai"])
        pyg_style_name = theme_info["pygments"]

        try:
            pyg_style = get_style_by_name(pyg_style_name)
        except ClassNotFound:
            try:
                pyg_style = get_style_by_name("monokai")
            except ClassNotFound:
                from pygments.styles.monokai import MonokaiStyle
                pyg_style = MonokaiStyle

        # 1. Base Dimensions (Nominal 1x calculations)
        nominal_font = cls.get_monospace_font(font_size)

        try:
            if hasattr(nominal_font, 'getbbox'):
                nominal_char_height = nominal_font.getbbox("Agj")[3] - nominal_font.getbbox("Agj")[1]
            else:
                nominal_char_height = nominal_font.getsize("Agj")[1]
        except Exception:
            nominal_char_height = font_size

        nominal_line_height = int(nominal_char_height * 1.5)
        nominal_inner_padding = 32
        nominal_top_bar_height = 45

        # Measure baseline bounds
        nominal_line_num_width = 0
        if show_line_numbers:
            line_num_sample = f" {len(token_lines): >3}  "
            nominal_line_num_width = cls.get_text_width(line_num_sample, nominal_font)

        nominal_max_line_width = 0
        for idx, line in enumerate(token_lines):
            line_w = 0
            if show_line_numbers:
                line_w += nominal_line_num_width
            for token_type, value in line:
                line_w += cls.get_text_width(value, nominal_font)
            if line_w > nominal_max_line_width:
                nominal_max_line_width = line_w

        nominal_card_width = int(max(nominal_max_line_width + 2 * nominal_inner_padding, 350))
        nominal_card_height = int(len(token_lines) * nominal_line_height + nominal_top_bar_height + nominal_inner_padding)

        # 2. Preset Constraint Calculations (if applicable)
        preset = CANVAS_PRESETS.get(canvas_size_opt)
        card_scale_ratio = 1.0

        if preset is not None:
            w_base, h_base = preset
            max_allowed_w = w_base - 120
            max_allowed_h = h_base - 120
            # Scale down the internal card locally if it overflows preset boundaries
            if nominal_card_width > max_allowed_w or nominal_card_height > max_allowed_h:
                card_scale_ratio = max(0.1, min(max_allowed_w / nominal_card_width, max_allowed_h / nominal_card_height))

        # 3. Target Render Scale Combination
        rendering_scale = scale_factor * card_scale_ratio

        # Determine finalized resolutions
        if preset is None:
            # Auto canvas fits tightly
            r_card_width = int(nominal_card_width * scale_factor)
            r_card_height = int(nominal_card_height * scale_factor)
            outer_margin = int(60 * scale_factor)
            canvas_width = r_card_width + 2 * outer_margin
            canvas_height = r_card_height + 2 * outer_margin
        else:
            # Aspect ratio fixed canvas scaled by factor
            canvas_width = int(preset[0] * scale_factor)
            canvas_height = int(preset[1] * scale_factor)
            r_card_width = int(nominal_card_width * rendering_scale)
            r_card_height = int(nominal_card_height * rendering_scale)

        # 4. Create and render high-resolution code card
        r_font_size = int(font_size * rendering_scale)
        render_font = cls.get_monospace_font(r_font_size)
        r_inner_padding = int(nominal_inner_padding * rendering_scale)
        r_top_bar_height = int(nominal_top_bar_height * rendering_scale)

        # Calculate heights under exact rendering scale
        try:
            if hasattr(render_font, 'getbbox'):
                r_char_height = render_font.getbbox("Agj")[3] - render_font.getbbox("Agj")[1]
            else:
                r_char_height = render_font.getsize("Agj")[1]
        except Exception:
            r_char_height = r_font_size
        r_line_height = int(r_char_height * 1.5)

        card_img = Image.new('RGBA', (r_card_width, r_card_height), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card_img)

        # Card body
        bg_rgb = cls.hex_to_rgb(theme_info["bg"])
        card_draw.rounded_rectangle([0, 0, r_card_width, r_card_height], radius=int(12 * rendering_scale), fill=bg_rgb + (255,))
        card_draw.rounded_rectangle([0, 0, r_card_width, r_card_height], radius=int(12 * rendering_scale), outline=(255, 255, 255, 20), width=max(1, int(1 * rendering_scale)))

        # MacOS Traffic Lights
        dot_radius = int(5 * rendering_scale)
        dot_y = int(20 * rendering_scale)
        dot_x_base = int(20 * rendering_scale)
        dot_spacing = int(16 * rendering_scale)
        card_draw.ellipse([dot_x_base - dot_radius, dot_y - dot_radius, dot_x_base + dot_radius, dot_y + dot_radius], fill=(255, 95, 86, 255))
        card_draw.ellipse([dot_x_base + dot_spacing - dot_radius, dot_y - dot_radius, dot_x_base + dot_spacing + dot_radius, dot_y + dot_radius], fill=(255, 189, 46, 255))
        card_draw.ellipse([dot_x_base + 2 * dot_spacing - dot_radius, dot_y - dot_radius, dot_x_base + 2 * dot_spacing + dot_radius, dot_y + dot_radius], fill=(39, 201, 63, 255))

        # Carbon Header Text "main.ext • Language"
        lang_exts = {
            "Python": "main.py",
            "JavaScript": "index.js",
            "HTML": "index.html",
            "CSS": "styles.css",
            "JSON": "data.json",
            "C++": "main.cpp",
            "Java": "Main.java"
        }
        filename = lang_exts.get(language, "main.txt")
        header_text = f"{filename} • {language}"

        header_font_size = max(11, int(r_font_size * 0.75))
        header_font = cls.get_monospace_font(header_font_size)
        header_w = cls.get_text_width(header_text, header_font)
        header_x = (r_card_width - header_w) // 2

        try:
            if hasattr(header_font, 'getbbox'):
                header_h = header_font.getbbox(header_text)[3] - header_font.getbbox(header_text)[1]
            else:
                header_h = header_font.getsize(header_text)[1]
        except Exception:
            header_h = header_font_size
        header_y = (r_top_bar_height - header_h) // 2

        header_color = cls.hex_to_rgb("#8a8a93") + (255,)
        card_draw.text((header_x, header_y), header_text, font=header_font, fill=header_color)

        # Draw Code Lines
        r_line_num_col_width = 0
        if show_line_numbers:
            line_num_sample = f" {len(token_lines): >3}  "
            r_line_num_col_width = cls.get_text_width(line_num_sample, render_font)

        for idx, line_tokens in enumerate(token_lines):
            current_y = r_top_bar_height + idx * r_line_height
            current_x = r_inner_padding

            if show_line_numbers:
                num_str = f" {idx + 1: >3}  "
                num_color = cls.hex_to_rgb("#6272a4") + (255,)
                card_draw.text((current_x, current_y), num_str, font=render_font, fill=num_color)
                current_x += r_line_num_col_width

            for token_type, value in line_tokens:
                token_color_hex = cls.get_token_color(token_type, pyg_style, theme_info["fg"])
                token_color = cls.hex_to_rgb(token_color_hex) + (255,)
                card_draw.text((current_x, current_y), value, font=render_font, fill=token_color)
                current_x += cls.get_text_width(value, render_font, card_draw)

        # 5. Build high-performance shadow using 1x baseline and upscale mapping
        base_w_1x = int(canvas_width / scale_factor)
        base_h_1x = int(canvas_height / scale_factor)
        card_w_1x = int(r_card_width / scale_factor)
        card_h_1x = int(r_card_height / scale_factor)

        shadow_1x = Image.new('RGBA', (base_w_1x, base_h_1x), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_1x)

        c_x_1x = (base_w_1x - card_w_1x) // 2
        c_y_1x = (base_h_1x - card_h_1x) // 2

        s_left = c_x_1x + 10
        s_top = c_y_1x + 15
        s_right = s_left + card_w_1x
        s_bottom = s_top + card_h_1x

        shadow_draw.rounded_rectangle([s_left, s_top, s_right, s_bottom], radius=14, fill=(0, 0, 0, 110))
        shadow_1x = shadow_1x.filter(ImageFilter.GaussianBlur(radius=18))

        # Scaling up to full canvas dimensions
        shadow_img = shadow_1x.resize((canvas_width, canvas_height), resample=Image.Resampling.BILINEAR)

        # 6. Generate Gradient Background Canvas & Composite
        grad_colors = GRADIENT_PRESETS.get(gradient_name, GRADIENT_PRESETS["Mint → Purple"])
        color1, color2 = grad_colors
        base = cls.create_gradient(canvas_width, canvas_height, color1, color2)
        base = base.convert('RGBA')

        # Paste Shadow & Card Centered
        paste_x = (canvas_width - r_card_width) // 2
        paste_y = (canvas_height - r_card_height) // 2

        base.alpha_composite(shadow_img)
        base.alpha_composite(card_img, (paste_x, paste_y))

        # 7. Apply Custom Watermark
        if enable_watermark and watermark_text.strip():
            watermark_size = max(10, int(13 * scale_factor))
            watermark_font = cls.get_monospace_font(watermark_size)
            w_width = cls.get_text_width(watermark_text, watermark_font)
            wx = canvas_width - w_width - int(25 * scale_factor)
            wy = canvas_height - int(30 * scale_factor)

            draw_base = ImageDraw.Draw(base)
            draw_base.text((wx, wy), watermark_text, font=watermark_font, fill=(255, 255, 255, 110))

        final_img = base.convert('RGB')

        # Handle LRU eviction and save to Cache
        if len(cls._cache) >= cls._cache_limit:
            first_key = next(iter(cls._cache))
            cls._cache.pop(first_key, None)

        cls._cache[cache_key] = final_img
        return final_img.copy()


class ForgeSnapApp(QMainWindow):
    """Main Application window organizing UI layouts, QSettings state and Drag & Drop events."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ForgeSnap v1.3.0")
        self.setMinimumSize(1200, 700)
        self.resize(1280, 800)

        # Allow full window drag & drop
        self.setAcceptDrops(True)

        # Store full high-res pixmap buffers for responsive scaling
        self.current_preview_image = None
        self.current_pixmap = None

        # Debounce timer for smooth updates
        self.preview_timer = QTimer()
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.update_preview)

        self.setup_ui()
        self.apply_styles()

        # Generate and apply a high-tech programmatically-drawn neon icon
        self.setWindowIcon(self.generate_app_icon())

        # Load previously saved QSettings or load initial data fallback
        self.load_settings()
        self.update_preview()

    def generate_app_icon(self):
        """Generates a high-tech custom application icon programmatically without external files."""
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Glowing gradient background inside rounded corners
        for y in range(64):
            # Gradient: Neon Teal #00ffaa to Royal Purple #9d4edd
            r = int(0 + (157 - 0) * (y / 63.0))
            g = int(255 + (78 - 255) * (y / 63.0))
            b = int(170 + (221 - 170) * (y / 63.0))
            draw.line([(0, y), (63, y)], fill=(r, g, b, 40))

        # Sleek outer neon border
        draw.rounded_rectangle([4, 4, 59, 59], radius=16, fill=(11, 11, 13, 255), outline=(0, 255, 170, 255), width=3)

        # Stylized terminal brackets '< / >' inside
        draw.line([(22, 22), (14, 32), (22, 42)], fill=(0, 255, 170, 255), width=3)
        draw.line([(42, 22), (50, 32), (42, 42)], fill=(0, 255, 170, 255), width=3)
        draw.line([(35, 18), (29, 46)], fill=(157, 78, 221, 255), width=3)

        with io.BytesIO() as byte_io:
            img.save(byte_io, format='PNG')
            qimg = QImage.fromData(byte_io.getvalue())
            pixmap = QPixmap.fromImage(qimg)
            return QIcon(pixmap)

    def setup_ui(self):
        # Central Widget & Horizontal Splitter
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- LEFT PANEL: Editor & Controls ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 10, 0)

        # Brand Header
        header_layout = QVBoxLayout()
        title_label = QLabel("ForgeSnap")
        title_label.setObjectName("appTitle")
        sub_label = QLabel("Beautiful code screenshots instantly")
        sub_label.setObjectName("appSubTitle")
        header_layout.addWidget(title_label)
        header_layout.addWidget(sub_label)
        left_layout.addLayout(header_layout)

        # Editor
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Paste your code here...")
        left_layout.addWidget(self.editor)

        # Control Panel Frame
        control_frame = QFrame()
        control_frame.setObjectName("controlFrame")
        control_layout = QGridLayout(control_frame)
        control_layout.setSpacing(12)

        # Row 0: Language & Theme
        control_layout.addWidget(QLabel("Language"), 0, 0)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(list(LEXERS.keys()))
        control_layout.addWidget(self.lang_combo, 0, 1)

        control_layout.addWidget(QLabel("Theme"), 0, 2)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEME_COLORS.keys()))
        control_layout.addWidget(self.theme_combo, 0, 3)

        # Row 1: Font Size & Canvas Size
        control_layout.addWidget(QLabel("Font Size"), 1, 0)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(10, 30)
        self.font_spin.setValue(15)
        control_layout.addWidget(self.font_spin, 1, 1)

        control_layout.addWidget(QLabel("Canvas Size"), 1, 2)
        self.canvas_size_combo = QComboBox()
        self.canvas_size_combo.addItems(list(CANVAS_PRESETS.keys()))
        control_layout.addWidget(self.canvas_size_combo, 1, 3)

        # Row 2: Gradient & Export Quality
        control_layout.addWidget(QLabel("Gradient"), 2, 0)
        self.gradient_combo = QComboBox()
        self.gradient_combo.addItems(list(GRADIENT_PRESETS.keys()))
        control_layout.addWidget(self.gradient_combo, 2, 1)

        control_layout.addWidget(QLabel("Export Quality"), 2, 2)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_MAP.keys()))
        self.quality_combo.setCurrentText("2× (High)")
        control_layout.addWidget(self.quality_combo, 2, 3)

        # Row 3: Line Numbers Checkbox & Watermark Text Setup
        self.line_num_checkbox = QCheckBox("Show line numbers")
        self.line_num_checkbox.setChecked(True)
        control_layout.addWidget(self.line_num_checkbox, 3, 0, 1, 2, Qt.AlignmentFlag.AlignVCenter)

        # Watermark layout embedded in grid (Col 2: Checkbox, Col 3: Line Edit)
        self.watermark_checkbox = QCheckBox("Watermark")
        self.watermark_checkbox.setChecked(False)
        control_layout.addWidget(self.watermark_checkbox, 3, 2, Qt.AlignmentFlag.AlignVCenter)

        self.watermark_text_edit = QLineEdit("Generated with ForgeSnap")
        self.watermark_text_edit.setPlaceholderText("Watermark text...")
        control_layout.addWidget(self.watermark_text_edit, 3, 3)

        # Row 4: Buttons (Copy Image & Export PNG)
        self.copy_btn = QPushButton("Copy Image")
        self.copy_btn.setObjectName("copyBtn")
        control_layout.addWidget(self.copy_btn, 4, 0, 1, 2)

        self.export_btn = QPushButton("Export PNG")
        self.export_btn.setObjectName("exportBtn")
        control_layout.addWidget(self.export_btn, 4, 2, 1, 2)

        left_layout.addWidget(control_frame)
        splitter.addWidget(left_widget)

        # --- RIGHT PANEL: Live Preview Area ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)

        preview_header = QLabel("Live Preview")
        preview_header.setObjectName("panelHeader")
        right_layout.addWidget(preview_header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("previewScroll")

        scroll_content = QWidget()
        scroll_content_layout = QHBoxLayout(scroll_content)
        scroll_content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.preview_label = QLabel()
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        scroll_content_layout.addWidget(self.preview_label)

        self.scroll_area.setWidget(scroll_content)
        right_layout.addWidget(self.scroll_area)

        splitter.addWidget(right_widget)
        splitter.setSizes([550, 650])

        # --- Connect Events ---
        self.editor.textChanged.connect(self.on_code_changed)
        self.lang_combo.currentIndexChanged.connect(self.update_preview)
        self.theme_combo.currentIndexChanged.connect(self.update_preview)
        self.font_spin.valueChanged.connect(self.update_preview)
        self.canvas_size_combo.currentIndexChanged.connect(self.update_preview)
        self.gradient_combo.currentIndexChanged.connect(self.update_preview)
        self.quality_combo.currentIndexChanged.connect(self.update_preview)
        self.line_num_checkbox.stateChanged.connect(self.update_preview)
        self.watermark_checkbox.stateChanged.connect(self.update_preview)
        self.watermark_text_edit.textChanged.connect(self.on_code_changed)

        self.copy_btn.clicked.connect(self.copy_image)
        self.export_btn.clicked.connect(self.export_png)

    def apply_styles(self):
        """Elegant obsidian-dark styling with neon emerald highlights."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0b0b0d;
            }
            QWidget {
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
            }
            QLabel {
                color: #94a3b8;
                font-size: 13px;
                font-weight: 500;
            }
            #appTitle {
                color: #ffffff;
                font-size: 28px;
                font-weight: 800;
                letter-spacing: -1px;
            }
            #appSubTitle {
                color: #64748b;
                font-size: 13px;
                font-weight: 400;
                margin-bottom: 12px;
            }
            #panelHeader {
                color: #64748b;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-bottom: 6px;
            }
            QPlainTextEdit {
                background-color: #121215;
                color: #f1f5f9;
                border: 1px solid #1f1f23;
                border-radius: 12px;
                font-family: 'Consolas', 'Fira Code', 'Courier New', monospace;
                font-size: 14px;
                padding: 16px;
            }
            QPlainTextEdit:focus {
                border: 1px solid #00ffaa;
            }
            #controlFrame {
                background-color: #121215;
                border: 1px solid #1f1f23;
                border-radius: 16px;
                padding: 16px;
            }
            QComboBox, QSpinBox, QLineEdit {
                background-color: #1a1a1e;
                border: 1px solid #27272a;
                border-radius: 8px;
                color: #f8fafc;
                padding: 8px 12px;
                font-size: 13px;
                min-height: 24px;
            }
            QComboBox:hover, QSpinBox:hover, QLineEdit:hover {
                border-color: #3f3f46;
            }
            QComboBox:focus, QSpinBox:focus, QLineEdit:focus {
                border-color: #00ffaa;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #121215;
                color: #f8fafc;
                selection-background-color: #1f1f23;
                selection-color: #00ffaa;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
            QCheckBox {
                color: #94a3b8;
                font-size: 13px;
                font-weight: 500;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #27272a;
                border-radius: 5px;
                background-color: #1a1a1e;
            }
            QCheckBox::indicator:hover {
                border-color: #3f3f46;
            }
            QCheckBox::indicator:checked {
                background-color: #00ffaa;
                border-color: #00ffaa;
            }
            QPushButton {
                border: none;
                border-radius: 8px;
                font-weight: 700;
                font-size: 14px;
                padding: 12px 20px;
            }
            QPushButton#exportBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00ffaa, stop:1 #00dd90);
                color: #0b0b0d;
            }
            QPushButton#exportBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1affb5, stop:1 #1ae69d);
            }
            QPushButton#exportBtn:pressed {
                background: #00bb7a;
            }
            QPushButton#copyBtn {
                background-color: #1a1a1e;
                border: 1px solid #27272a;
                color: #f8fafc;
            }
            QPushButton#copyBtn:hover {
                background-color: #242429;
                border-color: #3f3f46;
                color: #ffffff;
            }
            #previewScroll {
                background-color: #0b0b0d;
                border: 1px solid #1f1f23;
                border-radius: 16px;
            }
            QSplitter::handle {
                background-color: #1f1f23;
                width: 4px;
                height: 4px;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background-color: #0b0b0d;
                border: none;
                width: 8px;
                height: 8px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background-color: #1f1f23;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background-color: #27272a;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                background: none;
            }
        """)

    def load_initial_data(self):
        initial_snippet = (
            "import math\n\n"
            "class ForgeSnap:\n"
            "    def __init__(self, name: str):\n"
            "        self.name = name\n"
            "        self.version = \"1.3.0\"\n\n"
            "    def greet(self) -> str:\n"
            "        # Drag and Drop code files onto this window!\n"
            "        msg = f\"Welcome to {self.name} v{self.version}!\"\n"
            "        print(msg)\n"
            "        return msg\n\n"
            "snap = ForgeSnap(\"ForgeSnap\")\n"
            "snap.greet()"
        )
        self.editor.setPlainText(initial_snippet)

    # --- QSettings Implementation ---

    def block_all_signals(self, block):
        """Temporarily blocks signals of all widgets during state load to avoid redundant rendering."""
        self.editor.blockSignals(block)
        self.lang_combo.blockSignals(block)
        self.theme_combo.blockSignals(block)
        self.font_spin.blockSignals(block)
        self.canvas_size_combo.blockSignals(block)
        self.gradient_combo.blockSignals(block)
        self.quality_combo.blockSignals(block)
        self.line_num_checkbox.blockSignals(block)
        self.watermark_checkbox.blockSignals(block)
        self.watermark_text_edit.blockSignals(block)

    def restore_combobox_text(self, combo, value):
        """Finds index for string value and sets selected combobox index safely."""
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_settings(self):
        """Saves current GUI states and code input safely using QSettings on app shutdown."""
        settings = QSettings("ForgeSnap", "ForgeSnapApp")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("code", self.editor.toPlainText())
        settings.setValue("language", self.lang_combo.currentText())
        settings.setValue("theme", self.theme_combo.currentText())
        settings.setValue("font_size", self.font_spin.value())
        settings.setValue("canvas_size", self.canvas_size_combo.currentText())
        settings.setValue("gradient", self.gradient_combo.currentText())
        settings.setValue("quality", self.quality_combo.currentText())
        settings.setValue("show_line_numbers", self.line_num_checkbox.isChecked())
        settings.setValue("enable_watermark", self.watermark_checkbox.isChecked())
        settings.setValue("watermark_text", self.watermark_text_edit.text())

    def load_settings(self):
        """Loads previously saved states or initializes defaults without cascading render updates."""
        settings = QSettings("ForgeSnap", "ForgeSnapApp")

        geom = settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

        self.block_all_signals(True)

        saved_code = settings.value("code")
        if saved_code is not None:
            self.editor.setPlainText(saved_code)
        else:
            self.load_initial_data()

        self.restore_combobox_text(self.lang_combo, settings.value("language", "Python"))
        self.restore_combobox_text(self.theme_combo, settings.value("theme", "Dracula"))
        self.restore_combobox_text(self.canvas_size_combo, settings.value("canvas_size", "Auto"))
        self.restore_combobox_text(self.gradient_combo, settings.value("gradient", "Mint → Purple"))
        self.restore_combobox_text(self.quality_combo, settings.value("quality", "2× (High)"))

        self.font_spin.setValue(int(settings.value("font_size", 15)))

        show_ln = settings.value("show_line_numbers")
        if show_ln is not None:
            self.line_num_checkbox.setChecked(str(show_ln).lower() == 'true' or show_ln is True)

        enable_wm = settings.value("enable_watermark")
        if enable_wm is not None:
            self.watermark_checkbox.setChecked(str(enable_wm).lower() == 'true' or enable_wm is True)

        wm_text = settings.value("watermark_text")
        if wm_text is not None:
            self.watermark_text_edit.setText(wm_text)

        self.block_all_signals(False)

    def closeEvent(self, event):
        """Ensures modern state saving is successfully triggered before window closes."""
        self.save_settings()
        super().closeEvent(event)

    # --- Drag & Drop Implementation ---

    def dragEnterEvent(self, event):
        """Allows dragging files into the window boundaries."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Tracks current drag action inside the window canvas."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Processes dropped URLs, parses local files and loads matching extensions."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                local_path = urls[0].toLocalFile()
                if os.path.isfile(local_path):
                    self.load_dropped_file(local_path)
                    event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def load_dropped_file(self, file_path):
        """Loads contents of external files with fallback encodings and detects programming language."""
        try:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='latin-1') as f:
                    content = f.read()

            self.editor.setPlainText(content)

            # Auto-detect language mappings from file extensions
            _, ext = os.path.splitext(file_path)
            ext = ext.lower().replace(".", "")

            ext_map = {
                "py": "Python",
                "js": "JavaScript", "jsx": "JavaScript", "ts": "JavaScript", "tsx": "JavaScript",
                "html": "HTML", "htm": "HTML",
                "css": "CSS",
                "json": "JSON",
                "cpp": "C++", "cc": "C++", "h": "C++", "hpp": "C++",
                "java": "Java"
            }

            matched_lang = ext_map.get(ext)
            if matched_lang:
                idx = self.lang_combo.findText(matched_lang)
                if idx >= 0:
                    self.lang_combo.setCurrentIndex(idx)

            self.update_preview()
        except Exception as e:
            print(f"Failed to load dropped file: {e}")

    # --- Core Render & GUI Controllers ---

    def on_code_changed(self):
        self.preview_timer.start(150)

    def update_preview(self):
        """Generates PIL composition at baseline (1x) for instant live feedback."""
        code = self.editor.toPlainText()
        language = self.lang_combo.currentText()
        theme = self.theme_combo.currentText()
        font_size = self.font_spin.value()
        show_lines = self.line_num_checkbox.isChecked()
        canvas_size = self.canvas_size_combo.currentText()
        gradient = self.gradient_combo.currentText()
        enable_watermark = self.watermark_checkbox.isChecked()
        watermark_text = self.watermark_text_edit.text()

        # Build PNG locally at scale=1 for real-time responsiveness
        self.current_preview_image = ForgeSnapRenderer.render(
            code=code,
            language=language,
            theme_name=theme,
            font_size=font_size,
            show_line_numbers=show_lines,
            canvas_size_opt=canvas_size,
            gradient_name=gradient,
            enable_watermark=enable_watermark,
            watermark_text=watermark_text,
            scale_factor=1
        )

        # Convert PIL to QImage in a safe managed context
        with io.BytesIO() as img_byte_arr:
            self.current_preview_image.save(img_byte_arr, format='PNG')
            qimg = QImage.fromData(img_byte_arr.getvalue())
            self.current_pixmap = QPixmap.fromImage(qimg)

        self.display_preview_image()

    def generate_export_image(self):
        """Generates the finalized image rendered natively at the selected Quality Scale."""
        code = self.editor.toPlainText()
        language = self.lang_combo.currentText()
        theme = self.theme_combo.currentText()
        font_size = self.font_spin.value()
        show_lines = self.line_num_checkbox.isChecked()
        canvas_size = self.canvas_size_combo.currentText()
        gradient = self.gradient_combo.currentText()
        enable_watermark = self.watermark_checkbox.isChecked()
        watermark_text = self.watermark_text_edit.text()

        quality_str = self.quality_combo.currentText()
        scale_factor = QUALITY_MAP.get(quality_str, 1)

        return ForgeSnapRenderer.render(
            code=code,
            language=language,
            theme_name=theme,
            font_size=font_size,
            show_line_numbers=show_lines,
            canvas_size_opt=canvas_size,
            gradient_name=gradient,
            enable_watermark=enable_watermark,
            watermark_text=watermark_text,
            scale_factor=scale_factor
        )

    def display_preview_image(self):
        """Displays preview cleanly scaled to viewport bounds without breaking layout constraints."""
        if self.current_pixmap is None or self.current_pixmap.isNull():
            return

        viewport_size = self.scroll_area.viewport().size()
        scroll_w = max(100, viewport_size.width() - 15)
        scroll_h = max(100, viewport_size.height() - 15)

        # Downscale smoothly only if the actual image size exceeds our layout bounds
        if self.current_pixmap.width() > scroll_w or self.current_pixmap.height() > scroll_h:
            scaled_pixmap = self.current_pixmap.scaled(
                scroll_w,
                scroll_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.preview_label.setPixmap(scaled_pixmap)
        else:
            self.preview_label.setPixmap(self.current_pixmap)

    def resizeEvent(self, event):
        """Overrides window resize event to trigger dynamically responsive preview adjustments."""
        super().resizeEvent(event)
        self.display_preview_image()

    def copy_image(self):
        """Copies the currently generated image directly to the system clipboard at full selected quality."""
        export_image = self.generate_export_image()
        if not export_image:
            return

        with io.BytesIO() as img_byte_arr:
            export_image.save(img_byte_arr, format='PNG')
            qimg = QImage.fromData(img_byte_arr.getvalue())
            clipboard = QApplication.clipboard()
            clipboard.setImage(qimg)

    def export_png(self):
        """Saves high-resolution rendered representation to local disk using selected export quality."""
        export_image = self.generate_export_image()
        if not export_image:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Code Screenshot",
            "forgesnap.png",
            "PNG Images (*.png)"
        )

        if file_path:
            export_image.save(file_path, "PNG", dpi=(300, 300))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ForgeSnapApp()
    window.show()
    sys.exit(app.exec())
