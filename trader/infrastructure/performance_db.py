"""
Performance database for recording trade outcomes.
Writes to SQLite on every position close.
Why: Provides raw data for Phase 1 decision quality analysis (EV, MFE/MAE, capture ratio).
"""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id         TEXT UNIQUE,
    symbol           TEXT    NOT NULL,
    side             TEXT    NOT NULL,
    is_v6_pyramid    INTEGER NOT NULL,
    signal_tier      TEXT,
    entry_price      REAL    NOT NULL,
    exit_price       REAL    NOT NULL,
    total_size       REAL    NOT NULL,
    initial_r        REAL    NOT NULL,
    entry_time       TEXT    NOT NULL,
    exit_time        TEXT    NOT NULL,
    holding_hours    REAL    NOT NULL,
    pnl_usdt         REAL    NOT NULL,
    pnl_pct          REAL    NOT NULL,
    realized_r       REAL    NOT NULL,
    mfe_pct          REAL    NOT NULL,
    mae_pct          REAL    NOT NULL,
    capture_ratio    REAL,
    stage_reached    INTEGER NOT NULL,
    exit_reason      TEXT    NOT NULL,
    market_regime    TEXT,
    entry_adx        REAL,
    fakeout_depth_atr REAL,
    reverse_2b_depth_atr REAL,
    original_size        REAL,
    partial_pnl_usdt     REAL,
    btc_trend_aligned    INTEGER,
    trend_adx            REAL,
    mtf_aligned          INTEGER,
    volume_grade         TEXT,
    tier_score           INTEGER,
    strategy_name        TEXT,
    created_at       TEXT    DEFAULT (datetime('now'))
);
"""

INSERT_SQL = """
INSERT OR IGNORE INTO trades (
    trade_id, symbol, side, is_v6_pyramid, signal_tier,
    entry_price, exit_price, total_size, initial_r,
    entry_time, exit_time, holding_hours,
    pnl_usdt, pnl_pct, realized_r,
    mfe_pct, mae_pct, capture_ratio,
    stage_reached, exit_reason, market_regime,
    entry_adx, fakeout_depth_atr, reverse_2b_depth_atr,
    original_size, partial_pnl_usdt,
    btc_trend_aligned,
    trend_adx, mtf_aligned, volume_grade, tier_score,
    strategy_name
) VALUES (
    :trade_id, :symbol, :side, :is_v6_pyramid, :signal_tier,
    :entry_price, :exit_price, :total_size, :initial_r,
    :entry_time, :exit_time, :holding_hours,
    :pnl_usdt, :pnl_pct, :realized_r,
    :mfe_pct, :mae_pct, :capture_ratio,
    :stage_reached, :exit_reason, :market_regime,
    :entry_adx, :fakeout_depth_atr, :reverse_2b_depth_atr,
    :original_size, :partial_pnl_usdt,
    :btc_trend_aligned,
    :trend_adx, :mtf_aligned, :volume_grade, :tier_score,
    :strategy_name
);
"""


class PerformanceDB:
    def __init__(self, db_path: str = "v6_performance.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(CREATE_TABLE_SQL)
                # Migration: 新增 Phase 1 分析欄位（idempotent，欄位已存在會靜默跳過）
                for col_sql in [
                    "ALTER TABLE trades ADD COLUMN entry_adx REAL",
                    "ALTER TABLE trades ADD COLUMN fakeout_depth_atr REAL",
                    "ALTER TABLE trades ADD COLUMN reverse_2b_depth_atr REAL",
                    "ALTER TABLE trades ADD COLUMN original_size REAL",
                    "ALTER TABLE trades ADD COLUMN partial_pnl_usdt REAL",
                    "ALTER TABLE trades ADD COLUMN btc_trend_aligned INTEGER",
                    "ALTER TABLE trades ADD COLUMN trend_adx REAL",
                    "ALTER TABLE trades ADD COLUMN mtf_aligned INTEGER",
                    "ALTER TABLE trades ADD COLUMN volume_grade TEXT",
                    "ALTER TABLE trades ADD COLUMN tier_score INTEGER",
                    "ALTER TABLE trades ADD COLUMN strategy_name TEXT",
                ]:
                    try:
                        conn.execute(col_sql)
                    except sqlite3.OperationalError:
                        pass  # 欄位已存在，正常跳過
                conn.commit()
            logger.info(f"PerformanceDB initialized: {self.db_path}")
        except Exception as e:
            # Non-fatal: DB failure must not crash the bot
            logger.error(f"PerformanceDB init failed: {e}")

    def record_trade(self, data: dict) -> bool:
        """
        Write one trade record. Returns True on success.
        Non-fatal: logs error and returns False on failure.
        """
        try:
            data = dict(data)  # 不改動呼叫方的 dict
            data.setdefault('original_size', None)
            data.setdefault('partial_pnl_usdt', None)
            data.setdefault('btc_trend_aligned', None)
            data.setdefault('reverse_2b_depth_atr', None)
            data.setdefault('trend_adx', None)
            data.setdefault('mtf_aligned', None)
            data.setdefault('volume_grade', None)
            data.setdefault('tier_score', None)
            data.setdefault('strategy_name', None)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(INSERT_SQL, data)
                conn.commit()
            logger.info(f"PerformanceDB recorded: {data.get('trade_id')} {data.get('symbol')} R={data.get('realized_r', 0):.2f}")
            return True
        except Exception as e:
            logger.error(f"PerformanceDB record_trade failed: {e} | data={data}")
            return False

    def get_last_loss_exit_time(self, symbol: str) -> str | None:
        """
        Query the most recent exit_time for a losing trade on the given symbol.
        Returns ISO timestamp string, or None if no losing trade found.
        Non-fatal: returns None on any error.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT exit_time FROM trades "
                    "WHERE symbol = ? AND pnl_usdt < 0 "
                    "ORDER BY exit_time DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.warning(f"PerformanceDB get_last_loss_exit_time failed: {e}")
            return None
