# Trader Refactor 指令書（4 Phase）

**目的**：將 `v6/` 改名為 `trader/`，瘦身移除冗餘，建立策略拔插（Plugin）架構
**執行環境**：rwUbuntu `/home/rwfunder/文件/tradingbot/trading_bot_v6`
**風險等級**：低～中（每 Phase 獨立可測，不改核心交易邏輯）
**前置條件**：每 Phase 開始前確認無持倉（`positions.json` 為空 or 全 closed）

---

## 安全紅線

1. **不改交易邏輯**：策略判斷、進出場條件、風控參數值一律不動
2. **不改數據格式**：`v6_performance.db` schema 不變、`positions.json` 向下相容
3. **不改版本標識**：`'bot': 'v6.0'` 版本字串保留（這是歷史標記，不是路徑）
4. **不改 config 參數值**：只改結構/命名，數值不動
5. **每 Phase 完成後**：`pytest` 全過 + systemd 重啟驗證 + Telegram 通知確認

---

## Phase 0：改名 `v6/` → `trader/`

**目的**：模組從版本號改為平台名，為後續 Phase 鋪路
**預估影響**：~30 檔案 import 路徑替換（純機械操作）
**風險**：低（不改邏輯，只改路徑）

### 0-1. 停止服務

```bash
sudo systemctl stop trader.service scanner.service
```

### 0-2. 資料夾改名

```bash
cd /home/rwfunder/文件/tradingbot/trading_bot_v6
mv v6 trader
```

### 0-3. 全域 import 替換

所有 `.py` 檔案，將 `from v6.` 替換為 `from trader.`，`import v6.` 替換為 `import trader.`。

**影響檔案清單**（逐一確認）：

| 檔案 | import 數量 |
|------|------------|
| `trader/bot.py` | 12 |
| `trader/core.py` | 6 |
| `trader/positions.py` | 6 |
| `trader/config.py` | 1 |
| `trader/signals.py` | 1 |
| `trader/__init__.py` | 6 |
| `trader/execution/order_engine.py` | 3 |
| `trader/indicators/technical.py` | 1 |
| `trader/infrastructure/notifier.py` | 1 |
| `trader/infrastructure/api_client.py` | 1 |
| `trader/risk/manager.py` | 3 |
| `trader/strategies/base.py` | 4 |
| `trader/strategies/v6_pyramid.py` | 3 |
| `trader/strategies/v53_sop.py` | 3 |
| `trader/strategies/__init__.py` | 3 |
| `trader/tests/conftest.py` | 3 |
| `trader/tests/test_*.py` | 每檔 2~5 |
| `scanner/market_scanner.py` | 2 |

**指令**（在專案根目錄執行）：

```bash
# 預覽（先看不動）
grep -rn "from v6\." trader/ scanner/ --include="*.py"
grep -rn "import v6\." trader/ scanner/ --include="*.py"

# 執行替換
find trader/ scanner/ -name "*.py" -exec sed -i 's/from v6\./from trader./g' {} +
find trader/ scanner/ -name "*.py" -exec sed -i 's/import v6\./import trader./g' {} +

# 驗證無殘留
grep -rn "from v6\.\|import v6\." trader/ scanner/ --include="*.py"
# 應該回傳空
```

### 0-4. sys.path 確認

`trader/bot.py` line 23：
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```
這是相對路徑，**不需要改**（`parent.parent` 仍指向專案根目錄）。

### 0-5. Config 路徑確認

`trader/config.py` 中的路徑：
- `LOG_FILE_PATH` → 使用 `Path(__file__)` 相對解析，**不需要改**
- `DB_PATH = "v6_performance.db"` → **保留不改**（歷史數據檔名）
- `POSITIONS_FILE` → 使用相對路徑，**不需要改**

### 0-6. systemd 更新

```bash
sudo nano /etc/systemd/system/trader.service
# ExecStart 改為：python3 trader/bot.py（原本是 python3 v6/bot.py）

sudo nano /etc/systemd/system/scanner.service
# ExecStart 改為：python3 -m scanner.market_scanner（確認路徑）

sudo systemctl daemon-reload
```

### 0-7. 刪除 .bak 檔

```bash
rm -f trader/bot.py.bak trader/risk/manager.py.bak
```

### 0-8. 驗證

```bash
cd /home/rwfunder/文件/tradingbot/trading_bot_v6
python3 -m pytest trader/tests/ -x -q
# 預期：259 tests passed

