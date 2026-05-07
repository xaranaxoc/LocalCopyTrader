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
    from copier import CopyTrader, is_terminal_running, activate_terminal
    _COPIER_OK = True
except ImportError:
    _COPIER_OK = False

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MT5CopyTrader")
CONFIG_FILE = os.path.join(APP_DATA_DIR, "config.json")
STATE_FILE = os.path.join(APP_DATA_DIR, "state.json")
LOGS_DIR = os.path.join(APP_DATA_DIR, "logs")

BG = "#0F0F11"
BG_ROW = "#16161A"
BG_ROW_HOVER = "#1C1C22"
BG_INPUT = "#1E1E24"
FG = "#E0E0E0"
FG_DIM = "#6B6B76"
FG_LABEL = "#8B8B96"
ACCENT = "#6C5CE7"
ACCENT_H = "#7D6FF0"
GREEN = "#00D68F"
GREEN_DIM = "#00B377"
RED = "#FF6B6B"
RED_DIM = "#D44444"
YELLOW = "#FFD93D"
BORDER = "#2A2A32"
DIVIDER = "#222228"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_SM = ("Segoe UI", 8)
FONT_XS = ("Segoe UI", 7)
FONT_MONO = ("Cascadia Mono", 8)
FONT_MONO_SM = ("Cascadia Mono", 7)
FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_VAL = ("Segoe UI", 10)
FONT_VAL_BOLD = ("Segoe UI", 10, "bold")

#  (col_index, header, min_width, weight, anchor)
COL_SPEC = [
    (0, "ON", 36, 0, "center"),
    (1, "", 20, 0, "center"),
    (2, "ИМЯ", 72, 0, "w"),
    (3, "ЛОГИН", 72, 0, "w"),
    (4, "БАЛАНС", 88, 0, "e"),
    (5, "ЭКВИТИ", 88, 0, "e"),
    (6, "P&L", 72, 0, "e"),
    (7, "СИМВОЛЫ", 100, 1, "w"),
    (8, "РИСК", 60, 0, "e"),
    (9, "", 110, 0, "e"),
]


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
        self.geometry(f"{300}x{380}+{pw + (pw2 - 300) // 2}+{py + (py2 - 380) // 2}")

    def _build(self):
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill="x", padx=10, pady=8)
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *_: self._filter())
        ent = tk.Entry(frm, textvariable=self.var_search, width=28,
                       bg=BG_INPUT, fg=FG, insertbackground=FG, relief="flat",
                       font=FONT, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=ACCENT)
        ent.pack(fill="x")
        ent.focus_set()

        frm_list = tk.Frame(self, bg=BG)
        frm_list.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.listbox = tk.Listbox(frm_list, bg=BG_ROW, fg=FG, font=FONT,
                                   selectbackground=ACCENT, selectforeground="white",
                                   relief="flat", highlightthickness=0, activestyle="none")
        sb = ttk.Scrollbar(frm_list, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-1>", lambda e: self._pick())
        self.listbox.bind("<Return>", lambda e: self._pick())
        for s in self._all_symbols:
            self.listbox.insert("end", s)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=10, pady=(0, 8))
        self._btn(btn_frame, "Выбрать", self._pick, accent=True).pack(side="left", padx=(0, 6))
        self._btn(btn_frame, "Отмена", self.destroy).pack(side="left")

    def _btn(self, parent, text, cmd, accent=False):
        bg = ACCENT if accent else BG_INPUT
        fg = "white" if accent else FG_DIM
        abg = ACCENT_H if accent else BG_ROW_HOVER
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
                         font=FONT_BOLD if accent else FONT,
                         activebackground=abg, activeforeground=fg,
                         cursor="hand2", padx=12, pady=2)

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


