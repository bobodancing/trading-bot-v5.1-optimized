# Trading Bot V6.0 — Code Review & Roadmap

> 小波 | 初版 2026-02-17 | 更新 2026-03-14
> 架構參考：`project_structure_map_v3.md`

---

## 大目標

**穩定獲利、機靈止損，要吃下高評級倉位的大波段獲利。**

---

## 整體評價：8.0 / 10

| 維度 | 原分 | 現分 | 說明 |
|------|------|------|------|
| 架構設計 | 8 | **9.5** | core.py 拆四層 + v6/strategies/ 子目錄 + secrets 分離 |
| 策略邏輯 | 8.5 | 8.5 | 風險壓縮正確、三階段滾倉嚴謹 |
| 測試覆蓋 | 5 | **8.0** | 229 passed（+ btc_trend 18 + stage2_diagnosis 6 + stage2_neckline 9 + risk_guard 16 等），integration 仍缺 |
| 錯誤處理 | 6.5 | **8.0** | OrderEngine + _handle_close rollback + _sync 四重防護 + ghost adoption |
| 可維護性 | 6 | **8.5** | 模組拆分 + 策略可插拔 + 設定分層 |
| 文件品質 | 9 | 9 | README + project_structure_map_v3.md |

---

## ✅ 已完成

### 穩定性修補（全清）
- [x] 下單 error handling（OrderExecutionEngine + _handle_close rollback + re-raise）
- [x] 出場邏輯 12 tests（4H EMA20 / 反向 2B / 快速止損 / 時間退出 / PROFIT_PULLBACK）
- [x] Graceful shutdown（SIGTERM → KeyboardInterrupt → flush positions）
- [x] `initial_r` 除以 0 guard（positions.py 三處）

### 架構重構
- [x] core.py 1009 行 → 四層：infrastructure / indicators / risk / execution
- [x] Stage trigger 封裝至 PositionManager（`check_stage2/3_trigger()`）
- [x] MarketDataProvider 統一 retry + sandbox fallback（Scanner/Bot 共用）
- [x] OrderExecutionEngine 獨立下單層
- [x] 出場決策集中化（`_get_exit_decision()` → 結構化 dict）

### 數據基建
- [x] Phase 0：`v6_performance.db`（MFE / MAE / capture_ratio / stage_reached / market_regime）
- [x] 63 tests passed（Windows）

### V7 P1（Rate Limit 防禦 + 平倉優先化）
- [x] `api_client.py`：`_current_weight` 追蹤 + 超限 sleep 1s
- [x] `positions.py`：`pending_stop_cancels` 欄位 + to/from_dict + backward compat
- [x] `bot.py`：`_handle_close` 翻轉（先平倉後排隊取消）+ `monitor_positions` 背景清理
- [x] 3 個新 tests 全數通過（test_v7p1.py）

### V7 P2（Strategy Pattern 重構）
- [x] 新增 `v6/strategies/base.py`（TradingStrategy ABC + StrategyFactory）
- [x] 新增 `v6/strategies/v6_pyramid.py`（V6 出場邏輯）
- [x] 新增 `v6/strategies/v53_sop.py`（V5.3 出場邏輯）
- [x] `positions.py`：`_get_exit_decision` 邏輯全數搬出、`_exit_reason` 公開為 `exit_reason`、`monitor()` 回傳 Dict
- [x] `bot.py`：dispatch 改接 Dict（`action == "CLOSE"` 等）
- [x] 16 個新 tests，85 passed 全數通過

### Structure Refactor（Strategy 搬移 + Config Secrets 分離）
- [x] `v6/strategies/__init__.py`：re-export 全部 symbols，外部 import 路徑簡潔
- [x] 舊 `v6/strategy.py` / `v6_pyramid_strategy.py` / `v53_sop_strategy.py` 刪除，無殘留 import
- [x] `secrets.json`：api_key / api_secret / telegram_bot_token / telegram_chat_id 抽離
- [x] `config.py` `load_from_json()`：自動讀同目錄 secrets.json
- [x] `bot_config.json` 清除 secrets（可 commit），`.gitignore` 加 `secrets.json`
- [x] 85 tests passed，端到端驗證通過