# 重啟服務
sudo systemctl start trader.service scanner.service
sudo systemctl status trader.service scanner.service
# 確認 Active: active (running)

# 確認 Telegram 啟動通知正常收到
```

### 0-9. 不動的東西（明確列出）

| 項目 | 值 | 原因 |
|------|---|------|
| `'bot': 'v6.0'` | 保留 | 版本歷史標記 |
| `v6_performance.db` | 保留 | 歷史數據檔名 |
| `v6_bot.log` / `v6_trades.log` | 保留 | log 檔名不影響邏輯 |
| `is_v6_pyramid` | 保留 | Phase 2 處理 |
| `strategy_type: "v6"` | 保留 | 策略名稱，Phase 2 處理 |
| `V6_STAGE1_MAX_HOURS` 等 config 名 | 保留 | Phase 2 處理 |
| `bot_config.json` 的 key 名 | 保留 | 與 config.py 對應 |
| `positions.json` 現有數據 | 保留 | 不改序列化格式 |

### 0-10. Git Commit

```bash
git add -A
git commit -m "refactor: rename v6/ to trader/ — platform identity, not version"
```

---

## Phase 1：安全瘦身（刪死代碼 + 提煉重複）

**目的**：移除冗餘，降低維護成本，不改任何行為
**前置**：Phase 0 完成
**預估**：砍 ~200 行，提煉 6 個 helper

### 1-1. 刪除 technical.py 死代碼（~120 行）

刪除以下三個函數（已被 `signals.py` 的 V6 版取代，grep 確認無 caller）：

1. **`detect_2B_signal()`**（約 L146-237）— 舊版 rolling min/max 2B 偵測
2. **`detect_ema_pullback_signal()`**（約 L240-303）— EMA 回踩策略
3. **`detect_volume_breakout_signal()`**（約 L306-361）— 量能突破策略

**刪除前驗證**：
```bash
grep -rn "detect_2B_signal\|detect_ema_pullback_signal\|detect_volume_breakout_signal" trader/ scanner/ --include="*.py" | grep -v "def \|#\|test_"
# 確認除了定義和註解外無 caller
```

**同步清理**：
- `trader/config.py`：移除 `EMA_PULLBACK_*`（4 個）、`VOLUME_BREAKOUT_MULT`、`ACCEPT_WEAK_SIGNALS`
- `trader/bot.py`：移除 V5.3 fallback signal path（`scan_for_signals()` 中 EMA/Volume 分支，約 L425-437）
  - **注意**：只移除 signal detection 的 fallback 分支，不動 V53SopStrategy（V53 策略仍由 2B 以外的信號觸發）
- `trader/indicators/technical.py` L95-96：移除 TECH_DEBT 註解
- 移除 `calculate_indicators()` 中只為死函數計算的 `ema_fast`、`ema_slow`（L113-114），**但先確認 scanner 是否用到這些欄位**

### 1-2. 刪除 positions.py deprecated wrapper（~25 行）

刪除三個空殼方法：

1. **`monitor_v6()`**（L515-521）
2. **`monitor_v53()`**（L523-529）
3. **`_get_exit_decision()`**（L531-538）

**前置**：先更新 test 中的呼叫：
```bash
grep -rn "monitor_v6\|monitor_v53\|_get_exit_decision" trader/tests/ --include="*.py"
```

將所有 test 改為呼叫 `pm.monitor(current_price, df_1h, df_4h)`，取回 `DecisionDict` 後斷言 `decision['action']`。

### 1-3. 提煉 bot.py 重複邏輯（6 個 helper）

在 `bot.py` 中新增 private helper，逐一替換重複程式碼：

#### Helper A：`_check_btc_trend()` → `Optional[str]`
- 取代 L505-509 和 L748-752 的重複 BTC EMA20/50 計算
- 回傳 `"LONG"` / `"SHORT"` / `None`（fetch 失敗）

#### Helper B：`_refresh_stop_loss(pm, new_sl)`
- 取代 L1395-1398、L1476-1479、L1548-1551 的三處 SL 更新序列
- 邏輯：cancel → place → update pm.stop_order_id

#### Helper C：`_get_close_side(side: str)` → `str`
- 取代 3 處 `order_side = 'BUY' if pm.side == 'LONG' else 'SELL'`

#### Helper D：`_validate_position_size(symbol, raw_size, entry_price)` → `Optional[float]`
- 取代 L619-622、L1368-1371、L1448-1451 的精度驗證

#### Helper E：`_calculate_pnl(side, size, current_price, entry_price)` → `float`
- 取代 L1205-1208、L1502-1505、L1539-1542 的 LONG/SHORT PnL 計算

#### Helper F：`_build_log_base(event, trade_id, symbol, side)` → `dict`
- 取代 10 處 `_trade_log({...})` 的共用欄位組裝
- 回傳 `{'ts': ..., 'bot': 'v6.0', 'event': event, 'trade_id': ..., 'symbol': ..., 'side': ...}`

### 1-4. 清理 config.py 孤兒參數

移除已無 caller 的參數（Phase 1-1 刪除對應函數後）：
- `EMA_PULLBACK_THRESHOLD`
- `EMA_PULLBACK_LOOKBACK`
- `EMA_PULLBACK_MIN_DISTANCE`
- `EMA_PULLBACK_MAX_DISTANCE`
- `VOLUME_BREAKOUT_MULT`
- `ACCEPT_WEAK_SIGNALS`

**注意**：`bot_config.json` 中若有對應 key，一併移除或保留不讀取（config.py `load_from_json` 的 `getattr` 會忽略不存在的屬性）。

### 1-5. 驗證

```bash
python3 -m pytest trader/tests/ -x -q
# 預期：259 tests passed（或因刪除 wrapper 而調整後的數量）

