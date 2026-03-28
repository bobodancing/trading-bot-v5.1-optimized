# 🚀 Project Structure Map V3 (AI-Optimized)
依賴關係、類別屬性、內部呼叫，專為 AI Agent 快速定位設計。Docstring 只保留第一行。

## 🗺 架構總覽

> 雙 systemd 服務：trader.service（trader/bot.py）+ scanner.service（scanner/market_scanner.py）
> tradingStart.py 已廢棄（Bot/Scanner 分離後不再需要）

```
scanner/
└── market_scanner.py    ← 四層 Scanner（流動性→動能→形態→相關性）[scanner.service]

trader/                  ← [trader.service]
├── bot.py               ← TradingBotV6 主引擎（monitor loop + SIGTERM handler + TradeFilter）
├── positions.py         ← PositionManager（strategy_name 插件 + Stage 管理 + 出場委派）
├── signals.py           ← detect_2b_with_pivots（入場信號）
├── structure.py         ← StructureAnalysis（swing point / neckline / BOS 追蹤）
├── config.py            ← ConfigV6（交易參數 + SIGNAL_STRATEGY_MAP；secrets 另存 secrets.json）
├── persistence.py       ← PositionPersistence（atomic write）
├── infrastructure/
│   ├── api_client.py    ← BinanceFuturesClient（HMAC 簽章 + recvWindow + -1021 偵測）
│   ├── data_provider.py ← MarketDataProvider（retry + sandbox fallback）
│   ├── notifier.py      ← TelegramNotifier
│   └── performance_db.py← PerformanceDB（SQLite performance.db，平倉自動寫入）
├── indicators/
│   └── technical.py     ← TechnicalAnalysis, DynamicThresholdManager,
│                           MTFConfirmation, MarketFilter
├── risk/
│   └── manager.py       ← PrecisionHandler, RiskManager, SignalTierSystem
├── execution/
│   └── order_engine.py  ← OrderExecutionEngine（下單封裝）
└── strategies/          ← 策略插件層（Registry Pattern，新策略 register 即可）
    ├── base.py          ← Action enum + DecisionDict + TradingStrategy ABC + StrategyFactory
    ├── v6_pyramid.py    ← V6PyramidStrategy（結構追蹤 + profit_pullback + stage trigger）
    └── v53_sop.py       ← V53SopStrategy（1.0R/1.5R/2.0R SOP + state isolation）
```

---
## 📄 File: `map_generator_v3.py`
**Constants:** `PROJECT_ROOT, OUTPUT_FILE, IGNORE_DIRS, IGNORE_FILES, DEPRECATED_STUBS, STDLIB_MODULES, KEY_NON_PYTHON_FILES, ARCHITECTURE_OVERVIEW`
- Function: `_first_line(doc)` -> str — 只取 docstring 第一個非空行。
### Class: `EnhancedProjectMapper` (Inherits: ast.NodeVisitor)
    - **Properties:** `constants, current_class, file_path, imports, results`
  - Method: `__init__(self, file_path)`
  - Method: `_is_stdlib(self, name)` -> bool
  - Method: `visit_Import(self, node)` [Calls: _is_stdlib]
  - Method: `visit_ImportFrom(self, node)` [Calls: _is_stdlib]
  - Method: `visit_Assign(self, node)` [Calls: generic_visit]
  - Method: `visit_ClassDef(self, node)` [Calls: generic_visit]
  - Method: `visit_FunctionDef(self, node)`
- Function: `scan_project()`

---

## 📄 File: `vpn_watchdog.py`
**Constants:** `TARGET_HOST, TARGET_PORT, CHECK_INTERVAL, FAIL_THRESHOLD, VPN_COOLDOWN, VPN_REGIONS, _SCRIPT_DIR, _SECRETS_PATH`
- Function: `_load_telegram_creds()` -> tuple[str, str] — 從 secrets.json 讀取 Telegram 憑證，回傳 (token, chat_id)
- Function: `send_telegram(message)`
- Function: `check_connectivity()` -> bool — 用 TCP 握手測試是否能連到 Binance（不送 HTTP 請求，不消耗 API 權重）
- Function: `_try_switch_vpn()` — 切換到下一個 VPN 區域。全部輪完後發通知。
- Function: `main()`

---

