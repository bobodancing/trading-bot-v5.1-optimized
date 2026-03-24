# Trading Bot — Pluggable Crypto Futures Trading Platform

基於 Swing Point 結構分析的加密貨幣期貨交易平台，支援策略拔插（Plugin Architecture）。

> 最後更新：2026-03-24 | 348 tests passed

## 目錄

- [概覽](#概覽)
- [專案結構](#專案結構)
- [快速開始](#快速開始)
- [架構設計](#架構設計)
- [策略系統](#策略系統)
- [Market Scanner](#market-scanner)
- [風險管理](#風險管理)
- [配置系統](#配置系統)
- [持久化與 Crash Recovery](#持久化與-crash-recovery)
- [測試](#測試)
- [技術棧](#技術棧)

---

## 概覽

本系統為**策略平台**，核心引擎處理進場、監控、平倉、風控的完整交易生命週期，策略模組透過 Registry Pattern 以插件形式載入。

**內建策略**：

| 策略 | 進場信號 | 持倉管理 |
|------|----------|----------|
| **V7 Structure** | 2B Swing Pivot Breakout | 三段結構加倉（Swing-based SL 棘輪）+ 反向 2B + 超時退出 |
| **V53 SOP** | EMA Pullback / Volume Breakout | 1.0R / 1.5R / 2.0R 分批減倉 |
| ~~V6 Pyramid~~ | ~~2B~~ | ~~deprecated，既有倉位仍可運行~~ |

**新增策略**只需：寫 class → `StrategyFactory.register()` → config 映射，不動 bot.py。

**交易所**: Binance Futures（支援 Testnet）
**信號來源**: 2B Swing Pivot Detection + Market Scanner 動態標的

---

## 專案結構

```
trading_bot/
├── bot_config.json              # 交易參數（不含 secrets，可 commit）
├── secrets.json                 # API keys + Telegram tokens（.gitignore）
├── requirements.txt
│
├── trader/                      # 核心引擎（Python package）
│   ├── bot.py                   # 主引擎 TradingBotV6 — 交易主循環
│   ├── config.py                # ConfigV6 — 集中參數管理
│   ├── positions.py             # PositionManager — 倉位生命週期
│   ├── signals.py               # 2B Pivot 偵測（穿透深度過濾）
│   ├── structure.py             # Swing Point + Neckline + BOS 追蹤
│   ├── persistence.py           # Atomic write + Crash recovery
│   │
│   ├── strategies/              # 策略插件層（Registry Pattern）
│   │   ├── base.py              # Action enum + DecisionDict + TradingStrategy ABC + StrategyFactory
│   │   ├── v7_structure.py      # V7 結構加倉（Swing-based 三段加倉 + 反向 2B + 超時）
│   │   ├── v53_sop.py           # V5.3 SOP 出場（1.0R/1.5R/2.0R 減倉）
│   │   └── v6_pyramid.py        # [deprecated] V6 滾倉（既有倉位保留）
│   │
│   ├── infrastructure/          # 基礎設施
│   │   ├── api_client.py        # BinanceFuturesClient（HMAC + rate limit）
│   │   ├── notifier.py          # TelegramNotifier
│   │   ├── data_provider.py     # MarketDataProvider（retry + sandbox fallback）
│   │   └── performance_db.py    # PerformanceDB（SQLite — MFE/MAE/R/Tier）
│   │
│   ├── indicators/
│   │   └── technical.py         # TechnicalAnalysis / DynamicThresholdManager / MTF / MarketFilter
│   │
│   ├── risk/
│   │   └── manager.py           # PrecisionHandler / RiskManager / SignalTierSystem
│   │
│   ├── execution/
│   │   └── order_engine.py      # OrderExecutionEngine（下單 / 止損 / 平倉）
│   │
│   └── tests/                   # 348 tests
│       ├── conftest.py
│       ├── test_integration.py  # StatefulMockEngine + FaultInjector
│       └── test_*.py            # 28 test modules
│
├── scanner/                     # Market Scanner（獨立服務）
│   └── market_scanner.py        # 四層掃描（流動性 → 動能 → 形態 → 相關性）
│
└── .log/                        # Runtime 日誌（自動建立）
    ├── v6_bot.log
    ├── v6_trades.log
    └── scanner.log
```

Runtime 自動產生（`.gitignore`）：
- `positions.json` — 持倉持久化
- `v6_performance.db` — 交易績效 SQLite
- `hot_symbols.json` — Scanner 掃描結果
- `scanner_results.db` — Scanner 歷史

---

## 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 設定 secrets.json

```json
{
    "api_key": "your Binance API Key",
    "api_secret": "your Binance API Secret",
    "telegram_bot_token": "optional",
    "telegram_chat_id": "optional"
}
```

### 3. 設定 bot_config.json

```json
{
  "sandbox_mode": true,
  "telegram_enabled": false,
  "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  "trading_direction": "both",
  "leverage": 3,
  "risk_per_trade": 0.017,
  "pyramid_enabled": true,
  "DB_PATH": "v6_performance.db"
}
```

### 4. 執行

```bash
# Bot
python3 trader/bot.py

# Scanner（另開終端）
python3 -m scanner.market_scanner
```

**systemd 部署**：

```bash
sudo systemctl start trader.service
sudo systemctl start scanner.service
```

### 5. 執行流程

```
Bot 啟動
 ├─ startup_diagnostics()        驗證 API / Balance / 數據
 ├─ _restore_positions()         恢復 positions.json
 ├─ _adopt_ghost_positions()     接管交易所孤立倉位
 └─ 主循環（每 60 秒）
     ├─ scan_for_signals()           信號掃描 → 開倉
     ├─ _sync_exchange_positions()   四重倉位同步
     ├─ monitor_positions()          監控 → 加倉/減倉/平倉
     └─ sleep(CHECK_INTERVAL)
```

---

## 架構設計

### 分層架構

| 層 | 模組 | 職責 |
|----|------|------|
| **主引擎** | `bot.py` | 交易主循環、信號掃描、倉位監控 |
| **倉位管理** | `positions.py` | 單 symbol 生命週期、Stage 管理、序列化 |
| **策略插件** | `strategies/` | 出場邏輯（Registry 模式，可拔插） |
| **信號偵測** | `signals.py` + `structure.py` | 2B Pivot 偵測、Swing Point、Neckline |
| **風控** | `risk/manager.py` | 精度處理、倉位計算、Tier 分級 |
| **執行** | `execution/order_engine.py` | 下單、止損、平倉 |
| **基礎設施** | `infrastructure/` | API client、Telegram、數據、績效 DB |
| **指標** | `indicators/technical.py` | TA / DTM / MTF / MarketFilter |

### 策略插件系統

```python
# 通用 Action 類型
class Action(str, Enum):
    HOLD          = "HOLD"           # 繼續持有
    CLOSE         = "CLOSE"          # 全平
    PARTIAL_CLOSE = "PARTIAL_CLOSE"  # 部分平倉
    ADD           = "ADD"            # 加倉
    UPDATE_SL     = "UPDATE_SL"      # 更新止損

# 策略 ABC
class TradingStrategy(ABC):
    def get_decision(self, pm, current_price, df_1h, df_4h) -> DecisionDict: ...
    def get_state(self) -> dict: ...       # 策略內部 state 序列化
    def load_state(self, state: dict): ... # state 還原

# Registry — 新策略只需 register
StrategyFactory.register("v7_structure", V7StructureStrategy)
StrategyFactory.register("v53_sop", V53SopStrategy)
StrategyFactory.register("v6_pyramid", V6PyramidStrategy)  # deprecated
# StrategyFactory.register("grid", GridStrategy)  # 未來擴充
```

bot.py 通過 `SIGNAL_STRATEGY_MAP` config 將信號類型映射到策略：

```python
SIGNAL_STRATEGY_MAP = {
    "2B": "v7_structure",       # V7 結構加倉（新）
    "EMA_PULLBACK": "v53_sop",
    "VOLUME_BREAKOUT": "v53_sop",
}
```

---

## 策略系統

### V7 Structure（結構驅動三段加倉）

#### 信號偵測

使用 Swing Point Pivot（左 7 根 + 右 3 根確認），穿透深度 ≥ 0.3 ATR 過濾噪音。

- **Bullish 2B**：價格跌破 confirmed swing low 後放量收回
- **Bearish 2B**：價格突破 confirmed swing high 後放量收回

#### 三段結構加倉

| Stage | 觸發 | 倉位 | 止損 |
|-------|------|------|------|
| 1 — 建倉 | 2B 信號 | `risk_per_trade` (1.7%) | swing point ± ATR buffer |
| 2 — 第一加倉 | Lower High（SHORT）/ Higher Low（LONG）+ 順勢K + 量能 | `risk_per_trade` (1.7%) | 新 swing point |
| 3 — 第二加倉 | 再次結構確認 | `risk_per_trade` (1.7%) | 最新 swing point |

加倉三條件 AND：**Swing Point 確認 + 順勢K（body/range ≥ 0.3）+ 量能（≥ vol_ma）**

#### 出場機制

1. **結構 Trailing SL**：棘輪追蹤最新 swing point（只往有利方向移動）
2. **反向 2B**：穿透深度 ≥ 0.3 ATR + 下根確認 → 全平
3. **Stage 1 超時**：36h 未觸發加倉 → 平倉釋放資金

### V53 SOP（分批減倉）

| 階段 | 觸發 | 動作 |
|------|------|------|
| 1.0R | 獲利達 1R | 移損至 +0.3R |
| 1.5R | 獲利達 1.5R | 減倉 30%，移損至 +1.0R，啟動 ATR trailing |
| 2.0R | 獲利達 2.0R | 減倉 30%，移損至 +1.5R |
| Structure Break | 連續 2 根收破 swing | 全平 |

### V6 Pyramid（已廢棄，歷史參考）

既有 V6 持倉仍可正常平倉，新進場不再使用。

---

### 風控 Guard（Risk Guard V1）

| Guard | 規則 | 效果 |
|-------|------|------|
| BTC Trend Filter | BTC 1D EMA20 < EMA50 時禁 LONG | 避免逆勢進場 |
| SL Distance Cap | SL 距離 > 6% 入場價 → 跳過 | 防止大波動吃大虧 |
| Symbol Cooldown | 同幣虧損後 24h 內不再進場 | 防連虧 |

---

## Market Scanner

四層過濾，從 Binance Futures 全市場篩選最符合 2B 策略的標的。

| Layer | 功能 | 關鍵參數 |
|-------|------|----------|
| 1 | 流動性過濾 | 24H 量 ≥ $30M |
| 2 | 動能篩選 | ADX ≥ 20，RSI 40~70，ATR% 1.5~15 |
| 3 | 形態匹配 | Swing Pivot + 量能確認 |
| 4 | 相關性過濾 | 同板塊 ≤ 2，相關性 < 0.7 |

輸出 `hot_symbols.json`，Bot 啟動時讀取。Scanner 永遠連接 Binance 正式網取得真實數據。

---

## 風險管理

### 倉位計算

```
V7:  每段 = balance × risk_per_trade / sl_distance_pct（每次加倉獨立計算）
V53: size = risk_amount / stop_distance，上限 equity × V53_CAP(10%) × leverage
```

### Tier 系統

| Tier | 倍率 | 影響 |
|------|------|------|
| A | 1.0x | 全額進場 |
| B | 0.7x | 縮小倉位 |
| C | 0.5x | 最小倉位 |

### 風險上限

| 參數 | 值 |
|------|------|
| `RISK_PER_TRADE` | 1.7%（V7 每段加倉） |
| `MAX_TOTAL_RISK` | 6.42%（三段合計上限） |
| `V53_EQUITY_CAP_PERCENT` | 10% |
| `MAX_POSITIONS_PER_GROUP` | 6 |
| `LEVERAGE` | 3x |

---

## 配置系統

`ConfigV6` 集中管理所有參數。

**載入流程**：`ConfigV6.load_from_json("bot_config.json")` → 自動讀取 `secrets.json` → `validate()`

JSON key 自動映射大寫（`risk_per_trade` → `RISK_PER_TRADE`）。

### 關鍵參數

| 參數 | 值 | 說明 |
|------|------|------|
| `PYRAMID_ENABLED` | true | 三段滾倉 |
| `SWING_LEFT_BARS` / `RIGHT` | 7 / 3 | Swing Point 確認 |
| `SL_ATR_BUFFER` | 0.8 | 止損 ATR 緩衝 |
| `V6_BREAKEVEN_MFE_R` | 1.5 | Tier 1 保本觸發（MFE≥1.5R） |
| `MIN_FAKEOUT_ATR` | 0.3 | 2B 最小穿透深度 |
| `BTC_TREND_FILTER_ENABLED` | true | BTC 趨勢過濾 |
| `MAX_SL_DISTANCE_PCT` | 0.06 | SL 距離上限 |
| `SYMBOL_LOSS_COOLDOWN_HOURS` | 24 | 同幣虧損冷卻 |

### 策略映射

```python
SIGNAL_STRATEGY_MAP = {
    "2B": "v7_structure",       # 結構加倉
    "EMA_PULLBACK": "v53_sop",  # 分批減倉
    "VOLUME_BREAKOUT": "v53_sop",
}
```

---

## 持久化與 Crash Recovery

### Atomic Write

```
1. 寫入 .positions.json.tmp_XXXX
2. flush + fsync
3. rename → positions.json（OS atomic）
```

### 啟動恢復

1. `_restore_positions()` — 從 `positions.json` 恢復 PositionManager + Strategy state
2. `_adopt_ghost_positions()` — 接管交易所未追蹤的倉位
3. 主循環 `_sync_exchange_positions()` — 四重防護 reconciliation

### 四重同步防護

| # | 觸發 | 行為 |
|---|------|------|
| 1 | API 錯誤 | 跳過同步（防誤殺） |
| 2 | bot 有、exchange 無 | hard_stop_hit → 平倉 |
| 3 | size 差 > 5% | 告警 SIZE_MISMATCH |
| 4 | exchange 有、bot 無 | 告警 GHOST_POSITION |

### 平倉失敗保護

`_handle_close()` try-except + rollback：失敗 → `pm.is_closed = False` → 下週期重試。

---

## 測試

348 個 pytest，全部通過。

```bash
python3 -m pytest trader/tests/ -v
python3 -m pytest trader/tests/test_v7_structure.py -v  # V7 單一模組
```

### 測試覆蓋

| 模組 | 數量 | 覆蓋 |
|------|------|------|
| `test_v7_structure.py` | 28 | V7 加倉觸發 / SL 棘輪 / 反向 2B / 超時 / sizing |
| `test_structure.py` | 9 | Swing Point / Neckline |
| `test_signals.py` | 13 | 2B Bullish/Bearish / 穿透過濾 |
| `test_risk.py` | 13 | Stage sizing / risk cap |
| `test_persistence.py` | 30 | Atomic write / 出場決策 16 場景 |
| `test_integration.py` | 18 | StatefulMockEngine + FaultInjector |
| `test_risk_guard.py` | 16 | BTC Filter / SL Cap / Cooldown |
| `test_v7p2.py` | 16 | Strategy dispatch |
| `test_tier_equity_balance.py` | 13 | Tier mult / equity cap |
| `test_reverse_2b_exit.py` | 9 | 穿透深度 + 雙根確認 |
| 其他 16 個模組 | 183 | 各子系統 |

---

## 技術棧

| 類別 | 技術 |
|------|------|
| 語言 | Python 3.10+ |
| 交易所 | CCXT + Binance Futures 直接 API（HMAC 簽章） |
| 指標 | pandas-ta（EMA / ATR / ADX / RSI） |
| 數據 | pandas + numpy |
| 通知 | Telegram Bot API |
| 測試 | pytest（348 tests） |
| 持久化 | JSON (atomic write) + SQLite (performance + scanner) |

---

## 日誌

`.log/` 目錄，`RotatingFileHandler`（5MB × 3 備份）：

| 檔案 | 說明 |
|------|------|
| `v6_bot.log` | 主日誌（信號/開倉/移損/平倉） |
| `v6_trades.log` | 純交易記錄（`[TRADE]` 標籤） |
| `scanner.log` | Scanner 掃描日誌 |

---

## 注意事項

- `secrets.json` 含 API keys，**不可** commit
- Scanner 永遠連 Binance 正式網；Bot 的 `sandbox_mode` 只控制下單端
- `V6_DRY_RUN=True` 時所有訂單只 log 不送出
- 硬止損掛單確保 Bot 斷線時交易所仍執行止損
