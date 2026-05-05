"""
MT5 Local Copy Trader — GUI (tkinter)
"""

import os
import sys
import json
import uuid
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Optional

try:
    import MetaTrader5 as mt5
    _MT5_OK = True
except ImportError:
    _MT5_OK = False

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

try:
    from copier import CopyTrader, is_terminal_running
    _COPIER_OK = True
except ImportError:
    _COPIER_OK = False

# Определяем базовую директорию (для EXE и для .py одинаково)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MT5CopyTrader")
CONFIG_FILE = os.path.join(APP_DATA_DIR, "config.json")
STATE_FILE = os.path.join(APP_DATA_DIR, "state.json")
LOGS_DIR = os.path.join(APP_DATA_DIR, "logs")

# Цвета MT5-стиль
BG = "#1E1E1E"
BG2 = "#2D2D2D"
BG3 = "#383838"
FG = "#D4D4D4"
FG2 = "#9A9A9A"
ACCENT = "#1E88E5"
ACCENT_HOVER = "#42A5F5"
GREEN = "#4CAF50"
GREEN_HOVER = "#66BB6A"
RED = "#EF5350"
RED_HOVER = "#EF5350"
YELLOW = "#FFC107"
BORDER = "#4A4A4A"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_SMALL = ("Segoe UI", 8)
FONT_MONO = ("Consolas", 8)


# ─────────────────────────────────────────────────────────────
#  Диалог выбора символа
# ─────────────────────────────────────────────────────────────

class SymbolPickerDialog(tk.Toplevel):
    def __init__(self, parent, symbols: List[str], title_text: str = "Выбор символа"):
        super().__init__(parent)
        self.selected: Optional[str] = None
        self._all_symbols = symbols

        self.title(title_text)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        self._build()
        self._center(parent)

    def _center(self, parent):
        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw2, py2 = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"{320}x{400}+{pw + (pw2 - 320) // 2}+{py + (py2 - 400) // 2}")

    def _build(self):
        # Поиск
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill="x", padx=8, pady=6)

        tk.Label(frm, text="🔍", bg=BG, fg=FG2, font=FONT).pack(side="left")
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *_: self._filter())
        ent = tk.Entry(frm, textvariable=self.var_search, width=30,
                       bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                       font=FONT, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=ACCENT)
        ent.pack(side="left", padx=(4, 0), fill="x", expand=True)
        ent.focus_set()

        # Список
        frm_list = tk.Frame(self, bg=BG)
        frm_list.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.listbox = tk.Listbox(
            frm_list, bg=BG2, fg=FG, font=FONT,
            selectbackground=ACCENT, selectforeground=BG,
            relief="flat", highlightthickness=0, activestyle="none"
        )
        sb = ttk.Scrollbar(frm_list, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)

        self.listbox.bind("<Double-1>", lambda e: self._pick())
        self.listbox.bind("<Return>", lambda e: self._pick())

        for s in self._all_symbols:
            self.listbox.insert("end", s)

        # Кнопки
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))

        tk.Button(btn_frame, text="Выбрать", command=self._pick,
                  bg=ACCENT, fg=BG, relief="flat", font=FONT_BOLD,
                  activebackground=ACCENT_HOVER, activeforeground=BG,
                  cursor="hand2", padx=14, pady=3).pack(side="left", padx=4)
        tk.Button(btn_frame, text="Отмена", command=self.destroy,
                  bg=BG3, fg=FG2, relief="flat", font=FONT,
                  activebackground=BG2, activeforeground=FG,
                  cursor="hand2", padx=14, pady=3).pack(side="left", padx=4)

    def _filter(self):
        query = self.var_search.get().strip().upper()
        self.listbox.delete(0, "end")
        for s in self._all_symbols:
            if not query or query in s.upper():
                self.listbox.insert("end", s)

    def _pick(self):
        sel = self.listbox.curselection()
        if sel:
            self.selected = self.listbox.get(sel[0])
            self.destroy()


# ─────────────────────────────────────────────────────────────
#  Диалог настроек слейва
# ─────────────────────────────────────────────────────────────

