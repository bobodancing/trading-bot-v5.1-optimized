# 最後更新：2026-04-04
import os
import ast
import sys

# --- 設定區 ---
PROJECT_ROOT = r"/home/rwfunder/文件/tradingbot/trading_bot"
OUTPUT_FILE = "project_structure_map_v3.md"

IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "build", "dist", "tests", ".pytest_cache", ".log"}
IGNORE_FILES = {"__init__.py", "tempCodeRunnerFile.py"}

# 已廢棄的檔案，只顯示標記不展開（rel_path → 說明，避免不同目錄同名誤判）
DEPRECATED_STUBS = {
    "core.py":          "re-export stub（拆分四層後廢棄，勿直接 import）",
    "tradingStart.py":  "舊入口點（Bot/Scanner 分離後廢棄，改用 systemd trader/scanner.service）",
}

# stdlib 模組名（過濾 Dependencies 噪音，只保留 project-internal + third-party）
STDLIB_MODULES = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else {
    "os", "sys", "json", "time", "datetime", "logging", "pathlib", "re", "math",
    "collections", "functools", "itertools", "typing", "abc", "enum", "dataclasses",
    "hashlib", "hmac", "copy", "threading", "asyncio", "signal", "traceback",
    "unittest", "sqlite3", "urllib", "contextlib", "io", "struct", "decimal",
}

# 掃描結束後附加的重要非 Python 檔案（相對於 PROJECT_ROOT）
KEY_NON_PYTHON_FILES = [
    ("secrets.json",              "API keys + Telegram tokens — ⚠️ 勿 commit（.gitignore）"),
    ("bot_config.json",           "交易參數（無 secrets，可 commit）"),
    ("positions.json",            "Runtime 持倉狀態（PositionPersistence 讀寫）"),
    ("performance.db",            "交易績效 SQLite（MFE/MAE/capture_ratio/market_regime）"),
    ("hot_symbols.json",          "Scanner 輸出的熱門標的清單"),
    ("scanner/scanner_config.json", "Scanner 專屬設定"),
    ("grid_positions.json",       "Grid runtime state + pool snapshot（schema v2，atomic write）"),
    ("requirements.txt",          "Python 依賴清單"),
    ("scanner_results.db",        "Scanner SQLite 輸出"),
]

ARCHITECTURE_OVERVIEW = """\
## 🗺 架構總覽

> 雙 systemd 服務：trader.service（trader/bot.py）+ scanner.service（scanner/market_scanner.py）
> tradingStart.py 已廢棄（Bot/Scanner 分離後不再需要）

```
scanner/
└── market_scanner.py    ← 四層 Scanner（流動性→動能→形態→板塊集中度）[scanner.service]

trader/                  ← [trader.service]
├── bot.py               ← TradingBotV6 主引擎（scan→_monitor_grid_state→hedge-aware sync→monitor）
├── positions.py         ← PositionManager（strategy_name 插件 + Stage 管理 + 出場委派）
├── signals.py           ← detect_2b_with_pivots / ema_pullback / volume_breakout（入場信號）
├── structure.py         ← StructureAnalysis（swing point / neckline / BOS 追蹤）
├── config.py            ← Config（交易參數 + SIGNAL_STRATEGY_MAP；secrets 另存 secrets.json）
├── persistence.py       ← PositionPersistence（atomic write）+ grid state persistence（schema v2）
├── regime.py            ← RegimeEngine（TRENDING/RANGING/SQUEEZE，ADX+BBW+ATR，3-candle hysteresis）
├── infrastructure/
│   ├── api_client.py    ← BinanceFuturesClient（HMAC 簽章 + recvWindow + hedge mode）
│   ├── data_provider.py ← MarketDataProvider（retry + sandbox fallback + DatetimeIndex）
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
    ├── v54_noscale.py   ← V54NoScaleStrategy（主力；1.0R/1.5R/2.0R 純移損 + ATR trailing）
    ├── v53_sop.py       ← V53SopStrategy（1.0R/1.5R/2.0R 分批減倉；新進場停用）
    ├── v7_structure.py  ← V7StructureStrategy（三段結構加倉 + 反向 2B + 超時）
    ├── v6_pyramid.py    ← [deprecated] V6PyramidStrategy（既有倉位保留）
    └── v8_grid/         ← V8 ATR Grid 策略插件（BTC RANGING 網格）
        ├── grid.py      ← V8AtrGrid（SMA±k*ATR 虛擬網格，4H canonical，regime exit 全平）
        └── pool_manager.py ← PoolManager（Grid/Trend 資金池隔離 + pool snapshot 持久化）
```
"""


def _first_line(doc: str) -> str:
    """只取 docstring 第一個非空行。"""
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


