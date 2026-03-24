# Trading Bot — Code Review & Roadmap

> 小波 | 初版 2026-02-17 | 更新 2026-03-24
> 架構參考：`project_structure_map_v3.md`

---

## 大目標

**穩定獲利、機靈止損，要吃下高評級倉位的大波段獲利。**

---

## 整體評價：8.0 / 10（2026-03-24 更新，V7 上線）

| 維度 | 原分 | 前次 | 現分 | 扣分原因 |
|------|------|------|------|---------|
| 架構設計 | 8 | 9.5 | **8.5** | bot.py 仍是 monolith（Phase 3 未做）+ Config global state（待修 #4） |
| 策略邏輯 | 8.5 | 7.0 | **8.0** | V7 結構加倉上線，V6 軟廢棄。待 Testnet 驗證加倉觸發率 |
| 測試覆蓋 | 5 | 8.5 | **7.5** | 無 coverage %、無 E2E test |
| 錯誤處理 | 6.5 | 8.0 | **7.0** | API client error 不一致（待修 #11）+ 無滑價保護（待修 #9） |
| 可維護性 | 6 | 9.0 | **8.0** | monolith 未拆 |
| 文件品質 | 9 | 9.0 | **8.0** | 無 operational runbook、無 ADR |

> **8.0 = 結構清晰、策略完整的個人量化系統。** V7 Testnet 驗證通過 + 待修問題修完 → 8.5 合理。

---

## ✅ 已完成

### Trader Refactor（2026-03-16，Phase 0 + 1 + 2）

- [x] **Phase 0 — `v6/` → `trader/` 改名**：全 import 替換 + systemd + scanner import
- [x] **Phase 1 — 安全瘦身（-286 行）**
  - 刪除 `technical.py` 3 個死函數（detect_2B_signal / detect_ema_pullback_signal / detect_volume_breakout_signal）
  - 移除 `bot.py` V5.3 EMA/Volume 信號 fallback 分支
  - 移除 `config.py` 4 個孤兒參數
  - 刪除 `positions.py` 3 個 deprecated wrapper（monitor_v6/monitor_v53/_get_exit_decision）
  - 提煉 `bot.py` 6 個 helper（_check_btc_trend / _refresh_stop_loss / _get_close_side / _validate_position_size / _calculate_pnl / _build_log_base）
- [x] **Phase 2 — 策略插件化架構**
  - `Action` enum（HOLD / CLOSE / PARTIAL_CLOSE / ADD / UPDATE_SL）取代硬編碼字串
  - `DecisionDict` 通用化（+ close_pct / add_stage 欄位）
  - `StrategyFactory` Registry 模式（register → create）
  - `strategy_name` 取代 `is_v6_pyramid`（PM 保留 property 向下相容）
  - V53 內部 state（is_1r_protected 等）搬進策略，PM 透過 proxy property 存取
  - `get_state()` / `load_state()` 介面實現策略 state 序列化
  - `SIGNAL_STRATEGY_MAP` config 化（Signal → Strategy 映射）
  - bot.py action dispatch 改為 generic（`Action.CLOSE` / `Action.ADD` / `Action.PARTIAL_CLOSE`）
- [x] **Phase 3（bot.py 拆分）延後**：等 Shadow Mode 需要時再做
- [x] 259 tests passed ✅

### 穩定性修補（全清）

- [x] 下單 error handling（OrderExecutionEngine + _handle_close rollback + re-raise）
- [x] 出場邏輯 12 tests（4H EMA20 / 反向 2B / 快速止損 / 時間退出 / PROFIT_PULLBACK）
- [x] Graceful shutdown（SIGTERM → KeyboardInterrupt → flush positions）
- [x] `initial_r` 除以 0 guard（positions.py 三處）

### 架構重構

- [x] core.py 1009 行 → 四層：infrastructure / indicators / risk / execution
- [x] Stage trigger 封裝至 PositionManager（`check_stage2/3_trigger()`）
- [x] MarketDataProvider 統一 retry + sandbox fallback
- [x] OrderExecutionEngine 獨立下單層
- [x] 出場決策集中化（`_get_exit_decision()` → 結構化 dict → 後續 Phase 2 升級為 Action enum）

### 數據基建

- [x] `v6_performance.db`（MFE / MAE / capture_ratio / stage_reached / market_regime）
- [x] entry_adx / fakeout_depth_atr / btc_trend_aligned / reverse_2b_depth_atr 持久化
- [x] Tier 診斷欄位（trend_adx / mtf_aligned / volume_grade / tier_score）

### V7 P1（Rate Limit 防禦 + 平倉優先化）

- [x] `api_client.py`：`_current_weight` 追蹤 + 超限 sleep
- [x] `bot.py`：`_handle_close` 翻轉（先平倉後取消止損）+ `pending_stop_cancels` 背景清理

### V7 P2（Strategy Pattern → Phase 2 升級為 Registry）