## 📄 File: `scanner/market_scanner.py`
**Dependencies:** `ccxt, pandas, pandas_ta, numpy, requests, trader.structure.(StructureAnalysis), trader.infrastructure.data_provider.(MarketDataProvider)`
**Constants:** `SCANNER_AVAILABLE, SECTOR_MAPPING`
### Class: `ScannerConfig` — Scanner 配置
  - Method: `load_from_json(cls, config_file)` — 從 JSON 載入配置
### Class: `SignalSide` (Inherits: Enum)
### Class: `SignalType` (Inherits: Enum)
### Class: `StructureQuality` (Inherits: Enum)
### Class: `VolumeGrade` (Inherits: Enum)
### Class: `ScanResult` — 單個標的的掃描結果
### Class: `MarketSummary` — 市場概況
- Function: `get_sector(symbol)` -> str — 獲取標的所屬板塊
### Class: `MarketScanner` — 市場掃描器主類
    - **Properties:** `_data_provider, _init_exchange, btc_data, exchange, excluded, market_summary, results`
  - Method: `__init__(self, data_provider)` [Calls: _init_exchange]
  - Method: `_normalize_symbol(symbol)` -> str — 正規化符號：BTC/USDT:USDT → BTC/USDT
  - Method: `_init_exchange(self)` — 初始化交易所（Scanner 永遠使用正式網）
  - Method: `fetch_ohlcv(self, symbol, timeframe, limit)` -> pd.DataFrame — 獲取 K 線數據（委託 MarketDataProvider 統一處理重試邏輯）
  - Method: `calculate_indicators(self, df)` -> pd.DataFrame — 計算技術指標
  - Method: `layer1_liquidity_filter(self)` -> List[str] [Calls: _normalize_symbol, fetch_ohlcv] — Layer 1: 流動性過濾
  - Method: `layer2_momentum_filter(self, symbols)` -> List[Tuple[str, Dict]] [Calls: fetch_ohlcv, calculate_indicators, _calculate_relative_strength] — Layer 2: 動能篩選
  - Method: `_calculate_relative_strength(self, df)` -> float — 計算相對 BTC 的強度
  - Method: `layer3_pattern_matching(self, candidates)` -> List[ScanResult] [Calls: _detect_2b_signal] — Layer 3: 形態匹配
  - Method: `_check_confirmed_2b(current, swing_point, opposite_swing, atr, is_long)` -> Optional[Dict] — 檢查已確認的 2B 反轉信號。
  - Method: `_check_pre_2b(current, swing_point, opposite_swing, atr, is_long)` -> Optional[Dict] — 檢查預警信號（價格接近但尚未突破 swing point）。
  - Method: `_detect_2b_signal(self, df, symbol, indicators)` -> Optional[ScanResult] [Calls: _check_mtf_alignment, _calculate_score, _check_confirmed_2b, _check_pre_2b] — 檢測 2B 信號
  - Method: `_check_mtf_alignment(self, symbol, signal_side)` -> bool [Calls: fetch_ohlcv, calculate_indicators] — 檢查多時間框架對齊
  - Method: `_calculate_score(self)` -> float — 計算綜合評分
  - Method: `layer4_correlation_filter(self, results)` -> List[ScanResult] — Layer 4: 相關性過濾
  - Method: `scan(self)` -> Tuple[List[ScanResult], MarketSummary] [Calls: layer1_liquidity_filter, layer2_momentum_filter, layer3_pattern_matching, layer4_correlation_filter, _generate_market_summary, _output_results] — 執行完整掃描
  - Method: `_generate_market_summary(self)` -> MarketSummary — 生成市場摘要
  - Method: `_output_results(self)` [Calls: _output_json, _output_sqlite, _print_summary, _send_telegram] — 輸出掃描結果
  - Method: `_output_json(self)` — 輸出 JSON
  - Method: `_output_sqlite(self)` — 輸出 SQLite
  - Method: `_print_summary(self)` — 終端輸出摘要（用 logger 避免 Windows cp950 encoding 問題）
  - Method: `_send_telegram(self)` — 發送 Telegram 通知
- Function: `main()` — 主程序入口

---

