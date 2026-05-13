"""
MT5 Local Copy Trader — логика копирования сделок
"""

import os
import json
import threading
import ctypes
from ctypes import wintypes
from datetime import datetime, date
from typing import Callable, Dict, Any, Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    import psutil
except ImportError:
    psutil = None


# ─────────────────────────────────────────────────────────────
#  Вспомогательные функции
# ─────────────────────────────────────────────────────────────

def is_terminal_running(path: str) -> bool:
    if psutil is None:
        return True
    norm_path = os.path.normcase(os.path.abspath(path))
    for proc in psutil.process_iter(['exe']):
        try:
            exe = proc.info.get('exe')
            if exe and os.path.normcase(exe) == norm_path:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


def activate_terminal(path: str) -> bool:
    if psutil is None:
        return False
    norm_path = os.path.normcase(os.path.abspath(path))
    pids = []
    for proc in psutil.process_iter(['exe', 'pid']):
        try:
            exe = proc.info.get('exe')
            if exe and os.path.normcase(exe) == norm_path:
                pids.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    if not pids:
        return False

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    IsWindowVisible = user32.IsWindowVisible
    SetForegroundWindow = user32.SetForegroundWindow
    ShowWindow = user32.ShowWindow
    SW_RESTORE = 9

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    result = [False]

    def enum_cb(hwnd, _):
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and IsWindowVisible(hwnd):
            ShowWindow(hwnd, SW_RESTORE)
            SetForegroundWindow(hwnd)
            result[0] = True
            return False
        return True

    EnumWindows(WNDENUMPROC(enum_cb), 0)
    return result[0]


def load_state(state_file: str) -> Dict:
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if "positions" not in data:
                data["positions"] = {}
            if "orders" not in data:
                data["orders"] = {}
            if "daily_trades" not in data:
                data["daily_trades"] = {}
            if "daily_loss_balance" not in data:
                data["daily_loss_balance"] = {}
            return data
        except Exception:
            pass
    return {"positions": {}, "orders": {}, "daily_trades": {}}


def save_state(state_file: str, state: Dict) -> None:
    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_currency_rate(from_curr: str, to_curr: str) -> float:
    if mt5 is None or not from_curr or not to_curr:
        return 1.0
    if from_curr == to_curr:
        return 1.0
    candidates = [from_curr + to_curr, to_curr + from_curr]
    for pair in candidates:
        info = mt5.symbol_info(pair)
        if info is not None:
            mt5.symbol_select(pair, True)
            tick = mt5.symbol_info_tick(pair)
            if tick and tick.bid > 0:
                mid = (tick.bid + tick.ask) / 2
                if pair == to_curr + from_curr:
                    return 1.0 / mid
                return mid
    all_symbols = mt5.symbols_get()
    if all_symbols:
        from_upper = from_curr.upper()
        to_upper = to_curr.upper()
        for s in all_symbols:
            name_upper = s.name.upper()
            if name_upper.startswith(from_upper) and to_upper in name_upper[len(from_upper):]:
                mt5.symbol_select(s.name, True)
                tick = mt5.symbol_info_tick(s.name)
                if tick and tick.bid > 0:
                    mid = (tick.bid + tick.ask) / 2
                    return mid
            if name_upper.startswith(to_upper) and from_upper in name_upper[len(to_upper):]:
                mt5.symbol_select(s.name, True)
                tick = mt5.symbol_info_tick(s.name)
                if tick and tick.bid > 0:
                    mid = (tick.bid + tick.ask) / 2
                    return 1.0 / mid
    if from_curr != "USD" and to_curr != "USD":
        r1 = _get_currency_rate(from_curr, "USD")
        r2 = _get_currency_rate("USD", to_curr)
        if r1 > 0 and r2 > 0:
            return r1 * r2
    return 1.0


def calculate_lot(symbol_info, sl_distance: float,
                   risk_type: str, risk_value: float,
                   balance: float) -> float:
    if sl_distance <= 0:
        return 0.0

    tick_size = symbol_info.trade_tick_size
    contract_size = symbol_info.trade_contract_size or 0.0

    tick_value = 0.0
    rate = 1.0
    profit_curr = getattr(symbol_info, 'currency_profit', '') or ''
    deposit_curr = ''
    if mt5 is not None:
        acc = mt5.account_info()
        if acc:
            deposit_curr = acc.currency or ''

    if contract_size > 0 and tick_size > 0:
        raw_tick_value = contract_size * tick_size
        if profit_curr and deposit_curr and profit_curr != deposit_curr:
            rate = _get_currency_rate(profit_curr, deposit_curr)
            tick_value = raw_tick_value * rate
        else:
            tick_value = raw_tick_value

    if tick_value <= 0:
        tick_value_profit = abs(symbol_info.trade_tick_value or 0.0)
        tick_value_loss = abs(symbol_info.trade_tick_value_loss or 0.0)
        tick_value = max(tick_value_profit, tick_value_loss)

    if tick_size <= 0 or tick_value <= 0:
        return 0.0

    sl_ticks = sl_distance / tick_size

    if risk_type == "percent":
        risk_amount = balance * risk_value / 100.0
    else:
        risk_amount = risk_value

    if sl_ticks <= 0:
        return 0.0

    lot = risk_amount / (sl_ticks * tick_value)

    volume_step = symbol_info.volume_step
    if volume_step > 0:
        lot = round(lot / volume_step) * volume_step

    lot = max(symbol_info.volume_min, min(symbol_info.volume_max, lot))
    return round(lot, 8)


def resolve_symbol(name: str) -> Optional[str]:
    if mt5 is None:
        return name
    info = mt5.symbol_info(name)
    if info is not None:
        return name
    all_symbols = mt5.symbols_get()
    if all_symbols:
        name_upper = name.upper()
        for s in all_symbols:
            if s.name.upper() == name_upper:
                return s.name
    return None