- [x] `TradingStrategy` ABC + `StrategyFactory`
- [x] `V6PyramidStrategy` / `V53SopStrategy` 策略抽離
- [x] `monitor()` 回傳 `DecisionDict`，bot.py generic dispatch

### Ghost Position Fix（四重防護 + Adoption）

- [x] `_sync_exchange_positions()` 四重防護（API error guard / hard_stop / size mismatch / ghost）
- [x] `_adopt_ghost_positions()`：啟動時接管孤立倉位

### Signal Quality（三方整合）

- [x] Strategy Dispatch bug fix（V53 恢復正常）
- [x] profit_pullback MFE 門檻（≥ 0.3R 才啟用）
- [x] 2B 穿透深度下限（< 0.3 ATR 視為噪音）

### Structure Trailing BOS + V5.3 修復

- [x] Temporal BOS：HL → BOS target → close 突破才移損
- [x] V5.3 Dead Zone Fix：2.5R → 2.0R 縮小死區 + 1.5R 後啟動 trailing

### Risk Guard V1（三道防線）

- [x] BTC Trend Filter（逆勢 mult=0 禁入）
- [x] SL Distance Cap（> 6% → 跳過）
- [x] Symbol Loss Cooldown（虧損後 24h 冷卻）

### V7 結構加倉策略（2026-03-24，S1+S2+S3）

- [x] `v7_structure.py`：三條件 AND 加倉（Swing Point + 順勢K body/range≥0.3 + 量能）
- [x] SL 棘輪（只往有利方向）+ 結構 Trailing SL
- [x] 反向 2B 全平 + Stage 1 超時退出
- [x] `calculate_add_size()` static method（risk_per_trade 獨立計算，每段 1.7%）
- [x] bot.py `_handle_stage2/3` V7 分支 + `_calc_total_risk_pct()` helper
- [x] V6 軟廢棄（`SIGNAL_STRATEGY_MAP["2B"] = "v7_structure"`，代碼保留）
- [x] 348 tests passed ✅

### V6 Three-Tier Defense（2026-03-20）

- [x] **移除 profit_pullback**（avg capture 0.3，Stage 2 到達率 8.3%，平均持倉 42 分鐘 — 結構性缺陷）
- [x] **Tier 1 — 保本移損（Breakeven Bridge）**：MFE ≥ 1.5R → SL 移 entry + 0.1R（一次性棘輪）
- [x] **Tier 2 — 加速結構追蹤**：Stage 1 用 `get_fast_trailing_swing()`（right=2, 無 BOS）
- [x] **Tier 3 — 標準追蹤**：Stage 2+ 沿用現有 left=7 right=3 + BOS + 4H EMA20
- [x] 281 tests passed ✅

### 其他已完成

- [x] close_position rollback tests
- [x] perf_db 資料品質修正（capture_ratio + MFE/MAE fallback）
- [x] Fill Price Fix（avgPrice 擷取）
- [x] V53 Partial Close PnL 追蹤（original_size + realized_partial_pnl）
- [x] Reverse 2B Exit 雙根確認
- [x] Stage 2 timeout bug fix + Neckline 重新定義
- [x] V53 disable early_stop_r + N-bar 確認
- [x] Tier & Equity Balance Fix + V6 Sizing Upgrade
- [x] Integration Test（StatefulMockEngine + FaultInjector）

---

## 核心亮點

| 亮點 | 與大目標的關係 |
|------|---------------|
| **策略拔插**：Registry 模式，新策略不動 bot.py | 擴展性 + 穩定 |
| **風險壓縮**：加倉三次 total risk ≤ initial_R | 穩定獲利 + 大波段 |
| **結構分析**：Swing Point + Temporal BOS 追蹤 | 機靈止損 |
| **三道風控**：BTC Filter + SL Cap + Cooldown | 穩定獲利 |
| **Crash Recovery**：atomic write + 四重同步 + ghost adoption | 穩定獲利 |
| **雙策略**：V7 結構加倉 / V53 SOP 共用 PositionManager | 穩定獲利 |

---

## 待修問題

### 中優先

| # | 問題 | 威脅 |
|---|------|------|
| 4 | Config global state（難測試、不支援多 instance） | 穩定：測試品質受限 |
| 9 | ATR 滑價侵蝕 R-value（市價單） | 穩定：實際 R 低於預期 |

### 低優先

| # | 問題 |
|---|------|
| 11 | BinanceFuturesClient error handling 不一致（包 dict） |

### ✅ 已修復

| # | 問題 | 修復日期 |
|---|------|---------|
| 6 | ~~Scanner 無 pytest~~ | 2026-03-21（19 tests） |
| 10 | ~~Magic numbers 散落~~ | 2026-03-21（8 個 → Config） |
| 12 | ~~positions.json 無版本號~~ | 2026-03-21（schema v2 envelope） |

---

## Roadmap