## 📄 File: `trader/bot.py`
**Dependencies:** `ccxt, pandas, trader.infrastructure.api_client.(BinanceFuturesClient), trader.infrastructure.notifier.(TelegramNotifier), trader.infrastructure.telegram_handler.(TelegramCommandHandler), trader.infrastructure.data_provider.(MarketDataProvider), trader.infrastructure.performance_db.(PerformanceDB), trader.indicators.technical.(TechnicalAnalysis, DynamicThresholdManager, MTFConfirmation, MarketFilter), trader.risk.manager.(PrecisionHandler, RiskManager, SignalTierSystem), trader.execution.order_engine.(OrderExecutionEngine), trader.config.(ConfigV6), trader.positions.(PositionManager), trader.persistence.(PositionPersistence), trader.signals.(detect_2b_with_pivots, detect_ema_pullback, detect_volume_breakout), trader.strategies.base.(Action)`
- Function: `_trade_log(fields)` — Emit structured [TRADE] log line for log_summarizer.py
### Class: `TradingBotV6` — V6.0 終極滾倉版交易機器人
    - **Properties:** `_init_exchange, _log_startup, _restore_positions, _start_time, active_trades, data_provider, early_exit_cooldown, exchange, execution_engine, futures_client, initial_balance, order_failed_symbols` ... (+6 more)
  - Method: `__init__(self)` [Calls: _init_exchange, _restore_positions, _log_startup]
  - Method: `_init_exchange(self)` — 初始化交易所（沿用 V5.3）
  - Method: `_log_startup(self)` — 啟動日誌
  - Method: `_restore_positions(self)` — 從 positions.json 恢復 positions
  - Method: `_save_positions(self)` — 儲存所有 positions 到 JSON
  - Method: `fetch_ohlcv(self, symbol, timeframe, limit)` -> pd.DataFrame — 獲取 OHLCV 數據（委託 MarketDataProvider 統一處理重試與沙盒 fallback）
  - Method: `fetch_ticker(self, symbol)` -> dict — 獲取 ticker（含 Demo Trading fallback）
  - Method: `load_scanner_results(self)` -> List[str] — 從 Scanner 載入動態標的（沿用 V5.3）
  - Method: `_futures_set_leverage(self, symbol)` -> bool — 設置槓桿
  - Method: `_futures_create_order(self, symbol, side, quantity)` -> dict — 下市價單
  - Method: `_extract_fill_price(order_result, fallback_price)` -> float — 從訂單回應取實際成交均價（avgPrice / average）。
  - Method: `_futures_close_position(self, symbol, side, quantity)` -> dict — 平倉
  - Method: `_place_hard_stop_loss(self, symbol, side, size, stop_price)` -> Optional[str] — 設置硬止損單，回傳 order ID
  - Method: `_cancel_stop_loss_order(self, symbol, order_id)` -> bool — 取消止損單
  - Method: `_update_hard_stop_loss(self, pm, new_stop)` — 更新硬止損單
  - Method: `scan_for_signals(self)` [Calls: load_scanner_results, fetch_ohlcv, _execute_trade, _check_total_risk, _check_btc_trend] — 掃描交易信號
  - Method: `_check_btc_trend(self)` -> Optional[str] — Fetch BTC 1D EMA20/50 trend. Returns 'LONG', 'SHORT', 'RANGING', or None on failure.
  - Method: `_refresh_stop_loss(self, pm, new_sl)` [Calls: _cancel_stop_loss_order, _place_hard_stop_loss] — Cancel existing SL order, place new one, update pm.stop_order_id.
  - Method: `_calc_total_risk_pct(self, balance)` -> float — 計算所有活躍持倉的總風險佔比
  - Method: `_get_close_side(side)` -> str — Return exchange order side for closing a position.
  - Method: `_validate_position_size(self, symbol, raw_size, entry_price, label)` -> Optional[float] — Round amount and check limits. Returns size or None if below minimum.
  - Method: `_calculate_pnl(side, size, price, avg_entry)` -> float — Calculate unrealised/realised PnL for a position.
  - Method: `_build_log_base(event, trade_id, symbol, side)` -> dict — Build common fields for _trade_log calls.
  - Method: `_check_total_risk(self, active_positions)` -> bool — 總風險檢查（改用 PositionManager）
  - Method: `_execute_trade(self, symbol, signal_details, signal_type, tier_multiplier, df_signal)` [Calls: _get_close_side, _extract_fill_price, _place_hard_stop_loss, _save_positions, _validate_position_size, _futures_create_order, _build_log_base] — 執行開倉
  - Method: `monitor_positions(self)` [Calls: _save_positions, fetch_ticker, fetch_ohlcv, _update_hard_stop_loss, _handle_close, _build_log_base, _handle_stage2, _handle_stage3, _handle_v53_reduce] — 監控持倉
  - Method: `_fetch_exchange_stop_map(self)` -> Dict[str, float] — 從交易所取得開放中的止損單。
  - Method: `_adopt_ghost_positions(self)` [Calls: _fetch_exchange_stop_map, _save_positions] — 啟動後一次性接管幽靈倉位（exchange 有、positions.json 未記錄）。
  - Method: `_sync_exchange_positions(self)` [Calls: _save_positions] — 交易所倉位 reconciliation（每次 monitor_positions 都執行）。
  - Method: `_handle_close(self, pm, current_price)` -> bool [Calls: _calculate_pnl, _futures_close_position, fetch_ticker, _save_positions, _build_log_base] — 處理平倉。
  - Method: `_handle_stage2(self, pm, current_price, df_1h, decision)` [Calls: _validate_position_size, _get_close_side, _futures_create_order, _extract_fill_price, _refresh_stop_loss, _calc_total_risk_pct] — 處理 Stage 2 加倉
  - Method: `_handle_stage3(self, pm, current_price, df_1h, decision)` [Calls: _validate_position_size, _get_close_side, _futures_create_order, _extract_fill_price, _refresh_stop_loss, _calc_total_risk_pct] — 處理 Stage 3 加倉
  - Method: `_handle_v53_reduce(self, pm, pct, label, current_price)` [Calls: _futures_close_position, _extract_fill_price, _calculate_pnl, _refresh_stop_loss, _build_log_base] — 處理 V5.3 減倉
  - Method: `startup_diagnostics(self)` -> bool [Calls: fetch_ohlcv] — 啟動診斷
  - Method: `run(self)` [Calls: _adopt_ghost_positions, startup_diagnostics, scan_for_signals, _sync_exchange_positions, monitor_positions, _save_positions] — 主運行循環
