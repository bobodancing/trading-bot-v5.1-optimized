# Trading Bot V6.0 — 滾倉版

三段式金字塔加倉系統，透過 Swing Point 結構分析和動態風險壓縮，實現加密貨幣期貨趨勢交易。

> 最後更新：2026-02-27

## 目錄

- [概覽](#概覽)
- [專案結構](#專案結構)
- [快速開始](#快速開始)
- [架構設計](#架構設計)
- [交易策略](#交易策略)
- [Market Scanner](#market-scanner)
- [風險管理](#風險管理)
- [配置系統](#配置系統)
- [持久化與 Crash Recovery](#持久化與-crash-recovery)
- [測試](#測試)
- [技術棧](#技術棧)
- [日誌系統](#日誌系統)

---

## 概覽

V6.0 是基於 V5.3 引擎的重大升級。核心改變：

| 比較 | V5.3 | V6.0 |
|------|------|------|
| **入場信號** | 2B (rolling min/max) | 2B (Swing Pivot + Neckline) |
| **加倉** | 無 | 三階段滾倉 (Stage 1→2→3) |
| **出場** | 1.0R/1.5R/2.5R 分批減倉 | 結構追蹤 + 反向 2B |
| **止損** | ATR 固定 | Swing Point + 動態移損 |
| **風險壓縮** | 固定 R | 加倉後風險 ≤ initial R |

V5.3 的 EMA Pullback 和 Volume Breakout 策略仍保留作為互補，走原有的分批減倉 SOP。

**交易所**: Binance Futures (支援 Testnet)
**交易對**: BTC/ETH/SOL/DOGE + Scanner 動態標的

---

## 專案結構

```
trading_bot/
├── bot_config.json              # 交易參數（不含 secrets，可 commit）
├── secrets.json                 # API keys + Telegram tokens（不可 commit）
├── requirements.txt             # Python 依賴
├── .gitignore
│
├── v6/                          # V6.0 核心引擎
│   ├── bot.py                   # 主引擎 TradingBotV6
│   ├── config.py                # 配置類 ConfigV6
│   ├── positions.py             # PositionManager 倉位管理（含 Stage trigger）
│   ├── signals.py               # 升級版 2B 偵測（含穿透深度過濾）
│   ├── structure.py             # Swing Point 結構分析
│   ├── persistence.py           # Atomic write 持久化
│   │
│   ├── infrastructure/          # 基礎設施層
│   │   ├── api_client.py        # BinanceFuturesClient（HMAC 簽章 + rate limit）
│   │   ├── notifier.py          # TelegramNotifier
│   │   ├── data_provider.py     # MarketDataProvider（統一 retry + sandbox fallback）
│   │   └── performance_db.py    # PerformanceDB（SQLite，平倉自動寫入 MFE/MAE/R）
│   │
│   ├── indicators/              # 技術指標層
│   │   └── technical.py         # TechnicalAnalysis, DynamicThresholdManager,
│   │                            # MTFConfirmation, MarketFilter
│   │
│   ├── risk/                    # 風險管理層
│   │   └── manager.py           # PrecisionHandler, RiskManager, SignalTierSystem
│   │
│   ├── execution/               # 執行層
│   │   └── order_engine.py      # OrderExecutionEngine（下單/止損/平倉）
│   │
│   ├── strategies/              # 策略層（V7 P2 Strategy Pattern）
│   │   ├── base.py              # TradingStrategy ABC + StrategyFactory
│   │   ├── v6_pyramid.py        # V6 滾倉出場邏輯
│   │   └── v53_sop.py           # V5.3 SOP 出場邏輯
│   │
│   └── tests/                   # Pytest 測試（137 tests）
│       ├── conftest.py          # mock_bot fixture
│       ├── test_structure.py
│       ├── test_signals.py
│       ├── test_persistence.py
│       ├── test_risk.py
│       ├── test_v7p1.py
│       ├── test_v7p2.py
│       ├── test_handle_close.py
│       ├── test_sync_positions.py
│       ├── test_perf_db_quality.py
│       ├── test_tier_equity_balance.py
│       ├── test_signal_quality.py
│       └── test_fill_price.py
│
├── scanner/                     # Market Scanner（獨立服務）
│   ├── market_scanner.py        # 四層掃描器
│   ├── scanner_config.json      # Scanner 獨立配置
│   └── README.md
│
└── .log/                        # Runtime（自動建立）
    ├── v6_bot.log
    ├── v6_trades.log            # 純交易記錄（[TRADE] 標籤）
    └── scanner.log
```

Runtime 自動產生的檔案（已在 `.gitignore`）：
- `positions.json` — 持倉持久化（crash recovery 用）
- `v6_performance.db` — 交易績效 SQLite
- `hot_symbols.json` — Scanner 掃描結果
- `scanner_results.db` — Scanner 歷史記錄 (SQLite)

---

## 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 設定 `secrets.json`

在專案根目錄建立 `secrets.json`（已在 `.gitignore`，不可 commit）：

```json
{
    "api_key": "你的 Binance API Key",
    "api_secret": "你的 Binance API Secret",
    "telegram_bot_token": "選填",
    "telegram_chat_id": "選填"
}
```

### 3. 設定 `bot_config.json`

```json
{
  "sandbox_mode": true,
  "telegram_enabled": false,
  "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
  "trading_direction": "both",
  "leverage": 3,
  "risk_per_trade": 0.017,
  "pyramid_enabled": true,
  "strategy_use_v6": {
    "2B_BREAKOUT": true,
    "EMA_PULLBACK": true,
    "VOLUME_BREAKOUT": false
  },
  "DB_PATH": "v6_performance.db"
}
```

> `sandbox_mode: true` 連接 Binance Testnet（模擬交易），正式環境改為 `false`。

### 4. 執行

Bot 和 Scanner 為獨立服務，分別啟動：

```bash
# Bot（交易引擎）
python3 v6/bot.py

# Scanner（市場掃描，建議另開終端）
python3 scanner/market_scanner.py
```

**systemd 服務（rwUbuntu 部署）**：

```bash
sudo systemctl start trader.service    # Bot
sudo systemctl start scanner.service   # Scanner
sudo systemctl status trader.service   # 查看狀態
```

### 5. 執行流程

```
Bot 啟動
 ├─ startup_diagnostics()   驗證 API / Balance / 數據
 ├─ _restore_positions()    恢復 positions.json
 ├─ _adopt_ghost_positions() 接管交易所孤立倉位
 └─ 主循環（每 60 秒）
     ├─ scan_for_signals()      多策略信號掃描
     ├─ _sync_exchange_positions() 四重倉位同步
     ├─ monitor_positions()     持倉監控 + 加倉/減倉/平倉
     └─ sleep(CHECK_INTERVAL)
```

---

## 架構設計

### 模組職責

| 模組 | 類別 | 職責 |
|------|------|------|
| `bot.py` | `TradingBotV6` | 交易主循環：信號掃描 → 開倉 → 監控 → 平倉 |
| `config.py` | `ConfigV6` | 合併 V5.3 + V6.0 所有參數（獨立，不依賴外部） |
| `positions.py` | `PositionManager` | 單 symbol 倉位管理（V6 滾倉 / V5.3 SOP 雙路徑，含 Stage trigger 邏輯） |
| `signals.py` | `detect_2b_with_pivots()` | 升級版 2B 偵測（Swing Pivot + Neckline + 穿透深度過濾） |
| `structure.py` | `StructureAnalysis` | Swing Point 偵測 + Neckline 識別 |
| `persistence.py` | `PositionPersistence` | Atomic write + Crash recovery + Exchange 對帳 |

### 分層架構

原 `core.py` 已拆分為五個獨立子層：

| 層 | 模組 | 類別 | 功能 |
|----|------|------|------|
| **基礎設施** | `infrastructure/api_client.py` | `BinanceFuturesClient` | Binance Futures 直接 API（HMAC 簽章 + rate limit throttle） |
| | `infrastructure/notifier.py` | `TelegramNotifier` | Telegram 推送（信號/平倉通知） |
| | `infrastructure/data_provider.py` | `MarketDataProvider` | 統一 OHLCV 獲取（retry + sandbox fallback，Scanner 和 Bot 共用） |
| | `infrastructure/performance_db.py` | `PerformanceDB` | SQLite 績效記錄（MFE/MAE/R/ADX/fakeout_depth） |
| **技術指標** | `indicators/technical.py` | `TechnicalAnalysis` | 指標計算 + 2B/EMA Pullback/Volume Breakout 偵測 |
| | | `DynamicThresholdManager` | 動態 ADX/ATR 閾值 |
| | | `MTFConfirmation` | 多時間框架確認（4H EMA20/50） |
| | | `MarketFilter` | 市場狀態過濾（ADX / ATR spike） |
| **風險管理** | `risk/manager.py` | `PrecisionHandler` | 下單精度（exchangeInfo + ccxt + 預設三層 fallback） |
| | | `RiskManager` | balance / position sizing / stop loss |
| | | `SignalTierSystem` | 信號分級（Tier A/B/C → 倉位倍率） |
| **執行引擎** | `execution/order_engine.py` | `OrderExecutionEngine` | 下單 / 止損設置 / 平倉（封裝所有交易所 API 互動） |
| **策略** | `strategies/base.py` | `TradingStrategy` / `StrategyFactory` | Strategy ABC + 自動 dispatch（by `is_v6_pyramid`） |
| | `strategies/v6_pyramid.py` | `V6PyramidStrategy` | V6 出場邏輯（結構追蹤 / profit_pullback / 反向 2B） |
| | `strategies/v53_sop.py` | `V53SopStrategy` | V5.3 出場邏輯（1.0R/1.5R/2.5R SOP） |

### 雙路徑策略架構

Bot 根據信號來源自動選擇路徑：

```
信號掃描
 ├─ 2B Breakout（V6 偵測）  → V6 滾倉路徑（V6PyramidStrategy）
 │   └─ Stage 1 → Stage 2 → Stage 3
 │
 ├─ EMA Pullback（V5.3）    → V5.3 SOP 路徑（V53SopStrategy）
 │   └─ 1.0R → 1.5R → 2.5R 分批減倉
 │
 └─ Volume Breakout（V5.3） → V5.3 SOP 路徑（V53SopStrategy）
     └─ 1.0R → 1.5R → 2.5R 分批減倉
```

信號優先級：**2B > Volume Breakout > EMA Pullback**

---

## 交易策略

### 策略一：2B Breakout（V6.0 滾倉路徑）

#### 信號偵測

使用真正的 Swing Point Pivot（左 7 根 + 右 3 根確認）取代 V5.3 的 rolling min/max。

**Bullish 2B**：價格跌破 confirmed swing low 後放量收回
**Bearish 2B**：價格突破 confirmed swing high 後放量收回

穿透深度過濾：穿透幅度 < 0.3 ATR 視為噪音，不觸發信號（`MIN_FAKEOUT_ATR=0.3`）。

信號同時回傳 **neckline**（反向 swing point），供 Stage 2 觸發使用。

量能分級：
- `explosive`：成交量 ≥ 2.5x 均量
- `strong`：≥ 1.5x
- `moderate`：≥ 1.0x
- `weak`：≥ 0.7x（可配置是否接受）

#### 三階段滾倉

**Stage 1 — 試單入場**

| 項目 | 內容 |
|------|------|
| 觸發 | 2B 信號確認 |
| 倉位 | `equity × 20% × 33% ≈ 6.6%` |
| 止損 | swing point ± 0.5 ATR |
| 記錄 | `initial_R = 風險金額`，`equity_base = 當前 balance` |

**Stage 2 — Neckline 突破加倉**

| 項目 | 內容 |
|------|------|
| 觸發 | 收盤突破 neckline + 成交量 ≥ 1.2x 均量（`check_stage2_trigger()` in PositionManager） |
| 加倉 | `equity × 20% × 37% ≈ 7.4%`（受 risk cap 限制） |
| 移損 | Stage 1 入場價（保本） |
| 風險 | total risk ≤ initial_R |

**Stage 3 — EMA 回測加倉**

| 項目 | 內容 |
|------|------|
| 觸發 | 前根 K 觸碰 EMA20 + 縮量 + 當根反轉 K（`check_stage3_trigger()` in PositionManager） |
| 加倉 | `equity × 20% × 30% = 6.0%`（受 risk cap 限制） |
| 移損 | 最近 confirmed swing point ± 0.5 ATR |
| 目標 | SL 在均價以上/以下（risk-free） |

#### V6 出場機制（三重）

1. **結構追蹤止損**：每產生新的 confirmed swing point（`find_latest_confirmed_swing()`）就即時移損
2. **獲利回吐保護**：從 `highest_price` 回撤達 55%（`PROFIT_PULLBACK_THRESHOLD`）→ 強制全平（需先達 0.3R MFE 才啟用）
3. **反向 2B 出場**：出現反向 2B 信號 → 全部平倉

> 4H EMA20 force exit 暫停中（`V6_4H_EMA20_FORCE_EXIT=False`）：瞬間觸碰即觸發問題，Phase 3 後改 N-bar 確認邏輯再重啟。

出場邏輯集中至 `strategies/v6_pyramid.py`（`V6PyramidStrategy`），回傳結構化 dict `{action, reason, new_sl, close_pct}`。

---

### 策略二：EMA Pullback（V5.3 SOP 路徑）

**入場**：EMA 快線 (10) > 慢線 (20)，價格回測 EMA 快線後反彈

**出場 SOP**：

| 階段 | 觸發 | 動作 |
|------|------|------|
| 1.0R | 獲利達 1R | 移損至 +0.3R |
| 1.5R | 獲利達 1.5R | 減倉 30%，移損至 +0.5R |
| 2.5R | 獲利達 2.5R | 減倉 30%，移損至 +1.5R，啟動 ATR trailing |

---

### 策略三：Volume Breakout（V5.3 SOP 路徑）

**入場**：成交量 > 均量 2.0x，收盤突破近 10 根高點/低點

**出場**：同 EMA Pullback SOP

---

### 共用安全機制

| 機制 | V6 路徑 | V5.3 路徑 |
|------|---------|----------|
| 快速止損 | -0.5R 即平倉 | -0.5R 即平倉 |
| 時間退出 | Stage 1 超過 36h 未升級 | 24h 未達 1.5R |
| 獲利回吐 | 從高點回撤 ≥ 55%（需 MFE ≥ 0.3R）→ 全平 | N/A |
| 冷卻期 | 快速止損後 10h 不交易該幣種 | 同左 |
| 結構破壞 | N/A（用結構追蹤） | swing low/high 破壞 → 平倉 |
| 硬止損 | 交易所掛單 + 四重 reconciliation | 同左 |
| 平倉失敗 | rollback 保留持倉，待下週期重試 | 同左 |
| 平倉優先 | `_handle_close` 先平倉後取消止損單 | 同左 |

---

## Market Scanner

四層過濾架構，從 Binance Futures 全市場篩選最符合 2B 策略的標的。

### 過濾層級

| Layer | 名稱 | 功能 | 關鍵參數 |
|-------|------|------|----------|
| Layer 1 | 流動性過濾 | 排除低流動性幣種 | 24H 成交量 ≥ $30M，排除穩定幣/槓桿幣 |
| Layer 2 | 動能篩選 | 找趨勢中的標的 | ADX ≥ 20，RSI 40~70，ATR% 1.5~15 |
| Layer 3 | 形態匹配 | 2B 信號 + 預警 | Swing Pivot 偵測，量能確認 |
| Layer 4 | 相關性過濾 | 分散風險 | 同板塊最多 2 個，相關性 < 0.7 |

### 輸出

Scanner 產生 `hot_symbols.json`，Bot 啟動時讀取：

```json
{
  "scan_time": "2026-02-16T15:53:32+00:00",
  "market_regime": "TRANSITIONING",
  "total_scanned": 165,
  "final_count": 6,
  "hot_symbols": [
    {
      "symbol": "ICP/USDT",
      "rank": 1,
      "score": 72.4,
      "signal_side": "LONG",
      "signal_type": "CONFIRMED_2B",
      "volume_grade": "explosive",
      "entry_price": 2.356,
      "stop_loss": 2.328
    }
  ]
}
```

> Scanner 永遠使用 Binance 正式網（`SANDBOX_MODE: false`），確保獲取真實市場數據。

---

## 風險管理

### 單筆風險

```
risk_amount = balance × RISK_PER_TRADE (1.7%)
```

### V6 滾倉風險壓縮

核心原則：**三次加倉，但總風險始終 ≤ initial_R**

```
Stage 1: size = equity × 20% × 33%
         initial_R = size × |entry - stop_loss|

Stage 2: 加倉 + 移損至保本
         total_risk = |avg_entry - new_SL| × total_size ≤ initial_R
         若超標則自動縮減 Stage 2 size

Stage 3: 加倉 + 移損至 swing point
         目標：SL 在 avg_entry 以上（risk-free 持倉）
```

### Tier 系統（信號分級）

三段加倉均乘以 tier 倍率，Tier A 全部吃滿，Tier C 縮半：

| 等級 | 倉位倍率 | Stage 1 實際 equity% |
|------|----------|----------------------|
| Tier A | 1.0x | ~6.6% |
| Tier B | 0.7x | ~4.6% |
| Tier C | 0.5x | ~3.3% |

### V5.3 Risk-Based Sizing

```
risk_amount = balance × 1.7%
stop_distance = |entry - stop_loss| / entry
position_value = risk_amount / stop_distance
position_size = position_value / entry
上限：position_value ≤ balance × V53_EQUITY_CAP_PERCENT(10%) × leverage
```

### 總風險控制

| 參數 | 值 | 說明 |
|------|------|------|
| `RISK_PER_TRADE` | 1.7% | 單筆最大風險 |
| `MAX_TOTAL_RISK` | 5% | 所有持倉風險總和上限 |
| `EQUITY_CAP_PERCENT` | 20% | V6 單筆最大 equity 暴露（三段合計） |
| `V53_EQUITY_CAP_PERCENT` | 10% | V5.3 獨立 equity 上限 |
| `MAX_POSITIONS_PER_GROUP` | 6 | 最大同時持倉數 |
| `LEVERAGE` | 3x | 槓桿倍數 |

---

## 配置系統

### ConfigV6

所有參數集中在 `v6/config.py` 的 `ConfigV6` 類別，分為：

**V5.3 基礎參數**（交易所 / 風險 / 指標 / 時間框架 / 市場過濾 / 出場 SOP）
**V6.0 滾倉參數**（三段分配 / Swing Point / Stage 觸發 / V6 出場）
**系統參數**（持久化路徑 / Scanner 整合 / Debug）

### 載入方式

`ConfigV6.load_from_json("bot_config.json")` 從 JSON 覆寫 class attributes，並自動讀取同目錄 `secrets.json` 注入 API keys。
JSON key 自動映射為大寫（例：`risk_per_trade` → `RISK_PER_TRADE`）。
載入後自動執行 `validate()`，驗證：
- Stage ratio 加總 = 1.0
- Equity cap 在 1%~50% 之間
- Swing right bars ≥ 2
- Stage 2 volume mult ≥ 1.0

### 關鍵 V6 參數

| 參數 | 值 | 說明 |
|------|--------|------|
| `PYRAMID_ENABLED` | `true` | 啟用三段滾倉 |
| `EQUITY_CAP_PERCENT` | 0.20 | V6 三段合計最大 20% equity |
| `V53_EQUITY_CAP_PERCENT` | 0.10 | V5.3 獨立最大 10% equity |
| `STAGE1_RATIO` | 0.33 | Stage 1 佔 33% |
| `STAGE2_RATIO` | 0.37 | Stage 2 佔 37% |
| `STAGE3_RATIO` | 0.30 | Stage 3 佔 30% |
| `SWING_LEFT_BARS` | 7 | Swing point 左側確認根數 |
| `SWING_RIGHT_BARS` | 3 | Swing point 右側確認根數 |
| `SL_ATR_BUFFER` | 0.8 | 止損緩衝（0.8 ATR） |
| `STAGE2_VOLUME_MULT` | 1.2 | Neckline 突破放量門檻 |
| `PROFIT_PULLBACK_THRESHOLD` | 0.55 | 從高點回撤 55% 即全平（V6 路徑） |
| `MIN_MFE_R_FOR_PULLBACK` | 0.3 | 啟用 profit_pullback 的最低 MFE（R 倍數） |
| `MIN_FAKEOUT_ATR` | 0.3 | 2B 穿透深度下限（< 0.3 ATR 視為噪音） |
| `EARLY_STOP_R_THRESHOLD` | 0.75 | 快速止損（-0.75R） |
| `V6_STAGE1_MAX_HOURS` | 36 | V6 路徑 Stage 1 最大持倉時間 |
| `STAGE1_MAX_HOURS` | 24 | V5.3 路徑時間退出 |
| `EARLY_EXIT_COOLDOWN_HOURS` | 10 | 快速止損後冷卻時間 |
| `V6_4H_EMA20_FORCE_EXIT` | `false` | 4H EMA20 強制出場（暫停中） |

### 策略開關（strategy_use_v6）

```json
"strategy_use_v6": {
  "2B_BREAKOUT": true,
  "EMA_PULLBACK": true,
  "VOLUME_BREAKOUT": false
}
```

### 信號分級倍率

| 等級 | 倉位倍率 | 說明 |
|------|----------|------|
| Tier A | 1.0x | 最強信號，全倉位 |
| Tier B | 0.7x | 中等信號 |
| Tier C | 0.5x | 最弱可接受信號 |

---

## 持久化與 Crash Recovery

### Atomic Write

防止 crash 時檔案損壞：

```
1. 寫入 .positions.json.tmp_XXXX（臨時檔）
2. flush + fsync（確保寫入磁碟）
3. rename → positions.json（OS 層級 atomic operation）
```

### 啟動恢復

Bot 啟動時：
1. `_restore_positions()` — 從 `positions.json` 恢復所有 PositionManager
2. `_adopt_ghost_positions()` — 掃描交易所持倉，接管 positions.json 未記錄的倉位（幽靈倉位）
3. 主循環每次執行 `_sync_exchange_positions()` — 四重防護 reconciliation

### 四重同步防護（_sync_exchange_positions）

| 防護 | 觸發 | 行為 |
|------|------|------|
| 1 | API 錯誤回 None | 跳過整次同步（防止誤殺） |
| 2 | bot 有、exchange 無 | 標記 hard_stop_hit → 觸發平倉流程 |
| 3 | 兩邊都有，size 差 > 5% | 告警 [SIZE_MISMATCH] |
| 4 | exchange 有、bot 無 | 告警 [GHOST_POSITION] |

### 平倉失敗保護

`_handle_close()` 採用 try-except + rollback：
- 先移除止損單進入 `pending_stop_cancels`（平倉優先）
- 平倉下單失敗 → rollback：`pm.is_closed = False`，立即 `_save_positions()`，待下週期重試

### 備份

重大操作（Stage 升級、部分平倉）前自動備份 `positions.json.bak`。

---

## 測試

137 個 pytest 測試，全部通過。

```bash
# 執行全部測試
python3 -m pytest v6/tests/ -v

# 執行特定模組
python3 -m pytest v6/tests/test_signals.py -v
python3 -m pytest v6/tests/test_persistence.py -v
python3 -m pytest v6/tests/test_fill_price.py -v
```

### 測試覆蓋

| 測試模組 | 測試數 | 覆蓋範圍 |
|----------|--------|----------|
| `test_structure.py` | 9 | Swing point 偵測、Neckline 識別、edge cases |
| `test_signals.py` | 13 | Bullish/Bearish 2B、量能過濾、穿透深度過濾 |
| `test_risk.py` | 13 | Stage 2/3 sizing、risk cap、proportional scaling |
| `test_persistence.py` | 30 | Atomic write、Load/Save、Exchange reconciliation、出場決策 16 場景 |
| `test_v7p1.py` | 3 | Rate limit throttle、平倉優先、pending_stop_cancels |
| `test_v7p2.py` | 16 | Strategy Pattern dispatch、V6/V53 出場邏輯 |
| `test_handle_close.py` | 5 | 平倉失敗 rollback、save_positions、stop_order 邊界 |
| `test_sync_positions.py` | 7 | 四重防護、API error guard、ghost detection |
| `test_perf_db_quality.py` | 8 | capture_ratio 公式、MFE/MAE fallback |
| `test_tier_equity_balance.py` | 13 | Stage 2/3 tier_mult、V5.3 equity_cap、sizing ratio |
| `test_signal_quality.py` | 4 | MFE 門檻、Strategy dispatch V53 |
| `test_fill_price.py` | 8 | avgPrice 擷取、fallback、Stage 1 integration |

---

## 技術棧

| 類別 | 技術 |
|------|------|
| 語言 | Python 3.10+ |
| 交易所 API | CCXT + Binance Futures 直接 API（HMAC 簽章） |
| 技術指標 | pandas-ta（EMA / ATR / ADX / RSI） |
| 數據處理 | pandas + numpy |
| 通知 | Telegram Bot API |
| 測試 | pytest |
| 持久化 | JSON (atomic write) + SQLite (scanner history + performance DB) |

### 依賴套件

```
ccxt>=4.0.0
pandas>=2.0.0
numpy>=1.24.0
pandas-ta>=0.3.14b
requests>=2.28.0
python-dateutil>=2.8.0
```

---

## 日誌系統

所有日誌存放在 `.log/` 目錄：

| 檔案 | 來源 | 說明 |
|------|------|------|
| `v6_bot.log` | `v6/bot.py` | Bot 交易日誌（信號/開倉/平倉/移損） |
| `v6_trades.log` | `v6/bot.py` | 純交易記錄（僅含 `[TRADE]` 標籤訊息） |
| `scanner.log` | `scanner/` | Scanner 掃描日誌 |

日誌採用 `RotatingFileHandler`，單檔上限 5MB，最多保留 3 個備份。

---

## 注意事項

- `secrets.json` 含 API keys，**不可** commit 到版本控制（已在 `.gitignore`）
- `bot_config.json` 不含 secrets，可安全 commit
- Scanner 永遠連接 Binance 正式網取得真實數據，Bot 的 `sandbox_mode` 只控制下單端
- `--dry-run` 模式下所有訂單只會 log 不會送出（`V6_DRY_RUN=True`）
- 首次使用建議先確認 `sandbox_mode: true` 觀察信號品質
- 硬止損掛單確保即使 Bot 斷線，交易所也會自動執行止損