class SlaveDialog(tk.Toplevel):
    def __init__(self, parent, slave_data: Optional[Dict] = None):
        super().__init__(parent)
        self.result: Optional[Dict] = None
        self._symbol_rows: List[Dict] = []
        self._master_symbols: List[str] = []
        self._slave_symbols: List[str] = []
        self._parent_app = parent
        self._updating_risk = False
        self.title("Настройки аккаунта")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()
        data = slave_data or {}
        self._build(data)
        self._center(parent)

    def _center(self, parent):
        self.update_idletasks()
        pw, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw2, py2 = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw + (pw2 - w) // 2}+{py + (py2 - h) // 2}")

    def _lbl(self, parent, text, **kw):
        return tk.Label(parent, text=text, bg=BG, fg=FG_LABEL, font=FONT_SM, **kw)

    def _ent(self, parent, var=None, width=28, **kw):
        return tk.Entry(parent, textvariable=var, width=width,
                        bg=BG_INPUT, fg=FG, insertbackground=FG, relief="flat",
                        font=FONT, highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT, **kw)

    def _btn(self, parent, text, cmd, accent=False, small=False):
        bg = ACCENT if accent else BG_INPUT
        fg = "white" if accent else FG_DIM
        abg = ACCENT_H if accent else BG_ROW_HOVER
        f = (FONT_BOLD if accent else FONT_SM) if not small else FONT_XS
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
                         font=f, activebackground=abg, activeforeground=fg,
                         cursor="hand2", padx=10, pady=2)

    def _build(self, data: Dict):
        pad = {"padx": 12, "pady": 3}
        frm_top = tk.Frame(self, bg=BG)
        frm_top.pack(fill="x", **pad)

        self._lbl(frm_top, "Имя").grid(row=0, column=0, sticky="w", pady=2)
        self.var_name = tk.StringVar(value=data.get("name", ""))
        self._ent(frm_top, self.var_name, 26).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=2)

        self._lbl(frm_top, "terminal64.exe").grid(row=1, column=0, sticky="w", pady=2)
        self.var_path = tk.StringVar(value=data.get("path", ""))
        path_frame = tk.Frame(frm_top, bg=BG)
        path_frame.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=2)
        self._ent(path_frame, self.var_path, 20).pack(side="left", fill="x", expand=True)
        self._btn(path_frame, "...", self._browse, small=True).pack(side="left", padx=(4, 0))

        tk.Frame(self, bg=DIVIDER, height=1).pack(fill="x", padx=12, pady=6)

        sym_header = tk.Frame(self, bg=BG)
        sym_header.pack(fill="x", padx=12, pady=(2, 0))
        self._lbl(sym_header, "Символы (мастер \u2192 слейв)").pack(side="left")
        self._btn(sym_header, "Загрузить", self._load_symbols, small=True).pack(side="right")

        self.lbl_sym_status = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=FONT_XS)
        self.lbl_sym_status.pack(anchor="w", padx=12)

        self.sym_frame = tk.Frame(self, bg=BG)
        self.sym_frame.pack(fill="x", padx=12, pady=2)

        symbol_map = data.get("symbol_map", {})
        for master_sym, slave_sym in symbol_map.items():
            self._add_symbol_row(master_sym, slave_sym)

        self._btn(self, "+ Символ", self._add_symbol_row, small=True).pack(anchor="w", padx=12, pady=(0, 2))

        tk.Frame(self, bg=DIVIDER, height=1).pack(fill="x", padx=12, pady=6)

        # ── Риск (PositionSizer-стиль) ──────────────────────
        frm_risk = tk.Frame(self, bg=BG)
        frm_risk.pack(fill="x", padx=12, pady=2)

        self.var_risk_type = tk.StringVar(value=data.get("risk_type", "percent"))

        risk_value = data.get("risk_value", 1.0)
        risk_type = data.get("risk_type", "percent")

        self._lbl(frm_risk, "Риск %").grid(row=0, column=0, sticky="w", pady=2)
        pct_frame = tk.Frame(frm_risk, bg=BG)
        pct_frame.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=2)
        self.var_risk_pct = tk.StringVar(
            value=str(risk_value) if risk_type == "percent" else "")
        self._ent(pct_frame, self.var_risk_pct, 8).pack(side="left")

        self._lbl(frm_risk, "Риск $").grid(row=1, column=0, sticky="w", pady=2)
        doll_frame = tk.Frame(frm_risk, bg=BG)
        doll_frame.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=2)
        self.var_risk_doll = tk.StringVar(
            value=str(risk_value) if risk_type == "fixed" else "")
        self._ent(doll_frame, self.var_risk_doll, 8).pack(side="left")

        self.lbl_risk_hint = tk.Label(frm_risk, text="", bg=BG, fg=FG_DIM, font=FONT_XS)
        self.lbl_risk_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self.var_risk_pct.trace_add("write", lambda *_: self._sync_risk("percent"))
        self.var_risk_doll.trace_add("write", lambda *_: self._sync_risk("fixed"))

        self._lbl(frm_risk, "Лот без SL").grid(row=3, column=0, sticky="w", pady=2)
        self.var_default_lot = tk.StringVar(value=str(data.get("default_lot", "0.01")))
        self._ent(frm_risk, self.var_default_lot, 8).grid(row=3, column=1, sticky="w", padx=(6, 0), pady=2)

        self._lbl(frm_risk, "Макс. просадка %").grid(row=4, column=0, sticky="w", pady=2)
        self.var_max_drawdown = tk.StringVar(value=str(data.get("max_drawdown", 0)))
        self._ent(frm_risk, self.var_max_drawdown, 8).grid(row=4, column=1, sticky="w", padx=(6, 0), pady=2)
        tk.Label(frm_risk, text="0 = выкл", bg=BG, fg=FG_DIM, font=FONT_XS).grid(
            row=5, column=1, sticky="w", padx=(6, 0))

        tk.Frame(self, bg=DIVIDER, height=1).pack(fill="x", padx=12, pady=6)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=(0, 10))
        self._btn(btn_frame, "Сохранить", self._save, accent=True).pack(side="left", padx=6)
        self._btn(btn_frame, "Отмена", self.destroy).pack(side="left", padx=6)

    def _get_ref_balance(self) -> float:
        if hasattr(self._parent_app, '_rows') and self._parent_app._rows:
            for row, slave in zip(self._parent_app._rows, self._parent_app._slaves):
                if slave.get("name") == self.var_name.get().strip():
                    try:
                        return float(row.lbl_balance.cget("text").replace("$", "").replace(",", ""))
                    except Exception:
                        pass
        return 0.0

    def _sync_risk(self, source: str):
        if self._updating_risk:
            return
        self._updating_risk = True
        try:
            if source == "percent":
                try:
                    pct_val = float(self.var_risk_pct.get())
                    self.var_risk_type.set("percent")
                    bal = self._get_ref_balance()
                    if bal > 0:
                        self.var_risk_doll.set(f"{bal * pct_val / 100.0:.2f}")
                    self.lbl_risk_hint.config(text=f"{pct_val}% от баланса", fg=ACCENT)
                except (ValueError, tk.TclError):
                    pass
            elif source == "fixed":
                try:
                    doll_val = float(self.var_risk_doll.get())
                    self.var_risk_type.set("fixed")
                    bal = self._get_ref_balance()
                    if bal > 0:
                        self.var_risk_pct.set(f"{doll_val / bal * 100.0:.2f}")
                    self.lbl_risk_hint.config(text=f"${doll_val:.2f} фиксированный", fg=ACCENT)
                except (ValueError, tk.TclError):
                    pass
        finally:
            self._updating_risk = False

    def _browse(self):
        path = filedialog.askopenfilename(
            title="terminal64.exe", filetypes=[("MT5", "terminal64.exe"), ("EXE", "*.exe")],
            initialdir="C:\\")
        if path:
            self.var_path.set(path.replace("/", "\\"))

    def _load_symbols(self):
        if not _MT5_OK:
            self.lbl_sym_status.config(text="MT5 не установлен", fg=RED)
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
        self.lbl_sym_status.config(text="Загружено: " + ", ".join(parts), fg=GREEN_DIM)

    def _get_master_path(self) -> str:
        parent = self.master
        if hasattr(parent, "var_master_path"):
            return parent.var_master_path.get().strip()
        return ""

    def _fetch_symbols(self, path: str, label: str) -> List[str]:
        if not path or not is_terminal_running(path):
            self.lbl_sym_status.config(text=f"Терминал {label} не запущен", fg=YELLOW)
            return []
        if not mt5.initialize(path=path):
            self.lbl_sym_status.config(text=f"Ошибка подключения к {label}", fg=YELLOW)
            return []
        try:
            symbols = mt5.symbols_get()
            return sorted([s.name for s in symbols if s.name]) if symbols else []
        finally:
            mt5.shutdown()

    def _add_symbol_row(self, master_sym: str = "", slave_sym: str = ""):
        row_frame = tk.Frame(self.sym_frame, bg=BG)
        row_frame.pack(fill="x", pady=1)
        var_master = tk.StringVar(value=master_sym)
        var_slave = tk.StringVar(value=slave_sym)
        self._ent(row_frame, var_master, 8).pack(side="left")

        def pick_m():
            dlg = SymbolPickerDialog(self, self._master_symbols, "Мастер")
            self.wait_window(dlg)
            if dlg.selected:
                var_master.set(dlg.selected)

        self._btn(row_frame, "...", pick_m, small=True).pack(side="left", padx=1)
        tk.Label(row_frame, text="\u2192", bg=BG, fg=FG_DIM, font=FONT_SM).pack(side="left", padx=3)
        self._ent(row_frame, var_slave, 8).pack(side="left")

        def pick_s():
            dlg = SymbolPickerDialog(self, self._slave_symbols, "Слейв")
            self.wait_window(dlg)
            if dlg.selected:
                var_slave.set(dlg.selected)

        self._btn(row_frame, "...", pick_s, small=True).pack(side="left", padx=1)

        def remove():
            row_frame.destroy()
            self._symbol_rows = [r for r in self._symbol_rows if r["frame"] != row_frame]

        self._btn(row_frame, "\u2715", remove, small=True).pack(side="left", padx=(2, 0))
        self._symbol_rows.append({"frame": row_frame, "master": var_master, "slave": var_slave})

    def _save(self):
        name = self.var_name.get().strip()
        path = self.var_path.get().strip()
        if not name:
            messagebox.showwarning("Ошибка", "Введите имя", parent=self)
            return
        if not path:
            messagebox.showwarning("Ошибка", "Укажите путь", parent=self)
            return

        symbol_map = {}
        for row in self._symbol_rows:
            m = row["master"].get().strip().upper()
            s = row["slave"].get().strip()
            if m and s:
                symbol_map[m] = s

        risk_type = self.var_risk_type.get()
        try:
            if risk_type == "percent":
                risk_value = float(self.var_risk_pct.get())
            else:
                risk_value = float(self.var_risk_doll.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("Ошибка", "Неверное значение риска", parent=self)
            return
        try:
            default_lot = float(self.var_default_lot.get())
        except ValueError:
            messagebox.showwarning("Ошибка", "Неверный лот", parent=self)
            return
        try:
            max_drawdown = float(self.var_max_drawdown.get())
        except ValueError:
            max_drawdown = 0.0

        self.result = {
            "name": name, "path": path, "symbol_map": symbol_map,
            "risk_type": risk_type, "risk_value": risk_value,
            "default_lot": default_lot, "max_drawdown": max_drawdown,
        }
        self.destroy()


class AccountRow:
    def __init__(self, parent, row_index, slave_data, on_edit, on_delete, on_toggle, on_test, on_open, on_close_all):
        self._parent = parent
        self._row = row_index
        self.slave_data = slave_data
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._on_toggle = on_toggle
        self._on_test = on_test
        self._on_open = on_open
        self._on_close_all = on_close_all
        self._hover = False
        self._leave_timer = None
        self._widgets = []
        self._build()

    @property
    def row_index(self):
        return self._row

    @row_index.setter
    def row_index(self, value):
        self._row = value
        if hasattr(self, '_bg_frame') and self._bg_frame:
            self._bg_frame.grid(row=value)
        for w in self._widgets:
            w.grid(row=value)

    def _cur_bg(self):
        return BG_ROW_HOVER if self._hover else BG_ROW

    def _build(self):
        d = self.slave_data
        bg = BG_ROW
        r = self._row

        self._bg_frame = tk.Frame(self._parent, bg=bg)
        self._bg_frame.grid(row=r, column=0, columnspan=10, sticky="nsew", pady=1)
        self._bg_frame.lower()

        enabled = d.get("enabled", True)
        self.var_enabled = tk.BooleanVar(value=enabled)
        self.lbl_check = tk.Label(self._parent, text="\u2611" if enabled else "\u2610",
                                   bg=bg, fg=GREEN if enabled else FG_DIM,
                                   font=FONT_BOLD, cursor="hand2")
        self.lbl_check.grid(row=r, column=0, padx=(8, 2), sticky="ew")
        self.lbl_check.bind("<Button-1>", lambda e: self._toggle())
        self._widgets.append(self.lbl_check)

        self.lbl_dot = tk.Label(self._parent, text="\u25CF", bg=bg, fg=FG_DIM, font=FONT)
        self.lbl_dot.grid(row=r, column=1, padx=2, sticky="w")
        self._widgets.append(self.lbl_dot)

        self.lbl_name = tk.Label(self._parent, text=d.get("name", "\u2014"), bg=bg, fg=FG,
                                  font=FONT_BOLD, anchor="w")
        self.lbl_name.grid(row=r, column=2, padx=(4, 4), sticky="ew")
        self._widgets.append(self.lbl_name)

        self.lbl_login = tk.Label(self._parent, text="\u2014", bg=bg, fg=FG_DIM,
                                   font=FONT_MONO_SM, anchor="w")
        self.lbl_login.grid(row=r, column=3, padx=4, sticky="ew")
        self._widgets.append(self.lbl_login)

        self.lbl_balance = tk.Label(self._parent, text="\u2014", bg=bg, fg=FG,
                                     font=FONT_VAL_BOLD, anchor="e")
        self.lbl_balance.grid(row=r, column=4, padx=4, sticky="ew")
        self._widgets.append(self.lbl_balance)

        self.lbl_equity = tk.Label(self._parent, text="\u2014", bg=bg, fg=FG_DIM,
                                    font=FONT_MONO_SM, anchor="e")
        self.lbl_equity.grid(row=r, column=5, padx=4, sticky="ew")
        self._widgets.append(self.lbl_equity)

        self.lbl_pnl = tk.Label(self._parent, text="\u2014", bg=bg, fg=FG_DIM,
                                 font=FONT_VAL, anchor="e")
        self.lbl_pnl.grid(row=r, column=6, padx=4, sticky="ew")
        self._widgets.append(self.lbl_pnl)

        sym_map = d.get("symbol_map", {})
        sym_text = "  ".join(f"{k}\u2192{v}" for k, v in list(sym_map.items())[:3])
        if len(sym_map) > 3:
            sym_text += f" +{len(sym_map) - 3}"
        self.lbl_symbols = tk.Label(self._parent, text=sym_text or "\u2014", bg=bg, fg=FG_DIM,
                                     font=FONT_XS, anchor="w")
        self.lbl_symbols.grid(row=r, column=7, padx=4, sticky="ew")
        self._widgets.append(self.lbl_symbols)

        rt = d.get("risk_type", "percent")
        rv = d.get("risk_value", 1.0)
        risk_text = f"{rv}{'%' if rt == 'percent' else '$'}"
        self.lbl_risk = tk.Label(self._parent, text=risk_text, bg=bg, fg=YELLOW,
                                  font=FONT_SM, anchor="e")
        self.lbl_risk.grid(row=r, column=8, padx=4, sticky="ew")
        self._widgets.append(self.lbl_risk)

        bf = tk.Frame(self._parent, bg=bg)
        bf.grid(row=r, column=9, padx=(2, 6), sticky="e")
        tk.Button(bf, text="\U0001F4C8", command=self._open_terminal,
                  bg=bg, fg=FG_DIM, relief="flat", font=FONT_SM,
                  activebackground=BG_ROW_HOVER, activeforeground=ACCENT,
                  cursor="hand2", width=2, highlightthickness=0).pack(side="left", padx=1)
        tk.Button(bf, text="\u2716", command=self._close_all,
                  bg=bg, fg=RED_DIM, relief="flat", font=FONT_SM,
                  activebackground=BG_ROW_HOVER, activeforeground=RED,
                  cursor="hand2", width=2, highlightthickness=0).pack(side="left", padx=1)
        tk.Button(bf, text="\u26A0", command=self._test,
                  bg=bg, fg=YELLOW, relief="flat", font=FONT_SM,
                  activebackground=BG_ROW_HOVER, activeforeground=YELLOW,
                  cursor="hand2", width=2, highlightthickness=0).pack(side="left", padx=1)
        tk.Button(bf, text="\u270E", command=self._edit,
                  bg=bg, fg=FG_DIM, relief="flat", font=FONT_SM,
                  activebackground=BG_ROW_HOVER, activeforeground=ACCENT,
                  cursor="hand2", width=2, highlightthickness=0).pack(side="left", padx=1)
        tk.Button(bf, text="\u2715", command=self._delete,
                  bg=bg, fg=FG_DIM, relief="flat", font=FONT_SM,
                  activebackground=BG_ROW_HOVER, activeforeground=RED,
                  cursor="hand2", width=2, highlightthickness=0).pack(side="left", padx=1)
        self._widgets.append(bf)

        for w in self._widgets:
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def _on_enter(self, event=None):
        if self._leave_timer:
            self._leave_timer = None
        self._set_hover(True)

    def _on_leave(self, event=None):
        self._leave_timer = self._parent.after(50, self._do_leave)

    def _do_leave(self):
        self._leave_timer = None
        self._set_hover(False)

    def _set_hover(self, hover: bool):
        self._hover = hover
        bg = self._cur_bg()
        if hasattr(self, '_bg_frame') and self._bg_frame:
            self._bg_frame.configure(bg=bg)
        for w in self._widgets:
            self._recolor(w, bg)

    def _recolor(self, w, bg):
        try:
            if isinstance(w, tk.Label):
                w.configure(bg=bg)
            elif isinstance(w, tk.Frame):
                w.configure(bg=bg)
                for c in w.winfo_children():
                    self._recolor(c, bg)
            elif isinstance(w, tk.Button):
                w.configure(bg=bg)
        except Exception:
            pass

    def update_info(self, balance: float, equity: float, login: int = 0,
                    status: str = ""):
        bg = self._cur_bg()
        self.lbl_balance.config(text=f"${balance:,.2f}", bg=bg)
        self.lbl_equity.config(text=f"${equity:,.2f}", bg=bg)
        if login:
            self.lbl_login.config(text=f"#{login}", bg=bg)

        pnl = equity - balance
        pnl_color = GREEN if pnl >= 0 else RED
        pnl_sign = "+" if pnl >= 0 else ""
        self.lbl_pnl.config(text=f"{pnl_sign}${pnl:,.2f}", fg=pnl_color, bg=bg)

        if status:
            dot_color = GREEN if "\U0001F7E2" in status else RED if "\U0001F534" in status else YELLOW if "\U0001F7E1" in status else FG_DIM
            self.lbl_dot.config(fg=dot_color, bg=bg)

    def update_status_only(self, status: str, balance: float = 0, equity: float = 0):
        bg = self._cur_bg()
        dot_color = GREEN if "\U0001F7E2" in status else RED if "\U0001F534" in status else YELLOW if "\U0001F7E1" in status else FG_DIM
        self.lbl_dot.config(fg=dot_color, bg=bg)
        if balance > 0:
            self.lbl_balance.config(text=f"${balance:,.2f}", bg=bg)
        if equity > 0:
            self.lbl_equity.config(text=f"${equity:,.2f}", bg=bg)
            pnl = equity - balance
            pnl_color = GREEN if pnl >= 0 else RED
            pnl_sign = "+" if pnl >= 0 else ""
            self.lbl_pnl.config(text=f"{pnl_sign}${pnl:,.2f}", fg=pnl_color, bg=bg)

    def _toggle(self):
        new_val = not self.var_enabled.get()
        self.var_enabled.set(new_val)
        bg = self._cur_bg()
        self.lbl_check.config(text="\u2611" if new_val else "\u2610",
                               fg=GREEN if new_val else FG_DIM, bg=bg)
        self.slave_data["enabled"] = new_val
        if self._on_toggle:
            self._on_toggle(self.slave_data)

    def _edit(self):
        if self._on_edit:
            self._on_edit(self.slave_data, self)

    def _delete(self):
        if self._on_delete:
            self._on_delete(self.slave_data, self)

    def _test(self):
        if self._on_test:
            self._on_test(self.slave_data)

    def _open_terminal(self):
        if self._on_open:
            self._on_open(self.slave_data)

    def _close_all(self):
        if self._on_close_all:
            self._on_close_all(self.slave_data)

    def destroy(self):
        if self._leave_timer:
            try:
                self._parent.after_cancel(self._leave_timer)
            except Exception:
                pass
            self._leave_timer = None
        for w in self._widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._widgets.clear()
        if hasattr(self, '_bg_frame') and self._bg_frame:
            try:
                self._bg_frame.destroy()
            except Exception:
                pass

    def refresh(self, data: Dict):
        self.slave_data = data
        self.destroy()
        self._build()


class TradesTable(tk.Frame):
    COLS = ["time", "slave", "symbol", "dir", "lot", "master", "slave_tk", "status"]
    HEADERS = ["Время", "Слейв", "Символ", "\u2191\u2193", "Лот", "Мастер #", "Слейв #", "Статус"]
    WIDTHS = [62, 50, 64, 28, 40, 70, 70, 140]

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self._max_rows = 200
        self._build()

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("T.Treeview", background=BG_ROW, foreground=FG,
                        fieldbackground=BG_ROW, font=FONT_MONO_SM,
                        rowheight=17, borderwidth=0)
        style.configure("T.Treeview.Heading", background=BG_INPUT, foreground=FG_DIM,
                        font=FONT_XS, borderwidth=0, relief="flat")
        style.map("T.Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])
        style.map("T.Treeview.Heading", background=[("active", BG_ROW_HOVER)])

        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings",
                                  style="T.Treeview", height=6)
        for col, hdr, w in zip(self.COLS, self.HEADERS, self.WIDTHS):
            self.tree.heading(col, text=hdr, anchor="w")
            self.tree.column(col, width=w, minwidth=w, anchor="w", stretch=True)

        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("ok", foreground=GREEN)
        self.tree.tag_configure("err", foreground=RED)
        self.tree.tag_configure("warn", foreground=YELLOW)

    def add_trade(self, time_str: str, slave: str, symbol: str,
                  direction: str, lot: float, master_ticket: str,
                  slave_ticket: str, status: str, tag: str = "ok"):
        self.tree.insert("", 0, values=(
            time_str, slave, symbol, direction,
            f"{lot:.2f}", master_ticket, slave_ticket, status
        ), tags=(tag,))
        children = self.tree.get_children()
        while len(children) > self._max_rows:
            self.tree.delete(children[-1])
            children = self.tree.get_children()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MT5 Copy Trader")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(980, 680)
        self.geometry("1020x720")

        self._slaves: List[Dict] = []
        self._rows: List[AccountRow] = []
        self._trader = None
        self._check_timer = None
        self._session_stats = {"copied": 0, "failed": 0}
        self._min_lot_mode = False

        self._build_ui()
        self._load_config()
        self._schedule_check()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _make_btn(self, parent, text, cmd, accent=False, danger=False):
        if accent:
            bg, fg, abg = ACCENT, "white", ACCENT_H
        elif danger:
            bg, fg, abg = RED_DIM, "white", RED
        else:
            bg, fg, abg = BG_INPUT, FG_LABEL, BG_ROW_HOVER
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
                         font=FONT_BOLD if accent else FONT,
                         activebackground=abg, activeforeground=fg,
                         cursor="hand2", padx=10, pady=3, highlightthickness=0, bd=0)

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(hdr, text="Copy Trader", bg=BG, fg=FG, font=FONT_TITLE).pack(side="left")

        btn_area = tk.Frame(hdr, bg=BG)
        btn_area.pack(side="right")

        self.btn_start = self._make_btn(btn_area, "\u25B6  Старт", self._start, accent=True)
        self.btn_start.pack(side="left", padx=2)
        self.btn_stop = self._make_btn(btn_area, "\u25A0  Стоп", self._stop, danger=True)
        self.btn_stop.pack(side="left", padx=2)
        self.btn_stop.config(state="disabled")

        self._make_btn(btn_area, "\u2716 Все", self._close_all_open, danger=True).pack(side="left", padx=2)

        self._make_btn(btn_area, "\u25B6 Запуск всех", self._launch_all).pack(side="left", padx=2)

        # ── Мастер ───────────────────────────────────────────
        tk.Frame(self, bg=DIVIDER, height=1).pack(fill="x", padx=14, pady=(8, 0))

        master_f = tk.Frame(self, bg=BG_ROW)
        master_f.pack(fill="x", padx=14, pady=1)

        tk.Label(master_f, text="\u25CF", bg=BG_ROW, fg=ACCENT, font=FONT).grid(row=0, column=0, padx=(8, 2))
        tk.Label(master_f, text="МАСТЕР", bg=BG_ROW, fg=ACCENT, font=FONT_BOLD).grid(row=0, column=1, padx=4)

        self.var_master_path = tk.StringVar()
        tk.Entry(master_f, textvariable=self.var_master_path, width=36,
                 bg=BG_INPUT, fg=FG, insertbackground=FG, relief="flat",
                 font=FONT_SM, highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT).grid(row=0, column=2, padx=4, sticky="ew")
        self._make_btn(master_f, "...", self._browse_master).grid(row=0, column=3, padx=2)
        self._make_btn(master_f, "\U0001F4C8", self._open_master_terminal).grid(row=0, column=4, padx=(8, 2))
        self._make_btn(master_f, "\u2716", self._close_all_master, danger=True).grid(row=0, column=5, padx=2)

        self.lbl_master_login = tk.Label(master_f, text="\u2014", bg=BG_ROW, fg=FG_DIM,
                                          font=FONT_MONO_SM, anchor="w")
        self.lbl_master_login.grid(row=0, column=6, padx=6, sticky="ew")

        self.lbl_master_bal = tk.Label(master_f, text="\u2014", bg=BG_ROW, fg=FG,
                                        font=FONT_VAL_BOLD, anchor="e")
        self.lbl_master_bal.grid(row=0, column=7, padx=4, sticky="ew")

        self.lbl_master_eq = tk.Label(master_f, text="\u2014", bg=BG_ROW, fg=FG_DIM,
                                       font=FONT_MONO_SM, anchor="e")
        self.lbl_master_eq.grid(row=0, column=8, padx=4, sticky="ew")

        self.lbl_master_pnl = tk.Label(master_f, text="\u2014", bg=BG_ROW, fg=FG_DIM,
                                        font=FONT_VAL, anchor="e")
        self.lbl_master_pnl.grid(row=0, column=9, padx=4, sticky="ew")

        master_f.columnconfigure(2, weight=1)

        # ── Таблица аккаунтов ────────────────────────────────────
        self._table_frame = tk.Frame(self, bg=BG)
        self._table_frame.pack(fill="both", expand=True, padx=14, pady=2)

        for idx, _, min_w, weight, _ in COL_SPEC:
            self._table_frame.columnconfigure(idx, minsize=min_w, weight=weight)

        for idx, text, _, _, anchor in COL_SPEC:
            tk.Label(self._table_frame, text=text, bg=BG, fg=FG_DIM,
                     font=FONT_XS, anchor=anchor).grid(row=0, column=idx, padx=2, pady=(2, 0), sticky="ew")
        self._make_btn(self._table_frame, "+ Аккаунт", self._add_slave,
                       accent=True).grid(row=0, column=9, sticky="e", padx=2, pady=(2, 0))
        self._next_row = 1

        # Статистика
        stats_f = tk.Frame(self, bg=BG)
        stats_f.pack(fill="x", padx=14, pady=(2, 0))
        self.lbl_stats = tk.Label(stats_f, text="", bg=BG, fg=FG_DIM, font=FONT_SM)
        self.lbl_stats.pack(side="left")

        # Вкладки
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_INPUT, foreground=FG_DIM,
                        padding=[12, 3], font=FONT_SM, borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", BG_ROW)],
                  foreground=[("selected", FG)])

        self.notebook = ttk.Notebook(self, style="TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(4, 10))

        trades_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(trades_tab, text="  Сделки  ")
        self.trades_table = TradesTable(trades_tab)
        self.trades_table.pack(fill="both", expand=True, padx=1, pady=1)

        log_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(log_tab, text="  Лог  ")
        log_inner = tk.Frame(log_tab, bg=BG)
        log_inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.log_text = tk.Text(log_inner, bg=BG_ROW, fg=FG, font=FONT_MONO_SM,
                                relief="flat", state="disabled", wrap="word",
                                highlightthickness=0)
        log_sb = ttk.Scrollbar(log_inner, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self.log_text.tag_config("ok", foreground=GREEN)
        self.log_text.tag_config("err", foreground=RED)
        self.log_text.tag_config("warn", foreground=YELLOW)
        self.log_text.tag_config("info", foreground=FG_DIM)

    # ── Мастер ──────────────────────────────────────────────

    def _browse_master(self):
        path = filedialog.askopenfilename(
            title="terminal64.exe мастера",
            filetypes=[("MT5", "terminal64.exe"), ("EXE", "*.exe")],
            initialdir="C:\\")
        if path:
            self.var_master_path.set(path.replace("/", "\\"))
            self._save_config()

    def _open_master_terminal(self):
        path = self.var_master_path.get().strip()
        if not path:
            self._log("\u26A0\uFE0F Путь мастера не задан", "warn")
            return
        self._open_terminal_path(path)

    def _close_all_master(self):
        if not _COPIER_OK:
            self._log("\u274C copier.py не найден", "err")
            return
        path = self.var_master_path.get().strip()
        if not path:
            return
        self._log("\u2716 Закрытие всех позиций [МАСТЕР]...", "warn")
        cfg = self._build_config()
        trader = CopyTrader(
            config=cfg, state_file=STATE_FILE,
            log_callback=self._on_log,
            status_callback=self._on_status,
            config_file=CONFIG_FILE,
        )
        trader.close_all_positions(path, "МАСТЕР")

    def _close_all_open(self):
        self._close_all_master()
        for s in self._slaves:
            if s.get("enabled", True) and s.get("path"):
                if _COPIER_OK:
                    cfg = self._build_config()
                    trader = CopyTrader(
                        config=cfg, state_file=STATE_FILE,
                        log_callback=self._on_log,
                        status_callback=self._on_status,
                        config_file=CONFIG_FILE,
                    )
                    trader.close_all_positions(s["path"], s.get("name", "?"))

    # ── Слейвы ──────────────────────────────────────────────

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
        row = AccountRow(self._table_frame, self._next_row, data,
                         on_edit=self._edit_slave,
                         on_delete=self._delete_slave,
                         on_toggle=self._toggle_slave,
                         on_test=self._test_slave,
                         on_open=self._open_slave_terminal,
                         on_close_all=self._close_all_slave)
        self._rows.append(row)
        self._next_row += 1

    def _edit_slave(self, data: Dict, row: AccountRow):
        dlg = SlaveDialog(self, data)
        self.wait_window(dlg)
        if dlg.result:
            sid = data.get("id", "")
            enabled = data.get("enabled", True)
            data.clear()
            data.update(dlg.result)
            data["id"] = sid
            data["enabled"] = enabled
            row.refresh(data)
            self._save_config()

    def _delete_slave(self, data: Dict, row: AccountRow):
        if messagebox.askyesno("Удалить", f"Удалить \u00AB{data.get('name', '?')}\u00BB?", parent=self):
            self._slaves.remove(data)
            self._rebuild_rows()
            self._save_config()

    def _rebuild_rows(self):
        for r in self._rows:
            r.destroy()
        self._rows.clear()
        self._next_row = 1
        for s in self._slaves:
            self._add_slave_row(s)

    def _toggle_slave(self, data: Dict):
        self._save_config()

    def _test_slave(self, data: Dict):
        if not _COPIER_OK:
            self._log("\u274C copier.py не найден", "err")
            return
        symbol_map = data.get("symbol_map", {})
        if not symbol_map:
            self._log("\u26A0\uFE0F Нет символов в маппинге", "warn")
            return
        self._log(f"\U0001F9EA Тест копирования [{data.get('name', '?')}]", "warn")
        cfg = self._build_config()
        trader = CopyTrader(
            config=cfg, state_file=STATE_FILE,
            log_callback=self._on_log,
            status_callback=self._on_status,
            config_file=CONFIG_FILE,
        )
        trader.test_trade(data, cfg)

    def _open_slave_terminal(self, data: Dict):
        path = data.get("path", "")
        if not path:
            self._log("\u26A0\uFE0F Путь к терминалу не задан", "warn")
            return
        self._open_terminal_path(path)

    def _open_terminal_path(self, path: str):
        if not is_terminal_running(path):
            try:
                os.startfile(path)
                self._log(f"\U0001F680 Запуск: {os.path.basename(os.path.dirname(path))}")
            except Exception as e:
                self._log(f"\u274C Ошибка запуска: {e}", "err")
        else:
            if activate_terminal(path):
                self._log(f"\U0001F4C2 Терминал активирован")
            else:
                self._log("\u26A0\uFE0F Не удалось найти окно терминала", "warn")

    def _close_all_slave(self, data: Dict):
        if not _COPIER_OK:
            self._log("\u274C copier.py не найден", "err")
            return
        sname = data.get("name", "?")
        self._log(f"\u2716 Закрытие всех позиций [{sname}]...", "warn")
        cfg = self._build_config()
        trader = CopyTrader(
            config=cfg, state_file=STATE_FILE,
            log_callback=self._on_log,
            status_callback=self._on_status,
            config_file=CONFIG_FILE,
        )
        trader.close_all_positions(data.get("path", ""), sname)

    # ── Запуск всех терминалов ──────────────────────────────

    def _launch_all(self):
        paths = []
        master_path = self.var_master_path.get().strip()
        if master_path:
            paths.append(master_path)
        for s in self._slaves:
            p = s.get("path", "")
            if p and p not in paths:
                paths.append(p)
        launched = 0
        for p in paths:
            if not is_terminal_running(p):
                try:
                    os.startfile(p)
                    launched += 1
                    self._log(f"\U0001F680 Запуск: {os.path.basename(os.path.dirname(p))}")
                except Exception as e:
                    self._log(f"\u274C Ошибка запуска {p}: {e}", "err")
            else:
                self._log(f"\u2705 Уже запущен: {os.path.basename(os.path.dirname(p))}")
        if launched > 0:
            self._log(f"\u2705 Запущено {launched} терминалов", "ok")
        else:
            self._log("Все терминалы уже запущены")

    # ── Проверка связи / Старт / Стоп ───────────────────────

    def _schedule_check(self):
        if self._check_timer:
            self.after_cancel(self._check_timer)
        if not (self._trader and self._trader.is_running()):
            self._update_master_info_silent()
            for row, slave in zip(self._rows, self._slaves):
                self._update_row_info_silent(row, slave)
            self._check_timer = self.after(3000, self._schedule_check)

    def _update_master_info_silent(self):
        if not _MT5_OK:
            return
        master_path = self.var_master_path.get().strip()
        if not master_path:
            self.lbl_master_bal.config(text="\u2014", fg=FG_DIM)
            self.lbl_master_login.config(text="нет пути", fg=RED)
            return
        if not is_terminal_running(master_path):
            self.lbl_master_login.config(text="не запущен", fg=RED)
            return
        if mt5.initialize(path=master_path):
            try:
                acc = mt5.account_info()
                if acc:
                    ti = mt5.terminal_info()
                    pnl = acc.equity - acc.balance
                    pnl_color = GREEN if pnl >= 0 else RED
                    pnl_sign = "+" if pnl >= 0 else ""
                    at_off = ti and not ti.trade_allowed
                    self.lbl_master_login.config(
                        text=f"#{acc.login}" + (" \u26A0AT" if at_off else ""),
                        fg=RED if at_off else FG_DIM)
                    self.lbl_master_bal.config(text=f"${acc.balance:,.2f}")
                    self.lbl_master_eq.config(text=f"${acc.equity:,.2f}")
                    self.lbl_master_pnl.config(text=f"{pnl_sign}${pnl:,.2f}", fg=pnl_color)
                else:
                    self.lbl_master_login.config(text="нет аккаунта", fg=RED)
            finally:
                mt5.shutdown()
        else:
            self.lbl_master_login.config(text="ошибка", fg=RED)

    def _update_row_info_silent(self, row: AccountRow, slave: Dict):
        if not _MT5_OK:
            return
        slave_path = slave.get("path", "")
        if not slave_path:
            row.update_info(0, 0, status="\U0001F534 нет пути")
            return
        if not is_terminal_running(slave_path):
            row.update_info(0, 0, status="\U0001F534 не запущен")
            return
        if mt5.initialize(path=slave_path):
            try:
                acc = mt5.account_info()
                if acc:
                    ti = mt5.terminal_info()
                    at_off = ti and not ti.trade_allowed
                    if at_off:
                        status = f"\U0001F7E1 \u26A0AT #{acc.login}"
                    else:
                        status = f"\U0001F7E2 #{acc.login}"
                    row.update_info(acc.balance, acc.equity, acc.login, status)
                else:
                    row.update_info(0, 0, status="\U0001F534 нет аккаунта")
            finally:
                mt5.shutdown()
        else:
            row.update_info(0, 0, status="\U0001F534 ошибка")

    def _start(self):
        master_path = self.var_master_path.get().strip()
        if not master_path:
            messagebox.showwarning("Ошибка", "Укажите путь мастера", parent=self)
            return
        enabled = [s for s in self._slaves if s.get("enabled", True)]
        if not enabled:
            messagebox.showwarning("Ошибка", "Добавьте включённый аккаунт", parent=self)
            return
        self._save_config()
        if not _COPIER_OK:
            messagebox.showerror("Ошибка", "Не найден copier.py", parent=self)
            return
        self._trader = CopyTrader(
            config=self._build_config(),
            state_file=STATE_FILE,
            log_callback=self._on_log,
            status_callback=self._on_status,
            trade_callback=self._on_trade,
            config_file=CONFIG_FILE,
        )
        self._trader.start()
        if self._check_timer:
            self.after_cancel(self._check_timer)
            self._check_timer = None
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._session_stats = {"copied": 0, "failed": 0}
        self._log("\u2705 Копитрейдер запущен", "ok")

    def _stop(self):
        if self._trader:
            self._trader.stop()
            self._trader = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._log("\u25A0 Копитрейдер остановлен", "warn")
        self._schedule_check()

    # ── Колбэки ─────────────────────────────────────────────

    def _on_log(self, msg: str):
        self.after(0, self._log, msg)

    def _on_status(self, terminal_id: str, status: str,
                   balance: float = 0, equity: float = 0):
        self.after(0, self._update_status, terminal_id, status, balance, equity)

    def _on_trade(self, trade_info: Dict):
        self.after(0, self._add_trade_row, trade_info)

    def _add_trade_row(self, info: Dict):
        tag = "ok" if info.get("success") else "err"
        if tag == "ok":
            self._session_stats["copied"] += 1
        else:
            self._session_stats["failed"] += 1
        self.trades_table.add_trade(
            time_str=info.get("time", ""), slave=info.get("slave", ""),
            symbol=info.get("symbol", ""), direction=info.get("direction", ""),
            lot=info.get("lot", 0.0), master_ticket=info.get("master_ticket", ""),
            slave_ticket=info.get("slave_ticket", ""), status=info.get("status", ""),
            tag=tag)
        self.lbl_stats.config(
            text=f"\u2705 {self._session_stats['copied']}  \u274C {self._session_stats['failed']}")
        self.notebook.select(0)

    def _update_status(self, terminal_id: str, status: str,
                       balance: float = 0, equity: float = 0):
        if terminal_id == "master":
            if balance > 0:
                self.lbl_master_bal.config(text=f"${balance:,.2f}")
            if equity > 0:
                self.lbl_master_eq.config(text=f"${equity:,.2f}")
                pnl = equity - balance
                pnl_color = GREEN if pnl >= 0 else RED
                pnl_sign = "+" if pnl >= 0 else ""
                self.lbl_master_pnl.config(text=f"{pnl_sign}${pnl:,.2f}", fg=pnl_color)
            return
        for row, slave in zip(self._rows, self._slaves):
            if slave.get("name") == terminal_id or slave.get("id") == terminal_id:
                if balance > 0 and equity > 0:
                    row.update_status_only(status, balance, equity)
                else:
                    row.update_status_only(status)
                break

    # ── Лог ─────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = "info"):
        if "\u2705" in msg:
            tag = "ok"
        elif "\u274C" in msg:
            tag = "err"
        elif "\u26A0\uFE0F" in msg or "\u25A0" in msg:
            tag = "warn"
        self.log_text.config(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n", tag)
        self.log_text.see("end")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.config(state="disabled")
        self._write_log_file(f"[{ts}] {msg}")

    def _write_log_file(self, msg: str):
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_file = os.path.join(LOGS_DIR, f"{date_str}.log")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    # ── Конфиг ──────────────────────────────────────────────

    def _build_config(self) -> Dict:
        return {
            "master": {"path": self.var_master_path.get().strip()},
            "slaves": [
                {
                    "id": s.get("id", ""), "name": s.get("name", ""),
                    "enabled": s.get("enabled", True), "path": s.get("path", ""),
                    "symbol_map": s.get("symbol_map", {}),
                    "risk_type": s.get("risk_type", "percent"),
                    "risk_value": s.get("risk_value", 1.0),
                    "default_lot": s.get("default_lot", 0.01),
                    "max_drawdown": s.get("max_drawdown", 0),
                }
                for s in self._slaves
            ],
            "poll_interval_seconds": 1,
            "min_lot_mode": self._min_lot_mode,
        }

    def _save_config(self):
        try:
            os.makedirs(APP_DATA_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._build_config(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"\u26A0\uFE0F Ошибка конфига: {e}", "warn")

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return
        self.var_master_path.set(cfg.get("master", {}).get("path", ""))
        self._min_lot_mode = cfg.get("min_lot_mode", False)
        for s in cfg.get("slaves", []):
            if "id" not in s:
                s["id"] = str(uuid.uuid4())[:8]
            if "max_drawdown" not in s:
                s["max_drawdown"] = 0
            self._slaves.append(s)
            self._add_slave_row(s)

    def _on_close(self):
        if self._trader and self._trader.is_running():
            if not messagebox.askyesno("Выход",
                    "Копитрейдер запущен. Остановить и выйти?", parent=self):
                return
            self._stop()
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
