"""
Telegram 指令處理器

Polling 模式接收 Telegram 指令，回覆倉位/狀態/餘額資訊。
"""

import html
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from trader.config import Config

logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    """Telegram Bot 指令處理（Polling 模式）"""

    def __init__(self, bot):
        """
        Args:
            bot: TradingBot instance（存取 active_trades / risk_manager）
        """
        self.bot = bot
        self.last_update_id = 0
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"

    def poll(self):
        """檢查新訊息並處理指令。主 loop 每 cycle 呼叫一次。"""
        if not Config.TELEGRAM_ENABLED:
            return

        try:
            updates = self._get_updates()
            for update in updates:
                self._handle_update(update)
        except Exception as e:
            logger.debug(f"Telegram poll 錯誤: {e}")

    def _get_updates(self) -> list:
        """取得新訊息（long polling timeout=0，非阻塞）"""
        url = f"{self.base_url}/getUpdates"
        params = {
            'offset': self.last_update_id + 1,
            'timeout': 0,
            'allowed_updates': '["message"]',
        }
        resp = requests.get(url, params=params, timeout=5)
        if not resp.ok:
            return []

        data = resp.json()
        return data.get('result', [])

    def _handle_update(self, update: dict):
        """處理單一 update"""
        update_id = update.get('update_id', 0)
        if update_id > self.last_update_id:
            self.last_update_id = update_id

        message = update.get('message', {})
        chat_id = str(message.get('chat', {}).get('id', ''))
        text = message.get('text', '').strip()

        # 安全：只回應自己的 chat_id
        if chat_id != str(Config.TELEGRAM_CHAT_ID):
            return

        if not text.startswith('/'):
            return

        cmd = text.split()[0].lower()
        # 去掉 @botname 後綴（群組中會帶 /positions@boboTrading_bot）
        cmd = cmd.split('@')[0]

        handlers = {
            '/positions': self._cmd_positions,
            '/status': self._cmd_status,
            '/balance': self._cmd_balance,
            '/help': self._cmd_help,
        }

        handler = handlers.get(cmd)
        if handler:
            try:
                reply = handler()
                self._send_reply(chat_id, reply)
            except Exception as e:
                logger.error(f"Telegram 指令 {cmd} 執行失敗: {e}")
                self._send_reply(chat_id, f"<b>Error:</b> {html.escape(str(e))}")

    def _send_reply(self, chat_id: str, text: str):
        """發送回覆"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }
        try:
            resp = requests.post(url, data=payload, timeout=10)
            if not resp.ok:
                logger.error(f"Telegram 回覆失敗: {resp.status_code}")
        except Exception as e:
            logger.error(f"Telegram 回覆失敗: {e}")

    # ==================== 指令實作 ====================

    def _cmd_positions(self) -> str:
        """列出目前所有開倉部位"""
        trades = self.bot.active_trades
        if not trades:
            return "<b>目前無開倉部位</b>"

        lines = [f"<b>開倉部位 ({len(trades)})</b>", "──────────────────"]

        for symbol, pm in trades.items():
            now = datetime.now(timezone.utc)
            hold_hours = (now - pm.entry_time).total_seconds() / 3600
            strategy = 'V6' if pm.is_v6_pyramid else 'V53'

            # 未實現 PnL 估算（用 highest/lowest 近似，無即時價格）
            if pm.side == 'LONG':
                pnl_pct = (pm.highest_price - pm.avg_entry) / pm.avg_entry * 100
            else:
                pnl_pct = (pm.avg_entry - pm.lowest_price) / pm.avg_entry * 100
            pnl_emoji = '+' if pnl_pct >= 0 else ''

            lines.append(
                f"\n<b>{html.escape(symbol)}</b> {pm.side} ({strategy})\n"
                f"  入場: ${pm.avg_entry:.4f}\n"
                f"  止損: ${pm.stop_loss:.4f}\n"
                f"  倉位: {pm.position_size:.6f}\n"
                f"  階段: Stage {pm.stage}\n"
                f"  Tier: {pm.signal_tier}\n"
                f"  持倉: {hold_hours:.1f}h\n"
                f"  MFE: {pnl_emoji}{pnl_pct:.2f}%"
            )

        return "\n".join(lines)

    def _cmd_status(self) -> str:
        """Bot 運行狀態"""
        trades = self.bot.active_trades
        active_count = len(trades)

        # 啟動時間
        start_time = getattr(self.bot, '_start_time', None)
        if start_time:
            uptime_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600
            uptime_str = f"{uptime_hours:.1f}h"
        else:
            uptime_str = "N/A"

        # 策略分佈
        v6_count = sum(1 for pm in trades.values() if pm.is_v6_pyramid)
        v53_count = active_count - v6_count

        lines = [
            "<b>Bot Status</b>",
            "──────────────────",
            f"運行時間: {uptime_str}",
            f"活躍倉位: {active_count}",
            f"  V6: {v6_count} | V53: {v53_count}",
            f"監控幣種: {len(Config.SYMBOLS)}",
            f"DRY RUN: {'Yes' if Config.V6_DRY_RUN else 'No'}",
        ]

        return "\n".join(lines)

    def _cmd_balance(self) -> str:
        """帳戶餘額"""
        try:
            if Config.V6_DRY_RUN:
                balance = 10000.0
            else:
                balance = self.bot.risk_manager.get_balance()
        except Exception:
            balance = 0.0

        initial = getattr(self.bot, 'initial_balance', None)
        pnl_line = ""
        if initial and initial > 0:
            pnl = balance - initial
            pnl_pct = pnl / initial * 100
            emoji = '+' if pnl >= 0 else ''
            pnl_line = f"\n本次 PnL: {emoji}${pnl:.2f} ({emoji}{pnl_pct:.2f}%)"

        lines = [
            "<b>帳戶餘額</b>",
            "──────────────────",
            f"可用餘額: ${balance:.2f} USDT",
            f"{pnl_line}" if pnl_line else "",
        ]

        return "\n".join(line for line in lines if line)

    def _cmd_help(self) -> str:
        """指令說明"""
        return (
            "<b>可用指令</b>\n"
            "──────────────────\n"
            "/positions — 目前開倉部位\n"
            "/status — Bot 運行狀態\n"
            "/balance — 帳戶餘額\n"
            "/help — 顯示本說明"
        )
