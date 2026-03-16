# 最後更新：2026-03-16
import os
import ast
from pathlib import Path

# --- 設定區 ---
PROJECT_ROOT = r"/home/rwfunder/文件/tradingbot/trading_bot_v6"
OUTPUT_FILE = "project_structure_map_v3.md"

IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "build", "dist", "tests", ".pytest_cache", ".log"}
IGNORE_FILES = {"__init__.py", "tempCodeRunnerFile.py"}

# 已廢棄的檔案，只顯示標記不展開（filename → 說明）
DEPRECATED_STUBS = {
    "core.py":          "re-export stub（拆分四層後廢棄，勿直接 import）",
    "tradingStart.py":  "舊入口點（Bot/Scanner 分離後廢棄，改用 systemd trader/scanner.service）",
}

# 掃描結束後附加的重要非 Python 檔案（相對於 PROJECT_ROOT）
KEY_NON_PYTHON_FILES = [
    ("secrets.json",              "API keys + Telegram tokens — ⚠️ 勿 commit（.gitignore）"),
    ("bot_config.json",           "交易參數（無 secrets，可 commit）"),
    ("positions.json",            "Runtime 持倉狀態（PositionPersistence 讀寫）"),
    ("v6_performance.db",         "交易績效 SQLite（MFE/MAE/capture_ratio/market_regime）"),
    ("hot_symbols.json",          "Scanner 輸出的熱門標的清單"),
    ("scanner/scanner_config.json", "Scanner 專屬設定"),
    ("requirements.txt",          "Python 依賴清單"),
    ("scanner_results.db",        "Scanner SQLite 輸出"),
]

ARCHITECTURE_OVERVIEW = """\
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
│   └── performance_db.py← PerformanceDB（SQLite v6_performance.db，平倉自動寫入）
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

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node):
        module = node.module or ""
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
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
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

        calls_str = f" [Calls: {', '.join(set(internal_calls))}]" if internal_calls else ""

        prefix = "  - Method:" if self.current_class else "- Function:"
        self.results.append(f"{prefix} `{node.name}({', '.join(args)})`{returns}{calls_str}{doc_str}")


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
            if file == os.path.basename(__file__):
                continue
            if file in IGNORE_FILES:
                continue

            rel_path = os.path.relpath(os.path.join(root, file), PROJECT_ROOT)

            # 已廢棄檔案：只標記，不展開
            if file in DEPRECATED_STUBS:
                reason = DEPRECATED_STUBS[file]
                output_content.append(f"## 📄 File: `{rel_path}` ⚠️ Deprecated — {reason}\n\n---\n")
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