### Class: `_TradeFilter` (Inherits: logging.Filter)
  - Method: `filter(self, record)`
### Class: `_TelegramLogHandler` (Inherits: logging.Handler)
    - **Properties:** `_last_sent`
  - Method: `__init__(self)`
  - Method: `emit(self, record)` [Calls: format]

---

## 📄 File: `trader/config.py`
### Class: `Config` — Trading Bot 配置類（獨立版）
  - Method: `get_strategy(cls)` -> 'TradingStrategy'
  - Method: `validate(cls)` — 驗證 V6.0 config 參數合理性
  - Method: `load_from_json(cls, config_file)` — 從 JSON 配置文件加載設置

---

## 📄 File: `trader/core.py` ⚠️ Deprecated — re-export stub（拆分四層後廢棄，勿直接 import）

---

## 📄 File: `trader/persistence.py`
### Class: `PositionPersistence` — Position 狀態持久化管理
    - **Properties:** `encoding, file_path`
  - Method: `__init__(self, file_path)` — 初始化持久化管理器
  - Method: `save_positions(self, positions_data)` -> bool — 儲存 positions 到 JSON 檔案（atomic write）
  - Method: `load_positions(self)` -> Dict[str, Dict[str, Any]] — 從 JSON 檔案讀取 positions
  - Method: `reconcile_with_exchange(self, positions_data, exchange_positions)` -> Dict[str, Dict[str, Any]] — 比對 positions.json 和交易所實際持倉，修正不一致
  - Method: `backup_positions(self)` -> Optional[str] — 備份當前 positions.json（用於重大操作前）
  - Method: `clear_positions(self)` -> bool [Calls: backup_positions, save_positions] — 清空 positions.json（慎用！）

---