class EnhancedProjectMapper(ast.NodeVisitor):
    def __init__(self, file_path):
        self.file_path = file_path
        self.results = []
        self.current_class = None
        self.imports = []
        self.constants = []

    def _is_stdlib(self, name: str) -> bool:
        top = name.split(".")[0]
        return top in STDLIB_MODULES

    def visit_Import(self, node):
        for alias in node.names:
            if not self._is_stdlib(alias.name):
                self.imports.append(alias.name)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        if self._is_stdlib(module):
            return
        names = [alias.name for alias in node.names]
        self.imports.append(f"{module}.({', '.join(names)})")

    def visit_Assign(self, node):
        if not self.current_class:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    self.constants.append(target.id)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        base_classes = [ast.unparse(b) for b in node.bases]
        bases_str = f" (Inherits: {', '.join(base_classes)})" if base_classes else ""
        doc = _first_line(ast.get_docstring(node))
        doc_str = f" — {doc}" if doc else ""

        self.results.append(f"### Class: `{node.name}`{bases_str}{doc_str}")

        # 抓 __init__ 裡的 self.xxx 屬性
        properties = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
                for sub_item in ast.walk(item):
                    if isinstance(sub_item, ast.Attribute) and isinstance(sub_item.value, ast.Name):
                        if sub_item.value.id == 'self':
                            properties.append(sub_item.attr)

        if properties:
            unique_props = sorted(set(properties))
            # 超過 12 個只顯示前 12 個 + 數量提示
            if len(unique_props) > 12:
                shown = unique_props[:12]
                self.results.append(f"    - **Properties:** `{', '.join(shown)}` ... (+{len(unique_props) - 12} more)")
            else:
                self.results.append(f"    - **Properties:** `{', '.join(unique_props)}`")

        self.current_class = node
        self.generic_visit(node)
        self.current_class = None

    def visit_FunctionDef(self, node):
        args = [a.arg for a in node.args.args]
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        doc = _first_line(ast.get_docstring(node))
        doc_str = f" — {doc}" if doc else ""

        internal_calls = []
        for sub_node in ast.walk(node):
            if isinstance(sub_node, ast.Call) and isinstance(sub_node.func, ast.Attribute):
                if isinstance(sub_node.func.value, ast.Name) and sub_node.func.value.id == 'self':
                    internal_calls.append(sub_node.func.attr)

        calls_str = f" [Calls: {', '.join(dict.fromkeys(internal_calls))}]" if internal_calls else ""

        prefix = "  - Method:" if self.current_class else "- Function:"
        self.results.append(f"{prefix} `{node.name}({', '.join(args)})`{returns}{calls_str}{doc_str}")

    # async def 也走同一邏輯
    visit_AsyncFunctionDef = visit_FunctionDef


def scan_project():
    output_content = [
        "# 🚀 Project Structure Map V3 (AI-Optimized)",
        "依賴關係、類別屬性、內部呼叫，專為 AI Agent 快速定位設計。Docstring 只保留第一行。\n",
        ARCHITECTURE_OVERVIEW,
        "---",
    ]

    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for file in sorted(files):
            if not file.endswith(".py"):
                continue
            if file in IGNORE_FILES:
                continue

            rel_path = os.path.relpath(os.path.join(root, file), PROJECT_ROOT)

            # 已廢棄檔案：只標記，不展開（用 rel_path 或 filename 都查）
            dep_reason = DEPRECATED_STUBS.get(rel_path) or DEPRECATED_STUBS.get(file)
            if dep_reason:
                output_content.append(f"## 📄 File: `{rel_path}` ⚠️ Deprecated — {dep_reason}\n\n---\n")
                continue

            output_content.append(f"## 📄 File: `{rel_path}`")

            try:
                with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                    mapper = EnhancedProjectMapper(rel_path)
                    mapper.visit(tree)

                    if mapper.imports:
                        output_content.append(f"**Dependencies:** `{', '.join(mapper.imports)}`")
                    if mapper.constants:
                        output_content.append(f"**Constants:** `{', '.join(mapper.constants)}`")

                    output_content.extend(mapper.results)
            except Exception as e:
                output_content.append(f"⚠️ 解析失敗: {e}")

            output_content.append("\n---\n")

    # 附加重要非 Python 檔案清單
    output_content.append("## 📁 Key Non-Python Files\n")
    for fname, desc in KEY_NON_PYTHON_FILES:
        output_content.append(f"- `{fname}` — {desc}")

    with open(os.path.join(PROJECT_ROOT, OUTPUT_FILE), "w", encoding="utf-8") as f:
        f.write("\n".join(output_content))
    print(f"✅ V3 地圖已生成：{OUTPUT_FILE}")


if __name__ == "__main__":
    scan_project()
