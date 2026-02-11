# Trading Bot v5.3 — 智能演算法交易系統

基於 2B 突破形態的加密貨幣自動交易系統，整合 Market Scanner 動態選股、多策略信號偵測、分級入場與統一出場 SOP。

---

## 架構總覽

```
trading-bot-v5.2/
├── trading_bot_gui_v5.2.py      # GUI 控制面板 (CustomTkinter)
├── trading_bot_v5.2_optimized.py # 交易核心引擎
├── scanner/
│   ├── market_scanner.py         # 4 層市場掃描器
│   ├── scanner_config.json       # Scanner 配置
│   └── README.md                 # Scanner 文件
├── bot_config.json               # 交易機器人配置 (GUI 自動管理)
└── requirements.txt              # Python 依賴
```

### 核心類別

| 類別 | 位置 | 職責 |
|------|------|------|
| `Config` | 引擎 | 統一配置管理，JSON ↔ 屬性自動映射 (`_KEY_MAP`) |
| `RiskManager` | 引擎 | 倉位計算、總風險檢查、餘額查詢 |
| `TechnicalAnalysis` | 引擎 | 2B 偵測、EMA 回撤、量能突破、結構破壞 |
| `SignalTierSystem` | 引擎 | A/B/C 信號分級評分 |
| `MarketFilter` | 引擎 | ADX / ATR / EMA 糾纏度三重過濾 |
| `DynamicThresholdManager` | 引擎 | 根據市場波動動態調整 ADX、ATR 閾值 |
| `TradeManager` | 引擎 | 單筆交易管理（出場 SOP、硬止損、減倉） |
| `TradingBotV53` | 引擎 | 主循環：掃描→開倉→監控→出場 |
| `ModernTradingBotGUI` | GUI | 8 分頁控制面板、色彩日誌、HUD 儀表板 |

---

## 快速開始

### 環境需求

- Python 3.10+
- 建議在 Linux / macOS 上執行（Windows 亦可）

### 安裝依賴

```bash
pip install -r requirements.txt
```

### 啟動 GUI

```bash
python trading_bot_gui_v5.2.py
```

### 首次設定流程

1. 在「API 連線」分頁輸入 Binance Futures Testnet 的 API 金鑰和密鑰
2. 確認開啟「測試網模式」（預設已開啟）
3. 在「交易設定」設定交易對、方向與槓桿
4. 在「風險管理」調整單筆風險與總風險上限
5. 點擊 HUD 區「儲存設定」
6. 點擊「啟動系統」連線交易所，確認帳戶資訊
7. 點擊「開始交易」啟動自動掃描與下單

---

## 兩階段操作流程

| 階段 | 按鈕 | 說明 |
|------|------|------|
| 階段一 | `▶ 啟動系統` | 連線交易所，顯示帳戶餘額與現有持倉，不執行交易 |
| 階段二 | `▶ 開始交易` | 確認連線正確後，啟動自動信號掃描與交易 |
| 緊急操作 | `⚠ 全部平倉` | 一鍵關閉所有倉位（需確認） |
| 停止 | `■ 停止系統` | 停止交易並斷開連線 |

---

## 多策略信號偵測

系統同時執行三種策略，每個掃描周期選擇最優信號：

| 策略 | 優先級 | 說明 |
|------|--------|------|
| 量能突破 | 1 (最高) | 異常放量（2x+ 均量）突破近期高低點，signal_strength 固定為 strong |
| 2B 突破 | 2 | 假突破反轉形態：價格穿破近期極值後收回，配合量能分級過濾 |
| EMA 回撤 | 3 | 趨勢中回撤至 EMA 10/20 後反彈，接受較低量能門檻（0.6x） |

### 信號分級 (A/B/C)

根據以下維度綜合評分，決定入場倉位大小：

| 評分項目 | 分數 | 條件 |
|----------|------|------|
| MTF 對齊 | +2 | 4H EMA 結構確認交易方向 |
| 市場強度 | +2 | ADX >= 25（強趨勢） |
| 量能等級 | +2 / +1 | 爆發/強勢 +2，中等 +1 |
| K 線確認 | +1 | 當前 K 線收盤方向與信號一致 |

| 等級 | 總分 | 倉位乘數 |
|------|------|----------|
| A 級 | 6+ 分 | 100% (`TIER_A_POSITION_MULT`) |
| B 級 | 4-5 分 | 70% (`TIER_B_POSITION_MULT`) |
| C 級 | <4 分 | 50% (`TIER_C_POSITION_MULT`) |

> v5.3 重要變更：信號等級僅影響倉位大小，所有等級共用相同的出場 SOP。

---

## v5.3 統一出場 SOP