### close_position Rollback Tests（Mock Bot Fixture 初步建置）
- [x] `v6/tests/conftest.py`：`mock_bot` fixture（patch `_init_exchange` / `PrecisionHandler._load_exchange_info` / `_restore_positions`）
- [x] `v6/tests/test_handle_close.py`：5 tests — failure→False / save_positions / active_trades 保留 / stop_order 邊界情境顯式化 / 正常路徑
- [x] 90 tests passed，prod code 零修改
- [!] 已知邊界情境：close 失敗後 `stop_order_id=None` 但 `pending_stop_cancels` 有舊 ID → Phase 3 後評估是否需 rollback 還原

### Ghost Position Fix（交易所同步四重防護）
- [x] 根因：`get_positions()` API 錯誤回 `[]` → 全部 active_trades 誤判 `hard_stop_hit`
- [x] `risk/manager.py`：`get_positions()` / `_get_futures_positions()` 錯誤回 `None`；`get_account_info()` 加 `or []`
- [x] `bot.py` `_sync_exchange_positions()` 四重防護重寫：API error guard / 正向 hard_stop / size mismatch log / 反向 ghost 偵測
- [x] `bot.py` `monitor_positions`：`pending_stop_cancels` 在 `del active_trades[symbol]` 前清理
- [x] `v6/tests/test_sync_positions.py`：7 tests — API error skip / hard_stop_hit / 不誤殺 / SIZE_MISMATCH / GHOST_POSITION / pending cancel / 空雙邊
- [x] **97 tests passed**（Ghost Fix 後）

### Performance DB 資料品質修正
- [x] `bot.py` `_handle_close` L1171：`safe_capture` 公式 fix（`pnl_pct / mfe_pct`，原為 `realized_r / mfe_pct` 混合單位）
- [x] `bot.py` `_handle_close` 計算前：插入 MFE/MAE safety fallback（`max/min` exit_price）防快速出場回報 0
- [x] `v6/tests/test_perf_db_quality.py`：8 tests（公式正確性 + regression + fallback idempotent）
- [x] **105 tests passed**
- [!] 數據截點：2026-02-25 前的 `capture_ratio` 數值錯誤，Phase 3 分析只用 2026-02-25 之後數據

### Gemini Log Fix（recvWindow + Stage 3 Proportional + -1021 日誌）
- [x] `api_client.py`：`recvWindow=10000`（默認 5s → 10s 容差，減少 -1021 頻率）
- [x] `api_client.py`：`-1021` 偵測 + `[TIMESTAMP]` 專門 warning log
- [x] `positions.py` `calculate_stage3_size()`：ratio 公式從 `1.0-(risk/initial_r)` 改為 `min(1.0, initial_r/risk)`（proportional，與 Stage 2 一致）
- [x] `test_risk.py`：3 new tests（LONG/SHORT/極端 risk 不歸零）
- [x] **108 tests passed**（本機 100 + rwUbuntu 8 perf_db_quality）

### Ghost Adoption（啟動時接管幽靈倉位）
- [x] `bot.py` `_fetch_exchange_stop_map()`：查 algo stop 訂單（Demo Trading 404 fallback → /fapi/v1/openOrders STOP_MARKET）
- [x] `bot.py` `_adopt_ghost_positions()`：啟動時掃 exchange 倉位，跳過已追蹤的，接管幽靈（有 stop 用 exchange stop；無 stop 用入場價 × 2% fallback）
- [x] `tradingStart.py`：`_restore_positions()` 後加呼叫 `_adopt_ghost_positions()` + `_sync_exchange_positions()` 進主循環
- [x] 5 個幽靈 SHORT 倉位成功接管（V5.3 保守模式，不觸發 Stage 2/3）
- [!] 接管倉位 entry_time 從接管時起算；highest/lowest_price 重置；neckline=None

### Tier & Equity Balance Fix（Tier 貫穿三段 + V5.3 截頂）
- [x] `positions.py`：`calculate_stage2/3_size()` 加 tier_mult（A=1.0/B=0.7/C=0.5），Tier A 比 Tier B 大 43%
- [x] `bot.py`：V5.3 加 equity_cap 截頂（`V53_EQUITY_CAP_PERCENT`），對齊 V6 上限
- [x] `v6/tests/test_tier_equity_balance.py`：9 tests（Stage2/3 tier ratio + V5.3 cap）
- [x] **121 tests passed**