def get_filling_mode(symbol_info) -> int:
    if mt5 is None:
        return 0
    filling = symbol_info.filling_mode
    if filling & 2:
        return mt5.ORDER_FILLING_FOK
    if filling & 1:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def try_send_order(request: Dict, log_fn=None) -> object:
    result = mt5.order_send(request)
    if result is not None and result.retcode == 10030:
        original_filling = request.get("type_filling", 0)
        filling_names = {
            mt5.ORDER_FILLING_FOK: "FOK",
            mt5.ORDER_FILLING_IOC: "IOC",
            mt5.ORDER_FILLING_RETURN: "RETURN",
        }
        if log_fn:
            log_fn(
                f"⚠️ retcode=10030 с filling={filling_names.get(original_filling, original_filling)}, "
                f"пробуем другие"
            )
        for alt_filling in [mt5.ORDER_FILLING_FOK,
                            mt5.ORDER_FILLING_IOC,
                            mt5.ORDER_FILLING_RETURN]:
            if alt_filling == original_filling:
                continue
            request["type_filling"] = alt_filling
            result = mt5.order_send(request)
            if log_fn:
                log_fn(
                    f"🔄 filling={filling_names.get(alt_filling, alt_filling)} → "
                    f"retcode={result.retcode if result else -1}"
                )
            if result is not None and result.retcode != 10030:
                return result
        request["type_filling"] = original_filling
    return result


def normalize_price(price: float, digits: int) -> float:
    return round(price, digits)


def opposite_order_type(order_type: int) -> int:
    if mt5 is None:
        return 0
    if order_type == mt5.ORDER_TYPE_BUY:
        return mt5.ORDER_TYPE_SELL
    if order_type == mt5.ORDER_TYPE_SELL:
        return mt5.ORDER_TYPE_BUY
    return order_type


def order_type_name(order_type: int) -> str:
    if mt5 is None:
        return str(order_type)
    names = {
        mt5.ORDER_TYPE_BUY: "BUY",
        mt5.ORDER_TYPE_SELL: "SELL",
        mt5.ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT",
        mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
        mt5.ORDER_TYPE_BUY_STOP: "BUY_STOP",
        mt5.ORDER_TYPE_SELL_STOP: "SELL_STOP",
    }
    return names.get(order_type, str(order_type))


PENDING_ORDER_TYPES = None


def get_pending_types():
    global PENDING_ORDER_TYPES
    if PENDING_ORDER_TYPES is None and mt5 is not None:
        PENDING_ORDER_TYPES = {
            mt5.ORDER_TYPE_BUY_LIMIT,
            mt5.ORDER_TYPE_SELL_LIMIT,
            mt5.ORDER_TYPE_BUY_STOP,
            mt5.ORDER_TYPE_SELL_STOP,
        }
    return PENDING_ORDER_TYPES or set()


# ─────────────────────────────────────────────────────────────
#  Основной класс копитрейдера
# ─────────────────────────────────────────────────────────────