所有持倉不論信號等級，均按照以下流程管理：

```
入場
 │
 ├── 1.0R 獲利 → 移損至 +0.3R（保護性移損，不減倉）
 │
 ├── 1.5R 獲利 → 第一次減倉 30% + 移損至 +0.5R
 │
 ├── 2.5R 獲利 → 第二次減倉 30% + 移損至 +1.5R + 啟動 ATR 追蹤止損
 │
 ├── 尾倉 40% → ATR 追蹤止損管理（ATR × 1.5 乘數）
 │
 ├── 結構破壞 → 價格跌破/突破近 10 根 K 線極值 0.5%，全部平倉
 │
 └── 超時退出 → 持倉超過 24 小時且未達 1.5R，市價全部平倉
```

### 出場階段詳情

| 階段 | 觸發條件 | 操作 | 剩餘倉位 |
|------|----------|------|----------|
| 移損保護 | 浮盈 >= 1.0R | 止損移至 entry + 0.3R | 100% |
| 首次減倉 | 浮盈 >= 1.5R | 減倉 30%，止損移至 entry + 0.5R | 70% |
| 二次減倉 | 浮盈 >= 2.5R | 再減 30%，止損移至 entry + 1.5R，啟動追蹤 | 40% |
| 追蹤止損 | 2.5R 後持續 | ATR × 1.5 追蹤止損管理尾倉 | 40% → 0% |
| 結構破壞 | 近期結構破裂 | 全部平倉 | 0% |
| 超時出場 | > MAX_HOLD_HOURS | 市價全平 | 0% |

---

## 風控機制

### 倉位計算

```
position_size = (balance × RISK_PER_TRADE) / |entry_price - stop_loss|
```

- 套用信號等級乘數（A=1.0, B=0.7, C=0.5）
- 上限：`MAX_POSITION_PERCENT × LEVERAGE × balance`
- 止損距離由 ATR 動態決定

### 總風險計算

系統逐筆計算每個持倉的**實際剩餘風險**：

```
單筆風險 = current_size × |entry_price - current_sl|
```

- 已減倉的部位：`current_size` 自動反映剩餘數量
- 止損已移至獲利區（risk <= 0）：風險計為 0
- 已關閉部位：跳過
- 總風險 = 所有部位加總 / 帳戶餘額，需 <= `MAX_TOTAL_RISK`

### 其他風控

| 機制 | 說明 |
|------|------|
| 硬止損單 | 在 Binance Futures Testnet 設置 `STOP_MARKET` 條件單（直接 API，非 ccxt） |
| 重複開倉防護 | 同一標的不重複建倉 |
| 每組最大持倉 | `MAX_POSITIONS_PER_GROUP` 獨立閘門 |
| 單筆倉位上限 | 不超過帳戶餘額 × 槓桿的指定百分比 |

---

## 市場過濾器

進場前的三重過濾，拒絕不適合交易的市場環境：

| 層級 | 指標 | 邏輯 | 預設閾值 |
|------|------|------|----------|
| 1 | ADX | 趨勢強度不足 → 拒絕 | >= 20（動態下限 18） |
| 2 | ATR 突刺 | 當前 ATR > 均值 × 乘數 → 拒絕 | 乘數 2.0 |
| 3 | EMA 糾纏 | EMA 10/20 距離 < 閾值 → 拒絕 | 2% |

### 動態閾值系統

`DynamicThresholdManager` 根據市場狀態自動調整：

- **低波動**：ATR 乘數降為 1.2，放寬信號要求
- **正常**：ATR 乘數 1.5
- **高波動**：ATR 乘數升至 2.0，收緊過濾

---

## 量能分級系統

根據成交量與均量的比值，將量能分為四級：

| 等級 | 條件（vs 均量） | 預設閾值 |
|------|-----------------|----------|
| 爆發 (explosive) | >= 2.5x | `VOL_EXPLOSIVE_THRESHOLD` |
| 強勢 (strong) | >= 1.5x | `VOL_STRONG_THRESHOLD` |
| 中等 (moderate) | >= 1.0x | `VOL_MODERATE_THRESHOLD` |
| 弱勢 (weak) | >= 0.7x | `VOL_MINIMUM_THRESHOLD` |

低於最低閾值的信號預設仍可接受（`ACCEPT_WEAK_SIGNALS = True`）。

---

## Market Scanner 整合

Scanner 作為獨立選股器，自動從全市場 USDT 交易對中篩選潛力標的：

```
Layer 1: 流動性過濾 (24H 成交量)
Layer 2: 動量過濾 (ADX/RSI/ATR/EMA)
Layer 3: 形態匹配 (2B 信號 + Swing Point)
Layer 4: 相關性過濾 (板塊分散)
```

