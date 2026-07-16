"""
Trade Logger - Logging and Notifications
"""
import logging
import json
import os
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class TradeLogger:
    def __init__(self):
        self.log_dir = "logs"
        os.makedirs(self.log_dir, exist_ok=True)
        self._lock = threading.RLock()
        self.max_daily_entries = int(os.getenv("MAX_DAILY_LOG_ENTRIES", "3000"))
        self.max_rejection_entries = int(os.getenv("MAX_DAILY_REJECTION_LOG_ENTRIES", "800"))

    def _log_file_path(self):
        return os.path.join(
            self.log_dir, f"trades_{datetime.now().strftime('%Y-%m-%d')}.json"
        )

    def log_signal(self, signal):
        """Log FVG signal detection"""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "FVG_DETECTED",
                **signal,
            }
            self._save_log(log_entry)
            logger.info(f"FVG detected: {signal['symbol']} {signal['type']}")
        except Exception as e:
            logger.error(f"Error logging signal: {e}")

    def log_trade(self, trade):
        """Log trade execution"""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "TRADE_EXECUTED",
                **trade,
            }
            self._save_log(log_entry)
            logger.info(f"Trade executed: {trade['symbol']}")
        except Exception as e:
            logger.error(f"Error logging trade: {e}")

    def log_close(self, close_info):
        """Log trade closure"""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "TRADE_CLOSED",
                **close_info,
            }
            self._save_log(log_entry)
            logger.info(f"Trade closed: {close_info['symbol']}")
        except Exception as e:
            logger.error(f"Error logging closure: {e}")

    def _save_log(self, entry):
        """Save log entry to JSON file"""
        with self._lock:
            try:
                log_file = self._log_file_path()
                logs = self._read_logs_unlocked(log_file)
                logs.append(entry)
                logs = self._trim_logs(logs)

                tmp_file = f"{log_file}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(logs, f, separators=(",", ":"))
                os.replace(tmp_file, log_file)
            except Exception as e:
                logger.error(f"Error saving log: {e}")

    def _trim_logs(self, logs):
        """Keep trade lifecycle events while limiting noisy rejection growth."""
        if len(logs) <= self.max_daily_entries:
            return logs

        important_events = {
            "TRADE_EXECUTED",
            "TRADE_CLOSED",
            "PARTIAL_TP",
            "REVERSE_PROFIT_EXIT",
            "MAX_ADVERSE_EXIT",
            "NEWS_LADDER_ADDON",
        }
        important = [entry for entry in logs if entry.get("event") in important_events]
        rejections = [entry for entry in logs if entry.get("event") == "SIGNAL_REJECTED"][-self.max_rejection_entries:]
        others_budget = max(0, self.max_daily_entries - len(important) - len(rejections))
        others = [
            entry for entry in logs
            if entry.get("event") not in important_events and entry.get("event") != "SIGNAL_REJECTED"
        ][-others_budget:]
        trimmed = important + rejections + others
        trimmed.sort(key=lambda entry: entry.get("timestamp", ""))
        return trimmed[-self.max_daily_entries:]

    def _read_logs_unlocked(self, log_file):
        if not os.path.exists(log_file):
            return []

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return []
            loaded = json.loads(content)
            return loaded if isinstance(loaded, list) else []
        except json.JSONDecodeError as e:
            backup_file = f"{log_file}.corrupt-{datetime.now().strftime('%H%M%S')}"
            try:
                os.replace(log_file, backup_file)
                logger.error(f"Corrupt log moved to {backup_file}: {e}")
            except Exception:
                logger.error(f"Error reading logs: {e}")
            return []

    def get_logs(self, limit=None):
        """Get today's logs"""
        with self._lock:
            try:
                logs = self._read_logs_unlocked(self._log_file_path())
                if limit:
                    return logs[-int(limit):]
                return logs
            except Exception as e:
                logger.error(f"Error reading logs: {e}")
                return []

    def get_stats(self):
        """Compute performance statistics from today's logs."""
        logs = self.get_logs()
        closed_trades = [l for l in logs if l.get("event") == "TRADE_CLOSED"]
        rejected_signals = [l for l in logs if l.get("event") == "SIGNAL_REJECTED"]
        if not closed_trades:
            return {
                "trades": 0,
                "win_rate": None,
                "avg_win": None,
                "avg_loss": None,
                "expectancy": None,
                "avg_r": None,
                "rejections": len(rejected_signals),
            }

        wins = [t for t in closed_trades if t.get("profit", 0) > 0]
        losses = [t for t in closed_trades if t.get("profit", 0) <= 0]

        win_rate = len(wins) / len(closed_trades)
        avg_win = sum(t.get("profit", 0) for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t.get("profit", 0) for t in losses) / len(losses)) if losses else 0

        # R-multiple assumes trade includes 'risk' field
        r_values = []
        for t in closed_trades:
            risk = t.get("risk")
            profit = t.get("profit", 0)
            if risk and risk > 0:
                r_values.append(profit / risk)
        avg_r = sum(r_values) / len(r_values) if r_values else None

        expectancy = None
        if avg_win is not None and avg_loss is not None:
            expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        return {
            "trades": len(closed_trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "avg_r": avg_r,
            "rejections": len(rejected_signals),
        }