## 📄 File: `trader/positions.py`
**Dependencies:** `trader.strategies.(TradingStrategy)`
### Class: `EntryRecord` — 單次入場紀錄
### Class: `PositionManager` — 單一 symbol 的倉位管理器
    - **Properties:** `atr, avg_entry, btc_trend_aligned, current_sl, entries, entry_adx, entry_time, equity_base, exit_reason, fakeout_depth_atr, highest_price, initial_r` ... (+24 more)
  - Method: `__init__(self, symbol, side, entry_price, stop_loss, position_size, strategy_name, is_v6_pyramid, neckline, equity_base, initial_r, signal_tier, trade_id, market_regime, strategy)`
  - Method: `is_v6_pyramid(self)` -> bool — 是否為 V6 金字塔策略（向下相容）
  - Method: `is_v6_pyramid(self, value)` — 設定策略（向下相容 legacy setter）
  - Method: `is_1r_protected(self)` -> bool
  - Method: `is_1r_protected(self, value)`
  - Method: `is_first_partial(self)` -> bool
  - Method: `is_first_partial(self, value)`
  - Method: `is_second_partial(self)` -> bool
  - Method: `is_second_partial(self, value)`
  - Method: `is_trailing_active(self)` -> bool
  - Method: `is_trailing_active(self, value)`
  - Method: `add_stage2(self, price, size, new_sl)` -> bool — Stage 2 加倉
  - Method: `add_stage3(self, price, size, swing_stop)` -> bool — Stage 3 加倉：EMA pullback + 縮量 + 反轉 K 確認
  - Method: `check_stage2_trigger(self, df_1h)` -> bool — 檢查 Stage 2 觸發條件：neckline 突破 + 1.2x 放量
  - Method: `check_stage3_trigger(self, df_1h)` -> bool — 檢查 Stage 3 觸發條件：EMA20 回測 + 縮量 + 反轉 K 線
  - Method: `calculate_stage2_size(self, entry_price)` -> float — 計算 Stage 2 加倉數量（含 risk 驗證）
  - Method: `calculate_stage3_size(self, entry_price, swing_stop)` -> float — 計算 Stage 3 加倉數量（含 risk 驗證）
  - Method: `monitor(self, current_price, df_1h, df_4h)` -> Dict[str, Any] — 統一監控入口（V7 P2 起回傳 Dict）。
  - Method: `to_dict(self)` -> Dict[str, Any] — 序列化為 dict（for positions.json）
  - Method: `from_dict(cls, data)` -> 'PositionManager' — 從 dict 反序列化（from positions.json）

---

## 📄 File: `trader/signals.py`
**Dependencies:** `pandas, trader.config.(Config), trader.structure.(StructureAnalysis)`
- Function: `detect_2b_with_pivots(df, left_bars, right_bars, vol_minimum_threshold, accept_weak_signals, enable_volume_grading, vol_explosive_threshold, vol_strong_threshold, vol_moderate_threshold, min_fakeout_atr)` -> Tuple[bool, Optional[Dict]] — 升級版 2B 偵測（V6.0）
- Function: `detect_ema_pullback(df, ema_pullback_threshold)` -> Tuple[bool, Optional[Dict]] — EMA 回撤信號偵測
- Function: `detect_volume_breakout(df, volume_breakout_mult)` -> Tuple[bool, Optional[Dict]] — 量能突破信號偵測

---

## 📄 File: `trader/structure.py`
**Dependencies:** `pandas`
### Class: `StructureAnalysis` — 結構分析工具
  - Method: `find_swing_points(df, left_bars, right_bars)` -> Dict — 找出已確認的 Swing High/Low（Pivot Points）
  - Method: `get_confirmed_pivots(df, left, right)` -> Dict[str, List[Tuple[int, float]]] — 只回傳「已確認」的 pivot points（右側 K 線已收盤完成）
  - Method: `find_neckline(df, signal_side, swing_points, left_bars, right_bars, entry_price)` -> Optional[float] — 找出 2B 信號的 Neckline（頸線）
  - Method: `get_validated_trailing_swing(df, side, current_sl, left_bars, right_bars)` -> Optional[float] — 尋找符合 Temporal BOS + HL/LH 條件的結構移損點
  - Method: `get_fast_trailing_swing(df, side, current_sl, left_bars, right_bars)` -> Optional[float] — Tier 2 加速結構追蹤 — 只要求 HL/LH，不要求 BOS 確認
  - Method: `find_latest_confirmed_swing(df, direction, left_bars, right_bars)` -> Optional[float] — 找出最新的 confirmed swing point（用於 Stage 3 移損）

---

## 📄 File: `trader/execution/order_engine.py`
**Dependencies:** `trader.config.(Config), trader.infrastructure.api_client.(BinanceFuturesClient), trader.risk.manager.(PrecisionHandler)`
### Class: `OrderExecutionEngine` — 訂單執行引擎：封裝所有與交易所 API 的實際下單互動
    - **Properties:** `exchange, futures_client, precision_handler`
  - Method: `__init__(self, exchange, futures_client, precision_handler)` — Args:
  - Method: `set_leverage(self, symbol)` -> bool — 設置槓桿
  - Method: `create_order(self, symbol, side, quantity)` -> dict [Calls: set_leverage] — 下市價單（自動先設置槓桿）
  - Method: `close_position(self, symbol, side, quantity)` -> dict — 平倉（reduceOnly 市價單）。
  - Method: `place_hard_stop_loss(self, symbol, side, size, stop_price)` -> Optional[str] — 設置硬止損單，回傳 order ID（失敗或已關閉則回傳 None）
  - Method: `cancel_stop_loss_order(self, symbol, order_id)` -> bool — 取消止損單
  - Method: `update_hard_stop_loss(self, pm, new_stop)` [Calls: cancel_stop_loss_order, place_hard_stop_loss] — 更新硬止損單（取消舊的，設置新的，直接更新 pm.stop_order_id）