### 啟用 Scanner 聯動

1. 在 GUI「市場掃描」分頁點擊「立即掃描」
2. 勾選「使用掃描結果作為交易標的」
3. 儲存設定 → 啟動系統

Scanner 掃出的 Top 10 標的會取代靜態交易對清單。結果有效期預設 30 分鐘，過期則退回靜態清單。

### 獨立執行 Scanner

```bash
# 單次掃描
python -m scanner.market_scanner --once

# 持續掃描 (預設每 15 分鐘)
python -m scanner.market_scanner

# 自訂配置
python -m scanner.market_scanner --config scanner/scanner_config.json
```

### GUI 掃描功能

- 手動 / 自動定時掃描（可設間隔分鐘數）
- 市場概況：市場狀態、BTC 趨勢、平均 ADX、多空比例
- 掃描結果表：排名、標的、方向、評分、入場價、止損價、R/R、量能等級
- 預警信號（Pre-2B）列表
- 結果匯出為 JSON

---

## GUI 控制面板

### 整體設計

- **主題**：FinTech 深色主題 (`#080B11` 四層深度背景)
- **框架**：CustomTkinter 現代化元件
- **跨平台字體**：Linux (Noto Sans CJK TC)、macOS (PingFang TC)、Windows (Microsoft JhengHei UI)
- **日誌**：色彩分級 — 時間灰色、資訊/成功/警告/錯誤各有專色，自動裁剪超過 1500 行

### HUD 儀表板

頂部即時顯示 5 項指標：

| 指標 | 說明 |
|------|------|
| 帳戶餘額 | USDT 餘額 |
| 活躍持倉 | 當前 / 最大持倉數 |
| 24H 盈虧 | 未實現盈虧 |
| 信號產生 | 本次運行偵測到的信號數 |
| 掃描標的 | Scanner 掃描的標的數量 |

### 8 個設定分頁

| 分頁 | 內容 |
|------|------|
| **API 連線** | 交易所選擇（Binance/Bybit/OKX/Bitget）、API 金鑰、測試網開關、Telegram 通知設定 |
| **交易設定** | 交易模式（Spot/Future）、方向（Long/Short/Both）、槓桿（1-20x）、硬止損開關、交易對文字框、檢查間隔、最大持倉時間 |
| **風險管理** | 單筆風險（滑桿 1%-10%）、最大總風險（1%-20%）、最大持倉數（1-10）、單筆最大倉位%（10%-50%）、技術指標參數（回溯週期、均量週期、ATR 週期/乘數） |
| **市場過濾** | 市場過濾開關、ADX 閾值（5-40）、ATR 突出乘數（1.0-5.0）、均線糾纏閾值（0.01-0.10） |
| **量能分級** | 量能分級開關、爆發/強勢/中等/最低閾值滑桿、弱勢信號接受開關 |
| **出場設定** | 1.5R 減倉比例（10%-60%）、2.5R 減倉比例（10%-60%）、出場 SOP 流程說明 |
| **進階功能** | MTF 確認 / 動態閾值 / 分級入場 / EMA 回撤 / 量能突破 / 結構破壞出場 — 6 個功能開關 + A/B/C 倉位乘數滑桿 |
| **市場掃描** | 立即掃描 / 停止 / 自動掃描開關、掃描間隔、市場概況、Top 10 結果表、Pre-2B 預警、匯出按鈕、使用掃描結果 Checkbox |

---

## 完整配置參數

所有配置透過 GUI 管理，存於 `bot_config.json`。Config 類使用 `_KEY_MAP` 字典自動映射 JSON key 到類屬性。

### API 與連線

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 交易所 | `exchange` | binance / bybit / okx / bitget | binance |
| 測試網 | `sandbox_mode` | 是否使用測試網 | true |
| Telegram | `telegram_enabled` | 啟用通知 | false |

### 交易模式

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 交易模式 | `trading_mode` | future / spot | future |
| 交易方向 | `trading_direction` | both / long_only / short_only | both |
| 槓桿 | `leverage` | 1-20x | 5 |
| 硬止損 | `use_hard_stop_loss` | 交易所端止損單 | true |
| 檢查間隔 | `check_interval` | 秒 | 60 |

### 風險管理

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 單筆風險 | `risk_per_trade` | 每筆交易最大虧損占比 | 0.01 (1%) |
| 總風險上限 | `max_total_risk` | 所有持倉最大風險占比 | 0.05 (5%) |
| 最大持倉數 | `max_positions_per_group` | 同時持倉上限 | 2 |
| 單筆倉位上限 | `max_position_percent` | 單筆最大使用餘額比例 | 0.30 (30%) |

