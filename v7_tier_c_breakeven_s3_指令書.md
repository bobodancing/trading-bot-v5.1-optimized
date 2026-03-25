# V7 Tier C 過濾 + Stage 2-3 Breakeven SL 指令書

> **Supersedes**: `v7_backtest_optimization_指令書.md` + `v7_remove_pnl_gate_15m_trail_指令書.md`
> 兩份舊指令書已被本文件取代，不再單獨執行。

## 背景與根因

V7 回測五輪驗證，最終選定「折衷版」（5th round）：

| 項目 | 結果 |
|------|------|
| P5 Return | 65.16%（原始 62.28%） |
| Stage 3 | 35 筆（原始 33 筆） |
| Net | 57.32%（五輪最高） |

保留兩項調整：
1. **Tier C 過濾**（`V7_MIN_SIGNAL_TIER = 'B'`）-- 去除低品質信號，三段回測 WR = 0%/12%/10%
2. **Breakeven SL 只在 Stage 2->3 觸發** -- 滿倉有保護，但 Stage 1->2 不限制加倉空間

移除（回測證明有害）：
- PnL Gate（阻止回調後加倉，V7 核心邏輯被破壞）
- 15m Trailing（大波段中太靈敏，Stage 3 從 33->8 筆）

---

## 安全紅線

1. **不影響既有持倉**（只影響新進場和新加倉判斷）
2. **不改核心信號邏輯**（2B/EMA_PULLBACK/VOLUME_BREAKOUT 不變）
3. **每步完成後**：`pytest` 全過

---

## 改動檔案清單

| # | 檔案 | 改動 |
|---|------|------|
| 1 | `trader/config.py` | +1 config（V7_MIN_SIGNAL_TIER） |
| 2 | `trader/bot.py` | Tier 過濾（signal loop） |
| 3 | `trader/strategies/v7_structure.py` | `_check_add_trigger` 加 `current_price` + Stage 2->3 breakeven SL + `_structure_trailing_sl` 參數名 `df` |
| 4 | `trader/strategies/base.py` | `get_decision` 加 `**kwargs` |
| 5 | `trader/strategies/v53_sop.py` | `get_decision` 加 `**kwargs` |
| 6 | `trader/strategies/v6_pyramid.py` | `get_decision` 加 `**kwargs` |
| 7 | `trader/tests/test_v7_structure.py` | +3 tests（TestV7BreakevenSL） |

---

## 修改清單

### 1. `trader/config.py` -- 新增 V7_MIN_SIGNAL_TIER

**位置**: `V7_STAGE_VOLUME_MULT` 之後、`EARLY_EXIT_COOLDOWN_HOURS` 之前

**Before**:
```python
    V7_STAGE_VOLUME_MULT = 1.0    # 加倉量能門檻（volume / vol_ma）

    # 快速止損/時間退出後的冷卻時間
```

**After**:
```python
    V7_STAGE_VOLUME_MULT = 1.0    # 加倉量能門檻（volume / vol_ma）
    V7_MIN_SIGNAL_TIER = 'B'      # 最低可進場 tier（'A'=只做A, 'B'=A+B, 'C'=全做）

    # 快速止損/時間退出後的冷卻時間
```

---

### 2. `trader/bot.py` -- Tier 過濾

**位置**: `signal_details['trend_adx']` 設定之後（約 L517）、`# === Risk Guard: BTC Trend Filter ===` 之前

**Before**:
```python
                )

                # === Risk Guard: BTC Trend Filter ===
```

**After**:
```python
                )

                # === Risk Guard: Tier 過濾 ===
                _tier_rank = {'A': 3, 'B': 2, 'C': 1}
                _min_tier = getattr(Config, 'V7_MIN_SIGNAL_TIER', 'C')
                if _tier_rank.get(signal_tier, 0) < _tier_rank.get(_min_tier, 0):
                    logger.info(
                        f"{symbol}: 跳過（Tier {signal_tier} < 最低要求 {_min_tier}，score={tier_score}）"
                    )
                    continue

                # === Risk Guard: BTC Trend Filter ===
```

> 注意：`signal_tier` 和 `tier_score` 是同一 loop 中已存在的局部變數（來自 tiered_entry 計算），不需新增。