```
=== 已完成 ===
穩定性修補 → V7 P1/P2 → Ghost Fix → Signal Quality → Risk Guard V1
→ Integration Test → Trader Refactor (Phase 0+1+2) → Three-Tier Defense (3/20)

=== 你在這裡 === V7 上線，等 Testnet 驗證
  |
Testnet 驗證 V7 加倉觸發 + SL 棘輪行為
  |
4/3 分析：V7 Testnet 表現 + Capture ratio ATR 回測
  |
V7 P3（ATR 滑價保護）
  |
=== 中期 ===
決策品質驗證 → 資本重分配 → 參數微調
  |
=== 長期 ===
重構 + 效能 → Shadow Mode（需 Refactor Phase 3）→ DEX 遷移
  |
  ├── [獨立] Refactor Phase 3：bot.py 拆分（Shadow Mode 前置）
  ├── [獨立] Scanner pytest
  └── [選做] Remote Monitoring QMD endpoint
```

---

### 近期：4/3 數據分析（3 Phase）

**P1 驗證（4/3 當天）**
- V7 Testnet 驗證：加倉觸發率、SL 棘輪行為、capture ratio vs V6 歷史
- Three-Tier Defense 持續追蹤：Stage 2 到達率、avg hold、Tier 1/2 分析

**P2 優化（P1 通過才做）**
- Capture ratio → ATR mult 回測（低 cap <0.3，ATR×2.0 vs 1.5）
- Time exit 後續表現（需寫工具記錄 exit 後 4h/12h/24h 價格）

**P3 基建（獨立時間線）**
- Trader Refactor Phase 3：bot.py 拆分（Shadow Mode 前置）

---

### V7 P3：ATR 動態滑價保護

| 項目 | 做法 |
|------|------|
| 限價 | 買單 = `ref_price + ATR_SLIPPAGE_TOLERANCE * atr`，IOC 模式 |
| Partial Fill | 市價補齊剩餘，加權均價傳入 PM |
| 影響 | `order_engine.py` `config.py` `bot.py` |

---

### 中期

| 階段 | 內容 |
|------|------|
| 決策品質驗證 | Tier A EV（PF>1.8, Sharpe>1.5）/ 滾倉 vs 固定 / Regime 有效性 |
| 資本重分配 | 動態風險乘數 + 總曝險 + 板塊持倉上限 |
| 參數微調 | SL_ATR_BUFFER / Stage3_Ratio / Trailing（*嚴禁動核心 2B 結構邏輯*）|

---

### 長期

| 階段 | 內容 |
|------|------|
| 重構 + 效能 | Config DI + StructureAnalysis caching + Scanner async |
| Shadow Mode | 主 + 影子並行，30 天穩定優於主 → 切換。前提：Refactor Phase 3 |
| DEX 遷移 | dYdX / GMX / Hyperliquid。插件化已就緒 |

---

### 當前 Config（Testnet 基準）

| 參數 | 值 | 說明 |
|------|----|------|
| ATR_MULTIPLIER | 1.5 | SL 較寬鬆 |
| SL_ATR_BUFFER | 0.8 | 結構追蹤緩衝 |
| EARLY_STOP_R_THRESHOLD | 0.75 | 呼吸空間 |
| V6_BREAKEVEN_MFE_R | 1.5 | Tier 1 保本觸發門檻 |
| V6_BREAKEVEN_BUFFER_R | 0.1 | 保本 SL 緩衝（entry + 0.1R） |
| V6_FAST_TRAIL_RIGHT_BARS | 2 | Tier 2 加速追蹤右側確認 |
| V6_4H_EMA20_FORCE_EXIT | false | 暫停 |
| V6_STAGE1_MAX_HOURS | 36 | Stage 1 等待 |
| V7_STAGE1_MAX_HOURS | 36 | V7 Stage 1 等待 |
| V7_STAGE_VOLUME_MULT | 1.0 | V7 加倉量能門檻（volume/vol_ma）|
| STAGE2_VOLUME_MULT | 1.2 | Stage 2 觸發 |
| EQUITY_CAP_PERCENT | 0.20 | V6 三段上限 |
| V53_EQUITY_CAP_PERCENT | 0.10 | V5.3 獨立 cap |
| STAGE1/2/3_RATIO | 0.33/0.37/0.30 | Stage 分配 |
| MIN_FAKEOUT_ATR | 0.3 | 2B 最小穿透 |
| REVERSE_2B_MIN_FAKEOUT_ATR | 0.3 | 出場 2B 穿透 |
| V6_FAST_TRAIL_REQUIRE_BOS | false | Tier 2 不要求 BOS |
| BTC_TREND_FILTER_ENABLED | true | BTC 過濾 |
| BTC_COUNTER_TREND_MULT | 0.0 | 逆勢禁入 |
| MAX_SL_DISTANCE_PCT | 0.06 | SL 上限 6% |
| SYMBOL_LOSS_COOLDOWN_HOURS | 24 | 冷卻 |