# 功能驗證
sudo systemctl restart trader.service
# 確認正常啟動、Telegram 通知
```

### 1-6. Git Commit

```bash
git add -A
git commit -m "refactor: remove dead code + extract bot.py helpers — slim ~200 lines"
```

---

## Phase 2：插件化改造（策略拔插架構）

**目的**：讓新策略只要寫一個 class + register，不動 bot.py
**前置**：Phase 1 完成
**風險**：中（改介面但不改邏輯，需完整 regression test）

### 2-1. DecisionDict 通用化

**檔案**：`trader/strategies/base.py`

將 action 歸納為通用類型：

```python
# 舊的（策略特定）
action: "ACTIVE" | "CLOSE" | "STAGE2_TRIGGER" | "STAGE3_TRIGGER" | "V53_REDUCE_15R" | "V53_REDUCE_25R"

# 新的（通用）
class Action(str, Enum):
    HOLD = "HOLD"              # 繼續持有（原 ACTIVE）
    CLOSE = "CLOSE"            # 全平
    PARTIAL_CLOSE = "PARTIAL_CLOSE"  # 部分平倉（原 V53_REDUCE_*）
    ADD = "ADD"                # 加倉（原 STAGE2/3_TRIGGER）
    UPDATE_SL = "UPDATE_SL"    # 純更新止損
```

更新 `DecisionDict`：
```python
class DecisionDict(TypedDict):
    action: str           # Action enum value
    reason: str           # exit reason code
    new_sl: Optional[float]
    close_pct: Optional[float]  # PARTIAL_CLOSE 時的比例
    add_stage: Optional[int]    # ADD 時的階段（2 or 3）
```

### 2-2. 更新兩個策略的回傳值

**`trader/strategies/v6_pyramid.py`**：
- `"ACTIVE"` → `Action.HOLD`
- `"CLOSE"` → `Action.CLOSE`
- `"STAGE2_TRIGGER"` → `Action.ADD`（`add_stage=2`）
- `"STAGE3_TRIGGER"` → `Action.ADD`（`add_stage=3`）

**`trader/strategies/v53_sop.py`**：
- `"ACTIVE"` → `Action.HOLD`
- `"CLOSE"` → `Action.CLOSE`
- `"V53_REDUCE_15R"` → `Action.PARTIAL_CLOSE`（`close_pct=0.3`）
- `"V53_REDUCE_25R"` → `Action.PARTIAL_CLOSE`（`close_pct=0.3`）

### 2-3. 更新 bot.py action dispatch

**`monitor_positions()`** 改為 generic dispatch：

```python
# 舊的（每個 action 對應一個 handler）
if action == "CLOSE": ...
elif action == "STAGE2_TRIGGER": ...
elif action == "STAGE3_TRIGGER": ...
elif action == "V53_REDUCE_15R": ...
elif action == "V53_REDUCE_25R": ...

# 新的（通用 dispatch）
if action == Action.CLOSE:
    self._handle_close(symbol, pm, current_price)
elif action == Action.ADD:
    stage = decision.get('add_stage', 2)
    self._handle_add(symbol, pm, current_price, stage)
elif action == Action.PARTIAL_CLOSE:
    pct = decision.get('close_pct', 0.3)
    self._handle_partial_close(symbol, pm, current_price, pct)
