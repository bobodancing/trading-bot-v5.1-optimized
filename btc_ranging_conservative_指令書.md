# BTC RANGING 完全停止進場 + UNKNOWN Bypass 修復 指令書

> **Supersedes**: 先前的「保守模式（Tier A + 半倉）」版本
> 回測驗證：BTC 橫盤時即使 Tier A 也在虧（P4 期間 102 筆 Tier A 平均虧 $2.34/筆），故改為完全停止。

## 背景與根因

Testnet 3/19-3/25 勝率暴跌。BTC 處於均線糾纏，`_check_btc_trend()` 回傳 `None`
時 BTC 趨勢過濾被完全跳過（bypass bug）→ 橫盤市場中趨勢策略持續虧損。

修復：
1. `_check_btc_trend()` 新增 `RANGING` 狀態
2. BTC RANGING 或 UNKNOWN → **完全停止趨勢進場**（等待未來網格策略接手）
3. `btc_trend_aligned` 改用 `signal_details` 避免重複 API call

---

## 安全紅線

1. **不影響既有持倉**（只影響新進場判斷）
2. **不改核心信號邏輯**（2B/EMA_PULLBACK/VOLUME_BREAKOUT 不變）
3. **原有 BTC 逆勢過濾不變**（LONG/SHORT 判定 + COUNTER_TREND_MULT 邏輯不動）
4. **每步完成後**：`pytest` 全過

---

## 改動檔案清單

| # | 檔案 | 改動 |
|---|------|------|
| 1 | `trader/config.py` | +1 config（BTC_EMA_RANGING_THRESHOLD） |
| 2 | `trader/bot.py` | `_check_btc_trend()` 加 RANGING + BTC filter 全擋 + `btc_trend_aligned` 重構 |
| 3 | `trader/tests/test_risk_guard.py` | +5 tests（TestBTCRangingFilter）+ helper 更新 |

---

## 修改清單

### 1. `trader/config.py` -- 新增 1 個 Config

**位置**: `BTC_COUNTER_TREND_MULT` 之後

**Before**:
```python
    BTC_TREND_FILTER_ENABLED = True
    BTC_COUNTER_TREND_MULT = 0.0  # 0.0 = 禁止逆勢進場

    # SL 距離上限（佔 entry price 的百分比）
```

**After**:
```python
    BTC_TREND_FILTER_ENABLED = True
    BTC_COUNTER_TREND_MULT = 0.0  # 0.0 = 禁止逆勢進場
    BTC_EMA_RANGING_THRESHOLD = 0.005  # BTC EMA20/50 差距 < 0.5% → RANGING → 完全停止趨勢進場

    # SL 距離上限（佔 entry price 的百分比）
```

---

### 2. `trader/bot.py` -- 三處修改

#### 2a. `_check_btc_trend()` -- 新增 RANGING 偵測

**Before**:
```python
    def _check_btc_trend(self) -> Optional[str]:
        """Fetch BTC 1D EMA20/50 trend. Returns 'LONG', 'SHORT', or None on failure."""
        try:
            btc_df = self.data_provider.fetch_ohlcv("BTC/USDT", "1d", limit=60)
            if btc_df is not None and len(btc_df) >= 50:
                btc_ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                btc_ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                return "LONG" if btc_ema20 > btc_ema50 else "SHORT"
        except Exception as e:
            logger.warning(f"BTC trend check failed: {e}")
        return None
```

**After**:
```python
    def _check_btc_trend(self) -> Optional[str]:
        """Fetch BTC 1D EMA20/50 trend. Returns 'LONG', 'SHORT', 'RANGING', or None on failure."""
        try:
            btc_df = self.data_provider.fetch_ohlcv("BTC/USDT", "1d", limit=60)
            if btc_df is not None and len(btc_df) >= 50:
                btc_ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                btc_ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                if btc_ema50 != 0:
                    ema_diff = abs(btc_ema20 - btc_ema50) / btc_ema50
                    if ema_diff < Config.BTC_EMA_RANGING_THRESHOLD:
                        return "RANGING"
                return "LONG" if btc_ema20 > btc_ema50 else "SHORT"
        except Exception as e:
            logger.warning(f"BTC trend check failed: {e}")
        return None
```

#### 2b. BTC Trend Filter -- RANGING/UNKNOWN 完全停止進場