### V6 Sizing Upgrade（V6全三段=V5.3的2x，Stage1=V5.3的0.66x）
- [x] `config.py`：`EQUITY_CAP_PERCENT` 10%→20%，新增 `V53_EQUITY_CAP_PERCENT=0.10`，stage ratios 0.33/0.37/0.30
- [x] `config.py` `validate()`：V53_EQUITY_CAP_PERCENT 範圍驗證（1% ≤ V53 ≤ V6 cap）
- [x] `bot.py`：V5.3 截頂改用 `Config.V53_EQUITY_CAP_PERCENT`
- [x] `test_tier_equity_balance.py`：新增 `TestSizingDesign`（4 tests：0.66x/2.0x ratio 驗算）
- [x] `test_risk.py`：2 hardcode tests 改為動態讀 Cfg.*
- [x] **125 tests passed**

### Signal Quality Upgrade（三方優化整合：小波+Gemini 2026-02-27）
- [x] `bot.py`：移除 `strategy=Config.get_strategy()` hardcode → PositionManager 自動 dispatch（V53SopStrategy 恢復正常）
- [x] `v6_pyramid.py`：profit_pullback 加 MFE 門檻（`mfe_r >= MIN_MFE_R_FOR_PULLBACK=0.3R` 才啟用）
- [x] `config.py`：`MIN_MFE_R_FOR_PULLBACK=0.3`、`MIN_FAKEOUT_ATR=0.3`
- [x] `signals.py`：`detect_2b_with_pivots` 加穿透下限過濾（< 0.3 ATR → 噪音過濾）+ `fakeout_depth_atr` 記錄
- [x] `bot.py`：`detect_2b_with_pivots` 加 `min_fakeout_atr=Config.MIN_FAKEOUT_ATR`
- [x] `bot_config.json`：`min_fakeout_atr: 0.3`
- [x] `performance_db.py`：schema + idempotent migration 新增 `entry_adx`、`fakeout_depth_atr` 欄位
- [x] `bot.py`：開倉時記錄 `entry_adx`（df_signal['adx']）+ `pm.entry_adx/fakeout_depth_atr` 動態屬性 + `record_trade` 寫入
- [x] `test_persistence.py`：4 tests（MFE 門檻觸發/不觸發 LONG/SHORT + Strategy dispatch V53 驗證）
- [x] **129 tests passed**
- [!] `test_signal_quality.py`（MIN_FAKEOUT_ATR 專項 tests）未建立，待下次補

### Fill Price Fix（成交均價修正）
- [x] 根因：`_futures_create_order()` 回傳值遭丟棄，positions.json 記信號參考價而非實際成交均價（avgPrice）
- [x] `bot.py`：新增 `_extract_fill_price(order_result, fallback)` 靜態方法（avgPrice / average 雙路，失敗 fallback）
- [x] `bot.py` Stage 1/2/3：下單改捕捉 `order_result`，fill_price 傳入 `PositionManager` + `add_stage2/3()`
- [x] `v6/tests/test_fill_price.py`：8 tests（6 unit：字串/float/missing/0/空dict/非數字 + 2 integration：Stage 1 fill/fallback）
- [x] **137 tests passed**

### Phase 1 DB Persist Fix（entry_adx / fakeout_depth_atr 全 NULL 修復）
- [x] 根因：`positions.py` `to_dict()`/`from_dict()` 未含 `entry_adx`/`fakeout_depth_atr` → systemd restart 後動態屬性消失 → DB 全 NULL（46/46 trades）
- [x] `positions.py` `__init__()`：加 `self.entry_adx = None` / `self.fakeout_depth_atr = None` 預設屬性
- [x] `positions.py` `to_dict()`：序列化兩欄
- [x] `positions.py` `from_dict()`：還原兩欄
- [x] `v6/tests/test_pm_persist.py`：3 tests（entry_adx roundtrip / fakeout roundtrip / default None）
- [x] **140 tests passed**
- [!] 數據截點：restart 前 46 筆 entry_adx/fakeout_depth 全 NULL，Q3 Regime 分析只用 restart 後新數據