if new_sl:
    self._refresh_stop_loss(pm, new_sl)
```

### 2-4. `is_v6_pyramid` → `strategy_name: str`

**`trader/positions.py`**：

```python
# 舊
def __init__(self, ..., is_v6_pyramid=True, ...):
    self.is_v6_pyramid = is_v6_pyramid
    self.strategy = StrategyFactory.create("v6" if is_v6_pyramid else "v53")

# 新
def __init__(self, ..., strategy_name="v6_pyramid", ...):
    self.strategy_name = strategy_name
    self.strategy = StrategyFactory.create(strategy_name)
```

序列化/反序列化更新：
```python
# to_dict
'strategy_name': self.strategy_name,  # 取代 is_v6_pyramid + strategy_type

# from_dict（向下相容）
strategy_name = data.get('strategy_name') or ("v6_pyramid" if data.get('is_v6_pyramid', True) else "v53_sop")
```

**bot.py** 建構 PositionManager 時：
```python
# 舊
pm = PositionManager(..., is_v6_pyramid=(signal_type == "2B"), ...)
# 新
pm = PositionManager(..., strategy_name=strategy_registry[signal_type], ...)
```

### 2-5. V53 state 搬進策略

將 4 個 V53 專屬 flag 從 PositionManager 搬進 V53SopStrategy：

```python
class V53SopStrategy(TradingStrategy):
    def __init__(self):
        self.is_1r_protected = False
        self.is_first_partial = False
        self.is_second_partial = False
        self.is_trailing_active = False
```

序列化：策略需提供 `get_state()` / `load_state()` 介面：
```python
class TradingStrategy(ABC):
    @abstractmethod
    def get_decision(self, pm, current_price, df_1h, df_4h) -> DecisionDict: ...

    def get_state(self) -> dict:
        """回傳策略內部 state（for persistence）"""
        return {}

    def load_state(self, state: dict):
        """從 dict 還原策略 state"""
        pass
```

PositionManager 序列化時：
```python
# to_dict
'strategy_state': self.strategy.get_state(),

# from_dict
pm.strategy.load_state(data.get('strategy_state', {}))
```

### 2-6. StrategyFactory → Registry

```python
class StrategyFactory:
    _registry: Dict[str, Type[TradingStrategy]] = {}

    @classmethod
    def register(cls, name: str, strategy_cls: Type[TradingStrategy]):
        cls._registry[name] = strategy_cls

    @classmethod
    def create(cls, name: str) -> TradingStrategy:
        if name not in cls._registry:
            raise ValueError(f"Unknown strategy: {name}. Available: {list(cls._registry.keys())}")
        return cls._registry[name]()

# 各策略檔案底部自動註冊
StrategyFactory.register("v6_pyramid", V6PyramidStrategy)
StrategyFactory.register("v53_sop", V53SopStrategy)
```

### 2-7. Signal → Strategy 映射 config 化

**`trader/config.py`** 新增：

```python
    # Signal → Strategy 映射
    SIGNAL_STRATEGY_MAP = {
        "2B": "v6_pyramid",
        "EMA_PULLBACK": "v53_sop",     # 未來可替換
        "VOLUME_BREAKOUT": "v53_sop",  # 未來可替換
    }
```

**`bot.py`** 使用映射：
```python
strategy_name = Config.SIGNAL_STRATEGY_MAP.get(signal_type, "v6_pyramid")
pm = PositionManager(..., strategy_name=strategy_name, ...)
```

### 2-8. 更新所有 tests

- 所有 `is_v6_pyramid=True` → `strategy_name="v6_pyramid"`
- 所有 `is_v6_pyramid=False` → `strategy_name="v53_sop"`
- action 斷言改用 `Action.HOLD`、`Action.CLOSE` 等
- 新增 test：Registry 註冊/查詢/未知策略 ValueError

### 2-9. 驗證

```bash
python3 -m pytest trader/tests/ -x -q
# 全過