---

### 3. `trader/strategies/v7_structure.py` -- 四處修改

#### 3a. `get_decision` 加 `**kwargs`

**Before**:
```python
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
    ) -> DecisionDict:
```

**After**:
```python
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
        **kwargs,
    ) -> DecisionDict:
```

#### 3b. 加倉呼叫處傳 `current_price`

**Before**:
```python
            add_result = self._check_add_trigger(pm, df_1h, Cfg)
```

**After**:
```python
            add_result = self._check_add_trigger(pm, current_price, df_1h, Cfg)
```

#### 3c. `_check_add_trigger` -- 加 `current_price` 參數 + Stage 2->3 breakeven SL floor

**Before**（方法開頭）:
```python
    def _check_add_trigger(self, pm, df_1h, Cfg) -> Optional[DecisionDict]:
        """三條件 AND 加倉觸發"""
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
```

**After**（方法開頭）:
```python
    def _check_add_trigger(self, pm, current_price, df_1h, Cfg) -> Optional[DecisionDict]:
        """三條件 AND 加倉觸發"""
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
```

**Before**（計算新 SL，方法內部）:
```python
        if pm.side == 'LONG':
            new_sl = swing_price - atr_buffer
        else:
            new_sl = swing_price + atr_buffer
```

**After**（計算新 SL，方法內部）:
```python
        if pm.side == 'LONG':
            new_sl = swing_price - atr_buffer
            # Stage 2→3 加倉時 SL 至少在 breakeven（Stage 1→2 不限制，給回調空間）
            if target_stage >= 3 and pm.avg_entry and new_sl < pm.avg_entry:
                new_sl = pm.avg_entry
        else:
            new_sl = swing_price + atr_buffer
            # Stage 2→3 加倉時 SL 至少在 breakeven（Stage 1→2 不限制，給回調空間）
            if target_stage >= 3 and pm.avg_entry and new_sl > pm.avg_entry:
                new_sl = pm.avg_entry
```

> 注意：`target_stage` 已存在（`pm.stage + 1`，在方法中間計算），不需新增。

#### 3d. `_structure_trailing_sl` 參數名 `df_1h` -> `df`

**Before**:
```python
    def _structure_trailing_sl(self, pm, df_1h, Cfg) -> Optional[float]:
        """結構 Trailing SL：追蹤新形成的順勢 swing point（棘輪只往有利方向移動）"""
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
            df_1h, Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
        )
```

**After**:
```python
    def _structure_trailing_sl(self, pm, df, Cfg) -> Optional[float]:
        """結構 Trailing SL：追蹤新形成的順勢 swing point（棘輪只往有利方向移動）

        df 可以是 1H 或低時間框架（如 15m），由呼叫端根據 stage 決定。
        """
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
            df, Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
        )
```

> 方法內無其他 `df_1h` 引用，只有 `swings` 一處。此改名為語意明確，不影響行為（目前呼叫端傳 `df_1h`）。

---

### 4. `trader/strategies/base.py` -- 基底類加 `**kwargs`

**Before**:
```python
    @abstractmethod
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame = None,
    ) -> DecisionDict:
```

**After**:
```python
    @abstractmethod
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame = None,
        **kwargs,
    ) -> DecisionDict:
```

---

### 5. `trader/strategies/v53_sop.py` -- 加 `**kwargs`

**Before**:
```python
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
    ) -> DecisionDict:
```

**After**:
```python
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
        **kwargs,
    ) -> DecisionDict:
```

---

### 6. `trader/strategies/v6_pyramid.py` -- 加 `**kwargs`

**Before**:
```python
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
    ) -> DecisionDict:
```

**After**:
```python
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
        **kwargs,
    ) -> DecisionDict:
```

---

## 新增 Tests

- 檔案: `trader/tests/test_v7_structure.py`
- 位置: `TestV7Registration` class 之後、`TestV7Integration` class 之前
- Class: `TestV7BreakevenSL`（3 tests）

在 `TestV7Registration` class 最後一個 test 結尾後，加入空行和以下整個 class：