### V5.3 Partial Close PnL Fix（perf_db 減倉損益補全）
- [x] 根因：`_handle_v53_reduce()` 1.5R/2.5R 兩次減倉 PnL 從未累積，perf_db `pnl_usdt` 只算最終 ~49% 倉位，幣安 App 數字對不上
- [x] `positions.py` `__init__()`：加 `self.original_size = position_size` + `self.realized_partial_pnl = 0.0`
- [x] `positions.py` `to_dict()`/`from_dict()`：序列化 + 還原，backward compat（fallback `entries[0].size`）
- [x] `bot.py` `_handle_v53_reduce()`：加 `current_price` 參數、捕捉 fill_price（`_extract_fill_price`）、計算並累積 partial PnL、寫 `PARTIAL_CLOSE` 到 v6_trades.log
- [x] `bot.py` monitor dispatch：`V53_REDUCE_15R`/`V53_REDUCE_25R` 傳入 `current_price`
- [x] `bot.py` `_handle_close()`：`pnl_usdt = final_pnl + realized_partial_pnl`；`pnl_pct = total_pnl / original_notional`
- [x] `performance_db.py`：schema + INSERT_SQL + migration 加 `original_size REAL` + `partial_pnl_usdt REAL`
- [x] `v6/tests/test_v53_partial_pnl.py`：8 tests（5 unit + 3 integration）
- [x] **148 tests passed**

### Structure Trailing BOS + V5.3 Dead Zone Fix（移損升級 + V5.3 死區修復）
- [x] **Part 1 — V6 BOS 結構追蹤移損**
  - [x] 根因：原 `find_latest_confirmed_swing` 無時序驗證 → 任意 swing high 就更新 SL，無 BOS 確認
  - [x] `structure.py`：新增 `get_validated_trailing_swing(df, side, current_sl)`，實作 Temporal BOS：① 找最新 HL（swing low 且 > current_sl）② 在該 HL 之前找最近 swing high 作 BOS target ③ 當前 close 突破 BOS target 才回傳 HL 作新 SL（SHORT 鏡像邏輯）
  - [x] `v6_pyramid.py` STRUCTURE_TRAIL_SL 段：改呼叫 `get_validated_trailing_swing`；BOS 驗證失敗時不更新 SL
  - [x] `test_persistence.py`：新增 `_make_df_with_bos_long/short` helpers；test_02/03 改用 BOS data（assertion 不變：94.2/105.8）
  - [x] `v6/tests/test_structure_trailing_bos.py`（新建）：9 tests — LONG BOS confirmed / no BOS / no HL；SHORT BOS confirmed / no BOS / no LH；insufficient data / no swing points / temporal order enforced
- [x] **Part 2 — V5.3 Dead Zone Fix**
  - [x] 根因：Testnet 25 trades 中 0/25 達到 2.5R 第二減倉；1.5R→2.5R 死區無 trailing 保護；1.5R 後 SL 僅 +0.5R 不積極
  - [x] `v53_sop.py`：第二減倉門檻 `2.5R → 2.0R`（縮小死區）
  - [x] `v53_sop.py`：1.5R 減倉後 SL `+0.5R → +1.0R`（鎖定更多獲利）
  - [x] `v53_sop.py`：1.5R 減倉後立即啟用 ATR trailing（`pm.is_trailing_active = True`）
  - [x] `v6/tests/test_v53_deadzone_fix.py`（新建）：4 tests — 1.5R reduce SL+trailing LONG/SHORT / 2.0R 觸發第二減倉 / trailing 在 1.5R 後生效
- [x] **161 tests passed**

### Reverse 2B Exit Upgrade（出場 reverse 2B 優化，2026-03-02）
- [x] 根因：出場 reverse 2B 無穿透深度過濾（入場 2B 有 MIN_FAKEOUT_ATR=0.3，出場沒有）→ 單根 wick 即全平，Stage 3 滿倉誤殺風險高
- [x] `config.py`：新增 `REVERSE_2B_MIN_FAKEOUT_ATR = 0.3`（Config 可調）
- [x] `bot_config.json`：`"reverse_2b_min_fakeout_atr": 0.3`
- [x] `v6_pyramid.py`：reverse 2B 改為雙根邏輯 — `iloc[-2]` 穿透（high/low 穿過 swing point + 收回）+ 深度 >= MIN_FAKEOUT_ATR * ATR + `iloc[-1]` 收盤確認仍在錯誤側；stateless，不動 persistence
- [x] `positions.py`：`__init__` / `to_dict` / `from_dict` 加 `reverse_2b_depth_atr`
- [x] `performance_db.py`：schema + INSERT_SQL + idempotent migration 加 `reverse_2b_depth_atr REAL`
- [x] `bot.py`：`record_trade` 加 `reverse_2b_depth_atr`
- [x] `v6/tests/test_reverse_2b_exit.py`（新建）：9 tests — 深度過濾 LONG/SHORT（淺不觸發/深觸發）+ 確認機制（無確認不觸發/SHORT無確認）+ 無穿透不觸發 + 持久化 roundtrip（value/None）
- [x] **188 tests passed**