class SlaveDialog(tk.Toplevel):
    def __init__(self, parent, slave_data: Optional[Dict] = None):
        super().__init__(parent)
        self.result: Optional[Dict] = None
        self._symbol_rows: List[Dict] = []
        self._master_symbols: List[str] = []
        self._slave_symbols: List[str] = []

        self.title("Настройки слейва")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()

        data = slave_data or {}

        self._build(data)
        self._center(parent)

    def _center(self, parent):
        self.update_idletasks()
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw2 = parent.winfo_width()
        py2 = parent.winfo_height()
        w = self.winfo_width()
        h = self.winfo_height()
        x = pw + (pw2 - w) // 2
        y = py + (py2 - h) // 2
        self.geometry(f"+{x}+{y}")

    def _label(self, parent, text, **kw):
        return tk.Label(parent, text=text, bg=BG, fg=FG2, font=FONT, **kw)

    def _entry(self, parent, textvariable=None, width=30, **kw):
        e = tk.Entry(parent, textvariable=textvariable, width=width,
                     bg=BG3, fg=FG, insertbackground=FG,
                     relief="flat", font=FONT,
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT, **kw)
        return e

    def _build(self, data: Dict):
        pad = {"padx": 10, "pady": 4}

        # ── Имя ─────────────────────────────────────────────
        frm_top = tk.Frame(self, bg=BG)
        frm_top.pack(fill="x", **pad)

        self._label(frm_top, "Имя:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_name = tk.StringVar(value=data.get("name", ""))
        self._entry(frm_top, self.var_name, width=28).grid(
            row=0, column=1, sticky="ew", padx=(6, 0), pady=2)

        self._label(frm_top, "Путь к terminal64.exe:").grid(
            row=1, column=0, sticky="w", pady=2)
        self.var_path = tk.StringVar(value=data.get("path", ""))
        path_frame = tk.Frame(frm_top, bg=BG)
        path_frame.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=2)
        self._entry(path_frame, self.var_path, width=22).pack(
            side="left", fill="x", expand=True)
        tk.Button(path_frame, text="Обзор", command=self._browse,
                  bg=BG3, fg=ACCENT, relief="flat", font=FONT,
                  activebackground=BG2, activeforeground=ACCENT,
                  cursor="hand2").pack(side="left", padx=(4, 0))

        # ── Разделитель ──────────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=6)

        # ── Символы ─────────────────────────────────────────
        sym_header = tk.Frame(self, bg=BG)
        sym_header.pack(fill="x", padx=10, pady=(4, 0))
        self._label(sym_header, "Символы (мастер → слейв):").pack(side="left")
        tk.Button(sym_header, text="📥 Загрузить из терминалов", command=self._load_symbols,
                  bg=BG3, fg=ACCENT, relief="flat", font=FONT_SMALL,
                  activebackground=BG2, activeforeground=ACCENT,
                  cursor="hand2").pack(side="right")

        self.lbl_sym_status = tk.Label(self, text="", bg=BG, fg=FG2,
                                       font=FONT_SMALL)
        self.lbl_sym_status.pack(anchor="w", padx=10)

        self.sym_frame = tk.Frame(self, bg=BG)
        self.sym_frame.pack(fill="x", padx=10, pady=4)

        # Загружаем существующие символы
        symbol_map = data.get("symbol_map", {})
        for master_sym, slave_sym in symbol_map.items():
            self._add_symbol_row(master_sym, slave_sym)

        tk.Button(self, text="+ Добавить символ", command=self._add_symbol_row,
                  bg=BG3, fg=GREEN, relief="flat", font=FONT,
                  activebackground=BG2, activeforeground=GREEN,
                  cursor="hand2").pack(anchor="w", padx=10, pady=(0, 4))

        # ── Разделитель ──────────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=6)

        # ── Риск ─────────────────────────────────────────────
        frm_risk = tk.Frame(self, bg=BG)
        frm_risk.pack(fill="x", padx=10, pady=4)

        self.var_risk_type = tk.StringVar(
            value=data.get("risk_type", "percent"))
        self.var_risk_type.trace_add("write", lambda *_: self._update_risk_label())

        self._label(frm_risk, "Риск:").grid(
            row=0, column=0, sticky="w", pady=2)
        risk_input_frame = tk.Frame(frm_risk, bg=BG)
        risk_input_frame.grid(row=0, column=1, sticky="w", padx=(6, 0))

        self.var_risk_value = tk.StringVar(
            value=str(data.get("risk_value", "1.0")))
        self._entry(risk_input_frame, self.var_risk_value, width=8).pack(
            side="left")

        for text, val in [("%", "percent"), ("$", "fixed")]:
            tk.Radiobutton(
                risk_input_frame, text=text, variable=self.var_risk_type,
                value=val, bg=BG, fg=FG, selectcolor=BG3,
                activebackground=BG, activeforeground=FG, font=FONT,
                indicatoron=False, padx=6, relief="flat",
                bd=1
            ).pack(side="left", padx=2)

        self.lbl_risk_hint = tk.Label(frm_risk, text="", bg=BG, fg=FG2,
                                       font=FONT_SMALL)
        self.lbl_risk_hint.grid(row=1, column=0, columnspan=2,
                                 sticky="w", padx=(0, 0), pady=(2, 0))

        self._label(frm_risk, "Лот если нет SL:").grid(
            row=2, column=0, sticky="w", pady=2)
        self.var_default_lot = tk.StringVar(
            value=str(data.get("default_lot", "0.01")))
        self._entry(frm_risk, self.var_default_lot, width=10).grid(
            row=2, column=1, sticky="w", padx=(6, 0), pady=2)

        # ── Кнопки ───────────────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=6)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=(0, 10))

        tk.Button(btn_frame, text="Сохранить", command=self._save,
                  bg=ACCENT, fg=BG, relief="flat", font=FONT_BOLD,
                  activebackground=ACCENT_HOVER, activeforeground=BG,
                  cursor="hand2", padx=16, pady=4).pack(side="left", padx=6)

        tk.Button(btn_frame, text="Отмена", command=self.destroy,
                  bg=BG3, fg=FG2, relief="flat", font=FONT,
                  activebackground=BG2, activeforeground=FG,
                  cursor="hand2", padx=16, pady=4).pack(side="left", padx=6)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Выберите terminal64.exe",
            filetypes=[("MetaTrader 5", "terminal64.exe"), ("EXE файлы", "*.exe")],
            initialdir="C:\\",
        )
        if path:
            self.var_path.set(path.replace("/", "\\"))

    def _update_risk_label(self):
        if self.var_risk_type.get() == "percent":
            self.lbl_risk_hint.config(text="Процент от баланса слейва")
        else:
            self.lbl_risk_hint.config(text="Фиксированная сумма в валюте депозита")

    def _load_symbols(self):
        if not _MT5_OK:
            self.lbl_sym_status.config(text="⚠️ Библиотека MT5 не установлена", fg=RED)
            return

        master_path = self._get_master_path()
        slave_path = self.var_path.get().strip()

        if master_path:
            self._master_symbols = self._fetch_symbols(master_path, "мастер")
        if slave_path:
            self._slave_symbols = self._fetch_symbols(slave_path, "слейв")

        parts = []
        if self._master_symbols:
            parts.append(f"мастер: {len(self._master_symbols)}")
        if self._slave_symbols:
            parts.append(f"слейв: {len(self._slave_symbols)}")
        self.lbl_sym_status.config(text="Загружено: " + ", ".join(parts), fg=GREEN)

    def _get_master_path(self) -> str:
        parent = self.master
        if hasattr(parent, "var_master_path"):
            return parent.var_master_path.get().strip()
        return ""

    def _fetch_symbols(self, path: str, label: str) -> List[str]:
        if not path or not is_terminal_running(path):
            self.lbl_sym_status.config(
                text=f"⚠️ Терминал {label} не запущен", fg=YELLOW)
            return []
        if not mt5.initialize(path=path):
            self.lbl_sym_status.config(
                text=f"⚠️ Ошибка подключения к {label}", fg=YELLOW)
            return []
        try:
            symbols = mt5.symbols_get()
            if symbols:
                return sorted([s.name for s in symbols if s.name])
            return []
        finally:
            mt5.shutdown()

    def _add_symbol_row(self, master_sym: str = "", slave_sym: str = ""):
        row_frame = tk.Frame(self.sym_frame, bg=BG)
        row_frame.pack(fill="x", pady=2)

        var_master = tk.StringVar(value=master_sym)
        var_slave = tk.StringVar(value=slave_sym)

        ent_master = self._entry(row_frame, var_master, width=10)
        ent_master.pack(side="left")

        def pick_master():
            dlg = SymbolPickerDialog(self, self._master_symbols, "Символ мастера")
            self.wait_window(dlg)
            if dlg.selected:
                var_master.set(dlg.selected)

        tk.Button(row_frame, text="📋", command=pick_master,
                  bg=BG3, fg=ACCENT, relief="flat", font=FONT_SMALL,
                  activebackground=BG2, activeforeground=ACCENT,
                  cursor="hand2", width=2).pack(side="left", padx=1)

        tk.Label(row_frame, text="→", bg=BG, fg=FG2, font=FONT).pack(
            side="left", padx=4)

        ent_slave = self._entry(row_frame, var_slave, width=10)
        ent_slave.pack(side="left")

        def pick_slave():
            dlg = SymbolPickerDialog(self, self._slave_symbols, "Символ слейва")
            self.wait_window(dlg)
            if dlg.selected:
                var_slave.set(dlg.selected)

        tk.Button(row_frame, text="📋", command=pick_slave,
                  bg=BG3, fg=ACCENT, relief="flat", font=FONT_SMALL,
                  activebackground=BG2, activeforeground=ACCENT,
                  cursor="hand2", width=2).pack(side="left", padx=1)

        def remove():
            row_frame.destroy()
            self._symbol_rows = [r for r in self._symbol_rows
                                  if r["frame"] != row_frame]

        tk.Button(row_frame, text="✕", command=remove,
                  bg=BG, fg=RED, relief="flat", font=FONT,
                  activebackground=BG, activeforeground=RED,
                  cursor="hand2").pack(side="left", padx=(4, 0))

        self._symbol_rows.append({
            "frame": row_frame,
            "master": var_master,
            "slave": var_slave,
        })

    def _save(self):
        name = self.var_name.get().strip()
        path = self.var_path.get().strip()

        if not name:
            messagebox.showwarning("Ошибка", "Введите имя слейва", parent=self)
            return
        if not path:
            messagebox.showwarning("Ошибка", "Укажите путь к терминалу", parent=self)
            return

        symbol_map = {}
        for row in self._symbol_rows:
            m = row["master"].get().strip().upper()
            s = row["slave"].get().strip()
            if m and s:
                symbol_map[m] = s

        try:
            risk_value = float(self.var_risk_value.get())
        except ValueError:
            messagebox.showwarning("Ошибка", "Неверное значение риска", parent=self)
            return

        try:
            default_lot = float(self.var_default_lot.get())
        except ValueError:
            messagebox.showwarning("Ошибка", "Неверный лот по умолчанию", parent=self)
            return

        self.result = {
            "name": name,
            "path": path,
            "symbol_map": symbol_map,
            "risk_type": self.var_risk_type.get(),
            "risk_value": risk_value,
            "default_lot": default_lot,
        }
        self.destroy()