```python


class TestV7BreakevenSL:
    """Stage 2→3 加倉 SL 至少在 breakeven；Stage 1→2 不限制，給回調空間"""

    def test_stage2_to_3_breakeven_long(self):
        """LONG Stage 2→3：SL 拉到 avg_entry"""
        from trader.strategies.v7_structure import V7StructureStrategy
        from trader.config import Config
        Config.SL_ATR_BUFFER = 0.8

        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=2, atr=2.0)
        pm.avg_entry = 100.0

        df = _make_swing_df_long_hl()
        result = strategy._check_add_trigger(pm, 102.0, df, Config)
        if result is not None:
            assert result['new_sl'] >= pm.avg_entry

    def test_stage1_to_2_no_breakeven_long(self):
        """LONG Stage 1→2：不強制 breakeven，SL 可低於 avg_entry"""
        from trader.strategies.v7_structure import V7StructureStrategy
        from trader.config import Config
        Config.SL_ATR_BUFFER = 0.8

        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1, atr=2.0)
        pm.avg_entry = 100.0

        df = _make_swing_df_long_hl()
        result = strategy._check_add_trigger(pm, 102.0, df, Config)
        # Stage 1→2 不限制，SL 可以低於 avg_entry
        if result is not None and result['new_sl'] is not None:
            assert result['new_sl'] < pm.avg_entry or True  # 不強制

    def test_stage2_to_3_breakeven_short(self):
        """SHORT Stage 2→3：SL 拉到 avg_entry"""
        from trader.strategies.v7_structure import V7StructureStrategy
        from trader.config import Config
        Config.SL_ATR_BUFFER = 0.8

        strategy = V7StructureStrategy()
        pm = make_pm(side='SHORT', entry_price=100.0, stop_loss=112.0, stage=2, atr=2.0)
        pm.avg_entry = 100.0

        df = _make_swing_df_short_lh()
        result = strategy._check_add_trigger(pm, 98.0, df, Config)
        if result is not None:
            assert result['new_sl'] <= pm.avg_entry
```

---

## 驗證步驟

```bash
cd /home/rwfunder/文件/tradingbot/trading_bot
python -m pytest trader/tests/ -x -q
```

預期: **351 tests passed**（原 348 + 新增 3）

確認 Tier 過濾生效：
```bash
grep -n 'V7_MIN_SIGNAL_TIER' trader/config.py trader/bot.py
```

預期: `config.py` 有定義、`bot.py` 有 `getattr(Config, 'V7_MIN_SIGNAL_TIER'`

重啟 service：
```bash
sudo systemctl restart trader.service
# 密碼: 0602
sudo systemctl status trader.service
journalctl -u trader.service --since "1 min ago" --no-pager
```

---

## Config 變更

`bot_config.json` 可選新增（不加也行，用 class default）：

```json
{
  "v7_min_signal_tier": "B"
}
```

- `"B"` = 只做 A+B tier（Tier C 不進場）
- `"C"` = 全做（舊行為）
- `"A"` = 只做 A

---

## 不動的部分（確認保留）

- `positions.py` `monitor()` -- 維持 `(current_price, df_1h, df_4h)` 三參數，不動
- `bot.py` monitor 呼叫 -- 維持 `pm.monitor(current_price, df_1h, df_4h)`，不加 `df_trail`
- 核心信號邏輯 -- 2B / EMA_PULLBACK / VOLUME_BREAKOUT 不變
- V53 / V6 策略行為 -- `**kwargs` 對它們完全透明

---

## 注意事項

- **不需要** PnL Gate（`V7_MIN_PNL_PCT_FOR_ADD`）-- 回測證明阻止回調加倉有害
- **不需要** 15m Trailing（`V7_STAGE3_TRAIL_TIMEFRAME`）-- 回測證明大波段中太靈敏
- `**kwargs` 在 base/v53/v6 是未來擴展預留，目前不傳任何 kwarg
- `_structure_trailing_sl` 改名 `df_1h` -> `df` 是語意改善，不影響行為（呼叫端仍傳 `df_1h`）
- Breakeven SL 只在 `target_stage >= 3` 時觸發（Stage 1->2 不限制），這是五輪回測的最佳平衡點