### BTC Trend Alignment Tracking（perf_db 數據強化，2026-03-01）
- [x] `bot.py` `_execute_trade()`：開倉時抓 BTC/USDT 1D EMA20/EMA50 判定趨勢方向，比對 position side 記錄同向/逆向
- [x] `positions.py`：`__init__` / `to_dict` / `from_dict` 加 `btc_trend_aligned`（INTEGER nullable）
- [x] `performance_db.py`：schema + migration 加 `btc_trend_aligned INTEGER`
- [x] `bot.py` `record_trade`：寫入 btc_trend_aligned，non-fatal（失敗→NULL）
- [x] `v6/tests/test_btc_trend_aligned.py`（新建）：18 tests
- [x] **179 tests passed**

### Stage 2 Diagnosis（timeout bug fix + debug logging，2026-03-03）
- [x] 根因：`v6_pyramid.py:100` timeout 用 `STAGE1_MAX_HOURS`(24h) 而非 `V6_STAGE1_MAX_HOURS`(36h) → V6 trade 提早 12h 被 timeout
- [x] `v6_pyramid.py`：1 行 fix（`STAGE1_MAX_HOURS` → `V6_STAGE1_MAX_HOURS`）
- [x] `v6_pyramid.py` `check_stage2_trigger()`：加 `V6_STAGE2_DEBUG_LOG` 診斷 logging（記錄 FAIL profit/neckline/volume 各條件）
- [x] `v6/tests/test_stage2_diagnosis.py`（新建）：6 tests
- [x] **194 tests passed**

### Stage 2 Neckline 重新定義（2026-03-03）
- [x] 根因：neckline 選 last_swing_low（時間最新但距 entry 最遠），NEAR SHORT entry=$1.40/neckline=$1.11（需跌 21%），Stage 2 從未觸發（48 trades 全 Stage 1）
- [x] `structure.py` `find_neckline()`：加 `entry_price` 參數，SHORT 取 entry 以下 price 最高的 swing low（最近支撐），LONG 取 entry 以上 price 最低的 swing high（最近阻力），fallback 保持向後相容
- [x] `signals.py`：傳入 `entry_price=close`
- [x] `v6/tests/test_stage2_neckline.py`（新建）：9 tests
- [x] **203 tests passed**

### V53 Disable Early Stop（2026-03-06）
- [x] 根因：early_stop_r 用 price-R（-0.75 risk_dist）觸發，V53 因 equity cap 倉位遠小於 ideal，dollar loss 只有 -0.08~-0.46R 就被砍倉（23 筆 0% WR，-419.76 USDT，佔 V53 虧損 74.6%）
- [x] `strategies/base.py`：加 `pm.is_v6_pyramid` 條件，V53 跳過 early_stop_r，V6 不動
- [x] **204 tests passed**

### V53 Structure Break N-Bar 確認（2026-03-07）
- [x] 根因：`v53_sop.py` 用 `current_price`（live）單根比較，單根 wick 即誤觸出場（10/18 筆輸，avgR≈-0.20R，EV 為負）
- [x] `v53_sop.py`：改用 `df_1h.iloc[-2].close` + `iloc[-1].close` 雙確認（stateless，同 reverse_2b 模式）+ `len(df)>=2` guard
- [x] `v6/tests/test_v53_structure_break_nbar.py`（新建）：9 tests
- [x] **213 tests passed**

