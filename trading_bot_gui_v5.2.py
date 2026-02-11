# -*- coding: utf-8 -*-
"""
交易機器人 v5.3 - 現代化 GUI 控制面板 (中文版)
整合 Market Scanner + 統一出場 SOP

美化版：
- 精緻化配色方案 (Refined FinTech Dark Theme)
- 色彩日誌系統（依 level 上色）
- 更好的卡片、間距與邊框視覺層次
- 程式碼優化：統一配置映射、減少重複
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import json
import subprocess
import threading
import queue
import os
import sys
import platform
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# Scanner 整合
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from scanner import MarketScanner, ScannerConfig, ScanResult, MarketSummary, SCANNER_AVAILABLE
except ImportError:
    SCANNER_AVAILABLE = False
    print("⚠️ Market Scanner 模組未找到，掃描功能將被禁用")

# 設定 CustomTkinter 全局主題
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ==================== 跨平台字體工具 ====================
_SYSTEM = platform.system()

def _cjk_font() -> str:
    if _SYSTEM == "Linux":
        return "Noto Sans CJK TC"
    elif _SYSTEM == "Darwin":
        return "PingFang TC"
    return "Microsoft JhengHei UI"

def _mono_font() -> str:
    if _SYSTEM == "Linux":
        return "Monospace"
    elif _SYSTEM == "Darwin":
        return "Menlo"
    return "Consolas"


# ==================== 顏色 & 字體常數 ====================
C = {
    # 背景層次 (4 層深度)
    'bg_root':   '#080B11',
    'bg_panel':  '#111620',
    'bg_card':   '#1A1F2E',
    'bg_input':  '#232A3A',
    'bg_hover':  '#2C3548',

    # 主色調
    'primary':       '#00B4D8',
    'primary_hover': '#48CAE4',
    'primary_dim':   '#0A3D4F',
    'primary_glow':  '#00B4D820',   # 帶透明度

    # 語義色
    'success':       '#00E676',
    'success_dim':   '#0D2818',
    'warning':       '#FFB300',
    'warning_dim':   '#2A2000',
    'danger':        '#FF3D00',
    'danger_dim':    '#2A0E00',
    'danger_hover':  '#FF6E40',

    # 文字
    'text_main': '#E8ECF4',
    'text_sub':  '#8B95A8',
    'text_dim':  '#525E72',

    # 邊框 & 分隔
    'border':       '#262E3E',
    'border_light': '#3A4560',
    'border_focus': '#00B4D8',

    # 日誌顏色
    'log_info':    '#8B95A8',
    'log_success': '#00E676',
    'log_warning': '#FFB300',
    'log_error':   '#FF3D00',
    'log_time':    '#525E72',
}

_CJK = _cjk_font()
_MONO = _mono_font()

F = {
    'h1':        (_CJK, 26, "bold"),
    'h2':        (_CJK, 18, "bold"),
    'h3':        (_CJK, 14, "bold"),
    'body':      (_CJK, 12),
    'body_bold': (_CJK, 12, "bold"),
    'small':     (_CJK, 10),
    'small_bold':(_CJK, 10, "bold"),
    'mono':      (_MONO, 11),
    'mono_lg':   (_MONO, 18, "bold"),
    'mono_sm':   (_MONO, 10),
}


class ModernTradingBotGUI(ctk.CTk):
    """交易機器人 v5.3 專業版 GUI — 美化 & 優化版"""

    # ==================== 配置映射表 ====================
    # (json_key, attr_name, default_value)
    # 用於統一 load_config / save_config，消除重複的 if/set 邏輯
    _CONFIG_MAP: List[Tuple[str, str, Any]] = [
        # API
        ("api_key",           "api_key_var",           ""),
        ("api_secret",        "api_secret_var",        ""),
        ("exchange",          "exchange_var",           "binance"),
        ("sandbox_mode",      "sandbox_var",           True),
        # Telegram
        ("telegram_enabled",  "telegram_enabled_var",  False),
        ("telegram_bot_token","telegram_token_var",     ""),
        ("telegram_chat_id",  "telegram_chat_var",      ""),
        # 交易模式
        ("trading_mode",      "trading_mode_var",      "future"),
        ("trading_direction", "trading_direction_var",  "both"),
        ("leverage",          "leverage_var",          5),
        ("use_hard_stop_loss","hard_stop_var",          True),
        ("check_interval",    "check_interval_var",    60),
        # 風險
        ("risk_per_trade",       "risk_per_trade_var",       0.02),
        ("max_total_risk",       "max_total_risk_var",       0.06),
        ("max_positions_per_group","max_positions_var",       3),
        ("max_position_percent", "max_position_percent_var", 0.30),
        # 技術指標
        ("lookback_period",   "lookback_var",      20),
        ("volume_ma_period",  "volume_ma_var",     20),
        ("atr_period",        "atr_period_var",    14),
        ("atr_multiplier",    "atr_mult_var",      1.5),
        # 市場過濾
        ("enable_market_filter",      "enable_market_filter_var", True),
        ("adx_threshold",             "adx_threshold_var",        20),
        ("atr_spike_multiplier",      "atr_spike_var",            2.0),
        ("ema_entanglement_threshold","ema_entangle_var",         0.02),
        # 量能
        ("enable_volume_grading",  "enable_volume_grading_var", True),
        ("vol_explosive_threshold","vol_explosive_var",          2.5),
        ("vol_strong_threshold",   "vol_strong_var",             1.5),
        ("vol_moderate_threshold", "vol_moderate_var",            1.0),
        ("vol_minimum_threshold",  "vol_minimum_var",             0.7),
        ("accept_weak_signals",    "accept_weak_var",             True),
        # v5.1
        ("enable_mtf_confirmation",    "enable_mtf_var",       True),
        ("enable_dynamic_thresholds",  "enable_dynamic_var",   True),
        ("enable_tiered_entry",        "enable_tiered_var",    True),
        ("enable_ema_pullback",        "enable_pullback_var",  True),
        ("enable_volume_breakout",     "enable_breakout_var",  True),
        ("enable_structure_break_exit","enable_structure_var",  True),
        ("tier_a_position_mult", "tier_a_var", 1.0),
        ("tier_b_position_mult", "tier_b_var", 0.7),
        ("tier_c_position_mult", "tier_c_var", 0.5),
        # v5.3 出場設定
        ("max_hold_hours",      "max_hold_hours_var",    24),
        ("first_partial_pct",   "first_partial_pct_var", 30),
        ("second_partial_pct",  "second_partial_pct_var",30),
        # Scanner
        ("use_scanner_symbols", "use_scanner_var", False),
    ]

    # 非 GUI 變數的配置 (直接存為 instance attribute)
    _CONFIG_ATTRS: List[Tuple[str, str, Any]] = [
        ("aplus_trailing_atr_mult",  "_aplus_trailing_mult",1.5),
        ("scanner_json_path",        "_scanner_json_path",  "hot_symbols.json"),
        ("scanner_max_age_minutes",  "_scanner_max_age",    30),
    ]

    def __init__(self):
        super().__init__()

        # 視窗設定
        self.title("波茶波茶 v5.3 專業版")
        self.geometry("1520x980")
        self.minsize(960, 640)
        self.resizable(True, True)
        self.configure(fg_color=C['bg_root'])

        # 配置
        self.config_file = "bot_config.json"

        # 狀態
        self.bot_process = None
        self.is_running = False
        self.is_connected = False
        self.is_trading = False

        # 日誌批次緩衝（使用 thread-safe queue）
        self._log_queue = queue.Queue()
        self._log_flush_scheduled = False

        # Scanner 狀態
        self.scanner = None
        self.scanner_thread = None
        self.is_scanning = False
        self.auto_scan_enabled = False
        self.last_scan_time = None
        self.scan_results = []

        # 初始化非 GUI 配置
        for _, attr, default in self._CONFIG_ATTRS:
            setattr(self, attr, default)

        # 建立 UI → 載入配置
        self._build_ui()
        self.load_config()

        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ==================== UI 構建輔助工具 ====================

    def _section(self, parent, title: str, icon: str = "▸"):
        """區塊標題 + 分隔線"""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=(18, 8))
        ctk.CTkLabel(frame, text=f"{icon}  {title}", font=F['h3'],
                     text_color=C['primary']).pack(side="left", padx=5)
        ctk.CTkFrame(frame, height=1, fg_color=C['border']).pack(
            side="left", fill="x", expand=True, padx=(12, 0))

    def _card(self, parent) -> ctk.CTkFrame:
        """帶微妙邊框的設置卡片"""
        card = ctk.CTkFrame(parent, fg_color=C['bg_card'], corner_radius=10,
                            border_width=1, border_color=C['border'])
        card.pack(fill="x", pady=3, padx=2)
        return card

    def _row(self, parent, label: str, var, input_type="entry",
             options=None, from_=0, to=100, show=None):
        """通用設置列：標籤 + 控件"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=7)

        ctk.CTkLabel(row, text=label, font=F['body'], text_color=C['text_main'],
                     anchor="w", width=145).pack(side="left")

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right", fill="x", expand=True)

        if input_type == "entry":
            ctk.CTkEntry(right, textvariable=var, height=32, corner_radius=6,
                         fg_color=C['bg_input'], border_color=C['border'],
                         text_color=C['text_main'], font=F['mono'], show=show
                         ).pack(side="right", fill="x", expand=True)

        elif input_type == "switch":
            ctk.CTkSwitch(right, variable=var, text="", width=44, height=24,
                          progress_color=C['primary'],
                          button_color=C['text_main'],
                          button_hover_color=C['text_main']).pack(side="right")

        elif input_type == "slider":
            sf = ctk.CTkFrame(right, fg_color="transparent")
            sf.pack(side="right", fill="x", expand=True)

            is_int = isinstance(from_, int) and isinstance(to, int)
            fmt = (lambda v: str(int(float(v)))) if is_int else (lambda v: f"{float(v):.2f}")
            val_lbl = ctk.CTkLabel(sf, text=fmt(var.get()), font=F['mono'],
                                   text_color=C['primary'], width=55)
            val_lbl.pack(side="right", padx=(8, 0))

            def _update(v, _lbl=val_lbl, _var=var, _fmt=fmt, _int=is_int):
                _lbl.configure(text=_fmt(v))
                _var.set(int(float(v)) if _int else float(v))

            ctk.CTkSlider(sf, from_=from_, to=to, variable=var, height=16,
                          progress_color=C['primary'],
                          button_color=C['text_main'],
                          button_hover_color=C['primary_hover'],
                          command=_update).pack(side="right", fill="x", expand=True)

        elif input_type == "combo":
            ctk.CTkComboBox(right, variable=var, values=options or [], height=32,
                            corner_radius=6, fg_color=C['bg_input'],
                            border_color=C['border'], text_color=C['text_main'],
                            button_color=C['bg_hover'], button_hover_color=C['border_light'],
                            dropdown_fg_color=C['bg_card'],
                            dropdown_hover_color=C['bg_input'],
                            font=F['body']).pack(side="right", fill="x", expand=True)

    def _action_btn(self, parent, text, color, command, text_color=None,
                    border_color=None, width=120, height=40, **kw):
        """統一風格的操作按鈕"""
        tc = text_color or C['bg_root']
        hover = kw.pop('hover_color', None)
        if hover is None:
            # 自動計算 hover 色（稍微亮一點）
            hover = C.get('primary_hover', color)

        btn_kw = dict(
            text=text, font=F['body_bold'], fg_color=color,
            text_color=tc, hover_color=hover,
            height=height, width=width, corner_radius=8, command=command
        )
        if border_color:
            btn_kw['border_width'] = 1
            btn_kw['border_color'] = border_color
        btn_kw.update(kw)
        return ctk.CTkButton(parent, **btn_kw)

    # ==================== UI 主架構 ====================

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_hud()

        # 內容區
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=(8, 18))

        # 左側設定面板
        self.left_panel = ctk.CTkFrame(content, fg_color="transparent", width=560)
        self.left_panel.pack(side="left", fill="both", padx=(0, 12))
        self.left_panel.pack_propagate(False)

        # 右側日誌
        self.right_panel = ctk.CTkFrame(content, fg_color="transparent")
        self.right_panel.pack(side="right", fill="both", expand=True)

        self._build_tabs()
        self._build_log_panel()

    # ==================== 頂部標題列 ====================

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent", height=75)
        header.pack(fill="x", padx=24, pady=(14, 4))

        # ── 左：Logo ──
        logo = ctk.CTkFrame(header, fg_color="transparent")
        logo.pack(side="left")

        ctk.CTkLabel(logo, text="波茶波茶",
                     font=ctk.CTkFont(family=_CJK, size=28, weight="bold"),
                     text_color=C['text_main']).pack(side="left", anchor="s")

        # 版本標籤（pill badge）
        badge = ctk.CTkLabel(logo, text="  v5.3  ", height=22, corner_radius=11,
                             font=ctk.CTkFont(family=_CJK, size=10, weight="bold"),
                             text_color=C['bg_root'], fg_color=C['primary'])
        badge.pack(side="left", padx=(10, 0), pady=(6, 0))

        ctk.CTkLabel(logo, text="智能演算法交易系統",
                     font=ctk.CTkFont(family=_CJK, size=11),
                     text_color=C['text_dim']).pack(side="bottom", anchor="w", pady=(0, 4))

        # ── 右：狀態 + 按鈕 ──
        ctrl = ctk.CTkFrame(header, fg_color="transparent")
        ctrl.pack(side="right")

        # 狀態膠囊
        pill = ctk.CTkFrame(ctrl, fg_color=C['bg_card'], corner_radius=20,
                            height=38, border_width=1, border_color=C['border'])
        pill.pack(side="left", padx=(0, 16))

        self.status_dot = ctk.CTkLabel(pill, text="●", font=("Arial", 16),
                                       text_color=C['text_dim'])
        self.status_dot.pack(side="left", padx=(14, 4), pady=7)

        self.status_text = ctk.CTkLabel(pill, text="系統待命", font=F['body_bold'],
                                        text_color=C['text_dim'])
        self.status_text.pack(side="left", padx=(0, 14), pady=7)

        # 按鈕群
        self.start_btn = self._action_btn(ctrl, "▶  啟動系統", C['success'],
                                          self.start_bot, hover_color="#00C853")
        self.start_btn.pack(side="left", padx=4)

        self.trade_btn = self._action_btn(ctrl, "▶  開始交易", C['primary'],
                                          self.start_trading,
                                          hover_color=C['primary_hover'],
                                          state="disabled")
        self.trade_btn.pack(side="left", padx=4)

        self.stop_btn = self._action_btn(ctrl, "■  停止系統", C['bg_input'],
                                         self.stop_bot,
                                         text_color=C['danger'],
                                         border_color=C['danger'],
                                         hover_color=C['bg_hover'],
                                         state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.close_all_btn = self._action_btn(ctrl, "⚠  全部平倉", C['warning'],
                                              self.close_all_positions,
                                              hover_color="#FF8F00",
                                              state="disabled")
        self.close_all_btn.pack(side="left", padx=4)

    # ==================== HUD 儀表板 ====================

    def _build_hud(self):
        hud = ctk.CTkFrame(self, fg_color=C['bg_panel'], corner_radius=12,
                           height=100, border_width=1, border_color=C['border'])
        hud.pack(fill="x", padx=20, pady=(8, 8))
        hud.pack_propagate(False)

        inner = ctk.CTkFrame(hud, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14)

        # ── 左：數據卡片 ──
        data = ctk.CTkFrame(inner, fg_color="transparent")
        data.pack(side="left", fill="y", pady=12)

        self.status_cards: Dict[str, ctk.CTkLabel] = {}
        metrics = [
            ("balance",   "帳戶餘額",    "-- USDT",  C['primary']),
            ("positions", "活躍持倉",    "0 / 3",    C['text_main']),
            ("today_pnl", "24H 盈虧",   "-- USDT",  C['success']),
            ("signals",   "信號產生",    "0",        C['warning']),
            ("scanned",   "掃描標的",    "-- 個",    C['primary']),
        ]

        for key, title, default, color in metrics:
            card = ctk.CTkFrame(data, fg_color=C['bg_card'], corner_radius=8,
                                width=170, border_width=1, border_color=C['border'])
            card.pack(side="left", fill="y", padx=(0, 10))
            card.pack_propagate(False)

            ctk.CTkLabel(card, text=title, font=F['small_bold'],
                         text_color=C['text_sub']).pack(anchor="w", padx=12, pady=(10, 0))
            lbl = ctk.CTkLabel(card, text=default, font=F['mono_lg'], text_color=color)
            lbl.pack(anchor="w", padx=12, pady=(2, 8))
            self.status_cards[key] = lbl

        # ── 右：快捷按鈕 ──
        actions = ctk.CTkFrame(inner, fg_color="transparent")
        actions.pack(side="right", fill="y", pady=28)

        for text, is_primary, cmd in [
            ("儲存設定", True, self.save_config),
            ("重新載入", False, self.load_config),
            ("清除日誌", False, self.clear_log),
        ]:
            ctk.CTkButton(
                actions, text=text, font=F['body_bold'],
                fg_color=C['primary'] if is_primary else C['bg_input'],
                text_color=C['bg_root'] if is_primary else C['text_main'],
                hover_color=C['primary_hover'] if is_primary else C['bg_hover'],
                height=30, width=90, corner_radius=6, command=cmd
            ).pack(side="left", padx=4)

    # ==================== 設定分頁 ====================

    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(
            self.left_panel,
            fg_color=C['bg_panel'],
            segmented_button_fg_color=C['bg_root'],
            segmented_button_selected_color=C['primary_dim'],
            segmented_button_selected_hover_color=C['primary_dim'],
            segmented_button_unselected_color=C['bg_root'],
            segmented_button_unselected_hover_color=C['bg_card'],
            text_color=C['text_sub'],
            corner_radius=12,
            border_width=1,
            border_color=C['border'],
            width=560
        )
        self.tabview.pack(fill="both", expand=True)

        tab_builders = [
            ("API 連線",  self._tab_api),
            ("交易設定",  self._tab_trading),
            ("風險管理",  self._tab_risk),
            ("市場過濾",  self._tab_filter),
            ("量能分級",  self._tab_volume),
            ("出場設定",  self._tab_exit),
            ("進階功能",  self._tab_v51),
            ("市場掃描",  self._tab_scanner),
        ]
        for name, builder in tab_builders:
            self.tabview.add(name)
            builder(self.tabview.tab(name))

    def _scrollable(self, parent) -> ctk.CTkScrollableFrame:
        s = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        s.pack(fill="both", expand=True)
        return s

    # ── API 連線 ──
    def _tab_api(self, parent):
        s = self._scrollable(parent)

        self._section(s, "交易所 API 設定")
        card = self._card(s)
        self.api_key_var = ctk.StringVar()
        self.api_secret_var = ctk.StringVar()
        self.exchange_var = ctk.StringVar(value="binance")
        self.sandbox_var = ctk.BooleanVar(value=True)
        self._row(card, "API 金鑰", self.api_key_var)
        self._row(card, "API 密鑰", self.api_secret_var, show="•")
        self._row(card, "交易所", self.exchange_var, "combo",
                  options=["binance", "bybit", "okx", "bitget"])
        self._row(card, "測試網模式", self.sandbox_var, "switch")

        self._section(s, "Telegram 通知設定")
        card2 = self._card(s)
        self.telegram_enabled_var = ctk.BooleanVar(value=False)
        self.telegram_token_var = ctk.StringVar()
        self.telegram_chat_var = ctk.StringVar()
        self._row(card2, "啟用 Telegram", self.telegram_enabled_var, "switch")
        self._row(card2, "Bot Token", self.telegram_token_var)
        self._row(card2, "Chat ID", self.telegram_chat_var)

    # ── 交易設定 ──
    def _tab_trading(self, parent):
        s = self._scrollable(parent)

        self._section(s, "交易模式設定")
        card = self._card(s)
        self.trading_mode_var = ctk.StringVar(value="future")
        self.trading_direction_var = ctk.StringVar(value="both")
        self.leverage_var = ctk.IntVar(value=5)
        self.hard_stop_var = ctk.BooleanVar(value=True)
        self._row(card, "交易模式", self.trading_mode_var, "combo", options=["future", "spot"])
        self._row(card, "交易方向", self.trading_direction_var, "combo",
                  options=["both", "long_only", "short_only"])
        self._row(card, "槓桿倍數", self.leverage_var, "slider", from_=1, to=20)
        self._row(card, "硬止損", self.hard_stop_var, "switch")

        self._section(s, "交易標的設定")
        card2 = self._card(s)
        sym_frame = ctk.CTkFrame(card2, fg_color="transparent")
        sym_frame.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(sym_frame, text="交易對（以逗號分隔）", font=F['small'],
                     text_color=C['text_sub']).pack(anchor="w")
        self.symbols_entry = ctk.CTkTextbox(
            sym_frame, height=55, corner_radius=6, font=F['mono'],
            fg_color=C['bg_input'], border_color=C['border'], border_width=1,
            text_color=C['text_main'])
        self.symbols_entry.pack(fill="x", pady=(4, 0))
        self.symbols_entry.insert("1.0", "BTC/USDT, ETH/USDT, SOL/USDT")

        self._section(s, "時間設定")
        card3 = self._card(s)
        self.check_interval_var = ctk.IntVar(value=60)
        self.max_hold_hours_var = ctk.IntVar(value=24)
        self._row(card3, "檢查間隔（秒）", self.check_interval_var, "slider", from_=10, to=300)
        self._row(card3, "最大持倉時間(H)", self.max_hold_hours_var, "slider", from_=6, to=72)

    # ── 風險管理 ──
    def _tab_risk(self, parent):
        s = self._scrollable(parent)

        self._section(s, "倉位管理")
        card = self._card(s)
        self.risk_per_trade_var = ctk.DoubleVar(value=0.02)
        self.max_total_risk_var = ctk.DoubleVar(value=0.06)
        self.max_positions_var = ctk.IntVar(value=3)
        self.max_position_percent_var = ctk.DoubleVar(value=0.30)
        self._row(card, "單筆風險", self.risk_per_trade_var, "slider", from_=0.01, to=0.10)
        self._row(card, "最大總風險", self.max_total_risk_var, "slider", from_=0.01, to=0.20)
        self._row(card, "最大持倉數", self.max_positions_var, "slider", from_=1, to=10)
        self._row(card, "單筆最大倉位%", self.max_position_percent_var, "slider", from_=0.10, to=0.50)

        self._section(s, "技術指標參數")
        card2 = self._card(s)
        self.lookback_var = ctk.IntVar(value=20)
        self.volume_ma_var = ctk.IntVar(value=20)
        self.atr_period_var = ctk.IntVar(value=14)
        self.atr_mult_var = ctk.DoubleVar(value=1.5)
        self._row(card2, "回溯週期", self.lookback_var, "slider", from_=5, to=50)
        self._row(card2, "成交量均線週期", self.volume_ma_var, "slider", from_=5, to=50)
        self._row(card2, "ATR 週期", self.atr_period_var, "slider", from_=5, to=30)
        self._row(card2, "ATR 乘數", self.atr_mult_var, "slider", from_=0.5, to=4.0)

    # ── 市場過濾 ──
    def _tab_filter(self, parent):
        s = self._scrollable(parent)
        self._section(s, "市場過濾條件")
        card = self._card(s)
        self.enable_market_filter_var = ctk.BooleanVar(value=True)
        self.adx_threshold_var = ctk.IntVar(value=20)
        self.atr_spike_var = ctk.DoubleVar(value=2.0)
        self.ema_entangle_var = ctk.DoubleVar(value=0.02)
        self._row(card, "啟用市場過濾", self.enable_market_filter_var, "switch")
        self._row(card, "ADX 閾值", self.adx_threshold_var, "slider", from_=5, to=40)
        self._row(card, "ATR 突出乘數", self.atr_spike_var, "slider", from_=1.0, to=5.0)
        self._row(card, "均線糾纏閾值", self.ema_entangle_var, "slider", from_=0.01, to=0.10)

    # ── 量能分級 ──
    def _tab_volume(self, parent):
        s = self._scrollable(parent)
        self._section(s, "量能分級系統")
        card = self._card(s)
        self.enable_volume_grading_var = ctk.BooleanVar(value=True)
        self.vol_explosive_var = ctk.DoubleVar(value=2.5)
        self.vol_strong_var = ctk.DoubleVar(value=1.5)
        self.vol_moderate_var = ctk.DoubleVar(value=1.0)
        self.vol_minimum_var = ctk.DoubleVar(value=0.7)
        self.accept_weak_var = ctk.BooleanVar(value=True)
        self._row(card, "啟用量能分級", self.enable_volume_grading_var, "switch")
        self._row(card, "爆發量閾值", self.vol_explosive_var, "slider", from_=1.5, to=5.0)
        self._row(card, "強勢量閾值", self.vol_strong_var, "slider", from_=1.0, to=3.0)
        self._row(card, "中等量閾值", self.vol_moderate_var, "slider", from_=0.5, to=2.0)
        self._row(card, "最低量閾值", self.vol_minimum_var, "slider", from_=0.3, to=1.5)
        self._row(card, "接受弱勢信號", self.accept_weak_var, "switch")

    # ── 出場設定（v5.3 統一 SOP） ──
    def _tab_exit(self, parent):
        s = self._scrollable(parent)

        self._section(s, "統一出場 SOP")
        card = self._card(s)
        self.first_partial_pct_var = ctk.IntVar(value=30)
        self.second_partial_pct_var = ctk.IntVar(value=30)
        self._row(card, "1.5R 減倉比例(%)", self.first_partial_pct_var, "slider", from_=10, to=60)
        self._row(card, "2.5R 減倉比例(%)", self.second_partial_pct_var, "slider", from_=10, to=60)

        self._section(s, "出場流程說明")
        info_card = self._card(s)
        info_frame = ctk.CTkFrame(info_card, fg_color="transparent")
        info_frame.pack(fill="x", padx=16, pady=10)
        sop_text = (
            "1.0R  →  移損至 +0.3R（不減倉）\n"
            "1.5R  →  第一次減倉，移損至 +0.5R\n"
            "2.5R  →  第二次減倉，移損至 +1.5R，啟動 ATR 追蹤止損\n"
            "尾倉  →  由 ATR 追蹤止損管理\n"
            "超時  →  未達 1.5R 且超過最大持倉時間，全部平倉"
        )
        ctk.CTkLabel(info_frame, text=sop_text, font=F['small'],
                     text_color=C['text_sub'], justify="left").pack(anchor="w")

    # ── 進階功能 ──
    def _tab_v51(self, parent):
        s = self._scrollable(parent)

        self._section(s, "進階功能開關")
        card = self._card(s)
        self.enable_mtf_var = ctk.BooleanVar(value=True)
        self.enable_dynamic_var = ctk.BooleanVar(value=True)
        self.enable_tiered_var = ctk.BooleanVar(value=True)
        self.enable_pullback_var = ctk.BooleanVar(value=True)
        self.enable_breakout_var = ctk.BooleanVar(value=True)
        self.enable_structure_var = ctk.BooleanVar(value=True)
        self._row(card, "多時間框架確認", self.enable_mtf_var, "switch")
        self._row(card, "動態閾值調整", self.enable_dynamic_var, "switch")
        self._row(card, "分級入場", self.enable_tiered_var, "switch")
        self._row(card, "EMA 回撤策略", self.enable_pullback_var, "switch")
        self._row(card, "量能突破策略", self.enable_breakout_var, "switch")
        self._row(card, "結構破壞出場", self.enable_structure_var, "switch")

        self._section(s, "分層倉位設定")
        card2 = self._card(s)
        self.tier_a_var = ctk.DoubleVar(value=1.0)
        self.tier_b_var = ctk.DoubleVar(value=0.7)
        self.tier_c_var = ctk.DoubleVar(value=0.5)
        self._row(card2, "A 級倉位乘數", self.tier_a_var, "slider", from_=0.5, to=1.5)
        self._row(card2, "B 級倉位乘數", self.tier_b_var, "slider", from_=0.3, to=1.0)
        self._row(card2, "C 級倉位乘數", self.tier_c_var, "slider", from_=0.2, to=0.8)

    # ==================== 日誌面板（色彩日誌） ====================

    def _build_log_panel(self):
        frame = ctk.CTkFrame(self.right_panel, fg_color=C['bg_panel'],
                             corner_radius=12, border_width=1, border_color=C['border'])
        frame.pack(fill="both", expand=True)

        # 標題
        hdr = ctk.CTkFrame(frame, fg_color="transparent", height=38)
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(hdr, text="●  系統日誌", font=F['h3'],
                     text_color=C['text_main']).pack(side="left")

        # 日誌本體 — 使用 tk.Text 支援 tag 上色
        log_container = ctk.CTkFrame(frame, fg_color=C['bg_root'], corner_radius=8,
                                     border_width=1, border_color=C['border'])
        log_container.pack(fill="both", expand=True, padx=14, pady=14)

        self.log_text = tk.Text(
            log_container, bg=C['bg_root'], fg=C['log_info'],
            font=(_MONO, 11), bd=0, highlightthickness=0,
            wrap="word", insertbackground=C['text_dim'],
            selectbackground=C['primary_dim'], selectforeground=C['text_main'],
            padx=10, pady=8, state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)

        # 滾軸
        scrollbar = ctk.CTkScrollbar(log_container, command=self.log_text.yview,
                                     button_color=C['bg_hover'],
                                     button_hover_color=C['border_light'])
        scrollbar.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # 日誌 tag 配色
        self.log_text.tag_configure("time",    foreground=C['log_time'])
        self.log_text.tag_configure("資訊",    foreground=C['log_info'])
        self.log_text.tag_configure("成功",    foreground=C['log_success'])
        self.log_text.tag_configure("警告",    foreground=C['log_warning'])
        self.log_text.tag_configure("錯誤",    foreground=C['log_error'])
        self.log_text.tag_configure("level_info",    foreground=C['text_dim'])
        self.log_text.tag_configure("level_success", foreground=C['success_dim'] if C['success_dim'] != C['bg_root'] else C['log_success'])
        self.log_text.tag_configure("level_warning", foreground=C['warning'])
        self.log_text.tag_configure("level_error",   foreground=C['danger'])

        self.log_message("系統已初始化，準備就緒。")

    LOG_MAX_LINES = 1500  # 超過此行數自動裁剪舊日誌，防止長時間運行 OOM

    def log_message(self, message: str, level: str = "資訊"):
        """色彩日誌：時間灰色、level 標籤彩色、訊息依 level 上色"""
        ts = datetime.now().strftime("%H:%M:%S")
        tag = level  # 對應 tag name

        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}]", "time")
        self.log_text.insert("end", f" [{level}] ", tag)
        self.log_text.insert("end", f"{message}\n", tag)

        # 裁剪過舊日誌，避免數萬行後 GUI 卡頓
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > self.LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{line_count - self.LOG_MAX_LINES + 1}.0")

        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log_message("日誌已清除。")

    def _flush_log_buffer(self):
        """批次插入緩衝的 bot 日誌行，減少 Tkinter 操作次數"""
        self._log_flush_scheduled = False

        lines = []
        while not self._log_queue.empty():
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break

        if not lines:
            return

        self.log_text.configure(state="normal")
        for line in lines:
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{ts}]", "time")
            self.log_text.insert("end", f" [資訊] ", "資訊")
            self.log_text.insert("end", f"{line}\n", "資訊")

        # 批次插入後統一裁剪
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > self.LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{line_count - self.LOG_MAX_LINES + 1}.0")

        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ==================== 配置 Load / Save（統一映射版） ====================

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                self.log_message("已從文件載入配置。", "成功")
            else:
                cfg = self._default_config()
                self.log_message("使用預設配置。")

            # 批量設定 GUI 變數
            for json_key, attr_name, default in self._CONFIG_MAP:
                var = getattr(self, attr_name, None)
                if var is not None and hasattr(var, 'set'):
                    var.set(cfg.get(json_key, default))

            # 批量設定非 GUI 屬性
            for json_key, attr_name, default in self._CONFIG_ATTRS:
                setattr(self, attr_name, cfg.get(json_key, default))

            # 特殊處理：symbols textbox
            symbols = cfg.get("symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
            self.symbols_entry.delete("1.0", "end")
            self.symbols_entry.insert("1.0", ", ".join(symbols))

            # 特殊處理：scan_interval
            self.scan_interval_var.set(cfg.get("scanner_max_age_minutes", 30) // 2 or 15)

        except Exception as e:
            self.log_message(f"載入配置時發生錯誤: {e}", "錯誤")

    def save_config(self):
        try:
            symbols_text = self.symbols_entry.get("1.0", "end").strip()
            symbols = [s.strip() for s in symbols_text.split(",") if s.strip()]

            cfg = {"symbols": symbols}

            # 批量讀取 GUI 變數（含輸入驗證：若使用者輸入非法值則回退預設）
            for json_key, attr_name, default in self._CONFIG_MAP:
                var = getattr(self, attr_name, None)
                if var is not None and hasattr(var, 'get'):
                    try:
                        cfg[json_key] = var.get()
                    except (tk.TclError, ValueError):
                        cfg[json_key] = default
                        var.set(default)
                        self.log_message(
                            f"欄位 \"{json_key}\" 值無效，已還原為預設值 {default}", "警告")

            # 批量讀取非 GUI 屬性
            for json_key, attr_name, default in self._CONFIG_ATTRS:
                cfg[json_key] = getattr(self, attr_name, default)

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)

            self.log_message("配置已成功儲存。", "成功")
            messagebox.showinfo("成功", "配置已儲存！")
        except Exception as e:
            self.log_message(f"儲存配置失敗: {e}", "錯誤")
            messagebox.showerror("錯誤", f"儲存失敗: {e}")

    @staticmethod
    def _default_config() -> dict:
        return {
            "api_key": "", "api_secret": "", "exchange": "binance", "sandbox_mode": True,
            "trading_mode": "future", "trading_direction": "both", "leverage": 5,
            "use_hard_stop_loss": True,
            "telegram_enabled": False, "telegram_bot_token": "", "telegram_chat_id": "",
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            "risk_per_trade": 0.02, "max_total_risk": 0.06, "max_positions_per_group": 3,
            "max_position_percent": 0.30,
            "lookback_period": 20, "volume_ma_period": 20, "atr_period": 14, "atr_multiplier": 1.5,
            "enable_market_filter": True, "adx_threshold": 20, "atr_spike_multiplier": 2.0,
            "ema_entanglement_threshold": 0.02,
            "enable_volume_grading": True, "vol_explosive_threshold": 2.5, "vol_strong_threshold": 1.5,
            "vol_moderate_threshold": 1.0, "vol_minimum_threshold": 0.7, "accept_weak_signals": True,
            "enable_mtf_confirmation": True, "enable_dynamic_thresholds": True,
            "enable_tiered_entry": True, "enable_ema_pullback": True, "enable_volume_breakout": True,
            "tier_a_position_mult": 1.0, "tier_b_position_mult": 0.7, "tier_c_position_mult": 0.5,
            "enable_structure_break_exit": True, "check_interval": 60,
            "use_scanner_symbols": False, "scanner_json_path": "hot_symbols.json",
            "scanner_max_age_minutes": 30,
            "first_partial_pct": 30, "second_partial_pct": 30,
            "aplus_trailing_atr_mult": 1.5, "max_hold_hours": 24,
        }

    # ==================== Bot 控制邏輯 ====================

    def start_bot(self):
        if self.is_running:
            return
        self.save_config()
        self.is_running = True
        self.is_connected = False
        self.is_trading = False
        self._update_status("連線中", C['warning'])
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.log_message("正在連線交易所...", "資訊")

        def run_bot():
            try:
                bot_script = os.path.join(os.path.dirname(__file__),
                                          "trading_bot_v5.2_optimized.py")
                if not os.path.exists(bot_script):
                    self.after(0, lambda: self.log_message(
                        f"找不到機器人腳本: {bot_script}", "錯誤"))
                    return

                self.bot_process = subprocess.Popen(
                    [sys.executable, bot_script, "--info-only"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE, text=True, bufsize=1)

                for line in iter(self.bot_process.stdout.readline, ''):
                    if not self.is_running:
                        break
                    line = line.strip()
                    if line.startswith("__ACCOUNT_INFO_JSON__:"):
                        try:
                            info = json.loads(line.split(":", 1)[1])
                            self.after(0, lambda i=info: self._update_account(i))
                        except json.JSONDecodeError:
                            pass
                    else:
                        self._log_queue.put(line)
                        if not self._log_flush_scheduled:
                            self._log_flush_scheduled = True
                            self.after(200, self._flush_log_buffer)
                self.bot_process.wait()
            except Exception as e:
                self.after(0, lambda: self.log_message(f"機器人錯誤: {e}", "錯誤"))
            finally:
                self.after(0, self._on_bot_stopped)

        threading.Thread(target=run_bot, daemon=True).start()

    def _update_account(self, info: dict):
        self.is_connected = True
        self._update_status("已連線", C['primary'])
        self.trade_btn.configure(state="normal")
        self.close_all_btn.configure(state="normal")

        balance = info.get('balance', 0)
        self.status_cards['balance'].configure(text=f"{balance:.2f}")

        positions = info.get('positions', [])
        max_p = self.max_positions_var.get() if hasattr(self, 'max_positions_var') else 3
        self.status_cards['positions'].configure(text=f"{len(positions)} / {max_p}")

        self.log_message(f"帳戶餘額: {balance:.2f} USDT", "成功")
        if positions:
            self.log_message(f"現有持倉: {len(positions)} 個")
            for p in positions:
                sym = p.get('symbol', '?')
                amt = float(p.get('positionAmt', 0))
                pnl = float(p.get('unRealizedProfit', 0))
                side = 'LONG' if amt > 0 else 'SHORT'
                self.log_message(f"  {sym}: {side} {abs(amt):.4f} | PnL: ${pnl:.2f}")

    def start_trading(self):
        if not self.is_connected or not self.bot_process:
            self.log_message("請先啟動系統連線", "警告")
            return
        if self.is_trading:
            return
        self.is_trading = True
        self._update_status("交易中", C['success'])
        self.trade_btn.configure(state="disabled")
        try:
            self.bot_process.stdin.write("__START_TRADING__\n")
            self.bot_process.stdin.flush()
            self.log_message("交易已啟動！", "成功")
        except Exception as e:
            self.log_message(f"發送交易指令失敗: {e}", "錯誤")

    def close_all_positions(self):
        if not self.is_connected or not self.bot_process:
            self.log_message("請先啟動系統連線", "警告")
            return
        if not messagebox.askyesno("確認全平", "⚠️ 確定要關閉所有倉位嗎？\n此操作不可撤銷！"):
            return
        try:
            self.bot_process.stdin.write("__CLOSE_ALL_POSITIONS__\n")
            self.bot_process.stdin.flush()
            self.log_message("已發送全部平倉指令", "警告")
        except Exception as e:
            self.log_message(f"發送平倉指令失敗: {e}", "錯誤")

    def stop_bot(self):
        if not self.is_running:
            return
        self.is_running = False
        self.is_trading = False
        if self.bot_process:
            try:
                self.bot_process.stdin.write("__STOP__\n")
                self.bot_process.stdin.flush()
            except Exception:
                pass
            self.bot_process.terminate()
            self.bot_process = None
        self.log_message("正在停止交易機器人...", "警告")
        self._on_bot_stopped()

    def _on_bot_stopped(self):
        self.is_running = False
        self.is_connected = False
        self.is_trading = False
        self._update_status("已停止", C['danger'])
        self.start_btn.configure(state="normal")
        self.trade_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.close_all_btn.configure(state="disabled")
        self.status_cards['balance'].configure(text="-- USDT")
        self.status_cards['positions'].configure(text="0 / 3")
        self.log_message("交易機器人已停止。", "警告")

    def _update_status(self, text: str, color: str):
        self.status_dot.configure(text_color=color)
        self.status_text.configure(text=f"系統{text}", text_color=color)

    # ==================== Scanner 分頁 & 邏輯 ====================

    def _tab_scanner(self, parent):
        s = self._scrollable(parent)

        # ── 掃描控制 ──
        self._section(s, "掃描控制", "◉")
        ctrl = self._card(s)
        ci = ctk.CTkFrame(ctrl, fg_color="transparent")
        ci.pack(fill="x", padx=14, pady=10)

        btn_row = ctk.CTkFrame(ci, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))

        self.scan_btn = self._action_btn(btn_row, "▶  立即掃描", C['primary'],
                                         self._start_scan, width=115, height=36,
                                         hover_color=C['primary_hover'])
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.stop_scan_btn = self._action_btn(btn_row, "⏹  停止掃描", C['bg_input'],
                                              self._stop_scan, text_color=C['danger'],
                                              border_color=C['danger'], width=115, height=36,
                                              hover_color=C['bg_hover'], state="disabled")
        self.stop_scan_btn.pack(side="left", padx=(0, 8))

        # 自動掃描
        self.auto_scan_var = ctk.BooleanVar(value=False)
        af = ctk.CTkFrame(btn_row, fg_color="transparent")
        af.pack(side="left", padx=16)
        ctk.CTkLabel(af, text="自動掃描:", font=F['body'],
                     text_color=C['text_main']).pack(side="left", padx=(0, 4))
        ctk.CTkSwitch(af, variable=self.auto_scan_var, text="",
                      progress_color=C['primary'],
                      command=self._toggle_auto_scan).pack(side="left")

        # 間隔 & 上次掃描
        srow = ctk.CTkFrame(ci, fg_color="transparent")
        srow.pack(fill="x")
        self.scan_interval_var = ctk.IntVar(value=15)
        ctk.CTkLabel(srow, text="掃描間隔:", font=F['body'],
                     text_color=C['text_sub']).pack(side="left")
        ctk.CTkEntry(srow, textvariable=self.scan_interval_var, width=50, height=28,
                     fg_color=C['bg_input'], font=F['mono'],
                     text_color=C['text_main']).pack(side="left", padx=5)
        ctk.CTkLabel(srow, text="分鐘", font=F['body'],
                     text_color=C['text_sub']).pack(side="left")
        self.last_scan_label = ctk.CTkLabel(srow, text="上次掃描: --",
                                             font=F['small'], text_color=C['text_dim'])
        self.last_scan_label.pack(side="right")

        # ── 市場概況 ──
        self._section(s, "市場概況", "◉")
        mc = self._card(s)
        mi = ctk.CTkFrame(mc, fg_color="transparent")
        mi.pack(fill="x", padx=14, pady=10)

        self.market_info_labels: Dict[str, ctk.CTkLabel] = {}
        row1 = ctk.CTkFrame(mi, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        for key, label, default in [
            ("regime", "市場狀態", "--"),
            ("btc_trend", "BTC 趨勢", "--"),
            ("avg_adx", "平均 ADX", "--"),
        ]:
            pill = ctk.CTkFrame(row1, fg_color=C['bg_input'], corner_radius=6)
            pill.pack(side="left", padx=(0, 8), pady=2)
            ctk.CTkLabel(pill, text=f"{label}:", font=F['small'],
                         text_color=C['text_sub']).pack(side="left", padx=(10, 4), pady=7)
            lbl = ctk.CTkLabel(pill, text=default, font=F['body_bold'],
                               text_color=C['primary'])
            lbl.pack(side="left", padx=(0, 10), pady=7)
            self.market_info_labels[key] = lbl

        row2 = ctk.CTkFrame(mi, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        self.market_info_labels['sentiment'] = ctk.CTkLabel(
            row2, text="多空比例: -- 多 / -- 空",
            font=F['body_bold'], text_color=C['text_main'])
        self.market_info_labels['sentiment'].pack(side="left")

        # ── 掃描結果 ──
        self._section(s, "掃描結果（Top 10）", "◉")
        self.results_frame = ctk.CTkFrame(s, fg_color=C['bg_card'], corner_radius=8,
                                           border_width=1, border_color=C['border'])
        self.results_frame.pack(fill="x", padx=2, pady=4)

        # 表頭
        hdr = ctk.CTkFrame(self.results_frame, fg_color=C['bg_input'], corner_radius=6)
        hdr.pack(fill="x", padx=5, pady=(5, 2))
        for text, w in [("#", 30), ("標的", 95), ("方向", 50), ("評分", 55),
                        ("入場價", 88), ("止損價", 88), ("R/R", 45), ("量能", 65)]:
            ctk.CTkLabel(hdr, text=text, font=F['small_bold'],
                         text_color=C['text_dim'], width=w).pack(side="left", padx=2, pady=5)

        self.results_list_frame = ctk.CTkFrame(self.results_frame, fg_color="transparent")
        self.results_list_frame.pack(fill="x", padx=5, pady=5)
        self.no_results_label = ctk.CTkLabel(self.results_list_frame,
            text="尚未執行掃描，請點擊「立即掃描」",
            font=F['body'], text_color=C['text_dim'])
        self.no_results_label.pack(pady=18)

        # ── 預警 ──
        self._section(s, "預警信號（Pre-2B）", "◉")
        self.pre_signals_frame = ctk.CTkFrame(s, fg_color=C['bg_card'], corner_radius=8,
                                               border_width=1, border_color=C['border'])
        self.pre_signals_frame.pack(fill="x", padx=2, pady=4)
        self.pre_signals_list = ctk.CTkFrame(self.pre_signals_frame, fg_color="transparent")
        self.pre_signals_list.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(self.pre_signals_list, text="尚無預警信號",
                     font=F['body'], text_color=C['text_dim']).pack(pady=10)

        # ── 底部選項 ──
        bot = ctk.CTkFrame(s, fg_color="transparent")
        bot.pack(fill="x", padx=2, pady=14)
        self.use_scanner_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bot, text="使用掃描結果作為交易標的", variable=self.use_scanner_var,
                        font=F['body'], text_color=C['text_main'],
                        fg_color=C['primary'], hover_color=C['primary_hover']
                        ).pack(side="left")
        ctk.CTkButton(bot, text="匯出結果", font=F['body'],
                      fg_color=C['bg_input'], text_color=C['text_main'],
                      hover_color=C['bg_hover'], height=30, width=90,
                      command=self._export_scan).pack(side="right")

        if not SCANNER_AVAILABLE:
            self.scan_btn.configure(state="disabled")
            self.log_message("Scanner 模組未載入，請確保 scanner/ 資料夾存在", "警告")

    # ── Scanner 操作 ──

    def _start_scan(self):
        if not SCANNER_AVAILABLE or self.is_scanning:
            return
        self.is_scanning = True
        self.scan_btn.configure(state="disabled")
        self.stop_scan_btn.configure(state="normal")
        self.log_message("開始市場掃描...", "資訊")

        def _run():
            try:
                if self.scanner is None:
                    self.scanner = MarketScanner()
                results, summary = self.scanner.scan()
                self.after(0, lambda: self._display_scan(results, summary))
            except Exception as e:
                self.after(0, lambda: self.log_message(f"掃描錯誤: {e}", "錯誤"))
            finally:
                self.after(0, self._on_scan_done)

        threading.Thread(target=_run, daemon=True).start()

    def _stop_scan(self):
        self.is_scanning = False
        self.auto_scan_enabled = False
        self.auto_scan_var.set(False)
        self.scan_btn.configure(state="normal")
        self.stop_scan_btn.configure(state="disabled")
        self.log_message("掃描已停止", "警告")

    def _toggle_auto_scan(self):
        if self.auto_scan_var.get():
            self.auto_scan_enabled = True
            self.log_message(f"自動掃描已啟用，間隔 {self.scan_interval_var.get()} 分鐘")
            self._auto_scan_loop()
        else:
            self.auto_scan_enabled = False
            self.log_message("自動掃描已停用")

    def _auto_scan_loop(self):
        if not self.auto_scan_enabled:
            return
        if not self.is_scanning:
            self._start_scan()
        self.after(self.scan_interval_var.get() * 60_000, self._auto_scan_loop)

    def _on_scan_done(self):
        self.is_scanning = False
        if not self.auto_scan_enabled:
            self.scan_btn.configure(state="normal")
            self.stop_scan_btn.configure(state="disabled")
        self.last_scan_time = datetime.now()
        self.last_scan_label.configure(text=f"上次掃描: {self.last_scan_time:%H:%M:%S}")

    def _display_scan(self, results: list, summary):
        self.scan_results = results
        if 'scanned' in self.status_cards:
            self.status_cards['scanned'].configure(text=f"{summary.total_scanned} 個")

        self.market_info_labels['regime'].configure(text=summary.market_regime)
        self.market_info_labels['avg_adx'].configure(text=f"{summary.avg_adx:.1f}")

        btc_lbl = self.market_info_labels['btc_trend']
        btc_lbl.configure(text=summary.btc_trend)
        btc_colors = {"BULLISH": C['success'], "BEARISH": C['danger']}
        btc_lbl.configure(text_color=btc_colors.get(summary.btc_trend, C['text_main']))

        self.market_info_labels['sentiment'].configure(
            text=f"多空比例:  {summary.bullish_count} 多 /  {summary.bearish_count} 空")

        # 清空 + 渲染結果列表
        for w in self.results_list_frame.winfo_children():
            w.destroy()

        confirmed = [r for r in results if r.signal_type == "CONFIRMED_2B"]
        pre_sigs = [r for r in results if r.signal_type == "PRE_2B"]

        if confirmed:
            for r in confirmed[:10]:
                self._result_row(self.results_list_frame, r)
        else:
            ctk.CTkLabel(self.results_list_frame, text="未發現符合條件的 2B 信號",
                         font=F['body'], text_color=C['text_dim']).pack(pady=18)

        # 預警
        for w in self.pre_signals_list.winfo_children():
            w.destroy()
        if pre_sigs:
            for r in pre_sigs[:5]:
                self._pre_signal_row(self.pre_signals_list, r)
        else:
            ctk.CTkLabel(self.pre_signals_list, text="尚無預警信號",
                         font=F['body'], text_color=C['text_dim']).pack(pady=10)

        self.log_message(f"掃描完成：{len(confirmed)} 個確認信號，{len(pre_sigs)} 個預警", "成功")

    def _result_row(self, parent, r):
        """掃描結果行 — 交替底色"""
        idx = len(parent.winfo_children())
        bg = C['bg_input'] if idx % 2 == 0 else "transparent"

        row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=4, height=32)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        side_color = C['success'] if r.signal_side == "LONG" else C['danger']
        vol_color_map = {"explosive": C['warning'], "strong": C['success'],
                         "moderate": C['primary'], "weak": C['text_dim']}

        cells = [
            (f"#{r.rank}",              30,  C['text_sub']),
            (r.symbol,                   95,  C['text_main']),
            (r.signal_side,              50,  side_color),
            (f"{r.score:.1f}",           55,  C['warning']),
            (f"${r.entry_price:.4f}",    88,  C['text_main']),
            (f"${r.stop_loss:.4f}",      88,  C['danger']),
            (f"{r.risk_reward:.1f}",     45,  C['primary']),
            (r.volume_grade,             65,  vol_color_map.get(r.volume_grade, C['text_sub'])),
        ]
        for text, w, color in cells:
            ctk.CTkLabel(row, text=text, font=F['mono_sm'], text_color=color,
                         width=w).pack(side="left", padx=2)

    def _pre_signal_row(self, parent, r):
        row = ctk.CTkFrame(parent, fg_color=C['bg_input'], corner_radius=6)
        row.pack(fill="x", pady=2)
        emoji = "▲" if r.signal_side == "LONG" else "▼"
        color = C['warning'] if r.signal_side == "LONG" else C['danger']
        ctk.CTkLabel(row, text=f"{emoji}  {r.symbol}   {r.notes}",
                     font=F['body'], text_color=color).pack(side="left", padx=10, pady=7)

    def _export_scan(self):
        if not self.scan_results:
            messagebox.showwarning("警告", "沒有可匯出的掃描結果")
            return
        try:
            fn = f"scan_results_{datetime.now():%Y%m%d_%H%M%S}.json"
            data = {
                "export_time": datetime.now().isoformat(),
                "results": [
                    {"rank": r.rank, "symbol": r.symbol, "signal_side": r.signal_side,
                     "score": r.score, "entry_price": r.entry_price,
                     "stop_loss": r.stop_loss, "target": r.target,
                     "risk_reward": r.risk_reward, "volume_grade": r.volume_grade,
                     "notes": r.notes}
                    for r in self.scan_results
                ]
            }
            with open(fn, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.log_message(f"結果已匯出: {fn}", "成功")
            messagebox.showinfo("成功", f"已匯出至 {fn}")
        except Exception as e:
            self.log_message(f"匯出失敗: {e}", "錯誤")

    # ==================== 視窗關閉 ====================

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("確認離開", "交易機器人仍在運行中。\n是否要停止並離開？"):
                self.stop_bot()
            else:
                return
        self.destroy()


if __name__ == "__main__":
    app = ModernTradingBotGUI()
    app.mainloop()