---

## 📄 File: `trader/risk/manager.py`
**Dependencies:** `ccxt, trader.config.(Config), trader.infrastructure.api_client.(BinanceFuturesClient), trader.indicators.technical.(DynamicThresholdManager)`
### Class: `PrecisionHandler` — 交易所精度處理類
    - **Properties:** `_exchange_info_cache, _load_exchange_info, exchange, load_markets, markets, use_default_precision`
  - Method: `__init__(self, exchange)` [Calls: load_markets, _load_exchange_info]
  - Method: `load_markets(self)`
  - Method: `_load_exchange_info(self)` — 啟動時從 Binance exchangeInfo 一次載入所有幣種精度
  - Method: `_step_to_decimals(step)` -> int — 將步長轉換為小數位數
  - Method: `get_precision(self, symbol)` -> int [Calls: _step_to_decimals] — 獲取交易對的數量精度（優先 exchangeInfo → ccxt → DEFAULT → 預設 3）
  - Method: `get_price_precision(self, symbol)` -> int [Calls: _step_to_decimals] — 獲取交易對的價格精度
  - Method: `format_quantity(self, symbol, quantity)` -> str [Calls: get_precision] — 將數量格式化為交易所要求的字串精度
  - Method: `round_amount_up(self, symbol, amount, price)` -> float [Calls: get_precision] — 向上取整數量，確保訂單價值滿足最小要求
  - Method: `round_amount(self, symbol, amount)` -> float [Calls: get_precision] — 向下取整數量（用於平倉等操作）
  - Method: `get_min_amount(self, symbol)` -> float — 獲取交易對的最小交易數量
  - Method: `check_limits(self, symbol, amount, price)` -> bool — 檢查訂單是否滿足限制
### Class: `RiskManager` — 風險管理類
    - **Properties:** `exchange, futures_client, precision_handler`
  - Method: `__init__(self, exchange, precision_handler)`
  - Method: `_get_futures_balance(self)` -> float — 使用 /fapi/v2/balance 端點獲取 Futures 餘額
  - Method: `get_balance(self)` -> float [Calls: _get_futures_balance] — 獲取帳戶餘額
  - Method: `get_positions(self)` -> Optional[list] [Calls: _get_futures_positions] — 獲取現有持倉。
  - Method: `_get_futures_positions(self)` -> Optional[list] — 使用 Binance Futures API 獲取持倉。回傳 None 表示 API 錯誤。
  - Method: `get_account_info(self)` -> dict [Calls: get_balance, get_positions] — 獲取完整帳戶資訊
  - Method: `calculate_position_size(self, symbol, balance, entry_price, stop_loss, tier_multiplier)` -> float — 計算倉位大小
  - Method: `calculate_stop_loss(self, extreme_point, atr, side, df)` -> float — 計算止損價位
  - Method: `check_total_risk(self, active_positions)` -> bool [Calls: get_balance] — 計算所有持倉的實際剩餘風險
### Class: `SignalTierSystem` — 信號分級系統
  - Method: `calculate_signal_tier(signal_details, mtf_aligned, market_strong, volume_grade)` -> Tuple[str, float, int] — 計算信號等級並返回對應的倉位乘數

---

## 📄 File: `trader/strategies/base.py`
**Dependencies:** `pandas, trader.positions.(PositionManager)`
### Class: `Action` (Inherits: str, Enum) — 通用 action 類型（策略回傳值）
### Class: `DecisionDict` (Inherits: TypedDict)
- Function: `_apply_common_pre(pm, current_price, df_1h)` -> Optional[dict] — 共同前處理（V6 + V53 共用）：
### Class: `TradingStrategy` (Inherits: ABC) — 交易策略抽象基類
  - Method: `get_decision(self, pm, current_price, df_1h, df_4h)` -> DecisionDict — 根據當前市場狀態，回傳持倉決策。
  - Method: `get_state(self)` -> dict — 回傳策略內部 state（for persistence）
  - Method: `load_state(self, state)` — 從 dict 還原策略 state
### Class: `StrategyFactory` — 策略工廠（Registry 模式）：依名稱建立對應的 TradingStrategy 實例
  - Method: `register(cls, name, strategy_cls)`
  - Method: `create(cls, name)` -> TradingStrategy
  - Method: `create_strategy(cls, name)` -> TradingStrategy — Backward-compat alias for create(); also accepts legacy names.