### Risk Guard V1（三道結構性風控防線，2026-03-10）
- [x] 背景：3/6~3/10 數據分析（40 trades，PnL=-$95）發現三個結構缺陷：LONG+BTC逆勢（16筆PnL=-$131）/ V53 SL 無上限（BANANAS31 MAE=-18%）/ 同幣連虧無冷卻
- [x] **Guard A — BTC Trend Filter（P0）**
  - [x] `config.py`：`BTC_TREND_FILTER_ENABLED = True`、`BTC_COUNTER_TREND_MULT = 0.0`
  - [x] `bot.py` `scan_for_signals()`：在 signal_tier 計算後、_execute_trade 前，fetch BTC 1D EMA20/50，逆勢時 mult=0 禁入（或按 mult 降倉）
- [x] **Guard B — SL Distance Cap（P1）**
  - [x] `config.py`：`MAX_SL_DISTANCE_PCT = 0.06`（6%）
  - [x] `bot.py` `_execute_trade()`：SL 計算後檢查 `abs(entry-sl)/entry > 6%` → skip
- [x] **Guard C — Symbol Loss Cooldown（P1）**
  - [x] `performance_db.py`：新增 `get_last_loss_exit_time(symbol)` 查詢方法
  - [x] `config.py`：`SYMBOL_LOSS_COOLDOWN_HOURS = 24`
  - [x] `bot.py` `scan_for_signals()`：基於 perf_db persistent 查詢，同幣虧損後 24h 內不再進場
- [x] `bot_config.json`：+4 params（btc_trend_filter_enabled / btc_counter_trend_mult / max_sl_distance_pct / symbol_loss_cooldown_hours）
- [x] `v6/tests/test_risk_guard.py`（新建）：16 tests
- [x] **229 tests passed**

---

## 核心亮點

| 亮點 | 與大目標的關係 |
|------|---------------|
| **風險壓縮**：加倉三次 total risk ≤ initial_R、Stage 2 保本位、Stage 3 risk-free | 穩定獲利 + 大波段 |
| **結構分析**：Swing Point 左 7 右 3、Neckline → Stage 2 trigger | 機靈止損 |
| **Crash Recovery**：atomic write + corruption detect + exchange reconciliation | 穩定獲利 |
| **雙路徑**：V6 滾倉 / V5.3 SOP 共用 PositionManager | 穩定獲利 |
| **四層 Scanner**：200+ → Top 10，板塊分散 + 相關性過濾 | 大波段選股 |

---

## 待修問題

### 高優先（上真錢前）

| # | 問題 | 威脅 |
|---|------|------|
| ~~1~~ | ~~無 Mock Exchange（測試依賴真實 API）~~ | ✅ Integration Test 補齊：StatefulMockEngine + FaultInjector + 18 scenarios（259 tests passed）|
| ~~2~~ | ~~Rate Limit 無防禦~~ | ✅ V7 P1 完成 |
| ~~3~~ | ~~平倉邏輯卡死~~ | ✅ V7 P1 完成 |
| ~~8~~ | ~~`is_v6_pyramid` 硬編碼~~ | ✅ V7 P2 完成 |

### 中優先

| # | 問題 | 威脅 |
|---|------|------|
| 4 | Config global state（難測試、不支援多 instance） | 穩定：測試品質受限 |
| ~~5~~ | ~~Exchange reconciliation 只在啟動~~ | ✅ Ghost Fix：每 cycle 執行四重防護 + 啟動接管 |
| 6 | Scanner 無 pytest | 穩定：信號品質無自動驗證 |
| ~~7~~ | ~~V5.3 路徑 0 tests（Strategy Dispatch bug）~~ | ✅ signal_quality_upgrade 已修 dispatch；V53 路徑 tests 已有 partial_pnl / deadzone / structure_break_nbar / early_stop |
| ~~8~~ | ~~`is_v6_pyramid` 硬編碼~~ | ✅ V7 P2 完成 + signal_quality_upgrade 修 Dispatch bug |
| 9 | ATR 滑價侵蝕 R-value（市價單） | 穩定：實際 R 低於預期 |
| ~~14~~ | ~~profit_pullback 無最低 MFE 門檻~~ | ✅ signal_quality_upgrade 已修（MIN_MFE_R=0.3R）|
| ~~15~~ | ~~LONG 逆 BTC 趨勢無過濾~~ | ✅ Risk Guard V1：BTC_TREND_FILTER（mult=0 禁入）|
| ~~16~~ | ~~V53 SL 距離無上限~~ | ✅ Risk Guard V1：MAX_SL_DISTANCE_PCT=6% |
| ~~17~~ | ~~同幣連虧無冷卻~~ | ✅ Risk Guard V1：SYMBOL_LOSS_COOLDOWN_HOURS=24 |