**Before**:
```python
                # === Risk Guard: BTC Trend Filter ===
                if Config.BTC_TREND_FILTER_ENABLED and "BTC" not in symbol:
                    btc_trend = self._check_btc_trend()
                    if btc_trend is not None and signal_side != btc_trend:
                        if Config.BTC_COUNTER_TREND_MULT <= 0:
                            logger.info(
                                f"{symbol}: 跳過（BTC 趨勢={btc_trend}，信號={signal_side} 逆勢，"
                                f"BTC_COUNTER_TREND_MULT=0）"
                            )
                            continue
                        else:
                            # 降倉：乘以逆勢乘數
                            tier_multiplier *= Config.BTC_COUNTER_TREND_MULT
                            logger.info(
                                f"{symbol}: BTC 逆勢（BTC={btc_trend}，信號={signal_side}），"
                                f"倉位乘數 ×{Config.BTC_COUNTER_TREND_MULT}"
                            )
```

**After**:
```python
                # === Risk Guard: BTC Trend Filter ===
                if Config.BTC_TREND_FILTER_ENABLED and "BTC" not in symbol:
                    btc_trend = self._check_btc_trend()
                    signal_details['btc_trend'] = btc_trend or "UNKNOWN"

                    if btc_trend in ("RANGING", None):
                        # BTC 橫盤或數據失敗 → 完全停止趨勢進場
                        ranging_label = "RANGING" if btc_trend == "RANGING" else "UNKNOWN"
                        logger.info(
                            f"{symbol}: 跳過（BTC {ranging_label}，"
                            f"趨勢策略暫停，等待網格策略接手）"
                        )
                        continue

                    elif signal_side != btc_trend:
                        if Config.BTC_COUNTER_TREND_MULT <= 0:
                            logger.info(
                                f"{symbol}: 跳過（BTC 趨勢={btc_trend}，信號={signal_side} 逆勢，"
                                f"BTC_COUNTER_TREND_MULT=0）"
                            )
                            continue
                        else:
                            tier_multiplier *= Config.BTC_COUNTER_TREND_MULT
                            logger.info(
                                f"{symbol}: BTC 逆勢（BTC={btc_trend}，信號={signal_side}），"
                                f"倉位乘數 ×{Config.BTC_COUNTER_TREND_MULT}"
                            )
```

#### 2c. `_execute_trade` 內 btc_trend_aligned -- 改用 signal_details

**Before**:
```python
            # --- BTC Trend Alignment (data collection only) ---
            if "BTC" not in symbol:  # BTC/USDT 自身不適用
                btc_trend = self._check_btc_trend()
                pm.btc_trend_aligned = (side == btc_trend) if btc_trend is not None else None
            else:
                pm.btc_trend_aligned = None
```

**After**:
```python
            # --- BTC Trend Alignment (data collection) ---
            if "BTC" not in symbol:
                btc_trend = signal_details.get('btc_trend', 'UNKNOWN')
                if btc_trend in ("UNKNOWN", "RANGING"):
                    pm.btc_trend_aligned = None
                else:
                    pm.btc_trend_aligned = (side == btc_trend)
            else:
                pm.btc_trend_aligned = None
```

> 好處：不再重複呼叫 `_check_btc_trend()`（省一次 BTC API call），直接用 filter 階段寫入的值。

---

### 3. `trader/tests/test_risk_guard.py` -- 兩處修改

#### 3a. `_make_btc_df` helper -- 新增 `ranging` 參數

**Before**:
```python
def _make_btc_df(ema20_above_ema50: bool):
    """Helper: 製作 BTC 1D df，控制 EMA20 vs EMA50 方向。"""
    n = 60
    dates = pd.date_range(end=datetime.now(), periods=n, freq='1D')
    if ema20_above_ema50:
        # 上升趨勢：近期價格高
        prices = np.linspace(80000, 90000, n)
    else:
        # 下降趨勢：近期價格低
        prices = np.linspace(90000, 80000, n)
    df = pd.DataFrame({
        'open': prices, 'high': prices * 1.01,
        'low': prices * 0.99, 'close': prices, 'volume': 1000
    }, index=dates)
    return df
```