# ─────────────────────────────────────────────────────────────
#  Строка слейва в списке
# ─────────────────────────────────────────────────────────────

class SlaveRow(tk.Frame):
    def __init__(self, parent, slave_data: Dict,
                 on_edit, on_delete, on_toggle):
        super().__init__(parent, bg=BG2, pady=3)
        self.slave_data = slave_data
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.on_toggle = on_toggle

        self._build()

    def _build(self):
        # Чекбокс включения
        self.var_enabled = tk.BooleanVar(
            value=self.slave_data.get("enabled", True))
        cb = tk.Checkbutton(
            self, variable=self.var_enabled,
            command=self._toggle,
            bg=BG2, activebackground=BG2,
            selectcolor=BG3
        )
        cb.pack(side="left", padx=(6, 0))

        # Имя
        name = self.slave_data.get("name", "—")
        tk.Label(self, text=name, bg=BG2, fg=FG, font=FONT_BOLD,
                 width=10, anchor="w").pack(side="left", padx=4)

        # Путь (сокращённый)
        path = self.slave_data.get("path", "")
        short_path = ("..." + path[-28:]) if len(path) > 30 else path
        tk.Label(self, text=short_path, bg=BG2, fg=FG2, font=FONT_SMALL,
                 width=30, anchor="w").pack(side="left", padx=4)

        # Символы
        sym_map = self.slave_data.get("symbol_map", {})
        sym_text = ", ".join(
            f"{k}→{v}" for k, v in list(sym_map.items())[:3]
        )
        if len(sym_map) > 3:
            sym_text += "…"
        tk.Label(self, text=sym_text or "—", bg=BG2, fg=ACCENT,
                 font=FONT_SMALL, width=22, anchor="w").pack(side="left", padx=4)

        # Риск
        rt = self.slave_data.get("risk_type", "percent")
        rv = self.slave_data.get("risk_value", 1.0)
        risk_text = f"{rv}%" if rt == "percent" else f"${rv}"
        tk.Label(self, text=risk_text, bg=BG2, fg=YELLOW,
                 font=FONT_SMALL, width=6, anchor="w").pack(side="left", padx=4)

        # Статус
        self.lbl_status = tk.Label(self, text="⚪ —", bg=BG2, fg=FG2,
                                    font=FONT_SMALL, width=22, anchor="w")
        self.lbl_status.pack(side="left", padx=4)

        # Кнопки
        btn_frame = tk.Frame(self, bg=BG2)
        btn_frame.pack(side="right", padx=6)

        tk.Button(btn_frame, text="✎", command=self._edit,
                  bg=BG3, fg=ACCENT, relief="flat", font=FONT,
                  activebackground=BG, activeforeground=ACCENT,
                  cursor="hand2", width=2).pack(side="left", padx=2)

        tk.Button(btn_frame, text="✕", command=self._delete,
                  bg=BG3, fg=RED, relief="flat", font=FONT,
                  activebackground=BG, activeforeground=RED,
                  cursor="hand2", width=2).pack(side="left", padx=2)

    def update_status(self, status: str):
        self.lbl_status.config(text=status)

    def _toggle(self):
        self.slave_data["enabled"] = self.var_enabled.get()
        self.on_toggle(self.slave_data)

    def _edit(self):
        self.on_edit(self.slave_data, self)

    def _delete(self):
        self.on_delete(self.slave_data, self)

    def refresh(self):
        """Перестраивает строку после редактирования."""
        for w in self.winfo_children():
            w.destroy()
        self._build()