### 低優先

| # | 問題 |
|---|------|
| 10 | Magic numbers 散落（atr * 3, 0.995 等未抽至 Config） |
| 11 | BinanceFuturesClient error handling 不一致（包 dict） |
| 12 | positions.json 無版本號（schema 相容性） |
| 13 | .bak 累積無自動清理 |

---

## Roadmap

```
Phase 1（穩定性修補）✅
  |
V7 P1（Rate Limit + 平倉優先）✅
  |
V7 P2（Strategy Pattern）✅
  |
Ghost Fix（四重防護 + Adoption）✅
  |
  ├── [選做] Remote Monitoring QMD endpoint（不影響主線）
  |
Testnet 驗證 + Signal Quality ✅ + Fill Price ✅ + V53 PnL ✅ + Reverse 2B ✅ + BTC Trend ✅ + Stage2 Fix ✅ + Neckline ✅ + V53 Early Stop ✅ + V53 N-Bar ✅ + Risk Guard V1 ✅  <-- 你在這裡（3/10 部署，等 3/20 數據）
  |     |
  |   V7 P3（ATR 滑價）
  |     |
Phase 2（測試補齊）
  |
Phase 3（決策品質驗證）  <-- 需 Testnet 數據
  |
Phase 4（資本重分配）
  |
Phase 5（參數微調）
  |
Phase 6（進一步重構 + 效能）
  |
Phase 7（Shadow Mode）
  |
Phase 8（DEX 遷移）
```

---

### ✅ V7 P1：Rate Limit 防禦 + 平倉優先化 — 完成

69 passed，全數通過。Config 同步完成（SWING_LEFT/RIGHT_BARS、現行參數對齊 bot_config.json）。

**當前 Config 狀態（Testnet 基準）**

| 參數 | 值 | 說明 |
|------|----|------|
| ATR_MULTIPLIER | 1.5 | SL 較寬鬆 |
| SL_ATR_BUFFER | 0.8 | 結構追蹤緩衝（避免 wick 被砍） |
| EARLY_STOP_R_THRESHOLD | 0.75 | 給倉位呼吸空間 |
| PROFIT_PULLBACK_THRESHOLD | 0.55 | 趨勢允許更多回撤 |
| V6_4H_EMA20_FORCE_EXIT | **False** | 暫停（防 15 秒被 force exit） |
| V6_STAGE1_MAX_HOURS | 36 | Stage 1 等待充足 |
| STAGE2_VOLUME_MULT | 1.2 | 提升 Stage 2 觸發率 |
| SWING_LEFT_BARS | 7 | 與 bot_config.json 同步 |
| SWING_RIGHT_BARS | 3 | 與 bot_config.json 同步 |
| EARLY_EXIT_COOLDOWN_HOURS | 10 | 冷卻期 |
| STRATEGY_USE_V6[EMA_PULLBACK] | True | 備用信號多樣性 |
| EQUITY_CAP_PERCENT | 0.20 | V6 三段加滿上限（V5.3 的 2x）|
| V53_EQUITY_CAP_PERCENT | 0.10 | V5.3 獨立 cap（防緊止損暴倉）|
| STAGE1/2/3_RATIO | 0.33/0.37/0.30 | Stage1=V5.3 0.66x，Stage2 最大 |
| MIN_FAKEOUT_ATR | 0.3 | 2B 入場最小穿透深度（ATR 倍數）|
| REVERSE_2B_MIN_FAKEOUT_ATR | 0.3 | 出場 reverse 2B 最小穿透深度 |
| MIN_MFE_R_FOR_PULLBACK | 0.3 | profit_pullback 最低 MFE 門檻（R）|
| BTC_TREND_FILTER_ENABLED | true | BTC 趨勢過濾開關 |
| BTC_COUNTER_TREND_MULT | 0.0 | 逆勢倉位乘數（0=禁入）|
| MAX_SL_DISTANCE_PCT | 0.06 | SL 距離上限 6% |
| SYMBOL_LOSS_COOLDOWN_HOURS | 24 | 同幣虧損冷卻時間 |

