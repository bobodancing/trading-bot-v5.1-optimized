# -*- coding: utf-8 -*-
"""
交易機器人 v5.1 - 現代化 GUI 控制面板 (中文版)
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import json
import subprocess
import threading
import os
import sys
from datetime import datetime
from typing import Dict, Any

# 設定 CustomTkinter 全局主題
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

class ModernTradingBotGUI(ctk.CTk):
    """交易機器人 v5.1 現代化視覺控制面板 (中文版)"""

    # 顏色方案 (FinTech Dark Theme)
    COLORS = {
        'bg_root': '#0B0E14',          # 最底層背景 (Deep Midnight)
        'bg_panel': '#151922',         # 面板背景
        'bg_card': '#1E232F',          # 卡片背景
        'bg_input': '#2A303C',         # 輸入框背景

        'primary': '#00B4D8',          # 主色調 (Neon Blue)
        'primary_hover': '#48CAE4',    # 主色漸變

        'success': '#00E676',          # 成功 (Bright Green)
        'success_bg': '#00331A',       # 成功背景

        'warning': '#FFAB00',          # 警告 (Amber)
        'danger': '#FF3D00',           # 危險 (Deep Orange)
        'danger_hover': '#FF6E40',

        'text_main': '#ECEFF4',        # 主文字
        'text_sub': '#94A3B8',         # 次要文字
        'text_dim': '#64748B',         # 最淡文字

        'border': '#2E3440',           # 邊框
        'border_focus': '#00B4D8',     # 焦點邊框
    }

    # 字體設定
    FONTS = {
        'h1': ("Microsoft JhengHei UI", 26, "bold"),
        'h2': ("Microsoft JhengHei UI", 18, "bold"),
        'h3': ("Microsoft JhengHei UI", 15, "bold"),
        'body': ("Microsoft JhengHei UI", 12),
        'body_bold': ("Microsoft JhengHei UI", 12, "bold"),
        'mono': ("Consolas", 12),
        'mono_lg': ("Consolas", 20, "bold"),
    }

    def __init__(self):
        super().__init__()

        # 視窗設定
        self.title("交易機器人 v5.1 專業版")
        self.geometry("1480x950")
        self.minsize(1280, 850)
        self.configure(fg_color=self.COLORS['bg_root'])

        # 配置文件
        self.config_file = "bot_config.json"
        self.default_config = self.get_default_config()

        # 機器人狀態
        self.bot_process = None
        self.is_running = False

        # 建立 UI
        self.create_ui()
        self.load_config()

        # 綁定關閉事件
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def get_default_config(self) -> Dict[str, Any]:
        """預設配置"""
        return {
            "api_key": "", "api_secret": "", "exchange": "binance", "sandbox_mode": True,
            "trading_mode": "future", "trading_direction": "both", "leverage": 5, "use_hard_stop_loss": True,
            "telegram_enabled": False, "telegram_bot_token": "", "telegram_chat_id": "",
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            "risk_per_trade": 0.02, "max_total_risk": 0.06, "max_positions_per_group": 3,
            "lookback_period": 20, "volume_ma_period": 20, "atr_period": 14, "atr_multiplier": 1.5,
            "enable_market_filter": True, "adx_threshold": 15, "atr_spike_multiplier": 2.0, "ema_entanglement_threshold": 0.02,
            "enable_volume_grading": True, "vol_explosive_threshold": 2.5, "vol_strong_threshold": 1.5,
            "vol_moderate_threshold": 1.0, "vol_minimum_threshold": 0.7, "accept_weak_signals": True,
            "enable_mtf_confirmation": True, "enable_dynamic_thresholds": True, "enable_tiered_entry": True,
            "enable_ema_pullback": True, "enable_volume_breakout": True,
            "tier_a_position_mult": 1.0, "tier_b_position_mult": 0.7, "tier_c_position_mult": 0.5,
            "enable_structure_break_exit": False, "check_interval": 60,
        }

    def create_ui(self):
        """建立使用者介面"""
        # 主體局 Grid 設定
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 1. 頂部標題列
        self.create_header()

        # 2. 狀態儀表板 (HUD)
        self.create_status_panel()

        # 3. 內容區 (分割視窗)
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=20, pady=(10, 20))

        # 左側：設置面板 (使用 Tabview)
        self.left_panel = ctk.CTkFrame(content_frame, fg_color="transparent", width=550)
        self.left_panel.pack(side="left", fill="both", padx=(0, 15))
        self.left_panel.pack_propagate(False)

        # 右側：日誌終端機
        self.right_panel = ctk.CTkFrame(content_frame, fg_color="transparent")
        self.right_panel.pack(side="right", fill="both", expand=True)

        self.create_config_tabs()
        self.create_log_panel()

    def create_header(self):
        """建立頂部標題列"""
        header = ctk.CTkFrame(
            self,
            fg_color="transparent",
            height=80
        )
        header.pack(fill="x", padx=25, pady=(15, 5))

        # Logo 區域
        logo_frame = ctk.CTkFrame(header, fg_color="transparent")
        logo_frame.pack(side="left")

        title = ctk.CTkLabel(
            logo_frame,
            text="交易機器人",
            font=ctk.CTkFont(family="Microsoft JhengHei UI", size=28, weight="bold"),
            text_color=self.COLORS['text_main']
        )
        title.pack(side="left", anchor="s")

        ver_tag = ctk.CTkLabel(
            logo_frame,
            text=" v5.1 專業版 ",
            font=ctk.CTkFont(family="Microsoft JhengHei UI", size=12, weight="bold"),
            text_color=self.COLORS['bg_root'],
            fg_color=self.COLORS['primary'],
            corner_radius=4,
            height=20
        )
        ver_tag.pack(side="left", padx=(8, 0), pady=(8, 0))

        subtitle = ctk.CTkLabel(
            logo_frame,
            text="智能演算法交易系統",
            font=ctk.CTkFont(family="Microsoft JhengHei UI", size=10, weight="bold"),
            text_color=self.COLORS['text_dim'],
            justify="left"
        )
        subtitle.pack(side="bottom", anchor="w", pady=(0, 5))

        # 右側控制區
        ctrl_frame = ctk.CTkFrame(header, fg_color="transparent")
        ctrl_frame.pack(side="right")

        # 狀態燈
        self.status_indicator = ctk.CTkFrame(
            ctrl_frame,
            fg_color=self.COLORS['bg_card'],
            border_width=1,
            border_color=self.COLORS['border'],
            corner_radius=20,
            height=40
        )
        self.status_indicator.pack(side="left", padx=(0, 20))

        self.status_dot = ctk.CTkLabel(self.status_indicator, text="●", font=("Arial", 18), text_color=self.COLORS['text_dim'])
        self.status_dot.pack(side="left", padx=(15, 5), pady=8)

        self.status_text = ctk.CTkLabel(self.status_indicator, text="系統待命", font=self.FONTS['body_bold'], text_color=self.COLORS['text_dim'])
        self.status_text.pack(side="left", padx=(0, 15), pady=8)

        # 按鈕群組
        self.start_btn = ctk.CTkButton(
            ctrl_frame,
            text="▶ 啟動系統",
            font=self.FONTS['body_bold'],
            fg_color=self.COLORS['success'],
            hover_color="#00C853",
            text_color=self.COLORS['bg_root'],
            height=42,
            width=140,
            corner_radius=8,
            command=self.start_bot
        )
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = ctk.CTkButton(
            ctrl_frame,
            text="■ 停止系統",
            font=self.FONTS['body_bold'],
            fg_color=self.COLORS['bg_input'],
            text_color=self.COLORS['danger'],
            hover_color=self.COLORS['bg_card'],
            border_width=1,
            border_color=self.COLORS['danger'],
            height=42,
            width=140,
            corner_radius=8,
            command=self.stop_bot,
            state="disabled"
        )
        self.stop_btn.pack(side="left", padx=5)

    def create_status_panel(self):
        """建立狀態儀表板 (HUD Style)"""
        hud_frame = ctk.CTkFrame(self, fg_color=self.COLORS['bg_panel'], corner_radius=12, height=100)
        hud_frame.pack(fill="x", padx=20, pady=(10, 10))
        hud_frame.pack_propagate(False)

        # 內部容器
        inner = ctk.CTkFrame(hud_frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=15)

        # 左側：數據卡片
        data_area = ctk.CTkFrame(inner, fg_color="transparent")
        data_area.pack(side="left", fill="y", pady=15)

        self.status_cards = {}
        metrics = [
            ("balance", "帳戶餘額", "-- USDT", self.COLORS['primary']),
            ("positions", "活躍持倉", "0 / 3", self.COLORS['text_main']),
            ("today_pnl", "24小時盈虧", "-- USDT", self.COLORS['success']),
            ("signals", "信號產生", "0", self.COLORS['warning']),
        ]

        for key, title, default, color in metrics:
            card = ctk.CTkFrame(data_area, fg_color=self.COLORS['bg_card'], corner_radius=8, width=180)
            card.pack(side="left", fill="y", padx=(0, 12))
            card.pack_propagate(False)

            # 標題
            ctk.CTkLabel(card, text=title, font=("Microsoft JhengHei UI", 9, "bold"), text_color=self.COLORS['text_sub']).pack(anchor="w", padx=12, pady=(10, 0))
            # 數值
            val = ctk.CTkLabel(card, text=default, font=self.FONTS['mono_lg'], text_color=color)
            val.pack(anchor="w", padx=10, pady=(0, 5))
            self.status_cards[key] = val

        # 右側：快捷操作
        action_area = ctk.CTkFrame(inner, fg_color="transparent")
        action_area.pack(side="right", fill="y", pady=25)

        actions = [
            ("儲存設定", self.COLORS['primary'], self.save_config),
            ("重新載入", self.COLORS['bg_input'], self.load_config),
            ("清除日誌", self.COLORS['bg_input'], self.clear_log),
        ]

        for text, color, cmd in actions:
            is_primary = color == self.COLORS['primary']
            ctk.CTkButton(
                action_area,
                text=text,
                font=self.FONTS['body_bold'],
                fg_color=color,
                text_color=self.COLORS['bg_root'] if is_primary else self.COLORS['text_main'],
                hover_color=self.COLORS['primary_hover'] if is_primary else self.COLORS['border'],
                height=32,
                corner_radius=6,
                command=cmd
            ).pack(side="left", padx=5)

    def create_config_tabs(self):
        """建立配置分頁 (Tabview Styling)"""
        self.tabview = ctk.CTkTabview(
            self.left_panel,
            fg_color=self.COLORS['bg_panel'],
            segmented_button_fg_color=self.COLORS['bg_root'],
            segmented_button_selected_color=self.COLORS['bg_input'],
            segmented_button_selected_hover_color=self.COLORS['bg_input'],
            segmented_button_unselected_color=self.COLORS['bg_root'],
            segmented_button_unselected_hover_color=self.COLORS['bg_card'],
            text_color=self.COLORS['text_sub'],
            corner_radius=12,
            width=550
        )
        self.tabview.pack(fill="both", expand=True)

        # 增加分頁
        tabs = ["API 連線", "交易設定", "風險管理", "市場過濾", "量能分級", "v5.1 進階"]
        for t in tabs:
            self.tabview.add(t)

        # 映射到各分頁的創建函數
        self.create_api_tab(self.tabview.tab("API 連線"))
        self.create_trading_tab(self.tabview.tab("交易設定"))
        self.create_risk_tab(self.tabview.tab("風險管理"))
        self.create_filter_tab(self.tabview.tab("市場過濾"))
        self.create_volume_tab(self.tabview.tab("量能分級"))
        self.create_v51_tab(self.tabview.tab("v5.1 進階"))

    def create_section_header(self, parent, text, icon="▹"):
        """建立區塊標題"""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=(20, 10))

        ctk.CTkLabel(frame, text=icon, font=("Segoe UI Emoji", 14)).pack(side="left", padx=(5, 5))
        ctk.CTkLabel(frame, text=text, font=self.FONTS['h3'], text_color=self.COLORS['primary']).pack(side="left")

        # 分隔線
        ctk.CTkFrame(frame, height=1, fg_color=self.COLORS['border']).pack(side="left", fill="x", expand=True, padx=10)

    def create_input_card(self, parent):
        """建立輸入卡片容器"""
        card = ctk.CTkFrame(parent, fg_color=self.COLORS['bg_card'], corner_radius=8, border_width=1, border_color=self.COLORS['border'])
        card.pack(fill="x", pady=2)
        return card

    def create_modern_row(self, parent, label, var, input_type="entry", options=None, from_=0, to=100, tooltip=None, show=None):
        """現代化輸入列"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=15, pady=8)

        # 標籤
        ctk.CTkLabel(
            row, text=label, font=self.FONTS['body'], text_color=self.COLORS['text_main'],
            anchor="w", width=140
        ).pack(side="left")

        # 控制項容器
        ctrl_frame = ctk.CTkFrame(row, fg_color="transparent")
        ctrl_frame.pack(side="right", fill="x", expand=True)

        widget = None
        if input_type == "entry":
            widget = ctk.CTkEntry(
                ctrl_frame, textvariable=var, height=32, corner_radius=6,
                fg_color=self.COLORS['bg_input'], border_color=self.COLORS['border'],
                font=self.FONTS['mono'], show=show
            )
        elif input_type == "switch":
            widget = ctk.CTkSwitch(
                ctrl_frame, variable=var, text="",
                progress_color=self.COLORS['primary'], button_color=self.COLORS['text_main'],
                button_hover_color=self.COLORS['text_main'], height=24, width=44
            )
            widget.pack(side="right")
            return

        elif input_type == "slider":
            slider_frame = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
            slider_frame.pack(side="right", fill="x", expand=True)

            value_label = ctk.CTkLabel(slider_frame, text=str(var.get()), font=self.FONTS['mono'],
                                       text_color=self.COLORS['primary'], width=50)
            value_label.pack(side="right", padx=(10, 0))

            def update_label(val):
                if isinstance(from_, int) and isinstance(to, int):
                    value_label.configure(text=str(int(float(val))))
                    var.set(int(float(val)))
                else:
                    value_label.configure(text=f"{float(val):.2f}")
                    var.set(float(val))

            widget = ctk.CTkSlider(
                slider_frame, from_=from_, to=to, variable=var,
                progress_color=self.COLORS['primary'], button_color=self.COLORS['text_main'],
                button_hover_color=self.COLORS['primary_hover'], height=16,
                command=update_label
            )
            widget.pack(side="right", fill="x", expand=True)
            return

        elif input_type == "combo":
            widget = ctk.CTkComboBox(
                ctrl_frame, variable=var, values=options or [], height=32, corner_radius=6,
                fg_color=self.COLORS['bg_input'], border_color=self.COLORS['border'],
                button_color=self.COLORS['bg_card'], button_hover_color=self.COLORS['border'],
                dropdown_fg_color=self.COLORS['bg_card'], dropdown_hover_color=self.COLORS['bg_input'],
                font=self.FONTS['body']
            )

        if widget:
            widget.pack(side="right", fill="x", expand=True)

    def create_api_tab(self, parent):
        """API 連線設定分頁"""
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # API 設定
        self.create_section_header(scroll, "交易所 API 設定", "🔑")
        card = self.create_input_card(scroll)

        self.api_key_var = ctk.StringVar()
        self.api_secret_var = ctk.StringVar()
        self.exchange_var = ctk.StringVar(value="binance")
        self.sandbox_var = ctk.BooleanVar(value=True)

        self.create_modern_row(card, "API 金鑰", self.api_key_var, "entry")
        self.create_modern_row(card, "API 密鑰", self.api_secret_var, "entry", show="•")
        self.create_modern_row(card, "交易所", self.exchange_var, "combo",
                              options=["binance", "bybit", "okx", "bitget"])
        self.create_modern_row(card, "測試網模式", self.sandbox_var, "switch")

        # Telegram 設定
        self.create_section_header(scroll, "Telegram 通知設定", "📱")
        card2 = self.create_input_card(scroll)

        self.telegram_enabled_var = ctk.BooleanVar(value=False)
        self.telegram_token_var = ctk.StringVar()
        self.telegram_chat_var = ctk.StringVar()

        self.create_modern_row(card2, "啟用 Telegram", self.telegram_enabled_var, "switch")
        self.create_modern_row(card2, "Bot Token", self.telegram_token_var, "entry")
        self.create_modern_row(card2, "Chat ID", self.telegram_chat_var, "entry")

    def create_trading_tab(self, parent):
        """交易設定分頁"""
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # 交易模式
        self.create_section_header(scroll, "交易模式設定", "📊")
        card = self.create_input_card(scroll)

        self.trading_mode_var = ctk.StringVar(value="future")
        self.trading_direction_var = ctk.StringVar(value="both")
        self.leverage_var = ctk.IntVar(value=5)
        self.hard_stop_var = ctk.BooleanVar(value=True)

        self.create_modern_row(card, "交易模式", self.trading_mode_var, "combo",
                              options=["future", "spot"])
        self.create_modern_row(card, "交易方向", self.trading_direction_var, "combo",
                              options=["both", "long_only", "short_only"])
        self.create_modern_row(card, "槓桿倍數", self.leverage_var, "slider", from_=1, to=20)
        self.create_modern_row(card, "硬止損", self.hard_stop_var, "switch")

        # 交易對
        self.create_section_header(scroll, "交易標的設定", "💹")
        card2 = self.create_input_card(scroll)

        self.symbols_var = ctk.StringVar(value="BTC/USDT, ETH/USDT, SOL/USDT")

        symbols_frame = ctk.CTkFrame(card2, fg_color="transparent")
        symbols_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(symbols_frame, text="交易對 (以逗號分隔)",
                    font=self.FONTS['body'], text_color=self.COLORS['text_sub']).pack(anchor="w")

        self.symbols_entry = ctk.CTkTextbox(
            symbols_frame, height=60, corner_radius=6,
            fg_color=self.COLORS['bg_input'], border_color=self.COLORS['border'],
            font=self.FONTS['mono'], border_width=1
        )
        self.symbols_entry.pack(fill="x", pady=(5, 0))
        self.symbols_entry.insert("1.0", "BTC/USDT, ETH/USDT, SOL/USDT")

        # 檢查間隔
        self.create_section_header(scroll, "時間設定", "⏱️")
        card3 = self.create_input_card(scroll)

        self.check_interval_var = ctk.IntVar(value=60)
        self.create_modern_row(card3, "檢查間隔 (秒)", self.check_interval_var, "slider", from_=10, to=300)

    def create_risk_tab(self, parent):
        """風險管理分頁"""
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        self.create_section_header(scroll, "倉位管理", "⚖️")
        card = self.create_input_card(scroll)

        self.risk_per_trade_var = ctk.DoubleVar(value=0.02)
        self.max_total_risk_var = ctk.DoubleVar(value=0.06)
        self.max_positions_var = ctk.IntVar(value=3)

        self.create_modern_row(card, "單筆風險", self.risk_per_trade_var, "slider", from_=0.01, to=0.10)
        self.create_modern_row(card, "最大總風險", self.max_total_risk_var, "slider", from_=0.01, to=0.20)
        self.create_modern_row(card, "最大持倉數", self.max_positions_var, "slider", from_=1, to=10)

        # 技術指標
        self.create_section_header(scroll, "技術指標參數", "📈")
        card2 = self.create_input_card(scroll)

        self.lookback_var = ctk.IntVar(value=20)
        self.volume_ma_var = ctk.IntVar(value=20)
        self.atr_period_var = ctk.IntVar(value=14)
        self.atr_mult_var = ctk.DoubleVar(value=1.5)

        self.create_modern_row(card2, "回溯週期", self.lookback_var, "slider", from_=5, to=50)
        self.create_modern_row(card2, "成交量均線週期", self.volume_ma_var, "slider", from_=5, to=50)
        self.create_modern_row(card2, "ATR 週期", self.atr_period_var, "slider", from_=5, to=30)
        self.create_modern_row(card2, "ATR 乘數", self.atr_mult_var, "slider", from_=0.5, to=4.0)

    def create_filter_tab(self, parent):
        """市場過濾器分頁"""
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        self.create_section_header(scroll, "市場過濾條件", "🎯")
        card = self.create_input_card(scroll)

        self.enable_market_filter_var = ctk.BooleanVar(value=True)
        self.adx_threshold_var = ctk.IntVar(value=15)
        self.atr_spike_var = ctk.DoubleVar(value=2.0)
        self.ema_entangle_var = ctk.DoubleVar(value=0.02)

        self.create_modern_row(card, "啟用市場過濾", self.enable_market_filter_var, "switch")
        self.create_modern_row(card, "ADX 閾值", self.adx_threshold_var, "slider", from_=5, to=40)
        self.create_modern_row(card, "ATR 突出乘數", self.atr_spike_var, "slider", from_=1.0, to=5.0)
        self.create_modern_row(card, "均線糾纏閾值", self.ema_entangle_var, "slider", from_=0.01, to=0.10)

    def create_volume_tab(self, parent):
        """成交量分級分頁"""
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        self.create_section_header(scroll, "量能分級系統", "📊")
        card = self.create_input_card(scroll)

        self.enable_volume_grading_var = ctk.BooleanVar(value=True)
        self.vol_explosive_var = ctk.DoubleVar(value=2.5)
        self.vol_strong_var = ctk.DoubleVar(value=1.5)
        self.vol_moderate_var = ctk.DoubleVar(value=1.0)
        self.vol_minimum_var = ctk.DoubleVar(value=0.7)
        self.accept_weak_var = ctk.BooleanVar(value=True)

        self.create_modern_row(card, "啟用量能分級", self.enable_volume_grading_var, "switch")
        self.create_modern_row(card, "爆發量閾值", self.vol_explosive_var, "slider", from_=1.5, to=5.0)
        self.create_modern_row(card, "強勢量閾值", self.vol_strong_var, "slider", from_=1.0, to=3.0)
        self.create_modern_row(card, "中等量閾值", self.vol_moderate_var, "slider", from_=0.5, to=2.0)
        self.create_modern_row(card, "最低量閾值", self.vol_minimum_var, "slider", from_=0.3, to=1.5)
        self.create_modern_row(card, "接受弱勢信號", self.accept_weak_var, "switch")

    def create_v51_tab(self, parent):
        """v5.1 進階功能分頁"""
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        self.create_section_header(scroll, "v5.1 進階功能", "⚡")
        card = self.create_input_card(scroll)

        self.enable_mtf_var = ctk.BooleanVar(value=True)
        self.enable_dynamic_var = ctk.BooleanVar(value=True)
        self.enable_tiered_var = ctk.BooleanVar(value=True)
        self.enable_pullback_var = ctk.BooleanVar(value=True)
        self.enable_breakout_var = ctk.BooleanVar(value=True)
        self.enable_structure_var = ctk.BooleanVar(value=False)

        self.create_modern_row(card, "多時間框架確認", self.enable_mtf_var, "switch")
        self.create_modern_row(card, "動態閾值調整", self.enable_dynamic_var, "switch")
        self.create_modern_row(card, "分級入場", self.enable_tiered_var, "switch")
        self.create_modern_row(card, "EMA 回撤策略", self.enable_pullback_var, "switch")
        self.create_modern_row(card, "量能突破策略", self.enable_breakout_var, "switch")
        self.create_modern_row(card, "結構破壞出場", self.enable_structure_var, "switch")

        # 分層倉位
        self.create_section_header(scroll, "分層倉位設定", "🎯")
        card2 = self.create_input_card(scroll)

        self.tier_a_var = ctk.DoubleVar(value=1.0)
        self.tier_b_var = ctk.DoubleVar(value=0.7)
        self.tier_c_var = ctk.DoubleVar(value=0.5)

        self.create_modern_row(card2, "A 級倉位乘數", self.tier_a_var, "slider", from_=0.5, to=1.5)
        self.create_modern_row(card2, "B 級倉位乘數", self.tier_b_var, "slider", from_=0.3, to=1.0)
        self.create_modern_row(card2, "C 級倉位乘數", self.tier_c_var, "slider", from_=0.2, to=0.8)

    def create_log_panel(self):
        """建立日誌面板"""
        log_frame = ctk.CTkFrame(self.right_panel, fg_color=self.COLORS['bg_panel'], corner_radius=12)
        log_frame.pack(fill="both", expand=True)

        # 標題列
        header = ctk.CTkFrame(log_frame, fg_color="transparent", height=40)
        header.pack(fill="x", padx=15, pady=(10, 0))

        ctk.CTkLabel(header, text="📋 系統日誌", font=self.FONTS['h3'],
                    text_color=self.COLORS['text_main']).pack(side="left")

        # 日誌內容
        self.log_text = ctk.CTkTextbox(
            log_frame,
            fg_color=self.COLORS['bg_root'],
            font=self.FONTS['mono'],
            corner_radius=8,
            border_width=1,
            border_color=self.COLORS['border']
        )
        self.log_text.pack(fill="both", expand=True, padx=15, pady=15)
        self.log_text.configure(state="disabled")

        self.log_message("系統已初始化，準備就緒。")

    def log_message(self, message, level="資訊"):
        """添加日誌訊息"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] [{level}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        """清除日誌"""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log_message("日誌已清除。")

    def load_config(self):
        """載入配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                self.log_message("已從文件載入配置。", "成功")
            else:
                config = self.default_config
                self.log_message("使用預設配置。", "資訊")

            # 設定變數值
            self.api_key_var.set(config.get("api_key", ""))
            self.api_secret_var.set(config.get("api_secret", ""))
            self.exchange_var.set(config.get("exchange", "binance"))
            self.sandbox_var.set(config.get("sandbox_mode", True))

            self.trading_mode_var.set(config.get("trading_mode", "future"))
            self.trading_direction_var.set(config.get("trading_direction", "both"))
            self.leverage_var.set(config.get("leverage", 5))
            self.hard_stop_var.set(config.get("use_hard_stop_loss", True))

            self.telegram_enabled_var.set(config.get("telegram_enabled", False))
            self.telegram_token_var.set(config.get("telegram_bot_token", ""))
            self.telegram_chat_var.set(config.get("telegram_chat_id", ""))

            symbols = config.get("symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
            self.symbols_entry.delete("1.0", "end")
            self.symbols_entry.insert("1.0", ", ".join(symbols))

            self.check_interval_var.set(config.get("check_interval", 60))

            self.risk_per_trade_var.set(config.get("risk_per_trade", 0.02))
            self.max_total_risk_var.set(config.get("max_total_risk", 0.06))
            self.max_positions_var.set(config.get("max_positions_per_group", 3))

            self.lookback_var.set(config.get("lookback_period", 20))
            self.volume_ma_var.set(config.get("volume_ma_period", 20))
            self.atr_period_var.set(config.get("atr_period", 14))
            self.atr_mult_var.set(config.get("atr_multiplier", 1.5))

            self.enable_market_filter_var.set(config.get("enable_market_filter", True))
            self.adx_threshold_var.set(config.get("adx_threshold", 15))
            self.atr_spike_var.set(config.get("atr_spike_multiplier", 2.0))
            self.ema_entangle_var.set(config.get("ema_entanglement_threshold", 0.02))

            self.enable_volume_grading_var.set(config.get("enable_volume_grading", True))
            self.vol_explosive_var.set(config.get("vol_explosive_threshold", 2.5))
            self.vol_strong_var.set(config.get("vol_strong_threshold", 1.5))
            self.vol_moderate_var.set(config.get("vol_moderate_threshold", 1.0))
            self.vol_minimum_var.set(config.get("vol_minimum_threshold", 0.7))
            self.accept_weak_var.set(config.get("accept_weak_signals", True))

            self.enable_mtf_var.set(config.get("enable_mtf_confirmation", True))
            self.enable_dynamic_var.set(config.get("enable_dynamic_thresholds", True))
            self.enable_tiered_var.set(config.get("enable_tiered_entry", True))
            self.enable_pullback_var.set(config.get("enable_ema_pullback", True))
            self.enable_breakout_var.set(config.get("enable_volume_breakout", True))
            self.enable_structure_var.set(config.get("enable_structure_break_exit", False))

            self.tier_a_var.set(config.get("tier_a_position_mult", 1.0))
            self.tier_b_var.set(config.get("tier_b_position_mult", 0.7))
            self.tier_c_var.set(config.get("tier_c_position_mult", 0.5))

        except Exception as e:
            self.log_message(f"載入配置時發生錯誤: {e}", "錯誤")

    def save_config(self):
        """儲存配置"""
        try:
            symbols_text = self.symbols_entry.get("1.0", "end").strip()
            symbols = [s.strip() for s in symbols_text.split(",") if s.strip()]

            config = {
                "api_key": self.api_key_var.get(),
                "api_secret": self.api_secret_var.get(),
                "exchange": self.exchange_var.get(),
                "sandbox_mode": self.sandbox_var.get(),

                "trading_mode": self.trading_mode_var.get(),
                "trading_direction": self.trading_direction_var.get(),
                "leverage": self.leverage_var.get(),
                "use_hard_stop_loss": self.hard_stop_var.get(),

                "telegram_enabled": self.telegram_enabled_var.get(),
                "telegram_bot_token": self.telegram_token_var.get(),
                "telegram_chat_id": self.telegram_chat_var.get(),

                "symbols": symbols,
                "check_interval": self.check_interval_var.get(),

                "risk_per_trade": self.risk_per_trade_var.get(),
                "max_total_risk": self.max_total_risk_var.get(),
                "max_positions_per_group": self.max_positions_var.get(),

                "lookback_period": self.lookback_var.get(),
                "volume_ma_period": self.volume_ma_var.get(),
                "atr_period": self.atr_period_var.get(),
                "atr_multiplier": self.atr_mult_var.get(),

                "enable_market_filter": self.enable_market_filter_var.get(),
                "adx_threshold": self.adx_threshold_var.get(),
                "atr_spike_multiplier": self.atr_spike_var.get(),
                "ema_entanglement_threshold": self.ema_entangle_var.get(),

                "enable_volume_grading": self.enable_volume_grading_var.get(),
                "vol_explosive_threshold": self.vol_explosive_var.get(),
                "vol_strong_threshold": self.vol_strong_var.get(),
                "vol_moderate_threshold": self.vol_moderate_var.get(),
                "vol_minimum_threshold": self.vol_minimum_var.get(),
                "accept_weak_signals": self.accept_weak_var.get(),

                "enable_mtf_confirmation": self.enable_mtf_var.get(),
                "enable_dynamic_thresholds": self.enable_dynamic_var.get(),
                "enable_tiered_entry": self.enable_tiered_var.get(),
                "enable_ema_pullback": self.enable_pullback_var.get(),
                "enable_volume_breakout": self.enable_breakout_var.get(),
                "enable_structure_break_exit": self.enable_structure_var.get(),

                "tier_a_position_mult": self.tier_a_var.get(),
                "tier_b_position_mult": self.tier_b_var.get(),
                "tier_c_position_mult": self.tier_c_var.get(),
            }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)

            self.log_message("配置已成功儲存。", "成功")
            messagebox.showinfo("成功", "配置已儲存！")

        except Exception as e:
            self.log_message(f"儲存配置時發生錯誤: {e}", "錯誤")
            messagebox.showerror("錯誤", f"儲存配置失敗: {e}")

    def start_bot(self):
        """啟動機器人"""
        if self.is_running:
            return

        # 先儲存配置
        self.save_config()

        self.is_running = True
        self.update_status("運行中", self.COLORS['success'])

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        self.log_message("正在啟動交易機器人...", "資訊")

        # 在背景執行機器人
        def run_bot():
            try:
                bot_script = os.path.join(os.path.dirname(__file__), "trading_bot_v5.1_optimized.py")
                if os.path.exists(bot_script):
                    self.bot_process = subprocess.Popen(
                        [sys.executable, bot_script],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    for line in iter(self.bot_process.stdout.readline, ''):
                        if not self.is_running:
                            break
                        self.after(0, lambda l=line: self.log_message(l.strip()))

                    self.bot_process.wait()
                else:
                    self.after(0, lambda: self.log_message(f"找不到機器人腳本: {bot_script}", "錯誤"))

            except Exception as e:
                self.after(0, lambda: self.log_message(f"執行機器人時發生錯誤: {e}", "錯誤"))
            finally:
                self.after(0, self.on_bot_stopped)

        self.bot_thread = threading.Thread(target=run_bot, daemon=True)
        self.bot_thread.start()

    def stop_bot(self):
        """停止機器人"""
        if not self.is_running:
            return

        self.is_running = False

        if self.bot_process:
            self.bot_process.terminate()
            self.bot_process = None

        self.log_message("正在停止交易機器人...", "警告")
        self.on_bot_stopped()

    def on_bot_stopped(self):
        """機器人停止時的處理"""
        self.is_running = False
        self.update_status("已停止", self.COLORS['danger'])

        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

        self.log_message("交易機器人已停止。", "警告")

    def update_status(self, text, color):
        """更新狀態顯示"""
        self.status_dot.configure(text_color=color)
        self.status_text.configure(text=f"系統{text}", text_color=color)

    def on_closing(self):
        """關閉視窗時的處理"""
        if self.is_running:
            if messagebox.askyesno("確認離開", "交易機器人仍在運行中。是否要停止並離開？"):
                self.stop_bot()
            else:
                return

        self.destroy()


if __name__ == "__main__":
    app = ModernTradingBotGUI()
    app.mainloop()