---

## 📄 File: `trader/strategies/v53_sop.py`
**Dependencies:** `pandas, trader.positions.(PositionManager), trader.strategies.base.(Action, TradingStrategy, DecisionDict, _apply_common_pre), trader.strategies.base.(StrategyFactory)`
### Class: `V53SopStrategy` (Inherits: TradingStrategy) — V5.3 統一出場 SOP 策略
    - **Properties:** `is_1r_protected, is_first_partial, is_second_partial, is_trailing_active`
  - Method: `__init__(self)`
  - Method: `get_state(self)` -> dict
  - Method: `load_state(self, state)`
  - Method: `get_decision(self, pm, current_price, df_1h, df_4h)` -> DecisionDict — V5.3 出場決策：

---

## 📄 File: `trader/strategies/v6_pyramid.py`
**Dependencies:** `pandas, trader.positions.(PositionManager), trader.strategies.base.(Action, TradingStrategy, DecisionDict, _apply_common_pre), trader.strategies.base.(StrategyFactory)`
### Class: `V6PyramidStrategy` (Inherits: TradingStrategy) — V6.0 三段式金字塔滾倉策略
  - Method: `get_decision(self, pm, current_price, df_1h, df_4h)` -> DecisionDict — V6.0 出場決策：

---

## 📄 File: `trader/strategies/v7_structure.py`
**Dependencies:** `pandas, trader.positions.(PositionManager), trader.strategies.base.(Action, TradingStrategy, DecisionDict, _apply_common_pre), trader.strategies.base.(StrategyFactory)`
**Constants:** `MIN_BODY_RATIO`
### Class: `V7StructureStrategy` (Inherits: TradingStrategy) — V7 結構驅動三段加倉策略
    - **Properties:** `add_trigger_swings, last_structure_swing`
  - Method: `__init__(self)`
  - Method: `get_state(self)` -> dict
  - Method: `load_state(self, state)`
  - Method: `get_decision(self, pm, current_price, df_1h, df_4h)` -> DecisionDict [Calls: _check_reverse_2b, _check_add_trigger, _structure_trailing_sl]
  - Method: `_check_add_trigger(self, pm, current_price, df_1h, Cfg)` -> Optional[DecisionDict] — 三條件 AND 加倉觸發
  - Method: `_check_reverse_2b(self, pm, df_1h, Cfg)` -> Optional[DecisionDict] — 反向 2B 檢測（從 V6 移植，穿透深度 + 下根確認）
  - Method: `_structure_trailing_sl(self, pm, df, Cfg)` -> Optional[float] — 結構 Trailing SL：追蹤新形成的順勢 swing point（棘輪只往有利方向移動）
  - Method: `calculate_add_size(balance, risk_per_trade, entry_price, new_sl, max_position_percent, max_total_risk, current_total_risk_pct)` -> float — 獨立計算加倉倉位大小

---

## 📄 File: `trader/indicators/technical.py`
**Dependencies:** `pandas, numpy, pandas_ta, trader.config.(Config)`
- Function: `_ema(series, length)` -> pd.Series
- Function: `_sma(series, length)` -> pd.Series
- Function: `_atr(high, low, close, length)` -> pd.Series
- Function: `_adx(high, low, close, length)`
### Class: `TechnicalAnalysis` — 技術分析工具類
  - Method: `extract_adx_series(df, length)` -> Optional[pd.Series] — 安全提取 ADX Series
  - Method: `calculate_indicators(df)` -> pd.DataFrame — 計算所有必要的技術指標
  - Method: `check_trend(df, side)` -> Tuple[bool, str] — 檢查趨勢（雙向版本）
  - Method: `check_structure_break(df, current_price, side)` -> bool — 檢查結構是否破壞（雙向版本）
### Class: `DynamicThresholdManager` — 動態閾值管理器
  - Method: `get_adx_threshold(df)` -> float — 根據近期市場狀態動態調整 ADX 閾值
  - Method: `get_atr_multiplier(df)` -> float — 根據近期波動率動態調整 ATR 乘數
### Class: `MTFConfirmation` — 多時間框架確認系統
  - Method: `check_mtf_alignment(df_mtf, side)` -> Tuple[bool, str] — 檢查中間時間框架（4H）是否與交易方向一致
