"""
Tests: PositionManager Phase 1 欄位持久化
確保 entry_adx / fakeout_depth_atr 在 to_dict/from_dict round-trip 後不遺失
"""
import pytest
from trader.positions import PositionManager


def _make_pm(**kwargs) -> PositionManager:
    defaults = dict(
        symbol='BTC/USDT', side='LONG', entry_price=50000.0,
        stop_loss=49000.0, position_size=0.01, is_v6_pyramid=True,
        initial_r=100.0,
    )
    defaults.update(kwargs)
    return PositionManager(**defaults)


class TestPhase1FieldPersist:

    def test_entry_adx_roundtrip(self):
        """entry_adx 設值後，to_dict/from_dict 後應保留"""
        pm = _make_pm()
        pm.entry_adx = 28.5
        data = pm.to_dict()
        assert data['entry_adx'] == 28.5
        pm2 = PositionManager.from_dict(data)
        assert pm2.entry_adx == 28.5

    def test_fakeout_depth_atr_roundtrip(self):
        """fakeout_depth_atr 設值後，to_dict/from_dict 後應保留"""
        pm = _make_pm()
        pm.fakeout_depth_atr = 0.45
        data = pm.to_dict()
        assert data['fakeout_depth_atr'] == 0.45
        pm2 = PositionManager.from_dict(data)
        assert pm2.fakeout_depth_atr == 0.45

    def test_phase1_fields_default_none(self):
        """未設值時，to_dict 輸出 None，from_dict 還原也是 None"""
        pm = _make_pm()
        data = pm.to_dict()
        assert data['entry_adx'] is None
        assert data['fakeout_depth_atr'] is None
        pm2 = PositionManager.from_dict(data)
        assert pm2.entry_adx is None
        assert pm2.fakeout_depth_atr is None
