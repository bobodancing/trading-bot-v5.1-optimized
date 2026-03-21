"""
Position 持久化層

管理 positions.json 的讀寫，確保 crash recovery 和狀態一致性。
採用 atomic write（temp file + rename）避免寫入過程中 crash 造成檔案損壞。
"""

import json
import os
import tempfile
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class PositionPersistence:
    """Position 狀態持久化管理"""

    def __init__(self, file_path: str = "positions.json"):
        """
        初始化持久化管理器

        Args:
            file_path: positions.json 檔案路徑
                      - 如果是相對路徑，會相對於當前目錄（建議 bot 啟動時先 os.chdir 到專案根目錄）
                      - 如果是絕對路徑，直接使用
        """
        self.file_path = os.path.expanduser(file_path)
        self.encoding = 'utf-8'

    def save_positions(self, positions_data: Dict[str, Dict[str, Any]]) -> bool:
        """
        儲存 positions 到 JSON 檔案（atomic write）

        使用 temp file + rename 確保 atomic：
        1. 寫入到 temp file
        2. Flush to disk
        3. Rename to target（原子操作）

        如果 crash 發生在步驟 1-2，原檔案不受影響
        如果 crash 發生在步驟 3 rename 時，OS 會確保操作的 atomic

        Args:
            positions_data: {
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "side": "LONG",
                    "stage": 1,
                    "entries": [...],
                    "total_size": 0.035,
                    "avg_entry": 95000.0,
                    "current_sl": 94200.0,
                    "initial_sl": 94200.0,
                    "initial_r": 170.0,
                    "neckline": 96500.0,
                    "equity_base": 10000.0,
                    "stop_order_id": "12345",
                    "entry_time": "ISO8601",
                    "highest_price": 95500.0,
                    "lowest_price": 95000.0,
                    "last_updated": "ISO8601",
                    "is_v6_pyramid": true,
                    "v53_state": null
                }
            }

        Returns:
            bool: 成功 True，失敗 False
        """
        try:
            # 更新所有 position 的 last_updated timestamp
            for symbol, pos_data in positions_data.items():
                pos_data['last_updated'] = datetime.now(timezone.utc).isoformat()

            # 包裝 envelope（schema version + positions data）
            envelope = {
                "schema_version": 2,
                "positions": positions_data,
            }

            # 準備寫入內容（pretty print for readability）
            json_content = json.dumps(envelope, indent=2, ensure_ascii=False)

            # Atomic write: 先寫到 temp，再 rename
            import uuid
            # 建立 temp file 在同一目錄（確保 rename atomic）
            dir_path = os.path.dirname(os.path.abspath(self.file_path))
            os.makedirs(dir_path, exist_ok=True)
            base_name = os.path.basename(self.file_path)
            tmp_filename = f'.{base_name}.tmp_{uuid.uuid4().hex[:8]}'
            tmp_path = os.path.join(dir_path, tmp_filename)

            # 寫入 temp file
            with open(tmp_path, 'w', encoding=self.encoding) as tmp_file:
                tmp_file.write(json_content)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            # Atomic rename（same directory, so it's atomic on all OS）
            os.replace(tmp_path, self.file_path)

            logger.debug(f"✅ Positions saved: {len(positions_data)} active")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to save positions: {e}")
            # Clean up temp file if exists
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            return False

    def load_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        從 JSON 檔案讀取 positions

        Returns:
            positions_data dict，如果檔案不存在或讀取失敗則回傳空 dict
        """
        if not os.path.exists(self.file_path):
            logger.info(f"ℹ️ positions.json not found, starting fresh")
            return {}

        try:
            with open(self.file_path, 'r', encoding=self.encoding) as f:
                raw = json.load(f)

            # schema version 解析（向下相容）
            if isinstance(raw, dict) and 'schema_version' in raw:
                version = raw['schema_version']
                positions_data = raw.get('positions', {})
                if version > 2:
                    logger.warning(
                        f"⚠️ positions.json schema_version={version} > expected 2, "
                        f"attempting to load (may have compatibility issues)"
                    )
                logger.info(f"✅ Loaded {len(positions_data)} positions from disk (schema v{version})")
            else:
                # v1（無版本號）：raw 就是 positions dict
                positions_data = raw
                logger.info(f"✅ Loaded {len(positions_data)} positions from disk (schema v1, legacy)")

            return positions_data

        except json.JSONDecodeError as e:
            logger.error(f"❌ positions.json corrupted: {e}")
            # Backup corrupted file
            backup_path = f"{self.file_path}.corrupted.{int(datetime.now().timestamp())}"
            try:
                os.rename(self.file_path, backup_path)
                logger.warning(f"⚠️ Corrupted file backed up to {backup_path}")
            except:
                pass
            return {}

        except Exception as e:
            logger.error(f"❌ Failed to load positions: {e}")
            return {}

    def reconcile_with_exchange(
        self,
        positions_data: Dict[str, Dict[str, Any]],
        exchange_positions: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """
        比對 positions.json 和交易所實際持倉，修正不一致

        Reconciliation 規則：
        1. 交易所有持倉但 positions.json 沒有 → 警告（可能是手動下單或 positions.json 遺失）
        2. positions.json 有但交易所沒有 → 移除（可能被 hard stop 打掉或手動平倉）
        3. 數量不一致 → 更新為交易所數量（交易所是 source of truth）

        Args:
            positions_data: 從 positions.json 讀取的資料
            exchange_positions: 從交易所 API 取得的實際持倉
                {
                    "BTC/USDT": {
                        "side": "long",
                        "contracts": 0.035,
                        "entry_price": 95000.0,
                        ...
                    }
                }

        Returns:
            reconciled positions_data
        """
        reconciled = positions_data.copy()
        symbols_to_remove = []

        # Check 1: positions.json 有但交易所沒有 → 移除
        for symbol in positions_data.keys():
            if symbol not in exchange_positions or exchange_positions[symbol]['contracts'] == 0:
                logger.warning(f"⚠️ Position {symbol} in JSON but not on exchange, removing")
                symbols_to_remove.append(symbol)

        for symbol in symbols_to_remove:
            del reconciled[symbol]

        # Check 2: 交易所有但 positions.json 沒有 → 警告（不新增，因為缺少 V6.0 metadata）
        for symbol, exch_pos in exchange_positions.items():
            if exch_pos['contracts'] > 0 and symbol not in reconciled:
                logger.error(
                    f"❌ CRITICAL: Position {symbol} exists on exchange but not in positions.json. "
                    f"This may be a manual trade or data loss. Size: {exch_pos['contracts']}"
                )

        # Check 3: 數量不一致 → 更新（交易所是 source of truth）
        for symbol in reconciled.keys():
            if symbol in exchange_positions:
                json_size = reconciled[symbol].get('total_size', 0)
                exch_size = exchange_positions[symbol]['contracts']

                if abs(json_size - exch_size) > 0.0001:  # Float 精度容差
                    logger.warning(
                        f"⚠️ Size mismatch {symbol}: JSON={json_size} vs Exchange={exch_size}, "
                        f"updating to exchange value"
                    )
                    reconciled[symbol]['total_size'] = exch_size

        return reconciled

    def backup_positions(self) -> Optional[str]:
        """
        備份當前 positions.json（用於重大操作前）

        Returns:
            備份檔案路徑，失敗則回傳 None
        """
        if not os.path.exists(self.file_path):
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.file_path}.backup.{timestamp}"

            with open(self.file_path, 'r', encoding=self.encoding) as src:
                content = src.read()

            with open(backup_path, 'w', encoding=self.encoding) as dst:
                dst.write(content)

            logger.info(f"✅ Backup created: {backup_path}")
            return backup_path

        except Exception as e:
            logger.error(f"❌ Failed to backup positions: {e}")
            return None

    def clear_positions(self) -> bool:
        """
        清空 positions.json（慎用！）

        Returns:
            成功 True，失敗 False
        """
        try:
            # 先備份
            self.backup_positions()

            # 寫入空 dict
            return self.save_positions({})

        except Exception as e:
            logger.error(f"❌ Failed to clear positions: {e}")
            return False