### Class: `MarketFilter` — 市場狀態過濾器
  - Method: `check_market_condition(df_trend, symbol)` -> Tuple[bool, str, bool] — 檢查市場是否適合交易

---

## 📄 File: `trader/infrastructure/api_client.py`
**Dependencies:** `requests, trader.config.(Config)`
### Class: `BinanceFuturesClient` — 統一的 Binance Futures API 客戶端，消除重複的簽章與請求邏輯
    - **Properties:** `_current_weight, _weight_limit, api_key, api_secret, base_url`
  - Method: `__init__(self, api_key, api_secret, sandbox)`
  - Method: `is_enabled()` -> bool — 判斷是否應使用 Binance Futures 直接 API（取代 ccxt）
  - Method: `signed_request(self, method, endpoint, params)` -> requests.Response — HMAC SHA256 簽章 + HTTP 請求，回傳原始 Response。
  - Method: `signed_request_json(self, method, endpoint, params)` -> dict [Calls: signed_request] — 簽章 + 請求 + JSON 解析 + 統一錯誤處理。

---

## 📄 File: `trader/infrastructure/data_provider.py`
**Dependencies:** `pandas, ccxt`
### Class: `MarketDataProvider` — 統一市場數據提供者：封裝 ccxt exchange 與 OHLCV 獲取邏輯
    - **Properties:** `exchange, max_retry, retry_delay, sandbox_mode, trading_mode`
  - Method: `__init__(self, exchange, max_retry, retry_delay, sandbox_mode, trading_mode)` — Args:
  - Method: `fetch_ohlcv(self, symbol, timeframe, limit)` -> pd.DataFrame — 獲取 OHLCV K 線數據（含重試與沙盒 fallback）

---

## 📄 File: `trader/infrastructure/notifier.py`
**Dependencies:** `requests, trader.config.(Config)`
### Class: `TelegramNotifier` — Telegram 推送通知類
  - Method: `send_message(message)`
  - Method: `notify_signal(symbol, details)` — 通知交易信號
  - Method: `notify_action(symbol, action, price, details)`
  - Method: `notify_warning(message)` — 轉發 WARNING/ERROR 級別 log 到 Telegram（有節流）
  - Method: `notify_exit(symbol, details)` — 通知交易平倉

---

## 📄 File: `trader/infrastructure/performance_db.py`
**Constants:** `CREATE_TABLE_SQL, INSERT_SQL`
### Class: `PerformanceDB`
    - **Properties:** `_init_db, db_path`
  - Method: `__init__(self, db_path)` [Calls: _init_db]
  - Method: `_init_db(self)`
  - Method: `record_trade(self, data)` -> bool — Write one trade record. Returns True on success.
  - Method: `get_last_loss_exit_time(self, symbol)` -> str | None — Query the most recent exit_time for a losing trade on the given symbol.

---

## 📄 File: `trader/infrastructure/telegram_handler.py`
**Dependencies:** `requests, trader.config.(Config)`
### Class: `TelegramCommandHandler` — Telegram Bot 指令處理（Polling 模式）
    - **Properties:** `base_url, bot, last_update_id`
  - Method: `__init__(self, bot)` — Args:
  - Method: `poll(self)` [Calls: _get_updates, _handle_update] — 檢查新訊息並處理指令。主 loop 每 cycle 呼叫一次。
  - Method: `_get_updates(self)` -> list — 取得新訊息（long polling timeout=0，非阻塞）
  - Method: `_handle_update(self, update)` [Calls: _send_reply] — 處理單一 update
  - Method: `_send_reply(self, chat_id, text)` — 發送回覆
  - Method: `_cmd_positions(self)` -> str — 列出目前所有開倉部位
  - Method: `_cmd_status(self)` -> str — Bot 運行狀態
  - Method: `_cmd_balance(self)` -> str — 帳戶餘額
  - Method: `_cmd_help(self)` -> str — 指令說明

---

## 📁 Key Non-Python Files

- `secrets.json` — API keys + Telegram tokens — ⚠️ 勿 commit（.gitignore）
- `bot_config.json` — 交易參數（無 secrets，可 commit）
- `positions.json` — Runtime 持倉狀態（PositionPersistence 讀寫）
- `performance.db` — 交易績效 SQLite（MFE/MAE/capture_ratio/market_regime）
- `hot_symbols.json` — Scanner 輸出的熱門標的清單
- `scanner/scanner_config.json` — Scanner 專屬設定
- `requirements.txt` — Python 依賴清單
- `scanner_results.db` — Scanner SQLite 輸出