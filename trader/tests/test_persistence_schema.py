"""positions.json schema version 測試"""

import json
import pytest
from trader.persistence import PositionPersistence


class TestSchemaVersion:

    def test_save_writes_schema_version(self, tmp_path):
        """save 後 JSON 包含 schema_version"""
        pp = PositionPersistence(str(tmp_path / 'pos.json'))
        pp.save_positions({'BTC/USDT': {'symbol': 'BTC/USDT', 'side': 'LONG'}})

        with open(tmp_path / 'pos.json') as f:
            raw = json.load(f)

        assert raw['schema_version'] == 2
        assert 'BTC/USDT' in raw['positions']

    def test_load_v2_format(self, tmp_path):
        """v2 envelope → 正確解包"""
        data = {
            "schema_version": 2,
            "positions": {"ETH/USDT": {"symbol": "ETH/USDT", "side": "SHORT"}}
        }
        with open(tmp_path / 'pos.json', 'w') as f:
            json.dump(data, f)

        pp = PositionPersistence(str(tmp_path / 'pos.json'))
        result = pp.load_positions()
        assert 'ETH/USDT' in result
        assert result['ETH/USDT']['side'] == 'SHORT'

    def test_load_v1_legacy(self, tmp_path):
        """v1（無 schema_version）→ 直接當 positions 用"""
        data = {"SOL/USDT": {"symbol": "SOL/USDT", "side": "LONG"}}
        with open(tmp_path / 'pos.json', 'w') as f:
            json.dump(data, f)

        pp = PositionPersistence(str(tmp_path / 'pos.json'))
        result = pp.load_positions()
        assert 'SOL/USDT' in result

    def test_load_future_version_warns(self, tmp_path, caplog):
        """未來版本 → warning but still loads"""
        data = {
            "schema_version": 99,
            "positions": {"BTC/USDT": {"symbol": "BTC/USDT"}}
        }
        with open(tmp_path / 'pos.json', 'w') as f:
            json.dump(data, f)

        pp = PositionPersistence(str(tmp_path / 'pos.json'))
        result = pp.load_positions()
        assert 'BTC/USDT' in result
        assert any('schema_version=99' in r.message for r in caplog.records)

    def test_roundtrip(self, tmp_path):
        """save → load → 資料完整"""
        pp = PositionPersistence(str(tmp_path / 'pos.json'))
        original = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'side': 'LONG', 'stage': 2},
            'ETH/USDT': {'symbol': 'ETH/USDT', 'side': 'SHORT', 'stage': 1},
        }
        pp.save_positions(original)
        loaded = pp.load_positions()

        assert set(loaded.keys()) == set(original.keys())
        assert loaded['BTC/USDT']['stage'] == 2
