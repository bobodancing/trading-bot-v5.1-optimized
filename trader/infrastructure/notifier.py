"""
Telegram 通知器

封裝所有 Telegram Bot 推送邏輯，從 v6/core.py 提取。
"""

import logging
import requests
from typing import Dict

from trader.config import Config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram 推送通知類"""

    @staticmethod
    def send_message(message: str):
        if not Config.TELEGRAM_ENABLED:
            return

        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': Config.TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML'
            }
            requests.post(url, data=payload, timeout=10)
        except Exception as e:
            logger.error(f"Telegram 發送失敗: {e}")

    @staticmethod
    def notify_signal(symbol: str, details: Dict):
        """通知交易信號"""
        strength_emoji = {
            'explosive': '🔥🔥🔥',
            'strong': '💪💪',
            'moderate': '✅',
            'weak': '⚠️'
        }
        tier_emoji = {
            'A': '🏆',
            'B': '🥈',
            'C': '🥉'
        }
        strength = details.get('signal_strength', 'unknown')
        tier = details.get('signal_tier', 'B')
        emoji = strength_emoji.get(strength, '🚀')
        side = details.get('side', 'LONG')

        msg = f"""
{emoji} <b>交易信號 - {strength.upper()} ({side})</b>
{tier_emoji.get(tier, '')} 信號等級: {tier}
──────────────────
幣種: {symbol}
方向: {side}
市場狀態: {details.get('market_state', 'N/A')}
量能強度: {details.get('vol_ratio', 0):.2f}x 均量
入場價: ${details['entry_price']:.2f}
止損價: ${details['stop_loss']:.2f}
目標位: ${details.get('target_ref', 'N/A')}
倉位: {details['position_size']:.6f}
1.5R: ${details.get('r15_target', 'N/A')}
──────────────────
        """
        TelegramNotifier.send_message(msg.strip())

    @staticmethod
    def notify_action(symbol: str, action: str, price: float, details: str = ""):
        emoji_map = {
            '1.5R移損': '🛡',
            '目標減倉': '💰',
            '止損出場': '🚨',
            '結構破壞': '⚠️',
            '硬止損觸發': '🔴'
        }
        emoji = emoji_map.get(action, '🔔')

        msg = f"{emoji} <b>{action}</b>\n幣種: {symbol}\n價格: ${price:.2f}"
        if details:
            msg += f"\n{details}"
        TelegramNotifier.send_message(msg)

    @staticmethod
    def notify_exit(symbol: str, details: dict):
        """通知交易平倉"""
        side = details.get('side', '?')
        entry = details.get('entry_price', 0)
        reason = details.get('exit_reason', 'unknown')
        pnl = details.get('pnl_pct', 0)
        size = details.get('position_size', 0)
        emoji = '🟢' if pnl >= 0 else '🔴'
        msg = (
            f"{emoji} <b>平倉: {symbol} {side}</b>\n"
            f"原因: {reason}\n"
            f"入場: ${entry:.2f}\n"
            f"倉位: {size:.6f}\n"
            f"PnL: {pnl:+.2f}%"
        )
        TelegramNotifier.send_message(msg)