---

### [選做] Remote Monitoring — V7 P1 後，有空再做

不影響主線績效與架構優化進度。V7 P1 完成後若有餘裕可啟動。

| 項目 | 做法 |
|------|------|
| QMD endpoint | `rwUbuntu` 上加 `/bot-positions`（讀 `positions.json`，Bun+TS） |
| 本機 CLI | Python：`qmd_client` + `binance_client` + `analyzer` + `formatter` |

> 詳見 `projects/remoteMonitoring/PLAN.md`

---

### Testnet 驗證（2~4 週）

跑 Testnet 蒐集真實數據：信號品質、滾倉行為、滑價幅度、Regime 分布。
為後續 Phase 3~5 的數據驅動決策打基礎。

---

### ✅ V7 P2：Strategy Pattern 重構 — 完成

85 passed（69 + 16 新增）。策略可插拔，`is_v6_pyramid` 硬編碼消除，`monitor()` 統一回傳 Dict。

### V7 P3：ATR 動態滑價保護 — 需 Testnet 數據

**解決待修 #9 — 直接影響「穩定獲利」R-value 保護**

| 項目 | 做法 |
|------|------|
| 限價 | 買單 = `ref_price + ATR_SLIPPAGE_TOLERANCE * atr`，IOC 模式 |
| Partial Fill | 市價補齊剩餘，加權均價傳入 `pm.add_stage2/3()` |
| 影響 | `order_engine.py` `config.py` `bot.py` |

> V7 P2 / P3 無相互依賴可平行，均依賴 P1 完成（`positions.py` 衝突管理）
> 詳見 `.協作文件/V7_整合提案書.md` + `V7_整合實作指令書.md`

---

### Phase 2：測試補齊 — 與 V7 P2/P3 平行

| 項目 | 目的 |
|------|------|
| ~~Mock Exchange layer（close_position rollback）~~ | ✅ conftest.py + test_handle_close.py，90 tests |
| Integration test：Scanner → Bot → Order → Exit | 端到端驗證（待做）|
| V5.3 路徑測試 | 解決待修 #7 |
| Scanner pytest | 解決待修 #6 |

---

### Phase 3：決策品質驗證 — 需 Testnet 數據

用 `v6_performance.db` 回答三個關鍵問題：

| 問題 | 成功標準 | 與大目標 |
|------|---------|---------|
| Tier A 是否高正 EV？ | PF > 1.8, Sharpe > 1.5 | 大波段：確認高評級值得重押 |
| 滾倉是否優於固定倉位？ | 影子計算 Stage 1 only 收益對比 | 大波段：驗證三階段設計 |
| Regime 判斷是否有效？ | 不同 Regime 績效有顯著差異 | 穩定：行情判斷有效性 |

---

### Phase 4：資本重分配 — Phase 3 驗證後

| 項目 | 做法 | 與大目標 |
|------|------|---------|
| 動態風險乘數 | Bull + Tier A → x1.5~1.8 / Choppy + Tier C → x0.5 | 大波段：好機會加碼 |
| 動態總曝險 | Bull: MAX_TOTAL_RISK 5% → 8% | 大波段：牛市多吃 |
| 板塊上限 | 同板塊持 3 個 → 暫停新信號 | 穩定：集中風險控制 |

---

### Phase 5：參數微調 — Phase 4 後

- SL_ATR_BUFFER 優化
- Stage3_Ratio 調整
- Trailing 規則依 Regime 差異化
- **嚴禁動核心 2B 結構邏輯**

---

### Phase 6：進一步重構 + 效能

- Config instance-based + DI（解決待修 #4）
- Exchange reconciliation 定期執行（解決待修 #5）
- StructureAnalysis caching
- Scanner async（aiohttp）

---

### Phase 7：Shadow Mode — 真錢跑穩 30 天後

- 主進程（真錢）+ 影子進程（測試參數）並行
- 共享 Scanner，獨立出場規則
- 影子穩定優於主進程 → 考慮切換

---

### Phase 8：遷移 DEX

- dYdX / GMX / Hyperliquid
- 評估流動性、API、鏈選擇
- Wallet 整合 + 合約交易
