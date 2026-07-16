"""
Analytic Engine - Market Structure Quality Scoring

This module evaluates current market conditions using Trade Bible rules
and assigns quality scores (0.0 to 1.0) to different aspects of the setup.
"""
import logging
import os
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import pandas as pd

logger = logging.getLogger(__name__)


class AnalyticEngine:
    """Evaluates market structure quality using traditional trading rules."""

    def __init__(self):
        self.weights = {
            "liquidity": 0.22,       # Asian sweep quality
            "volume": 0.18,          # Volume confirmation
            "fvg": 0.20,             # Fair Value Gap presence
            "order_block": 0.15,     # ICT / order block confluence
            "multi_timeframe": 0.25, # M1/M5/M15/H1/H4 alignment
        }
        self.timeframe_names = {
            "M1": getattr(mt5, "TIMEFRAME_M1", None) if mt5 else None,
            "M5": getattr(mt5, "TIMEFRAME_M5", None) if mt5 else None,
            "M15": getattr(mt5, "TIMEFRAME_M15", None) if mt5 else None,
            "H1": getattr(mt5, "TIMEFRAME_H1", None) if mt5 else None,
            "H4": getattr(mt5, "TIMEFRAME_H4", None) if mt5 else None,
        }

    def evaluate_setup(self, symbol: str, signal: Dict) -> Dict:
        """
        Evaluate the overall setup quality for a trading signal.

        Args:
            symbol: Trading symbol
            signal: Signal dictionary from technical analysis

        Returns:
            Dict with scores and overall quality
        """
        scores = {}

        # Liquidity Check: Asian sweep quality
        scores["liquidity"] = self._score_liquidity(symbol)

        # Volume Profile: Current volume vs average
        scores["volume"] = self._score_volume(symbol)

        # FVG Quality: Gap size and positioning
        scores["fvg"] = self._score_fvg(signal)

        # ICT / order block confluence is optional, but the weighted score expects it.
        scores["order_block"] = self._score_order_block(signal)

        # Multi-timeframe structure: timing + local trend + higher-timeframe bias.
        mtf_score, mtf_details = self._score_multi_timeframe(symbol, signal)
        scores["multi_timeframe"] = mtf_score

        # Calculate weighted overall score
        overall_score = sum(scores[aspect] * weight for aspect, weight in self.weights.items())

        result = {
            "overall_score": overall_score,
            "component_scores": scores,
            "multi_timeframe": mtf_details,
            "quality_description": self._describe_quality(overall_score),
            "recommendation": "TRADE" if overall_score >= 0.7 else "WAIT"
        }

        logger.info(f"Analytic evaluation for {symbol}: {overall_score:.3f} - {result['quality_description']}")
        return result

    def _score_liquidity(self, symbol: str) -> float:
        """
        Score Asian session liquidity (0.0-1.0).
        1.0 = Perfect sweep of Asian range
        0.0 = No sweep, price hovering
        """
        try:
            if not mt5 or not mt5.initialize():
                return 0.5  # Neutral score if no MT5

            # Get current time and determine Asian session
            now = datetime.now(timezone.utc)
            current_utc = now

            # Determine Asian session (00:00-08:00 UTC)
            if current_utc.hour < 8:
                # Before 08:00, use previous day
                asian_day = current_utc - timedelta(days=1)
            else:
                asian_day = current_utc

            start = datetime(asian_day.year, asian_day.month, asian_day.day, 0, 0, tzinfo=timezone.utc)
            end = datetime(asian_day.year, asian_day.month, asian_day.day, 8, 0, tzinfo=timezone.utc)

            # Get Asian session data
            asian_bars = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
            if asian_bars is None or len(asian_bars) < 10:
                return 0.3  # Insufficient data

            df_asian = pd.DataFrame(asian_bars)
            asian_high = df_asian["high"].max()
            asian_low = df_asian["low"].min()
            asian_range = asian_high - asian_low

            if asian_range <= 0:
                return 0.0

            # Get post-Asian data (after 08:00)
            post_start = end
            post_end = now
            post_bars = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, post_start, post_end)

            if post_bars is None or len(post_bars) == 0:
                return 0.1  # No post-Asian data yet

            df_post = pd.DataFrame(post_bars)

            # Calculate sweep quality
            high_break = (df_post["high"] > asian_high).any()
            low_break = (df_post["low"] < asian_low).any()

            # Distance from range (how far price moved beyond Asian levels)
            current_price = df_post.iloc[-1]["close"]
            high_distance = max(0, current_price - asian_high) / asian_range
            low_distance = max(0, asian_low - current_price) / asian_range

            sweep_distance = max(high_distance, low_distance)

            # Combine factors
            base_score = 0.2  # Minimum score
            if high_break or low_break:
                base_score = 0.6
            if high_break and low_break:
                base_score = 0.8

            # Add distance bonus (up to 0.2)
            distance_bonus = min(0.2, sweep_distance * 0.5)

            final_score = min(1.0, base_score + distance_bonus)

            logger.debug(f"Liquidity score for {symbol}: {final_score:.3f} (breaks: {high_break}/{low_break}, distance: {sweep_distance:.3f})")
            return final_score

        except Exception as e:
            logger.warning(f"Error calculating liquidity score: {e}")
            return 0.5  # Neutral score on error

    def _score_volume(self, symbol: str) -> float:
        """
        Score volume confirmation (0.0-1.0).
        1.0 = Very high volume (2x+ average)
        0.0 = Very low volume (<0.5x average)
        """
        try:
            if not mt5 or not mt5.initialize():
                return 0.5

            # Get recent M5 bars for volume analysis
            bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50)
            if bars is None or len(bars) < 25:
                return 0.5

            df = pd.DataFrame(bars)
            volumes = df["tick_volume"]

            # Calculate volume metrics
            current_volume = volumes.iloc[-1]
            avg_volume_20 = volumes.iloc[-21:-1].mean()
            avg_volume_10 = volumes.iloc[-11:-1].mean()

            if avg_volume_20 <= 0 or avg_volume_10 <= 0:
                return 0.5

            # Score based on how much current volume exceeds average
            ratio_20 = current_volume / avg_volume_20
            ratio_10 = current_volume / avg_volume_10

            # Use the higher ratio for scoring
            volume_ratio = max(ratio_20, ratio_10)

            # Convert ratio to score (0.0-1.0)
            if volume_ratio >= 2.0:
                score = 1.0
            elif volume_ratio >= 1.5:
                score = 0.8
            elif volume_ratio >= 1.2:
                score = 0.6
            elif volume_ratio >= 0.8:
                score = 0.4
            elif volume_ratio >= 0.5:
                score = 0.2
            else:
                score = 0.0

            logger.debug(f"Volume score for {symbol}: {score:.3f} (ratio: {volume_ratio:.2f})")
            return score

        except Exception as e:
            logger.warning(f"Error calculating volume score: {e}")
            return 0.5

    def _score_fvg(self, signal: Dict) -> float:
        """
        Score FVG quality (0.0-1.0).
        Based on gap size, positioning, and confluence.
        """
        try:
            if not signal:
                return 0.0

            symbol = signal.get("symbol", "")
            gap_size = signal.get("gap_size", 0)
            nature = signal.get("nature", "")

            # Base score from gap size
            if gap_size <= 0:
                return 0.0

            # Get pip size for the symbol
            pip_size = self._get_pip_size(symbol) or 0.0001
            gap_pips = gap_size / pip_size

            # Score based on gap size in pips
            if gap_pips >= 5:
                size_score = 1.0
            elif gap_pips >= 3:
                size_score = 0.8
            elif gap_pips >= 1.5:
                size_score = 0.6
            elif gap_pips >= 0.8:
                size_score = 0.4
            else:
                size_score = 0.2

            # Bonus for nature (pullback > retest > breakout)
            nature_bonus = 0.0
            if "Pullback" in nature:
                nature_bonus = 0.2
            elif "Retest" in nature:
                nature_bonus = 0.1
            elif "Breakout" in nature:
                nature_bonus = 0.0

            # Trend context bonus
            context_bonus = 0.0
            if "Continuation" in nature:
                context_bonus = 0.1
            elif "Reversal" in nature:
                context_bonus = 0.05

            # ICT/order block bonus for aligned setups
            ob_bonus = 0.0
            order_block = signal.get("order_block") if isinstance(signal, dict) else None
            if order_block and signal.get("action"):
                if order_block.get("type") == signal.get("action"):
                    ob_bonus = 0.15
                else:
                    ob_bonus = 0.05

            final_score = min(1.0, size_score + nature_bonus + context_bonus + ob_bonus)

            logger.debug(f"FVG score for {symbol}: {final_score:.3f} (size: {gap_pips:.1f}p, nature: {nature})")
            return final_score

        except Exception as e:
            logger.warning(f"Error calculating FVG score: {e}")
            return 0.3

    def _score_order_block(self, signal: Dict) -> float:
        """Score optional order block confluence without making ICT mandatory."""
        try:
            if not signal:
                return 0.5

            order_block = signal.get("order_block")
            setup = signal.get("setup_score") or {}

            if not order_block:
                components = setup.get("components") or []
                ob_component = next((c for c in components if c.get("key") == "ob_fvg"), None)
                if ob_component:
                    return 0.75 if ob_component.get("passed") else 0.45
                return 0.5

            action = str(signal.get("action") or "").upper()
            ob_type = str(order_block.get("type") or "").upper()
            if action and ob_type and action == ob_type:
                return 0.85
            if order_block.get("zone"):
                return 0.65
            return 0.5
        except Exception as e:
            logger.warning(f"Error calculating order block score: {e}")
            return 0.5

    def _configured_timeframes(self) -> list:
        raw = os.getenv("ANALYTIC_TIMEFRAMES", "M1,M5,M15,H1,H4")
        names = [item.strip().upper() for item in raw.split(",") if item.strip()]
        return [name for name in names if self.timeframe_names.get(name) is not None]

    def _fetch_rates(self, symbol: str, timeframe_name: str, count: int = 120):
        if not mt5 or not mt5.initialize():
            return None
        timeframe = self.timeframe_names.get(timeframe_name)
        if timeframe is None:
            return None
        bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if bars is None or len(bars) < 30:
            return None
        return pd.DataFrame(bars)

    def _trend_snapshot(self, df: pd.DataFrame) -> Dict:
        closes = df["close"].astype(float)
        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        volumes = df["tick_volume"].astype(float) if "tick_volume" in df else pd.Series([0] * len(df))

        ema_fast = closes.ewm(span=8, adjust=False).mean().iloc[-1]
        ema_slow = closes.ewm(span=21, adjust=False).mean().iloc[-1]
        latest = closes.iloc[-1]
        previous = closes.iloc[-6] if len(closes) >= 6 else closes.iloc[0]
        recent_high = highs.iloc[-20:].max()
        recent_low = lows.iloc[-20:].min()
        range_size = max(recent_high - recent_low, 1e-12)
        displacement = abs(closes.iloc[-1] - closes.iloc[-4]) / range_size if len(closes) >= 4 else 0.0
        volume_avg = volumes.iloc[-21:-1].mean() if len(volumes) >= 22 else volumes.mean()
        volume_ratio = (volumes.iloc[-1] / volume_avg) if volume_avg and volume_avg > 0 else 1.0

        if latest > ema_fast > ema_slow and latest >= previous:
            direction = "BUY"
            strength = 0.70
        elif latest < ema_fast < ema_slow and latest <= previous:
            direction = "SELL"
            strength = 0.70
        elif latest > ema_slow:
            direction = "BUY"
            strength = 0.55
        elif latest < ema_slow:
            direction = "SELL"
            strength = 0.55
        else:
            direction = "NEUTRAL"
            strength = 0.45

        if displacement >= 0.35:
            strength += 0.12
        elif displacement >= 0.20:
            strength += 0.06
        if volume_ratio >= 1.4:
            strength += 0.08
        elif volume_ratio < 0.7:
            strength -= 0.08

        return {
            "direction": direction,
            "strength": round(max(0.0, min(1.0, strength)), 3),
            "ema_fast": float(ema_fast),
            "ema_slow": float(ema_slow),
            "close": float(latest),
            "displacement": round(float(displacement), 3),
            "volume_ratio": round(float(volume_ratio), 3),
        }

    def _score_multi_timeframe(self, symbol: str, signal: Dict) -> Tuple[float, Dict]:
        """Score agreement between lower-timeframe timing and higher-timeframe bias."""
        try:
            action = str((signal or {}).get("action") or "").upper()
            if action not in {"BUY", "SELL"}:
                return 0.5, {"score": 0.5, "reason": "No signal action for MTF alignment"}

            timeframe_names = self._configured_timeframes()
            if not timeframe_names:
                return 0.5, {"score": 0.5, "reason": "No analytic timeframes configured"}

            weights = {
                "M1": 0.12,
                "M5": 0.24,
                "M15": 0.24,
                "H1": 0.25,
                "H4": 0.15,
            }
            snapshots = {}
            weighted_score = 0.0
            total_weight = 0.0
            aligned = []
            conflicting = []

            for name in timeframe_names:
                df = self._fetch_rates(symbol, name, 140)
                if df is None:
                    continue
                snap = self._trend_snapshot(df)
                direction = snap["direction"]
                strength = float(snap["strength"])
                weight = float(weights.get(name, 0.15))

                if direction == action:
                    frame_score = 0.55 + (strength * 0.45)
                    aligned.append(name)
                elif direction == "NEUTRAL":
                    frame_score = 0.45
                else:
                    frame_score = max(0.0, 0.45 - (strength * 0.35))
                    conflicting.append(name)

                snapshots[name] = {**snap, "score": round(frame_score, 3)}
                weighted_score += frame_score * weight
                total_weight += weight

            if total_weight <= 0:
                return 0.5, {"score": 0.5, "reason": "Insufficient MTF data"}

            score = max(0.0, min(1.0, weighted_score / total_weight))

            htf_frames = [name for name in ["H1", "H4"] if name in snapshots]
            htf_aligned = [name for name in htf_frames if snapshots[name]["direction"] == action]
            htf_conflict = [name for name in htf_frames if snapshots[name]["direction"] not in [action, "NEUTRAL"]]
            if htf_frames and len(htf_aligned) == len(htf_frames):
                score = min(1.0, score + 0.08)
            if htf_conflict:
                score = max(0.0, score - 0.12)

            reason = (
                f"MTF aligned: {', '.join(aligned) or 'none'}; "
                f"conflict: {', '.join(conflicting) or 'none'}"
            )
            details = {
                "score": round(score, 3),
                "action": action,
                "aligned": aligned,
                "conflicting": conflicting,
                "snapshots": snapshots,
                "reason": reason,
            }
            return round(score, 3), details
        except Exception as e:
            logger.warning(f"Error calculating multi-timeframe score: {e}")
            return 0.5, {"score": 0.5, "reason": f"MTF scoring failed: {e}"}

    def _get_pip_size(self, symbol: str) -> float:
        """Get pip size for symbol."""
        try:
            if not mt5 or not mt5.initialize():
                return None
            info = mt5.symbol_info(symbol)
            if info and hasattr(info, 'digits'):
                return 0.0001 if info.digits > 3 else 0.01
        except:
            pass
        return 0.0001  # Default

    def _describe_quality(self, score: float) -> str:
        """Convert score to human-readable description."""
        if score >= 0.9:
            return "Excellent setup - all conditions perfect"
        elif score >= 0.8:
            return "Very good setup - minor weaknesses"
        elif score >= 0.7:
            return "Good setup - acceptable for trade"
        elif score >= 0.6:
            return "Fair setup - monitor closely"
        elif score >= 0.5:
            return "Poor setup - wait for better"
        elif score >= 0.4:
            return "Very poor setup - avoid"
        else:
            return "Terrible setup - no trade"