### 技術指標

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 回溯週期 | `lookback_period` | Swing Point 回溯 K 線數 | 20 |
| 均量週期 | `volume_ma_period` | 成交量移動平均週期 | 20 |
| ATR 週期 | `atr_period` | ATR 計算週期 | 14 |
| ATR 乘數 | `atr_multiplier` | 止損距離 = ATR × 乘數 | 1.5 |

### 市場過濾

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 啟用過濾 | `enable_market_filter` | 總開關 | true |
| ADX 閾值 | `adx_threshold` | 趨勢強度最低門檻 | 20 |
| ATR 突刺乘數 | `atr_spike_multiplier` | 極端波動過濾 | 2.0 |
| EMA 糾纏閾值 | `ema_entanglement_threshold` | 盤整過濾 | 0.02 |

### 量能分級

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 啟用分級 | `enable_volume_grading` | 總開關 | true |
| 爆發量 | `vol_explosive_threshold` | >= 2.5x 均量 | 2.5 |
| 強勢量 | `vol_strong_threshold` | >= 1.5x 均量 | 1.5 |
| 中等量 | `vol_moderate_threshold` | >= 1.0x 均量 | 1.0 |
| 最低量 | `vol_minimum_threshold` | >= 0.7x 均量 | 0.7 |
| 接受弱勢 | `accept_weak_signals` | 允許低量能信號 | true |

### v5.3 出場 SOP

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 1.5R 減倉% | `first_partial_pct` | 第一次減倉比例 | 30 |
| 2.5R 減倉% | `second_partial_pct` | 第二次減倉比例 | 30 |
| 追蹤 ATR 乘數 | `aplus_trailing_atr_mult` | 追蹤止損距離 | 1.5 |
| 最大持倉時間 | `max_hold_hours` | 超時強制平倉（小時） | 24 |

### 進階功能開關

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| MTF 確認 | `enable_mtf_confirmation` | 4H 多時間框架確認 | true |
| 動態閾值 | `enable_dynamic_thresholds` | ADX/ATR 自動調整 | true |
| 分級入場 | `enable_tiered_entry` | A/B/C 信號分級 | true |
| EMA 回撤 | `enable_ema_pullback` | EMA 回撤策略 | true |
| 量能突破 | `enable_volume_breakout` | 量能突破策略 | true |
| 結構破壞 | `enable_structure_break_exit` | 結構破壞出場 | true |

### 分層倉位

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| A 級乘數 | `tier_a_position_mult` | 最佳信號倉位 | 1.0 |
| B 級乘數 | `tier_b_position_mult` | 良好信號倉位 | 0.7 |
| C 級乘數 | `tier_c_position_mult` | 基本信號倉位 | 0.5 |

### Scanner 整合

| 參數 | JSON Key | 說明 | 預設值 |
|------|----------|------|--------|
| 使用 Scanner | `use_scanner_symbols` | 使用掃描結果作為標的 | false |
| 結果檔案 | `scanner_json_path` | Scanner 輸出路徑 | hot_symbols.json |
| 結果有效期 | `scanner_max_age_minutes` | 過期退回靜態清單 | 30 |

---

## 技術棧

| 套件 | 版本 | 用途 |
|------|------|------|
| ccxt | >= 4.0.0 | 交易所 API（含 Futures 支援） |
| pandas | >= 2.0.0 | 數據處理 |
| numpy | >= 1.24.0 | 數值運算 |
| pandas-ta | >= 0.3.14b | 技術指標（ADX、ATR、EMA、RSI） |
| customtkinter | >= 5.2.0 | 現代化 GUI 框架 |
| pillow | >= 10.0.0 | 圖像處理（GUI 支援） |
| requests | >= 2.28.0 | HTTP 請求（Telegram 通知 + Binance 直接 API） |
| python-dateutil | >= 2.8.0 | 日期處理 |

---

## v5.3 版本變更摘要

相對於 v5.2 的主要變更：

| 項目 | v5.2 | v5.3 |
|------|------|------|
| 出場機制 | A/B/C 三套不同出場流程 | 統一 SOP，信號等級僅影響倉位 |
| 出場階段 | 1.5R / 3.0R 減倉 | 1.0R 移損 → 1.5R 減倉 → 2.5R 減倉+追蹤 |
| 時間退出 | 無 | 24 小時超時強制平倉 |
| ADX 閾值 | 15 | 20（動態下限 18） |
| 結構破壞出場 | 預設關閉 | 預設開啟 |
| 總風險計算 | `持倉數 × 固定風險` | 逐筆計算實際剩餘風險（考慮減倉+移損） |

---

*文檔版本：v5.3*
*最後更新：2026-02-11*