# ─────────────────────────────────────────────────────────────
#  Главное окно
# ─────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MT5 Copy Trader")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(720, 560)

        self._slaves: List[Dict] = []
        self._slave_rows: List[SlaveRow] = []
        self._slave_status_labels: Dict[str, tk.Label] = {}
        self._trader = None
        self._check_timer = None

        self._build_ui()
        self._load_config()
        self._schedule_check()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Построение UI ────────────────────────────────────────

    def _build_ui(self):
        # ── Заголовок ────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG, pady=8)
        hdr.pack(fill="x", padx=12)

        tk.Label(hdr, text="🔄  MT5 Copy Trader",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).pack(side="left")

        btn_frame = tk.Frame(hdr, bg=BG)
        btn_frame.pack(side="right")

        self.btn_start = tk.Button(
            btn_frame, text="▶  Старт", command=self._start,
            bg=GREEN, fg=BG, relief="flat", font=FONT_BOLD,
            activebackground=GREEN_HOVER, activeforeground=BG,
            cursor="hand2", padx=14, pady=4
        )
        self.btn_start.pack(side="left", padx=4)

        self.btn_stop = tk.Button(
            btn_frame, text="■  Стоп", command=self._stop,
            bg=RED, fg=BG, relief="flat", font=FONT_BOLD,
            activebackground=RED_HOVER, activeforeground=BG,
            cursor="hand2", padx=14, pady=4, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=4)

        self.btn_check = tk.Button(
            btn_frame, text="🔌  Проверить связь", command=self._check_connection,
            bg=BG3, fg=ACCENT, relief="flat", font=FONT,
            activebackground=BG2, activeforeground=ACCENT,
            cursor="hand2", padx=10, pady=4
        )
        self.btn_check.pack(side="left", padx=4)

        # ── Мастер ───────────────────────────────────────────
        master_frame = tk.LabelFrame(
            self, text=" МАСТЕР ", bg=BG, fg=ACCENT,
            font=FONT_BOLD, relief="flat",
            highlightthickness=1, highlightbackground=BORDER
        )
        master_frame.pack(fill="x", padx=12, pady=(0, 6))

        inner = tk.Frame(master_frame, bg=BG)
        inner.pack(fill="x", padx=8, pady=6)

        tk.Label(inner, text="Путь к terminal64.exe:", bg=BG, fg=FG2,
                 font=FONT).pack(side="left")

        self.var_master_path = tk.StringVar()
        tk.Entry(inner, textvariable=self.var_master_path, width=46,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                 font=FONT, highlightthickness=1,
                 highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", padx=6)

        tk.Button(inner, text="Обзор", command=self._browse_master,
                  bg=BG3, fg=ACCENT, relief="flat", font=FONT,
                  activebackground=BG2, activeforeground=ACCENT,
                  cursor="hand2").pack(side="left")

        self.lbl_master_status = tk.Label(
            inner, text="⚪ —", bg=BG, fg=FG2, font=FONT_SMALL)
        self.lbl_master_status.pack(side="left", padx=(12, 0))

        # ── Слейвы ───────────────────────────────────────────
        slaves_frame = tk.LabelFrame(
            self, text=" СЛЕЙВЫ ", bg=BG, fg=ACCENT,
            font=FONT_BOLD, relief="flat",
            highlightthickness=1, highlightbackground=BORDER
        )
        slaves_frame.pack(fill="both", expand=False, padx=12, pady=(0, 6))

        # Заголовок списка + кнопка добавить
        sh = tk.Frame(slaves_frame, bg=BG)
        sh.pack(fill="x", padx=8, pady=(4, 0))

        for col, w in [("", 2), ("Имя", 10), ("Путь", 30),
                        ("Символы", 22), ("Риск", 6), ("Статус", 22)]:
            tk.Label(sh, text=col, bg=BG, fg=FG2, font=FONT_SMALL,
                     width=w, anchor="w").pack(side="left", padx=4)

        tk.Button(sh, text="+ Добавить", command=self._add_slave,
                  bg=BG3, fg=GREEN, relief="flat", font=FONT,
                  activebackground=BG2, activeforeground=GREEN,
                  cursor="hand2").pack(side="right", padx=6)

        ttk.Separator(slaves_frame, orient="horizontal").pack(
            fill="x", padx=8, pady=2)

        # Прокручиваемый список слейвов
        list_container = tk.Frame(slaves_frame, bg=BG, height=160)
        list_container.pack(fill="both", expand=True, padx=8, pady=4)
        list_container.pack_propagate(False)

        canvas = tk.Canvas(list_container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical",
                                  command=canvas.yview)
        self.slaves_list = tk.Frame(canvas, bg=BG)

        self.slaves_list.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.slaves_list, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # ── Статус-бар ───────────────────────────────────────
        status_outer = tk.LabelFrame(
            self, text=" СТАТУС ", bg=BG, fg=ACCENT,
            font=FONT_BOLD, relief="flat",
            highlightthickness=1, highlightbackground=BORDER
        )
        status_outer.pack(fill="x", padx=12, pady=(0, 6))

        self.status_bar = tk.Frame(status_outer, bg=BG)
        self.status_bar.pack(fill="x", padx=8, pady=4)

        self.lbl_master_status_bar = tk.Label(
            self.status_bar, text="Мастер: ⚪ —",
            bg=BG, fg=FG2, font=FONT_SMALL)
        self.lbl_master_status_bar.pack(side="left", padx=(0, 12))

        # ── Лог ──────────────────────────────────────────────
        log_frame = tk.LabelFrame(
            self, text=" ЛОГ ", bg=BG, fg=ACCENT,
            font=FONT_BOLD, relief="flat",
            highlightthickness=1, highlightbackground=BORDER
        )
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        log_inner = tk.Frame(log_frame, bg=BG)
        log_inner.pack(fill="both", expand=True, padx=4, pady=4)

        self.log_text = tk.Text(
            log_inner, bg=BG2, fg=FG, font=FONT_MONO,
            relief="flat", state="disabled",
            wrap="word", height=8,
            highlightthickness=0
        )
        log_scroll = ttk.Scrollbar(log_inner, orient="vertical",
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        # Теги для цветного лога
        self.log_text.tag_config("ok", foreground=GREEN)
        self.log_text.tag_config("err", foreground=RED)
        self.log_text.tag_config("warn", foreground=YELLOW)
        self.log_text.tag_config("info", foreground=FG2)

        # Кнопка очистки лога
        tk.Button(log_frame, text="Очистить лог", command=self._clear_log,
                  bg=BG, fg=FG2, relief="flat", font=FONT_SMALL,
                  activebackground=BG2, activeforeground=FG,
                  cursor="hand2").pack(anchor="e", padx=6, pady=(0, 4))

    # ── Мастер: обзор ────────────────────────────────────────

    def _browse_master(self):
        path = filedialog.askopenfilename(
            title="Выберите terminal64.exe мастера",
            filetypes=[("MetaTrader 5", "terminal64.exe"),
                       ("EXE файлы", "*.exe")],
            initialdir="C:\\",
        )
        if path:
            self.var_master_path.set(path.replace("/", "\\"))
            self._save_config()

    # ── Слейвы: добавить / редактировать / удалить ───────────

    def _add_slave(self):
        dlg = SlaveDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            data = dlg.result
            data["id"] = str(uuid.uuid4())[:8]
            data["enabled"] = True
            self._slaves.append(data)
            self._add_slave_row(data)
            self._save_config()

    def _add_slave_row(self, data: Dict):
        row = SlaveRow(
            self.slaves_list, data,
            on_edit=self._edit_slave,
            on_delete=self._delete_slave,
            on_toggle=self._toggle_slave,
        )
        row.pack(fill="x", pady=1)
        self._slave_rows.append(row)

    def _edit_slave(self, data: Dict, row: SlaveRow):
        dlg = SlaveDialog(self, data)
        self.wait_window(dlg)
        if dlg.result:
            sid = data.get("id", "")
            enabled = data.get("enabled", True)
            data.clear()
            data.update(dlg.result)
            data["id"] = sid
            data["enabled"] = enabled
            row.slave_data = data
            row.refresh()
            self._save_config()

    def _delete_slave(self, data: Dict, row: SlaveRow):
        if messagebox.askyesno(
                "Удалить слейв",
                f"Удалить слейв «{data.get('name', '?')}»?",
                parent=self):
            self._slaves.remove(data)
            self._slave_rows.remove(row)
            row.destroy()
            self._save_config()

    def _toggle_slave(self, data: Dict):
        self._save_config()

    # ── Старт / Стоп / Проверка связи ─────────────────────────

    def _schedule_check(self):
        """Запускает периодическую проверку связи (каждые 10 сек)."""
        if self._check_timer:
            self.after_cancel(self._check_timer)
        if not (self._trader and self._trader.is_running()):
            self._check_connection()
            self._check_timer = self.after(10000, self._schedule_check)

    def _check_connection(self):
        """Проверяет подключение ко всем терминалам и обновляет статусы."""
        if not _MT5_OK:
            self._log("❌ Библиотека MetaTrader5 не установлена", "err")
            self.lbl_master_status.config(text="🔴 MT5 не установлен", fg=RED)
            self.lbl_master_status_bar.config(text="Мастер: 🔴 MT5 не установлен", fg=RED)
            return

        master_path = self.var_master_path.get().strip()
        if not master_path:
            self.lbl_master_status.config(text="🔴 Путь не задан", fg=RED)
            self.lbl_master_status_bar.config(text="Мастер: 🔴 Путь не задан", fg=RED)
        else:
            if not is_terminal_running(master_path):
                status = "🔴 Не запущен"
                self.lbl_master_status.config(text=status, fg=RED)
                self.lbl_master_status_bar.config(text=f"Мастер: {status}", fg=RED)
            elif mt5.initialize(path=master_path):
                try:
                    acc = mt5.account_info()
                    if acc:
                        ti = mt5.terminal_info()
                        if ti and not ti.trade_allowed:
                            status = f"🔴 #{acc.login} Алготрейдинг ВЫКЛ"
                            color = RED
                        else:
                            status = f"🟢 #{acc.login} ${acc.balance:.2f}"
                            color = GREEN
                        self.lbl_master_status.config(text=status, fg=color)
                        self.lbl_master_status_bar.config(text=f"Мастер: {status}", fg=color)
                    else:
                        status = "🔴 Нет аккаунта"
                        self.lbl_master_status.config(text=status, fg=RED)
                        self.lbl_master_status_bar.config(text=f"Мастер: {status}", fg=RED)
                finally:
                    mt5.shutdown()
            else:
                err = mt5.last_error()
                status = f"🔴 Ошибка ({err[0]})"
                self.lbl_master_status.config(text=status, fg=RED)
                self.lbl_master_status_bar.config(text=f"Мастер: {status}", fg=RED)

        for row in self._slave_rows:
            d = row.slave_data
            sname = d.get("name", "?")
            slave_path = d.get("path", "")
            if not slave_path:
                status = "🔴 Путь не задан"
            elif not is_terminal_running(slave_path):
                status = "🔴 Не запущен"
            elif mt5.initialize(path=slave_path):
                try:
                    acc = mt5.account_info()
                    if acc:
                        ti = mt5.terminal_info()
                        if ti and not ti.trade_allowed:
                            status = f"🔴 #{acc.login} Алготрейдинг ВЫКЛ"
                        else:
                            status = f"🟢 #{acc.login} ${acc.balance:.2f}"
                    else:
                        status = "🔴 Нет аккаунта"
                finally:
                    mt5.shutdown()
            else:
                err = mt5.last_error()
                status = f"🔴 Ошибка ({err[0]})"

            color = GREEN if "🟢" in status else RED
            row.update_status(status)

            if sname not in self._slave_status_labels:
                lbl = tk.Label(self.status_bar, text="", bg=BG, fg=FG2,
                               font=FONT_SMALL)
                lbl.pack(side="left", padx=(0, 12))
                self._slave_status_labels[sname] = lbl
            self._slave_status_labels[sname].config(
                text=f"{sname}: {status}", fg=color)

        self._log("🔌 Проверка связи завершена", "info")

    def _start(self):
        master_path = self.var_master_path.get().strip()
        if not master_path:
            messagebox.showwarning(
                "Ошибка", "Укажите путь к мастер-терминалу", parent=self)
            return

        if not self._slaves:
            messagebox.showwarning(
                "Ошибка", "Добавьте хотя бы один слейв", parent=self)
            return

        self._save_config()

        config = self._build_config()

        if not _COPIER_OK:
            messagebox.showerror(
                "Ошибка",
                "Не найден модуль copier.py рядом с приложением",
                parent=self)
            return

        self._trader = CopyTrader(
            config=config,
            state_file=STATE_FILE,
            log_callback=self._on_log,
            status_callback=self._on_status,
        )
        self._trader.start()

        if self._check_timer:
            self.after_cancel(self._check_timer)
            self._check_timer = None
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._log("✅ Копитрейдер запущен", "ok")

    def _stop(self):
        if self._trader:
            self._trader.stop()
            self._trader = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._log("■ Копитрейдер остановлен", "warn")
        self.lbl_master_status.config(text="⚪ —", fg=FG2)
        self.lbl_master_status_bar.config(text="Мастер: ⚪ —", fg=FG2)
        for row in self._slave_rows:
            row.update_status("⚪ —")
        for w in self.status_bar.winfo_children():
            w.destroy()
        self._slave_status_labels.clear()
        self._schedule_check()

    # ── Колбэки от CopyTrader ────────────────────────────────

    def _on_log(self, msg: str):
        """Вызывается из фонового потока."""
        self.after(0, self._log, msg)

    def _on_status(self, terminal_id: str, status: str):
        """Вызывается из фонового потока."""
        self.after(0, self._update_status, terminal_id, status)

    def _update_status(self, terminal_id: str, status: str):
        if terminal_id == "master":
            color = GREEN if "🟢" in status else RED
            self.lbl_master_status.config(text=status, fg=color)
            self.lbl_master_status_bar.config(
                text=f"Мастер: {status}", fg=color)
        else:
            color = GREEN if "🟢" in status else RED
            for row in self._slave_rows:
                d = row.slave_data
                if d.get("name") == terminal_id or d.get("id") == terminal_id:
                    row.update_status(status)
                    break
            if terminal_id not in self._slave_status_labels:
                lbl = tk.Label(self.status_bar, text="", bg=BG, fg=FG2,
                               font=FONT_SMALL)
                lbl.pack(side="left", padx=(0, 12))
                self._slave_status_labels[terminal_id] = lbl
            self._slave_status_labels[terminal_id].config(
                text=f"{terminal_id}: {status}", fg=color)

    # ── Лог ──────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = "info"):
        if "✅" in msg:
            tag = "ok"
        elif "❌" in msg:
            tag = "err"
        elif "⚠️" in msg or "■" in msg:
            tag = "warn"

        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.config(state="disabled")

        self._write_log_file(msg)

    def _write_log_file(self, msg: str):
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_file = os.path.join(LOGS_DIR, f"{date_str}.log")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ── Конфиг ───────────────────────────────────────────────

    def _build_config(self) -> Dict:
        return {
            "master": {
                "path": self.var_master_path.get().strip()
            },
            "slaves": [
                {
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "enabled": s.get("enabled", True),
                    "path": s.get("path", ""),
                    "symbol_map": s.get("symbol_map", {}),
                    "risk_type": s.get("risk_type", "percent"),
                    "risk_value": s.get("risk_value", 1.0),
                    "default_lot": s.get("default_lot", 0.01),
                }
                for s in self._slaves
            ],
            "poll_interval_seconds": 1,
        }

    def _save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._build_config(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"⚠️ Ошибка сохранения конфига: {e}", "warn")

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return

        master = cfg.get("master", {})
        self.var_master_path.set(master.get("path", ""))

        for s in cfg.get("slaves", []):
            if "id" not in s:
                s["id"] = str(uuid.uuid4())[:8]
            self._slaves.append(s)
            self._add_slave_row(s)

    # ── Закрытие ─────────────────────────────────────────────

    def _on_close(self):
        if self._trader and self._trader.is_running():
            if not messagebox.askyesno(
                    "Выход",
                    "Копитрейдер запущен. Остановить и выйти?",
                    parent=self):
                return
            self._stop()
        self._save_config()
        self.destroy()


# ─────────────────────────────────────────────────────────────
#  Точка входа
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()