sudo systemctl restart trader.service
# Telegram 通知正常
# 等待一個交易週期確認 monitor_positions 正常運作
```

### 2-10. Git Commit

```bash
git add -A
git commit -m "feat: pluggable strategy architecture — registry + generic actions + state isolation"
```

---

## Phase 3：bot.py 職責拆分（為 Shadow Mode 鋪路）

**目的**：拆解 bot.py 巨型方法，降低單一檔案複雜度
**前置**：Phase 2 完成
**風險**：中（大規模方法搬移，需完整 regression）
**優先度**：可延後，視 Shadow Mode 時程決定

### 3-1. 提煉 `_execute_trade()` 221 行 → 3 個方法

```
_execute_trade()
├─ _calculate_position_sizing(symbol, side, entry_price, signal_tier) → SizingResult
├─ _place_entry_order(symbol, side, size, entry_price) → OrderResult
└─ _create_position_manager(symbol, side, order_result, signal_details) → PositionManager
```

### 3-2. 提煉 `_handle_close()` 170 行 → 2 個方法

```
_handle_close()
├─ _compute_close_metrics(pm, exit_price) → CloseMetrics (pnl, mfe, mae, r_multiple, duration)
└─ _persist_trade_close(pm, metrics) → None (DB write + Telegram + log)
```

### 3-3. 提煉 `monitor_positions()` 172 行

```
monitor_positions()
├─ _monitor_single_position(symbol, pm) → Decision
└─ _dispatch_action(symbol, pm, decision) → None
```

### 3-4. 提煉 `scan_for_signals()` 240 行

```
scan_for_signals()
├─ _fetch_scan_data(symbols) → Dict[symbol, DataBundle]
└─ _evaluate_signal(symbol, data) → Optional[SignalDetails]
```

### 3-5. 考慮抽出模組（可選）

如果 bot.py 仍然過長（>1000 行），考慮：
- `trader/trade_executor.py` — 進場邏輯
- `trader/trade_monitor.py` — 監控邏輯
- `trader/trade_closer.py` — 平倉邏輯

但 **只有在 Shadow Mode 實作需要時才做**，避免過度設計。

### 3-6. 驗證

```bash
python3 -m pytest trader/tests/ -x -q
# 全過

sudo systemctl restart trader.service
# 完整交易週期驗證
```

### 3-7. Git Commit

```bash
git add -A
git commit -m "refactor: decompose bot.py mega-methods — prep for shadow mode"
```

---

## Roadmap 連動確認

| Roadmap 項目 | 受益 Phase | 說明 |
|---|---|---|
| **Testnet 驗證**（進行中）| Phase 0-1 | 不影響數據蒐集，改名後 systemd restart 即可 |
| **V7 P3 ATR 滑價** | Phase 2 | 可作為策略內部 state 實作，不需改 bot.py |
| **決策品質驗證** | Phase 1-2 | Phase 1 analysis fields 全數保留，Phase 2 state isolation 讓分析更清晰 |
| **資本重分配** | Phase 2 | 動態風險乘數可在 strategy 層或 risk manager 層加，插件化後更靈活 |
| **黑天鵝防護** | Phase 3 | 全倉緊急平倉需要 bot.py 有清晰的平倉路徑，Phase 3 提煉後更安全 |
| **Shadow Mode** | Phase 3 | 主進程/影子進程共用同一 bot 類但不同策略，Phase 3 職責拆分是前提 |
| **遷移 DEX** | Phase 2 | 插件化後，DEX 可作為新的 execution engine 插入，策略層不需改 |
| **遠端監控** | Phase 0 | PLAN.md 引用更新即可，尚未實作不影響 |
| **網格/量能新策略** | Phase 2 | 完成後只需：新 class + register + config 映射 |

---

## 數據安全確認清單

| 數據 | 檔案 | Phase 0-3 影響 |
|---|---|---|
| 歷史交易績效 | `v6_performance.db` | ❌ 不改檔名、不改 schema |
| 持倉狀態 | `.log/positions.json` | ❌ Phase 2 向下相容（`from_dict` fallback） |
| Bot 日誌 | `.log/v6_bot.log` | ❌ 不改檔名 |
| 交易日誌 | `.log/v6_trades.log` | ❌ 不改檔名 |
| Scanner 結果 | `hot_symbols.json` | ❌ 不影響 |
| Scanner DB | `scanner_results.db` | ❌ 不影響 |
| Config | `bot_config.json` | ❌ Phase 1-2 只移除已廢棄 key |
| Secrets | `secrets.json` | ❌ 完全不動 |

---

## 執行建議

1. **Phase 0 + 1 一起做**（同一個 session，先改名再瘦身，避免做兩次 grep）
2. **Phase 2 獨立做**（介面改動較大，需要完整 regression）
3. **Phase 3 視需要做**（Shadow Mode 啟動前再做）
4. **每 Phase 完成後**在 rwUbuntu 跑完整 test + 重啟服務確認
5. **建議在無持倉時執行**，降低 positions.json 遷移風險

---

*本文件由小波與 Ruei 於 2026-03-16 協作產出。*