**After**:
```python
def _make_btc_df(ema20_above_ema50: bool, ranging: bool = False):
    """Helper: 製作 BTC 1D df，控制 EMA20 vs EMA50 方向。"""
    n = 60
    dates = pd.date_range(end=datetime.now(), periods=n, freq='1D')
    if ranging:
        # 橫盤：價格幾乎不動，EMA20 ≈ EMA50
        prices = np.full(n, 85000.0)
        # 微幅波動避免完全相同
        prices += np.random.default_rng(42).normal(0, 50, n)
    elif ema20_above_ema50:
        # 上升趨勢：近期價格高
        prices = np.linspace(80000, 90000, n)
    else:
        # 下降趨勢：近期價格低
        prices = np.linspace(90000, 80000, n)
    df = pd.DataFrame({
        'open': prices, 'high': prices * 1.01,
        'low': prices * 0.99, 'close': prices, 'volume': 1000
    }, index=dates)
    return df
```

#### 3b. 新增 `TestBTCRangingFilter` class

**位置**: `TestBTCTrendFilter` class 之後、`TestSLDistanceCap` class 之前

```python


class TestBTCRangingFilter:
    """A2. BTC Ranging / UNKNOWN → 完全停止趨勢進場"""

    def test_ranging_detected(self):
        """EMA20 ≈ EMA50（差距 < 0.5%）→ RANGING"""
        btc_df = _make_btc_df(ema20_above_ema50=True, ranging=True)
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema_diff = abs(ema20 - ema50) / ema50
        assert ema_diff < Config.BTC_EMA_RANGING_THRESHOLD  # < 0.5%

    def test_trending_not_ranging(self):
        """明確趨勢時差距 > 0.5% → 不是 RANGING"""
        btc_df = _make_btc_df(ema20_above_ema50=True)
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema_diff = abs(ema20 - ema50) / ema50
        assert ema_diff >= Config.BTC_EMA_RANGING_THRESHOLD

    def test_ranging_all_tiers_blocked(self):
        """BTC RANGING → 所有 tier 都被擋（A/B/C 全 skip）"""
        btc_trend = "RANGING"
        for tier in ('A', 'B', 'C'):
            # RANGING 時不區分 tier，全部 continue
            assert btc_trend in ("RANGING", None)

    def test_unknown_all_tiers_blocked(self):
        """btc_trend=None（API 失敗）→ 同 RANGING，全部停止"""
        btc_trend = None
        for tier in ('A', 'B', 'C'):
            assert btc_trend in ("RANGING", None)

    def test_config_defaults(self):
        """Config 預設值正確"""
        assert Config.BTC_EMA_RANGING_THRESHOLD == 0.005
        assert not hasattr(Config, 'BTC_RANGING_POSITION_MULT')
```

---

## 驗證步驟

```bash
cd /home/rwfunder/文件/tradingbot/trading_bot
python -m pytest trader/tests/test_risk_guard.py -v
python -m pytest trader/tests/ -x -q
```

預期: **356 tests passed**

確認 RANGING 偵測生效：
```bash
grep -n 'BTC_EMA_RANGING' trader/config.py trader/bot.py
grep -n 'RANGING' trader/bot.py
```

重啟 service：
```bash
sudo systemctl restart trader.service
# 密碼: 0602
sudo systemctl status trader.service
journalctl -u trader.service --since "1 min ago" --no-pager
```

觀察 log 中應出現：
- `跳過（BTC RANGING，趨勢策略暫停，等待網格策略接手）` — BTC 橫盤時所有信號被擋

---

## 不動的部分（確認保留）

- `positions.py` `monitor()` -- 不動
- 核心信號邏輯 -- 2B / EMA_PULLBACK / VOLUME_BREAKOUT 不變
- BTC LONG/SHORT 時的逆勢過濾 -- 邏輯完全不變
- V53 / V6 / V7 策略行為 -- 不影響

---

## 注意事項

- `BTC_EMA_RANGING_THRESHOLD = 0.005`（0.5%）— 三輪回測驗證：2% 太寬殺到順勢好單，0.5% 只抓真正糾纏
- UNKNOWN（API 失敗）也完全停止 — 修復原本的 bypass bug，不確定時停止比放行安全
- 未來網格策略建構後，RANGING 期間會由網格策略接手
- **不需要** `BTC_RANGING_POSITION_MULT` — 回測證明即使降倉 Tier A 仍在虧，完全停止是唯一正確做法