class CopyTrader:
    def __init__(
        self,
        config: Dict,
        state_file: str,
        log_callback: Callable[[str], None],
        status_callback: Callable[[str, str, float, float, float, float], None],
        trade_callback: Optional[Callable[[Dict], None]] = None,
        config_file: str = "",
    ):
        self.config = config
        self.config_file = config_file
        self.state_file = state_file
        self.log_cb = log_callback
        self.status_cb = status_callback
        self.trade_cb = trade_callback

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._drawdown_paused: Dict[str, bool] = {}
        self._trades_paused: Dict[str, bool] = {}
        self._daily_loss_paused: Dict[str, bool] = {}

        self.state = load_state(state_file)

    # ── Статический метод: поиск ключа в dict без учёта регистра ──

    @staticmethod
    def _sym_lookup(d: Dict[str, Any], key: str) -> Optional[Any]:
        key_upper = key.upper()
        for k, v in d.items():
            if k.upper() == key_upper:
                return v
        return None

    # ── Публичный интерфейс ──────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        with self._lock:
            save_state(self.state_file, self.state)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def test_trade(self, slave_cfg: Dict, full_config: Dict):
        """Тест копирования: пробует ВСЕ символы из маппинга, открывает BUY мин.лотом и сразу закрывает."""
        if mt5 is None:
            self._log("❌ MT5 не установлен")
            return
        sid = slave_cfg.get("id", "?")
        sname = slave_cfg.get("name", sid)
        slave_path = slave_cfg.get("path", "")
        symbol_map: Dict[str, str] = slave_cfg.get("symbol_map", {})

        if not slave_path:
            self._log(f"⚠️ [{sname}] Путь не задан")
            return
        if not is_terminal_running(slave_path):
            self._log(f"⚠️ [{sname}] Терминал не запущен")
            return
        if not symbol_map:
            self._log(f"⚠️ [{sname}] Нет символов в маппинге")
            return

        ok = mt5.initialize(path=slave_path)
        if not ok:
            self._log(f"⚠️ [{sname}] Ошибка подключения")
            return

        try:
            ti = mt5.terminal_info()
            if ti and not ti.trade_allowed:
                self._log(f"⚠️ [{sname}] Алготрейдинг ВЫКЛ — включите в терминале!")
                return

            acc = mt5.account_info()
            if acc is None:
                self._log(f"⚠️ [{sname}] Нет данных аккаунта")
                return

            self._log(f"📊 [{sname}] Аккаунт #{acc.login} ${acc.balance:.2f}")

            for master_sym, raw_slave_sym in symbol_map.items():
                slave_sym = resolve_symbol(raw_slave_sym)
                if slave_sym is None:
                    self._log(f"⚠️ [{sname}] Символ {raw_slave_sym} не найден, пробуем следующий")
                    continue
                if slave_sym != raw_slave_sym:
                    self._log(f"ℹ️ [{sname}] {raw_slave_sym} → {slave_sym}")

                if not mt5.symbol_select(slave_sym, True):
                    self._log(f"⚠️ [{sname}] Не удалось добавить {slave_sym} в Market Watch, пробуем следующий")
                    continue

                sym_info = mt5.symbol_info(slave_sym)
                if sym_info is None:
                    self._log(f"⚠️ [{sname}] symbol_info вернул None для {slave_sym}, пробуем следующий")
                    continue

                if sym_info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
                    self._log(
                        f"⚠️ [{sname}] {slave_sym} trade_mode={sym_info.trade_mode} "
                        f"(не FULL), пробуем следующий"
                    )
                    continue

                lot = sym_info.volume_min
                tick = mt5.symbol_info_tick(slave_sym)
                if tick is None:
                    self._log(f"⚠️ [{sname}] Нет тика для {slave_sym}, пробуем следующий")
                    continue

                filling = get_filling_mode(sym_info)
                self._log(
                    f"📊 [{sname}] {slave_sym} vol_min={lot} filling={filling} "
                    f"filling_flags={sym_info.filling_mode} "
                    f"tick_val={sym_info.trade_tick_value} "
                    f"tick_sz={sym_info.trade_tick_size}"
                )

                price = normalize_price(tick.ask, sym_info.digits)
                self._log(f"📤 [{sname}] Открываем BUY {slave_sym} lot={lot} price={price}")

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": slave_sym,
                    "volume": lot,
                    "type": mt5.ORDER_TYPE_BUY,
                    "price": price,
                    "comment": "CT_TEST",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }

                result = try_send_order(request, self._log)
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    retcode = result.retcode if result else -1
                    comment = result.comment if result else ""
                    self._log(
                        f"❌ [{sname}] Тест FAILED: retcode={retcode} {comment}"
                    )
                    self._trade_event({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "slave": sname, "symbol": slave_sym,
                        "direction": "BUY", "lot": lot,
                        "master_ticket": "TEST", "slave_ticket": "—",
                        "success": False, "status": f"❌ retcode={retcode} {comment}",
                    })
                    continue

                ticket = result.order
                self._log(f"✅ [{sname}] Тест BUY OK → #{ticket}, закрываем...")
                self._trade_event({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "slave": sname, "symbol": slave_sym,
                    "direction": "BUY", "lot": lot,
                    "master_ticket": "TEST", "slave_ticket": str(ticket),
                    "success": True, "status": f"✅ TEST BUY #{ticket}",
                })

                mt5.sleep(1000)

                pos = None
                all_pos = mt5.positions_get(symbol=slave_sym)
                if all_pos:
                    for p in all_pos:
                        if p.comment == "CT_TEST":
                            pos = p
                            break
                if pos is None:
                    self._log(f"ℹ️ [{sname}] Позиция не найдена — возможно уже закрыта")
                    return

                close_type = opposite_order_type(pos.type)
                close_tick = mt5.symbol_info_tick(pos.symbol)
                if close_tick is None:
                    self._log(f"⚠️ [{sname}] Нет тика для закрытия #{pos.ticket}")
                    return
                close_price = normalize_price(close_tick.bid, sym_info.digits)

                close_req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": close_type,
                    "position": pos.ticket,
                    "price": close_price,
                    "comment": "CT_TEST_CLOSE",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                close_result = try_send_order(close_req, self._log)
                if close_result and close_result.retcode == mt5.TRADE_RETCODE_DONE:
                    self._log(f"✅ [{sname}] Тест закрыт #{pos.ticket} — копирование работает!")
                else:
                    rc = close_result.retcode if close_result else -1
                    cmt = close_result.comment if close_result else ""
                    self._log(f"⚠️ [{sname}] BUY открыт, закрытие retcode={rc} {cmt} — закройте вручную #{pos.ticket}")
                return

            self._log(f"❌ [{sname}] Ни один символ из маппинга не подошёл для теста")
        finally:
            mt5.shutdown()

    def close_all_positions(self, terminal_path: str, label: str = ""):
        """Закрывает все открытые позиции на аккаунте терминала."""
        if mt5 is None:
            self._log("❌ MT5 не установлен")
            return
        if not terminal_path:
            self._log(f"⚠️ [{label}] Путь не задан")
            return
        if not is_terminal_running(terminal_path):
            self._log(f"⚠️ [{label}] Терминал не запущен")
            return

        ok = mt5.initialize(path=terminal_path)
        if not ok:
            self._log(f"⚠️ [{label}] Ошибка подключения")
            return

        try:
            positions = mt5.positions_get()
            if not positions:
                self._log(f"ℹ️ [{label}] Нет открытых позиций")
                return

            self._log(f"🔴 [{label}] Закрываем {len(positions)} позиций...")
            closed = 0
            for pos in positions:
                tick = mt5.symbol_info_tick(pos.symbol)
                if tick is None:
                    self._log(f"⚠️ [{label}] Нет тика для {pos.symbol}")
                    continue
                sym_info = mt5.symbol_info(pos.symbol)
                filling = get_filling_mode(sym_info) if sym_info else mt5.ORDER_FILLING_IOC
                close_type = opposite_order_type(pos.type)
                price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
                if sym_info:
                    price = normalize_price(price, sym_info.digits)
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": close_type,
                    "position": pos.ticket,
                    "price": price,
                    "comment": "CT_CLOSE_ALL",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                result = try_send_order(request, self._log)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    closed += 1
                else:
                    rc = result.retcode if result else -1
                    self._log(f"❌ [{label}] Ошибка закрытия #{pos.ticket} {pos.symbol} retcode={rc}")
            self._log(f"✅ [{label}] Закрыто {closed}/{len(positions)} позиций")
        finally:
            mt5.shutdown()

    # ── Логирование ──────────────────────────────────────────

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_cb(f"[{ts}] {msg}")

    def _status(self, terminal_id: str, status: str,
                 balance: float = 0, equity: float = 0,
                 daily_loss: float = 0, daily_loss_limit: float = 0):
        self.status_cb(terminal_id, status, balance, equity, daily_loss, daily_loss_limit)

    def _trade_event(self, info: Dict):
        if self.trade_cb:
            self.trade_cb(info)

    def _reload_config(self):
        if not self.config_file:
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            with self._lock:
                old_master = self.config.get("master", {}).get("path", "")
                self.config = cfg
                if not self.config.get("master"):
                    self.config["master"] = {}
                if not self.config.get("master", {}).get("path"):
                    self.config["master"]["path"] = old_master
        except Exception:
            pass

    # ── Инкремент дневного счётчика сделок ───────────────────

    def _increment_daily_trade(self, sid: str):
        today_str = date.today().isoformat()
        daily: Dict = self.state.setdefault("daily_trades", {})
        if daily.get("_date") != today_str:
            daily.clear()
            daily["_date"] = today_str
        daily[sid] = daily.get(sid, 0) + 1

    # ── Основной цикл ────────────────────────────────────────

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._reload_config()
                self._cycle()
            except Exception as e:
                self._log(f"❌ Критическая ошибка цикла: {e}")
            self._stop_event.wait(self.config.get("poll_interval_seconds", 1))

    def _cycle(self):
        if mt5 is None:
            self._log("❌ Библиотека MetaTrader5 не установлена")
            self._stop_event.wait(5)
            return

        master_cfg = self.config.get("master", {})
        master_path = master_cfg.get("path", "")

        # ── Подключение к мастеру ────────────────────────────
        if not master_path:
            self._status("master", "🔴 Путь не задан")
            return

        if not is_terminal_running(master_path):
            self._status("master", "🔴 Терминал не запущен")
            return

        ok = mt5.initialize(path=master_path)
        if not ok:
            err = mt5.last_error()
            self._status("master", f"🔴 Ошибка подключения ({err[0]})")
            return

        try:
            acc = mt5.account_info()
            if acc is None:
                self._status("master", "🔴 Нет данных аккаунта")
                return
            self._status("master", f"🟢 #{acc.login} ${acc.balance:.2f}",
                         acc.balance, acc.equity)

            ti = mt5.terminal_info()
            if ti and not ti.trade_allowed:
                self._log("⚠️ Мастер: Алготрейдинг ВЫКЛ — чтение работает, но для торговли на слейвах тоже включите")

            master_positions = mt5.positions_get() or []
            master_orders = mt5.orders_get() or []
        finally:
            mt5.shutdown()

        # ── Обработка каждого слейва ─────────────────────────
        with self._lock:
            slaves = self.config.get("slaves", [])
            for slave in slaves:
                sid = slave.get("name", slave.get("id", "?"))
                if not slave.get("enabled", True):
                    continue
                try:
                    self._sync_slave(slave, master_positions, master_orders)
                except Exception as e:
                    self._log(f"❌ [{sid}] Ошибка: {e}")

            save_state(self.state_file, self.state)

    # ── Обработка одного слейва (подключение + проверки) ─────

    def _sync_slave(self, slave: Dict, master_positions, master_orders):
        sid = slave.get("id", "slave")
        sname = slave.get("name", sid)
        slave_path = slave.get("path", "")
        symbol_map: Dict[str, str] = slave.get("symbol_map", {})

        if not slave_path:
            self._status(sname, "🔴 Путь не задан")
            return

        if not is_terminal_running(slave_path):
            self._status(sname, "🔴 Не запущен")
            return

        ok = mt5.initialize(path=slave_path)
        if not ok:
            err = mt5.last_error()
            self._status(sname, f"🔴 Ошибка ({err[0]})")
            return

        try:
            acc = mt5.account_info()
            if acc is None:
                self._status(sname, "🔴 Нет аккаунта")
                return

            ti = mt5.terminal_info()
            if ti and not ti.trade_allowed:
                self._status(sname, f"🔴 #{acc.login} Алготрейдинг ВЫКЛ")
                self._log(f"⚠️ [{sname}] Включите Алготрейдинг (AutoTrading) в терминале!")
                return

            balance = acc.balance
            equity = acc.equity

            # ── Защита по просадке ────────────────────────────────
            max_dd = slave.get("max_drawdown", 0)
            dd_paused = False
            if max_dd > 0 and balance > 0:
                dd_pct = (balance - equity) / balance * 100
                if dd_pct >= max_dd:
                    dd_paused = True
                    if not self._drawdown_paused.get(sid):
                        self._log(
                            f"🛑 [{sname}] Просадка {dd_pct:.1f}% >= {max_dd}% — "
                            f"копирование приостановлено"
                        )
                        self._drawdown_paused[sid] = True
                    self._status(sname, f"🔴 #{acc.login} просадка {dd_pct:.1f}%")
                else:
                    if self._drawdown_paused.get(sid):
                        self._log(
                            f"✅ [{sname}] Просадка {dd_pct:.1f}% < {max_dd}% — "
                            f"копирование возобновлено"
                        )
                        self._drawdown_paused[sid] = False

            # ── Лимит сделок в день ───────────────────────────────
            max_trades = slave.get("max_trades_per_day", 0)
            trades_paused = False
            if max_trades > 0:
                today_str = date.today().isoformat()
                daily: Dict = self.state.setdefault("daily_trades", {})
                if daily.get("_date") != today_str:
                    daily.clear()
                    daily["_date"] = today_str
                today_count = daily.get(sid, 0)
                if today_count >= max_trades:
                    trades_paused = True
                    if not self._trades_paused.get(sid):
                        self._log(
                            f"🛑 [{sname}] Лимит сделок {today_count}/{max_trades} — "
                            f"копирование приостановлено"
                        )
                        self._trades_paused[sid] = True
                else:
                    if self._trades_paused.get(sid):
                        self._log(
                            f"✅ [{sname}] Лимит сделок {today_count}/{max_trades} — "
                            f"копирование возобновлено"
                        )
                        self._trades_paused[sid] = False

            # ── Лимит убытка в день ────────────────────────────────
            daily_loss_limit = slave.get("daily_loss_limit", 0)
            loss_paused = False
            daily_loss = 0
            if daily_loss_limit > 0 and balance > 0:
                today_str = date.today().isoformat()
                dl_state: Dict = self.state.setdefault("daily_loss_balance", {})
                if dl_state.get("_date") != today_str:
                    if dl_state.get("_date"):
                        self._log(
                            f"🔄 [{sname}] Новый день — сброс дневного убытка "
                            f"(было: {dl_state.get(sid, '?')})"
                        )
                        if self._daily_loss_paused.get(sid):
                            self._log(
                                f"✅ [{sname}] Лимит убытка снят — копирование возобновлено"
                            )
                    dl_state.clear()
                    dl_state["_date"] = today_str
                    self._daily_loss_paused.pop(sid, None)
                if sid not in dl_state:
                    dl_state[sid] = balance
                start_bal = dl_state.get(sid, balance)
                daily_loss = max(0, start_bal - equity)
                if daily_loss >= daily_loss_limit:
                    loss_paused = True
                    if not self._daily_loss_paused.get(sid):
                        self._log(
                            f"🛑 [{sname}] Дневной убыток ${daily_loss:.2f} >= "
                            f"${daily_loss_limit:.2f} — закрываем все позиции"
                        )
                        self._close_positions_inline(sname)
                        self._daily_loss_paused[sid] = True

            if dd_paused or trades_paused or loss_paused:
                if not dd_paused and not loss_paused:
                    self._status(sname, f"🟢 #{acc.login} ${balance:.2f}",
                                 balance, equity, daily_loss, daily_loss_limit)
                elif loss_paused:
                    self._status(sname, f"🔴 #{acc.login} убыток ${daily_loss:.2f}",
                                 balance, equity, daily_loss, daily_loss_limit)
                return

            self._status(sname, f"🟢 #{acc.login} ${balance:.2f}",
                         balance, equity, daily_loss, daily_loss_limit)

            self._sync_positions(slave, sid, sname, symbol_map,
                                 master_positions, balance)
            self._sync_orders(slave, sid, sname, symbol_map,
                              master_orders, master_positions, balance)
        finally:
            mt5.shutdown()

    # ── Закрытие всех позиций (подключение уже установлено) ──

    def _close_positions_inline(self, sname: str):
        positions = mt5.positions_get()
        if not positions:
            return
        closed = 0
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                continue
            sym_info = mt5.symbol_info(pos.symbol)
            filling = get_filling_mode(sym_info) if sym_info else mt5.ORDER_FILLING_IOC
            close_type = opposite_order_type(pos.type)
            price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            if sym_info:
                price = normalize_price(price, sym_info.digits)
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "comment": "CT_DAILY_LIMIT",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            result = try_send_order(request, self._log)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                closed += 1
        self._log(f"🔴 [{sname}] Закрыто {closed}/{len(positions)} позиций (лимит убытка)")

    # ── Синхронизация позиций ────────────────────────────────

    def _sync_positions(self, slave, sid, sname, symbol_map,
                        master_positions, balance):
        master_pos_map = {
            str(p.ticket): p
            for p in master_positions
            if self._sym_lookup(symbol_map, p.symbol) is not None
        }
        master_tickets = set(master_pos_map.keys())

        state_pos: Dict = self.state["positions"]

        # Новые позиции (есть у мастера, нет в state для этого слейва)
        for ticket_str, pos in master_pos_map.items():
            already_copied = ticket_str in state_pos and sid in state_pos[ticket_str]
            if already_copied:
                continue
            # Проверяем: не был ли это отложенный ордер
            if ticket_str in self.state["orders"]:
                slave_symbol = self._sym_lookup(symbol_map, pos.symbol) or pos.symbol
                slave_ticket = self._find_slave_position(
                    sname, slave_symbol, ticket_str)
                if slave_ticket is None:
                    slave_ticket_old = self.state["orders"][ticket_str].get(sid)
                    if slave_ticket_old:
                        slave_pos_list = mt5.positions_get(ticket=slave_ticket_old)
                        if not slave_pos_list:
                            slave_ticket_old = None
                        else:
                            slave_ticket = slave_ticket_old
                if slave_ticket:
                    if ticket_str not in state_pos:
                        state_pos[ticket_str] = {}
                    state_pos[ticket_str][sid] = slave_ticket
                    self._log(f"✅ [{sname}] Ордер #{ticket_str} сработал → позиция #{slave_ticket}")
                # Удаляем из orders
                if ticket_str in self.state["orders"]:
                    del self.state["orders"][ticket_str]
            else:
                # Новая рыночная позиция
                slave_ticket = self._open_position(slave, sid, sname,
                                                   symbol_map, pos, balance)
                if slave_ticket:
                    if ticket_str not in state_pos:
                        state_pos[ticket_str] = {}
                    state_pos[ticket_str][sid] = slave_ticket
                    self._increment_daily_trade(sid)

        # Закрытые позиции (есть в state, нет у мастера)
        closed = [t for t in list(state_pos.keys()) if t not in master_tickets]
        for ticket_str in closed:
            slave_ticket = state_pos[ticket_str].get(sid)
            if slave_ticket:
                self._close_position(sname, ticket_str, slave_ticket)
            # Удаляем запись для этого слейва
            if sid in state_pos.get(ticket_str, {}):
                del state_pos[ticket_str][sid]
            # Если для этого мастер-тикета больше нет слейвов — удаляем запись
            if not state_pos.get(ticket_str):
                state_pos.pop(ticket_str, None)

        # ── SL/TP модификация на существующих позициях ─────────────
        for ticket_str, master_pos in master_pos_map.items():
            slave_ticket = state_pos.get(ticket_str, {}).get(sid)
            if not slave_ticket:
                continue
            slave_positions = mt5.positions_get(ticket=slave_ticket)
            if not slave_positions:
                continue
            slave_pos = slave_positions[0]

            sym_info = mt5.symbol_info(slave_pos.symbol)
            if not sym_info:
                continue
            digits = sym_info.digits

            new_sl = 0.0
            new_tp = 0.0
            need_modify = False

            if master_pos.sl != 0.0:
                sl_pct = abs(master_pos.price_open - master_pos.sl) / master_pos.price_open
                if master_pos.type == 0:  # BUY
                    new_sl = normalize_price(slave_pos.price_open * (1 - sl_pct), digits)
                else:
                    new_sl = normalize_price(slave_pos.price_open * (1 + sl_pct), digits)
                if abs(new_sl - slave_pos.sl) > sym_info.trade_tick_size:
                    need_modify = True
            else:
                if slave_pos.sl != 0.0:
                    new_sl = 0.0
                    need_modify = True

            if master_pos.tp != 0.0:
                tp_pct = abs(master_pos.price_open - master_pos.tp) / master_pos.price_open
                if master_pos.type == 0:  # BUY
                    new_tp = normalize_price(slave_pos.price_open * (1 + tp_pct), digits)
                else:
                    new_tp = normalize_price(slave_pos.price_open * (1 - tp_pct), digits)
                if abs(new_tp - slave_pos.tp) > sym_info.trade_tick_size:
                    need_modify = True
            else:
                if slave_pos.tp != 0.0:
                    new_tp = 0.0
                    need_modify = True

            if need_modify:
                self._modify_position(sname, slave_pos, new_sl, new_tp, sym_info)

            # ── Partial close: объём мастера уменьшился ──────────────
            if state_pos.get(ticket_str, {}).get("_master_vol"):
                old_vol = state_pos[ticket_str]["_master_vol"]
                if master_pos.volume < old_vol - 0.0001:
                    ratio = master_pos.volume / old_vol
                    slave_vol_to_close = slave_pos.volume * (1 - ratio)
                    vol_step = sym_info.volume_step
                    if vol_step > 0:
                        slave_vol_to_close = round(slave_vol_to_close / vol_step) * vol_step
                    slave_vol_to_close = max(sym_info.volume_min, slave_vol_to_close)
                    new_slave_vol = slave_pos.volume - slave_vol_to_close
                    if new_slave_vol < sym_info.volume_min:
                        slave_vol_to_close = slave_pos.volume
                        new_slave_vol = 0.0
                    if slave_vol_to_close >= sym_info.volume_min:
                        self._partial_close(sname, slave_pos, slave_vol_to_close,
                                            master_ticket_str=ticket_str)
                        self._log(
                            f"📝 [{sname}] Частичное закрытие #{slave_pos.ticket} "
                            f"vol={slave_vol_to_close:.2f} (мастер {old_vol}→{master_pos.volume})"
                        )
            state_pos.setdefault(ticket_str, {})["_master_vol"] = master_pos.volume

    # ── Синхронизация ордеров ────────────────────────────────

    def _sync_orders(self, slave, sid, sname, symbol_map,
                     master_orders, master_positions, balance):
        pending_types = get_pending_types()

        master_ord_map = {
            str(o.ticket): o
            for o in master_orders
            if self._sym_lookup(symbol_map, o.symbol) is not None
            and o.type in pending_types
        }
        master_ord_tickets = set(master_ord_map.keys())

        master_pos_tickets = {str(p.ticket) for p in master_positions}

        state_ord: Dict = self.state["orders"]

        # Новые ордера
        for ticket_str, order in master_ord_map.items():
            already_copied = ticket_str in state_ord and sid in state_ord[ticket_str]
            if already_copied:
                continue
            slave_ticket = self._place_order(slave, sid, sname,
                                             symbol_map, order, balance)
            if slave_ticket:
                if ticket_str not in state_ord:
                    state_ord[ticket_str] = {}
                state_ord[ticket_str][sid] = slave_ticket
                self._increment_daily_trade(sid)

        # Отменённые/сработавшие ордера
        gone = [t for t in list(state_ord.keys())
                if t not in master_ord_tickets]
        for ticket_str in gone:
            slave_ticket = state_ord[ticket_str].get(sid)
            if slave_ticket:
                if ticket_str in master_pos_tickets:
                    pass  # Ордер сработал — обработается в sync_positions
                else:
                    self._cancel_order(sname, ticket_str, slave_ticket)
            if sid in state_ord.get(ticket_str, {}):
                del state_ord[ticket_str][sid]
            if not state_ord.get(ticket_str):
                state_ord.pop(ticket_str, None)

    # ── Поиск позиции слейва по комментарию ──────────────────

    def _find_slave_position(self, sname: str, symbol: str,
                             master_ticket_str: str) -> Optional[int]:
        comment = f"CT_{master_ticket_str}"
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            for p in positions:
                if p.comment == comment:
                    return p.ticket
        return None

    # ── Открытие позиции на слейве ───────────────────────────

    def _open_position(self, slave, sid, sname, symbol_map,
                       master_pos, balance) -> Optional[int]:
        raw_symbol = self._sym_lookup(symbol_map, master_pos.symbol)
        if not raw_symbol:
            self._log(f"⚠️ [{sname}] Символ мастера {master_pos.symbol} не в маппинге")
            return None

        slave_symbol = resolve_symbol(raw_symbol)
        if slave_symbol is None:
            self._log(f"⚠️ [{sname}] Символ {raw_symbol} не найден (проверьте регистр)")
            return None

        if slave_symbol != raw_symbol:
            self._log(f"ℹ️ [{sname}] Символ {raw_symbol} → {slave_symbol}")

        if not mt5.symbol_select(slave_symbol, True):
            self._log(f"⚠️ [{sname}] Не удалось добавить {slave_symbol} в Market Watch")

        same_symbol = (slave_symbol == master_pos.symbol)

        sym_info = mt5.symbol_info(slave_symbol)
        if sym_info is None:
            self._log(f"⚠️ [{sname}] Символ {slave_symbol} не найден")
            return None

        if sym_info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            self._log(
                f"⚠️ [{sname}] {slave_symbol} trade_mode={sym_info.trade_mode} — не торгуется"
            )
            return None

        # Цена слейва
        tick = mt5.symbol_info_tick(slave_symbol)
        if tick is None:
            self._log(f"⚠️ [{sname}] Нет тика для {slave_symbol}")
            return None

        if master_pos.type == mt5.ORDER_TYPE_BUY:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL

        digits = sym_info.digits
        price = normalize_price(price, digits)

        # Расчёт лота
        if master_pos.sl != 0.0:
            sl_pct = abs(master_pos.price_open - master_pos.sl) / master_pos.price_open
            sl_distance = price * sl_pct
            risk_type = slave.get("risk_type", "percent")
            risk_value = slave.get("risk_value", 1.0)
            lot = calculate_lot(sym_info, sl_distance, risk_type, risk_value, balance)
            profit_curr = getattr(sym_info, 'currency_profit', '') or ''
            raw_tv = (sym_info.trade_contract_size or 0) * (sym_info.trade_tick_size or 0)
            self._log(
                f"📊 [{sname}] lot={lot:.2f} risk={risk_value}{'%' if risk_type == 'percent' else '$'} "
                f"bal={balance:.2f} SL_dist={sl_distance:.5f} "
                f"tick_val={sym_info.trade_tick_value} "
                f"tick_val_loss={sym_info.trade_tick_value_loss} "
                f"tick_sz={sym_info.trade_tick_size} "
                f"contract={sym_info.trade_contract_size} "
                f"profit_curr={profit_curr} "
                f"raw_tv={raw_tv} "
                f"filling_mode_flags={sym_info.filling_mode}"
            )
            if lot <= 0:
                lot = slave.get("default_lot", 0.01)
                self._log(f"⚠️ [{sname}] Расчёт=0, default={lot}")
        else:
            lot = slave.get("default_lot", 0.01)

        if self.config.get("min_lot_mode", False):
            lot = sym_info.volume_min
            self._log(f"📏 [{sname}] Мин. лот режим: lot={lot}")

        # SL/TP как процент от open price
        sl = 0.0
        tp = 0.0
        if master_pos.sl != 0.0:
            sl_pct = abs(master_pos.price_open - master_pos.sl) / master_pos.price_open
            if order_type == mt5.ORDER_TYPE_BUY:
                sl = normalize_price(price * (1 - sl_pct), digits)
            else:
                sl = normalize_price(price * (1 + sl_pct), digits)
        if master_pos.tp != 0.0:
            tp_pct = abs(master_pos.price_open - master_pos.tp) / master_pos.price_open
            if order_type == mt5.ORDER_TYPE_BUY:
                tp = normalize_price(price * (1 + tp_pct), digits)
            else:
                tp = normalize_price(price * (1 - tp_pct), digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": slave_symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "comment": f"CT_{master_pos.ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": get_filling_mode(sym_info),
        }

        result = try_send_order(request, self._log)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            comment = result.comment if result else ""
            self._log(
                f"❌ [{sname}] Ошибка открытия {slave_symbol} "
                f"{order_type_name(order_type)} lot={lot:.2f} "
                f"price={price:.5f} filling={request.get('type_filling')} "
                f"retcode={retcode} {comment}"
            )
            self._trade_event({
                "time": datetime.now().strftime("%H:%M:%S"),
                "slave": sname,
                "symbol": slave_symbol,
                "direction": order_type_name(order_type),
                "lot": lot,
                "master_ticket": str(master_pos.ticket),
                "slave_ticket": "—",
                "success": False,
                "status": f"❌ retcode={retcode} {comment}",
            })
            return None

        self._log(
            f"✅ [{sname}] {slave_symbol} {order_type_name(order_type)} "
            f"lot={lot:.2f} → #{result.order} (мастер #{master_pos.ticket})"
        )
        self._trade_event({
            "time": datetime.now().strftime("%H:%M:%S"),
            "slave": sname,
            "symbol": slave_symbol,
            "direction": order_type_name(order_type),
            "lot": lot,
            "master_ticket": str(master_pos.ticket),
            "slave_ticket": str(result.order),
            "success": True,
            "status": f"✅ Открыт #{result.order}",
        })
        return result.order

    # ── Закрытие позиции на слейве ───────────────────────────

    def _close_position(self, sname: str, master_ticket_str: str,
                        slave_ticket: int):
        positions = mt5.positions_get(ticket=slave_ticket)
        if not positions:
            return

        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return

        close_type = opposite_order_type(pos.type)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        sym_info = mt5.symbol_info(pos.symbol)
        filling = get_filling_mode(sym_info) if sym_info else mt5.ORDER_FILLING_IOC
        if sym_info:
            price = normalize_price(price, sym_info.digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": slave_ticket,
            "price": price,
            "comment": f"CT_close_{master_ticket_str}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = try_send_order(request, self._log)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            comment = result.comment if result else ""
            self._log(
                f"❌ [{sname}] Ошибка закрытия #{slave_ticket} "
                f"filling={request.get('type_filling')} retcode={retcode} {comment}"
            )
        else:
            self._log(
                f"✅ [{sname}] Закрыта позиция #{slave_ticket} "
                f"(мастер #{master_ticket_str})"
            )

    # ── Модификация SL/TP на слейве ────────────────────────────

    def _modify_position(self, sname: str, slave_pos, new_sl: float,
                         new_tp: float, sym_info):
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": slave_pos.symbol,
            "position": slave_pos.ticket,
            "volume": slave_pos.volume,
            "sl": new_sl,
            "tp": new_tp,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": get_filling_mode(sym_info),
        }

        result = try_send_order(request, self._log)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            self._log(
                f"⚠️ [{sname}] Ошибка модификации SL/TP #{slave_pos.ticket} "
                f"retcode={retcode}"
            )
        else:
            self._log(
                f"📝 [{sname}] SL/TP обновлены #{slave_pos.ticket} "
                f"SL={new_sl:.{sym_info.digits}f} TP={new_tp:.{sym_info.digits}f}"
            )

    # ── Частичное закрытие позиции на слейве ──────────────────

    def _partial_close(self, sname: str, slave_pos, close_volume: float,
                       master_ticket_str: str = ""):
        tick = mt5.symbol_info_tick(slave_pos.symbol)
        if tick is None:
            return

        sym_info = mt5.symbol_info(slave_pos.symbol)
        close_type = opposite_order_type(slave_pos.type)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        if sym_info:
            price = normalize_price(price, sym_info.digits)

        filling = get_filling_mode(sym_info) if sym_info else mt5.ORDER_FILLING_IOC

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": slave_pos.symbol,
            "volume": close_volume,
            "type": close_type,
            "position": slave_pos.ticket,
            "price": price,
            "comment": f"CT_pclose_{master_ticket_str}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = try_send_order(request, self._log)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            self._log(f"❌ [{sname}] Ошибка частичного закрытия #{slave_pos.ticket} retcode={retcode}")
        else:
            self._log(f"✅ [{sname}] Частичное закрытие #{slave_pos.ticket} vol={close_volume:.2f}")

    # ── Размещение отложенного ордера на слейве ──────────────

    def _place_order(self, slave, sid, sname, symbol_map,
                     master_order, balance) -> Optional[int]:
        raw_symbol = self._sym_lookup(symbol_map, master_order.symbol)
        if not raw_symbol:
            self._log(f"⚠️ [{sname}] Символ мастера {master_order.symbol} не в маппинге")
            return None

        slave_symbol = resolve_symbol(raw_symbol)
        if slave_symbol is None:
            self._log(f"⚠️ [{sname}] Символ {raw_symbol} не найден (проверьте регистр)")
            return None

        if slave_symbol != raw_symbol:
            self._log(f"ℹ️ [{sname}] Символ {raw_symbol} → {slave_symbol}")

        if not mt5.symbol_select(slave_symbol, True):
            self._log(f"⚠️ [{sname}] Не удалось добавить {slave_symbol} в Market Watch")

        same_symbol = (slave_symbol == master_order.symbol)

        sym_info = mt5.symbol_info(slave_symbol)
        if sym_info is None:
            self._log(f"⚠️ [{sname}] Символ {slave_symbol} не найден")
            return None

        if sym_info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
            self._log(
                f"⚠️ [{sname}] {slave_symbol} trade_mode={sym_info.trade_mode} — не торгуется"
            )
            return None

        # Расчёт лота
        if master_order.sl != 0.0 and same_symbol:
            sl_pct = abs(master_order.price_open - master_order.sl) / master_order.price_open
            sl_distance = master_order.price_open * sl_pct
            lot = calculate_lot(
                sym_info, sl_distance,
                slave.get("risk_type", "percent"),
                slave.get("risk_value", 1.0),
                balance
            )
            if lot <= 0:
                lot = slave.get("default_lot", 0.01)
        else:
            lot = slave.get("default_lot", 0.01)

        if self.config.get("min_lot_mode", False):
            lot = sym_info.volume_min

        digits = sym_info.digits
        order_price = normalize_price(master_order.price_open, digits)
        sl = master_order.sl if same_symbol else 0.0
        tp = master_order.tp if same_symbol else 0.0
        if sl != 0.0:
            sl = normalize_price(sl, digits)
        if tp != 0.0:
            tp = normalize_price(tp, digits)

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": slave_symbol,
            "volume": lot,
            "type": master_order.type,
            "price": order_price,
            "sl": sl,
            "tp": tp,
            "comment": f"CT_{master_order.ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": get_filling_mode(sym_info),
        }

        result = try_send_order(request, self._log)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            comment = result.comment if result else ""
            self._log(
                f"❌ [{sname}] Ошибка ордера {slave_symbol} "
                f"{order_type_name(master_order.type)} "
                f"filling={request.get('type_filling')} retcode={retcode} {comment}"
            )
            return None

        self._log(
            f"✅ [{sname}] Ордер {slave_symbol} "
            f"{order_type_name(master_order.type)} "
            f"lot={lot:.2f} price={master_order.price_open} "
            f"→ #{result.order}"
        )
        return result.order

    # ── Отмена отложенного ордера на слейве ─────────────────

    def _cancel_order(self, sname: str, master_ticket_str: str,
                      slave_ticket: int):
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": slave_ticket,
            "comment": f"CT_cancel_{master_ticket_str}",
        }
        result = try_send_order(request, self._log)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            self._log(
                f"❌ [{sname}] Ошибка отмены ордера #{slave_ticket} "
                f"retcode={retcode}"
            )
        else:
            self._log(
                f"✅ [{sname}] Отменён ордер #{slave_ticket} "
                f"(мастер #{master_ticket_str})"
            )
