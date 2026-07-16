"""
Trading Engine - Core Bot Logic
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

load_dotenv()
logger = logging.getLogger(__name__)

from mt5_interface import MT5Interface
from analytic_engine import AnalyticEngine
from predictive_engine import PredictiveEngine
from ensemble_decision import EnsembleDecision
from trade_logger import TradeLogger
from pending_order_manager import PendingOrderManager
from conditional_watchlist_manager import ConditionalWatchlistManager
from bible_logic import validate_trade
from technical_analysis import scan_symbols


class TradingEngine:
    def __init__(self):
        self.mt5 = MT5Interface()
        self.logger = TradeLogger()
        self.is_running = False
        
        # CRITICAL: Add threading locks for shared data
        self._lock = threading.RLock()
        self._signals_lock = threading.Lock()
        self._positions_lock = threading.Lock()
        self._trades_lock = threading.Lock()
        
        self.symbols = os.getenv("TRADING_SYMBOLS", "EURUSD,GBPUSD,USDJPY").split(",")
        execution_symbols_raw = os.getenv("EXECUTION_SYMBOLS", "").strip()
        self.execution_symbols = [
            s.strip().upper()
            for s in execution_symbols_raw.split(",")
            if s.strip()
        ]
        self.timeframe = 5  # M5 timeframe
        self.volume = float(os.getenv("TRADE_VOLUME", 0.001))
        self.base_trade_volume = self.volume
        self.broker_min_lot_fallback_enabled = os.getenv("FEATURE_BROKER_MIN_LOT_FALLBACK", "true").lower() in ["true", "1", "yes"]
        self.max_auto_min_lot = float(os.getenv("MAX_AUTO_MIN_LOT", 0.01))
        self.dynamic_account_profile_enabled = os.getenv("FEATURE_DYNAMIC_ACCOUNT_PROFILE", "true").lower() in ["true", "1", "yes"]
        self.small_account_mode_enabled = os.getenv("FEATURE_SMALL_ACCOUNT_MODE", "false").lower() in ["true", "1", "yes"]
        self.small_account_threshold = float(os.getenv("SMALL_ACCOUNT_EQUITY_THRESHOLD", 25))
        self.small_account_trade_volume = float(os.getenv("SMALL_ACCOUNT_TRADE_VOLUME", 0.001))
        self.small_account_max_auto_min_lot = float(os.getenv("SMALL_ACCOUNT_MAX_AUTO_MIN_LOT", 0.01))
        self.small_account_max_exposure_pct = float(os.getenv("SMALL_ACCOUNT_MAX_EXPOSURE_PERCENT", 0.01))
        self.small_account_max_active_trades = int(os.getenv("SMALL_ACCOUNT_MAX_ACTIVE_TRADES", 1))
        self.small_account_allow_metals = os.getenv("SMALL_ACCOUNT_ALLOW_METALS", "false").lower() in ["true", "1", "yes"]
        self.small_account_allow_crypto = os.getenv("SMALL_ACCOUNT_ALLOW_CRYPTO", "false").lower() in ["true", "1", "yes"]
        self.small_account_allow_stocks = os.getenv("SMALL_ACCOUNT_ALLOW_STOCKS", "false").lower() in ["true", "1", "yes"]
        self.small_account_disable_news_ladder = os.getenv("SMALL_ACCOUNT_DISABLE_NEWS_LADDER", "true").lower() in ["true", "1", "yes"]
        self.small_account_disable_pending_orders = os.getenv("SMALL_ACCOUNT_DISABLE_PENDING_ORDERS", "true").lower() in ["true", "1", "yes"]
        self.small_account_active = False
        self.account_profile_name = "manual"
        self.account_profile_equity = None
        self.dynamic_profile_last_applied_at = None
        self.active_trades = {}
        self.armed_signals = {}

        # recent detected signals (list of dict)
        self.recent_signals = []
        # favorable signals (passed validation) for UI insight
        self.favorable_signals = []
        # rejection log (exposed via /api/logs)
        self.rejection_logs = []
        # logic feed for dashboard (real-time reasoning)
        self.logic_feed = []
        # all detected signals (for logs and watchlist)
        self.signal_history = []
        # future trade candidates for dashboard
        self.future_trades = []
        # trade journal (open/closed trade tracking)
        self.trade_journal = []
        self.last_known_profit = {}

        # kill switches per symbol or global
        self.killed = {"all": False}  # set symbol to True to disable
        # rule toggle configuration (set from UI)
        self.rule_config = {"ema": False, "volume": False, "po3": False}

        # risk management
        self.risk_pct = 0.0  # legacy field; sizing is fixed-lot only
        self.position_sizing_mode = "fixed"
        self.max_exposure_pct = float(os.getenv("MAX_EXPOSURE_PERCENT", 0.05))  # max exposure on open positions
        self.no_revenge_cooldown = int(os.getenv("NO_REVENGE_COOLDOWN_SECONDS", 24 * 3600))
        self.cooldown_until = None
        self.reversal_shock_guard_enabled = os.getenv("FEATURE_REVERSAL_SHOCK_GUARD", "true").lower() in ["true", "1", "yes"]
        self.reversal_shock_cooldown_minutes = int(os.getenv("REVERSAL_SHOCK_COOLDOWN_MINUTES", 30))
        self.reversal_shock_xau_cooldown_minutes = int(os.getenv("REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES", 60))
        self.opposing_signal_profit_exit_enabled = os.getenv("FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT", "true").lower() in ["true", "1", "yes"]
        self.opposing_signal_min_r = float(os.getenv("OPPOSING_SIGNAL_MIN_R", 0.20))
        self.opposing_signal_min_score = float(os.getenv("OPPOSING_SIGNAL_MIN_SCORE", 0.58))

        # minimum profit target for signal (in pips)
        self.min_profit_pips = float(os.getenv("MIN_PROFIT_PIPS", 10))

        # Daily profit cap - shut down after hitting target
        self.daily_profit_cap = float(os.getenv("DAILY_PROFIT_CAP", 0.02))  # 2% daily profit cap
        self.daily_loss_brake_enabled = os.getenv("FEATURE_DAILY_LOSS_BRAKE", "true").lower() in ["true", "1", "yes"]
        self.daily_loss_cap_pct = float(os.getenv("DAILY_LOSS_CAP_PERCENT", 0.05))
        self.max_daily_losses = int(os.getenv("MAX_DAILY_LOSSES", 100))
        self.max_consecutive_losses = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 30))
        self.loss_cooldown_minutes = int(os.getenv("LOSS_COOLDOWN_MINUTES", 60))
        self.max_active_trades_total = int(os.getenv("MAX_ACTIVE_TRADES_TOTAL", 10))
        self.catastrophic_loss_stop_enabled = os.getenv("FEATURE_CATASTROPHIC_LOSS_STOP", "true").lower() in ["true", "1", "yes"]
        self.catastrophic_loss_r = float(os.getenv("CATASTROPHIC_LOSS_R", 1.5))
        self.catastrophic_loss_cooldown_minutes = int(os.getenv("CATASTROPHIC_LOSS_COOLDOWN_MINUTES", 360))
        self.daily_loss_count = 0
        self.consecutive_loss_count = 0
        self.last_loss_brake_reason = None
        self.daily_start_equity = None
        self.start_equity = None
        self.peak_equity = None

        # ========== GLOBAL TRADE REGISTRY - SIGNAL LOCKOUT SYSTEM ==========
        # Prevents looping triggers by tracking active trades and cooldowns per symbol
        self.trade_registry = {}  # {symbol: {"active_trades": count, "last_trade_time": datetime, "cooldown_until": datetime, "shock_until": datetime}}
        self.signal_lockout_enabled = True  # Master switch for lockout system
        self.max_trades_per_symbol = int(os.getenv("MAX_TRADES_PER_SYMBOL", 1))  # Default: 1 trade per symbol
        self.trade_cooldown_minutes = int(os.getenv("TRADE_COOLDOWN_MINUTES", 1))  # Faster early-entry retry window
        self.cooldown_override_enabled = os.getenv("FEATURE_COOLDOWN_OVERRIDE", "true").lower() in ["true", "1", "yes"]
        self.cooldown_override_min_grade = os.getenv("COOLDOWN_OVERRIDE_MIN_GRADE", "A").strip().upper() or "A"
        self.cooldown_override_min_score = float(os.getenv("COOLDOWN_OVERRIDE_MIN_SCORE", 0.78))
        self.cooldown_override_min_conviction = float(os.getenv("COOLDOWN_OVERRIDE_MIN_CONVICTION", 0.45))
        self.cooldown_override_require_spread_safe = os.getenv("COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE", "true").lower() in ["true", "1", "yes"]
        self.cooldown_override_require_new_structure = os.getenv("COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE", "true").lower() in ["true", "1", "yes"]
        self.trade_interval_minutes = int(os.getenv("TRADE_INTERVAL_MINUTES", 0))  # Global interval disabled; using symbol-specific cooldown only
        self.last_trade_timestamp = None
        self.scan_interval_seconds = int(os.getenv("SCAN_INTERVAL_SECONDS", 3))
        self.engine_loop_sleep_seconds = float(os.getenv("ENGINE_LOOP_SLEEP_SECONDS", min(3, self.scan_interval_seconds)))
        self.scan_on_new_candle = os.getenv("SCAN_ON_NEW_CANDLE", "false").lower() in ["true", "1", "yes"]
        self.scan_timeframe_minutes = int(os.getenv("SCAN_TIMEFRAME_MINUTES", 5))
        self.last_scan_at = None
        self.last_scan_candle_key = None
        self.next_scan_at = None
        self.last_scan_signal_count = 0
        self._signal_log_cache = {}
        self._signal_execution_ledger = {}
        self.duplicate_signal_cooldown_seconds = int(os.getenv("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", 300))
        self.signal_execution_ttl_seconds = int(os.getenv("SIGNAL_EXECUTION_TTL_SECONDS", 900))
        self.armed_confirmation_enabled = os.getenv("FEATURE_ARMED_CONFIRMATION", "true").lower() in ["true", "1", "yes"]
        self.armed_required_scans = max(1, int(os.getenv("ARMED_CONFIRMATION_REQUIRED_SCANS", 2)))
        self.armed_ttl_seconds = max(30, int(os.getenv("ARMED_CONFIRMATION_TTL_SECONDS", 180)))
        self.armed_min_score = float(os.getenv("ARMED_CONFIRMATION_MIN_SCORE", 0.58))
        self.armed_require_structure = os.getenv("ARMED_CONFIRMATION_REQUIRE_STRUCTURE", "true").lower() in ["true", "1", "yes"]
        self.market_execution_score_threshold = float(os.getenv("MARKET_EXECUTION_SCORE_THRESHOLD", 0.45))
        self.market_execution_conviction_threshold = float(os.getenv("MARKET_EXECUTION_CONVICTION_THRESHOLD", 0.35))
        self.conviction_threshold = float(os.getenv("CONVICTION_THRESHOLD", 0.20))  # Balanced War Room threshold for valid early entries
        self.trailing_stop_trigger_pct = float(os.getenv("TRAILING_STOP_TRIGGER_PCT", 0.55))  # Protect after 55% of TP reached
        self.trailing_stop_lock_pips = float(os.getenv("TRAILING_STOP_LOCK_PIPS", 10.0))
        self.trailing_stop_step_pct = float(os.getenv("TRAILING_STOP_STEP_PCT", 0.50))
        self.trailing_stop_min_step_pips = float(os.getenv("TRAILING_STOP_MIN_STEP_PIPS", 5.0))
        self.trailing_tp_enabled = os.getenv("FEATURE_TRAILING_TAKE_PROFIT", "true").lower() in ["true", "1", "yes"]
        self.trailing_tp_trigger_pct = float(os.getenv("TRAILING_TP_TRIGGER_PCT", 0.85))
        self.trailing_tp_extension_pct = float(os.getenv("TRAILING_TP_EXTENSION_PCT", 0.5))
        self.trailing_tp_cooldown_seconds = int(os.getenv("TRAILING_TP_COOLDOWN_SECONDS", 300))
        self.partial_tp_extend_enabled = os.getenv("FEATURE_PARTIAL_TP_EXTEND", "true").lower() in ["true", "1", "yes"]
        self.partial_tp_extend_pct = float(os.getenv("PARTIAL_TP_EXTEND_PCT", self.trailing_tp_extension_pct))
        self.partial_tp_enabled = os.getenv("FEATURE_PARTIAL_TAKE_PROFIT", "true").lower() in ["true", "1", "yes"]
        self.partial_tp_trigger_r = float(os.getenv("PARTIAL_TP_TRIGGER_R", 0.75))
        self.partial_tp_close_pct = float(os.getenv("PARTIAL_TP_CLOSE_PCT", 0.5))
        self.partial_tp_lock_pips = float(os.getenv("PARTIAL_TP_LOCK_PIPS", 10.0))
        self.breakeven_protection_enabled = os.getenv("FEATURE_BREAKEVEN_PROTECTION", "true").lower() in ["true", "1", "yes"]
        self.breakeven_trigger_r = float(os.getenv("BREAKEVEN_TRIGGER_R", 0.30))
        self.breakeven_lock_pips = float(os.getenv("BREAKEVEN_LOCK_PIPS", 0.0))
        self.first_profit_breakeven_enabled = os.getenv("FEATURE_FIRST_PROFIT_BREAKEVEN", "true").lower() in ["true", "1", "yes"]
        self.first_profit_breakeven_trigger_r = float(os.getenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", 0.10))
        self.first_profit_breakeven_trigger_r_scalp = float(os.getenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", 0.08))
        self.reversal_breakeven_at_entry_enabled = os.getenv("FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY", "true").lower() in ["true", "1", "yes"]
        self.reverse_profit_exit_enabled = os.getenv("FEATURE_REVERSE_PROFIT_EXIT", "true").lower() in ["true", "1", "yes"]
        self.reverse_profit_min_r = float(os.getenv("REVERSE_PROFIT_MIN_R", 1.20))
        self.reverse_profit_giveback_pct = float(os.getenv("REVERSE_PROFIT_GIVEBACK_PCT", 0.45))
        self.reverse_profit_close_pct = float(os.getenv("REVERSE_PROFIT_CLOSE_PCT", 0.5))
        self.reverse_after_partial_lock_r = float(os.getenv("REVERSE_AFTER_PARTIAL_LOCK_R", 0.20))
        self.max_adverse_exit_enabled = os.getenv("FEATURE_MAX_ADVERSE_EXIT", "true").lower() in ["true", "1", "yes"]
        self.max_adverse_r = float(os.getenv("MAX_ADVERSE_R", 0.60))
        self.symbol_profiles_enabled = os.getenv("FEATURE_SYMBOL_PROFILES", "true").lower() in ["true", "1", "yes"]
        self.instrument_profiles_enabled = os.getenv("FEATURE_INSTRUMENT_PROFILES", "true").lower() in ["true", "1", "yes"]
        self.trade_horizon_profiles_enabled = os.getenv("FEATURE_TRADE_HORIZON_PROFILES", "true").lower() in ["true", "1", "yes"]
        self.horizon_profile_mode = os.getenv("HORIZON_PROFILE_MODE", "exit_only").strip().lower() or "exit_only"
        self.scalp_profile_enabled = os.getenv("ENABLE_SCALP_PROFILE", "true").lower() in ["true", "1", "yes"]
        self.intraday_profile_enabled = os.getenv("ENABLE_INTRADAY_PROFILE", "true").lower() in ["true", "1", "yes"]
        self.swing_profile_enabled = os.getenv("ENABLE_SWING_PROFILE", "true").lower() in ["true", "1", "yes"]
        self.min_expected_r = float(os.getenv("MIN_EXPECTED_R", 1.2))
        self.min_expected_r_scalp = float(os.getenv("MIN_EXPECTED_R_SCALP", 0.8))
        self.take_profit_r_multiplier = float(os.getenv("TAKE_PROFIT_R_MULTIPLIER", 1.8))
        self.take_profit_r_multiplier_scalp = float(os.getenv("TAKE_PROFIT_R_MULTIPLIER_SCALP", 1.5))
        self.execution_conviction_threshold = float(os.getenv("EXECUTION_CONVICTION_THRESHOLD", 0.35))
        self.execution_setup_score_threshold = float(os.getenv("EXECUTION_SETUP_SCORE_THRESHOLD", 0.50))
        self.execution_archetype_score_threshold = float(os.getenv("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", 0.58))
        self.min_trade_readiness_score = float(os.getenv("MIN_TRADE_READINESS_SCORE", 0.62))
        self.ict_mode_enabled = os.getenv("FEATURE_ICT_MODE", "false").lower() in ["true", "1", "yes"]
        self.ict_min_setup_score = float(os.getenv("ICT_MIN_SETUP_SCORE", 0.60))
        self.ict_min_confluence = float(os.getenv("ICT_MIN_CONFLUENCE", 0.60))
        self.professional_gate_enabled = os.getenv("FEATURE_PROFESSIONAL_EXECUTION_GATE", "true").lower() in ["true", "1", "yes"]
        self.min_execution_grade = os.getenv("MIN_EXECUTION_GRADE", "B").strip().upper() or "B"
        self.allow_c_scalps = os.getenv("ALLOW_C_GRADE_SCALPS", "false").lower() in ["true", "1", "yes"]
        self.min_professional_score = float(os.getenv("MIN_PROFESSIONAL_SETUP_SCORE", 0.62))
        self.min_professional_conviction = float(os.getenv("MIN_PROFESSIONAL_CONVICTION", 0.30))
        self.min_session_score_for_trade = float(os.getenv("MIN_SESSION_SCORE_FOR_TRADE", 0.40))
        self.min_session_score_for_scalp = float(os.getenv("MIN_SESSION_SCORE_FOR_SCALP", 0.55))
        self.block_context_watch_trades = os.getenv("BLOCK_CONTEXT_WATCH_TRADES", "true").lower() in ["true", "1", "yes"]
        self.max_entry_drift_pct = float(os.getenv("MAX_ENTRY_DRIFT_PCT", 0.35))
        self.max_entry_drift_pips = float(os.getenv("MAX_ENTRY_DRIFT_PIPS", 10))
        self.mtf_execution_gate_enabled = os.getenv("FEATURE_MTF_EXECUTION_GATE", "true").lower() in ["true", "1", "yes"]
        self.min_mtf_execution_score = float(os.getenv("MIN_MTF_EXECUTION_SCORE", 0.30))
        self.min_mtf_execution_score_metal = float(os.getenv("MIN_MTF_EXECUTION_SCORE_METAL", 0.45))
        self.early_entry_enabled = os.getenv("FEATURE_EARLY_ENTRY", "true").lower() in ["true", "1", "yes"]
        self.early_entry_min_score = float(os.getenv("EARLY_ENTRY_MIN_SCORE", 0.50))
        self.false_move_detection_enabled = os.getenv("FEATURE_FALSE_MOVE_DETECTION", "true").lower() in ["true", "1", "yes"]
        self.news_mode_enabled = os.getenv("FEATURE_NEWS_MODE", "true").lower() in ["true", "1", "yes"]
        self.news_block_unsafe = os.getenv("NEWS_BLOCK_UNSAFE", "true").lower() in ["true", "1", "yes"]
        self.news_risk_multiplier = float(os.getenv("NEWS_RISK_MULTIPLIER", 0.35))
        self.news_allow_retest_follow = os.getenv("NEWS_ALLOW_RETEST_FOLLOW", "true").lower() in ["true", "1", "yes"]
        self.news_ladder_enabled = os.getenv("FEATURE_NEWS_LADDER", "true").lower() in ["true", "1", "yes"]
        self.news_ladder_max_addons = int(os.getenv("NEWS_LADDER_MAX_ADDONS", 2))
        self.news_ladder_min_r = float(os.getenv("NEWS_LADDER_MIN_R", 0.55))
        self.news_ladder_volume_pct = float(os.getenv("NEWS_LADDER_VOLUME_PCT", 0.35))
        self.news_ladder_cooldown_seconds = int(os.getenv("NEWS_LADDER_COOLDOWN_SECONDS", 180))

        # predefined market sessions (UTC times)
        self.sessions = {
            "Asia": {"start": "00:00", "end": "09:00"},
            "London": {"start": "08:00", "end": "17:00"},
            "New York": {"start": "13:00", "end": "22:00"},
        }

        # Advanced trading features
        self.pending_order_manager = PendingOrderManager(self.mt5)
        self.conditional_watchlist_manager = ConditionalWatchlistManager(self.mt5)
        
        # War Room Engines
        self.analytic_engine = AnalyticEngine()
        self.predictive_engine = PredictiveEngine()
        analytic_weight = float(os.getenv("ANALYTIC_WEIGHT", 0.6))
        predictive_weight = float(os.getenv("PREDICTIVE_WEIGHT", 0.4))
        self.ensemble_decision = EnsembleDecision(analytic_weight, predictive_weight)
        self.conviction_threshold = float(os.getenv("CONVICTION_THRESHOLD", 0.20))
        
        # Feature toggles (can be controlled via UI)
        self.features = {
            "pending_orders": os.getenv("FEATURE_PENDING_ORDERS", "true").lower() in ["true", "1"],
            "conditional_watchlist": os.getenv("FEATURE_CONDITIONAL_WATCHLIST", "true").lower() in ["true", "1"],
            "war_room": os.getenv("FEATURE_WAR_ROOM", "true").lower() in ["true", "1"],
        }
        
        # CRITICAL: Validate all config values on startup
        self._validate_config()

    def _validate_config(self):
        """CRITICAL FIX: Validate all configuration values on startup"""
        try:
            # Validate TRADING_SYMBOLS
            if isinstance(self.symbols, str):
                symbols = [s.strip().upper() for s in self.symbols.split(",") if s.strip()]
            else:
                symbols = [str(s).strip().upper() for s in self.symbols if s]
            
            if not symbols:
                logger.error("No valid TRADING_SYMBOLS configured. Using defaults.")
                self.symbols = ["EURUSD", "GBPUSD", "USDJPY"]
            else:
                self.symbols = symbols
                logger.info(f"Trading symbols validated: {self.symbols}")
            
            # Validate TRADE_VOLUME
            if self.volume <= 0:
                logger.error(f"Invalid TRADE_VOLUME: {self.volume}. Must be positive. Using default 0.001")
                self.volume = 0.001
            elif self.volume > 10:
                logger.warning(f"TRADE_VOLUME is very high: {self.volume}. Consider reducing.")
            self.position_sizing_mode = "fixed"
            
            # Validate MAX_EXPOSURE_PERCENT (allow 5 or 0.05 style inputs)
            if self.max_exposure_pct <= 0:
                logger.error(f"Invalid MAX_EXPOSURE_PERCENT: {self.max_exposure_pct}. Using default 0.05")
                self.max_exposure_pct = 0.05
            elif self.max_exposure_pct > 1 and self.max_exposure_pct <= 100:
                self.max_exposure_pct = self.max_exposure_pct / 100.0
            elif self.max_exposure_pct > 100:
                logger.error(f"Unrealistic MAX_EXPOSURE_PERCENT: {self.max_exposure_pct}. Using default 0.05")
                self.max_exposure_pct = 0.05
            
            # Validate MIN_PROFIT_PIPS
            if self.min_profit_pips < 1:
                logger.error(f"Invalid MIN_PROFIT_PIPS: {self.min_profit_pips}. Using default 10")
                self.min_profit_pips = 10

            self.partial_tp_trigger_r = max(0.1, self.partial_tp_trigger_r)
            self.partial_tp_close_pct = max(0.05, min(1.0, self.partial_tp_close_pct))
            self.partial_tp_extend_pct = max(0.0, min(3.0, self.partial_tp_extend_pct))
            self.partial_tp_lock_pips = max(0.0, self.partial_tp_lock_pips)
            self.breakeven_trigger_r = max(0.05, self.breakeven_trigger_r)
            self.breakeven_lock_pips = max(0.0, self.breakeven_lock_pips)
            self.first_profit_breakeven_trigger_r = max(0.02, self.first_profit_breakeven_trigger_r)
            self.first_profit_breakeven_trigger_r_scalp = max(0.02, self.first_profit_breakeven_trigger_r_scalp)
            self.reverse_profit_min_r = max(0.1, self.reverse_profit_min_r)
            self.reverse_profit_giveback_pct = max(0.05, min(0.95, self.reverse_profit_giveback_pct))
            self.reverse_profit_close_pct = max(0.1, min(1.0, self.reverse_profit_close_pct))
            self.max_adverse_r = max(0.1, min(1.0, self.max_adverse_r))
            self.reverse_after_partial_lock_r = max(0.0, self.reverse_after_partial_lock_r)
            self.daily_loss_cap_pct = max(0.005, min(0.10, self.daily_loss_cap_pct))
            self.max_daily_losses = max(0, min(100, self.max_daily_losses))
            self.max_consecutive_losses = max(0, min(100, self.max_consecutive_losses))
            self.loss_cooldown_minutes = max(30, self.loss_cooldown_minutes)
            self.max_active_trades_total = max(1, min(20, self.max_active_trades_total))
            self.small_account_threshold = max(1.0, self.small_account_threshold)
            self.small_account_trade_volume = max(0.001, self.small_account_trade_volume)
            self.small_account_max_auto_min_lot = max(0.001, min(0.10, self.small_account_max_auto_min_lot))
            if self.small_account_max_exposure_pct > 1 and self.small_account_max_exposure_pct <= 100:
                self.small_account_max_exposure_pct = self.small_account_max_exposure_pct / 100.0
            self.small_account_max_exposure_pct = max(0.001, min(0.05, self.small_account_max_exposure_pct))
            self.small_account_max_active_trades = max(1, min(5, self.small_account_max_active_trades))
            self.catastrophic_loss_r = max(0.5, self.catastrophic_loss_r)
            self.catastrophic_loss_cooldown_minutes = max(60, self.catastrophic_loss_cooldown_minutes)
            self.reversal_shock_cooldown_minutes = max(1, self.reversal_shock_cooldown_minutes)
            self.reversal_shock_xau_cooldown_minutes = max(self.reversal_shock_cooldown_minutes, self.reversal_shock_xau_cooldown_minutes)
            self.opposing_signal_min_r = max(0.0, self.opposing_signal_min_r)
            self.opposing_signal_min_score = max(0.0, min(1.0, self.opposing_signal_min_score))
            self.min_professional_score = max(0.0, min(1.0, self.min_professional_score))
            self.min_professional_conviction = max(0.0, min(1.0, self.min_professional_conviction))
            self.min_trade_readiness_score = max(0.0, min(1.0, self.min_trade_readiness_score))
            self.min_session_score_for_trade = max(0.0, min(1.0, self.min_session_score_for_trade))
            self.min_session_score_for_scalp = max(0.0, min(1.0, self.min_session_score_for_scalp))
            self.news_risk_multiplier = max(0.05, min(1.0, self.news_risk_multiplier))
            self.news_ladder_max_addons = max(0, min(5, self.news_ladder_max_addons))
            self.news_ladder_min_r = max(0.1, self.news_ladder_min_r)
            self.news_ladder_volume_pct = max(0.05, min(1.0, self.news_ladder_volume_pct))
            self.news_ladder_cooldown_seconds = max(30, self.news_ladder_cooldown_seconds)
            self.take_profit_r_multiplier = max(0.1, self.take_profit_r_multiplier)
            self.take_profit_r_multiplier_scalp = max(0.1, self.take_profit_r_multiplier_scalp)
            if self.min_execution_grade not in {"A", "B", "C", "D"}:
                self.min_execution_grade = "B"
            
            logger.info(f"Config validated: Sizing=fixed, Volume={self.volume}, MaxOpenRisk={self.max_exposure_pct*100}%, MinProfit={self.min_profit_pips}p")
            
            # Initialize symbols from MT5 Market Watch for multi-pair awareness
            self._initialize_symbols_from_market_watch()
            self._apply_dynamic_account_profile()
            
        except Exception as e:
            logger.critical(f"Config validation error: {e}. Using safe defaults.")
            self.symbols = ["EURUSD"]
            self.volume = 0.001
            self.risk_pct = 0.0
            self.position_sizing_mode = "fixed"
            self.max_exposure_pct = 0.05
            self.min_profit_pips = 10

    def _initialize_symbols_from_market_watch(self):
        """Initialize trading symbols from MT5 Market Watch for multi-pair awareness"""
        try:
            if not self.mt5.ensure_connected():
                logger.warning("MT5 not connected, using configured symbols")
                return
            
            # Get all symbols from Market Watch
            all_symbols = self.mt5.get_symbols()
            if all_symbols is None:
                logger.warning("Failed to get symbols from MT5, using configured symbols")
                return
            
            # Filter for visible symbols (in Market Watch)
            market_watch_symbols = [s.name for s in all_symbols if s.visible]
            
            if market_watch_symbols:
                # Use Market Watch symbols, but prioritize configured ones
                configured_symbols = set(self.symbols)
                market_watch_set = set(market_watch_symbols)
                
                # Combine: configured symbols first, then additional market watch symbols
                combined_symbols = list(configured_symbols) + [s for s in market_watch_symbols if s not in configured_symbols]
                
                self.symbols = combined_symbols[:50]  # Limit to 50 symbols to avoid overload
                logger.info(f"✓ Multi-pair awareness activated: {len(self.symbols)} symbols from Market Watch")
            else:
                logger.warning("No symbols visible in Market Watch, using configured symbols")
                
        except Exception as e:
            logger.error(f"Error initializing symbols from Market Watch: {e}")
            # Fall back to configured symbols

    def add_logic(self, symbol: str, message: str, level: str = "info"):
        """Add a logic feed entry for dashboard tracing."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "message": message,
            "level": level,
        }
        self.logic_feed.append(entry)
        self.logic_feed = self.logic_feed[-100:]
        if level == "error":
            logger.error(f"{symbol}: {message}")
        elif level == "warning":
            logger.warning(f"{symbol}: {message}")
        else:
            logger.info(f"{symbol}: {message}")

    def log_rejection(self, symbol: str, reason: str, details: dict | None = None):
        """Store rejection reasons for UI consumption."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "SIGNAL_REJECTED",
            "symbol": symbol,
            "reason": reason,
        }
        if details:
            entry.update(details)
        self.rejection_logs.append(entry)
        self.rejection_logs = self.rejection_logs[-50:]
        try:
            self.logger._save_log(entry)
        except Exception as e:
            logger.error(f"Failed to persist rejection log: {e}")
        self.add_logic(symbol, f"Rejected: {reason}", level="warning")

    def connect(self):
        """Connect to MT5"""
        connected = self.mt5.connect()
        if connected:
            info = self.mt5.get_account_info() or {}
            self.start_equity = info.get("equity")
            self.peak_equity = self.start_equity
        return connected

    def disconnect(self):
        """Disconnect from MT5"""
        self.mt5.disconnect()

    def _get_equity(self):
        info = self.mt5.get_account_info() or {}
        return info.get("equity")

    def _small_account_mode_should_apply(self, equity: float | None = None) -> bool:
        if not self.small_account_mode_enabled:
            return False
        try:
            current_equity = float(equity if equity is not None else (self._get_equity() or 0))
        except Exception:
            current_equity = 0.0
        return current_equity > 0 and current_equity <= self.small_account_threshold

    def _apply_small_account_overlay(self, equity: float | None = None):
        self.small_account_active = self._small_account_mode_should_apply(equity)
        if not self.small_account_active:
            return

        self.account_profile_name = "small_account"
        self.volume = max(0.001, self.small_account_trade_volume)
        self.max_auto_min_lot = max(0.001, min(self.max_auto_min_lot, self.small_account_max_auto_min_lot))
        self.max_exposure_pct = min(self.max_exposure_pct, self.small_account_max_exposure_pct)
        self.max_active_trades_total = max(1, min(self.max_active_trades_total, self.small_account_max_active_trades))
        self.max_trades_per_symbol = 1
        self.allow_c_scalps = False
        if self.min_execution_grade not in {"A"}:
            self.min_execution_grade = "B"
        self.min_trade_readiness_score = max(self.min_trade_readiness_score, 0.60)
        self.min_professional_score = max(self.min_professional_score, 0.62)
        self.min_professional_conviction = max(self.min_professional_conviction, 0.30)
        self.broker_min_lot_fallback_enabled = True
        if self.small_account_disable_news_ladder:
            self.news_ladder_enabled = False
        if self.small_account_disable_pending_orders:
            self.features["pending_orders"] = False

    def _apply_dynamic_account_profile(self):
        """Adapt execution strictness and fixed lot size to the attached MT5 account equity."""
        self.news_ladder_enabled = str(self._read_env_value("FEATURE_NEWS_LADDER", str(self.news_ladder_enabled))).lower() in ["true", "1", "yes"]
        self.features["pending_orders"] = str(self._read_env_value("FEATURE_PENDING_ORDERS", str(self.features.get("pending_orders", True)))).lower() in ["true", "1", "yes"]
        self.small_account_active = False
        if not self.dynamic_account_profile_enabled:
            self.account_profile_name = "manual"
            self._apply_small_account_overlay()
            return

        try:
            equity = float(self._get_equity() or 0)
        except Exception:
            equity = 0.0
        if equity <= 0:
            self._apply_small_account_overlay()
            return

        def env_float(key, default):
            try:
                return float(os.getenv(key, default))
            except Exception:
                return float(default)

        if equity < 25:
            profile = {
                "name": "tiny",
                "volume": env_float("DYNAMIC_TINY_TRADE_VOLUME", 0.001),
                "max_auto_min_lot": env_float("DYNAMIC_TINY_MAX_AUTO_MIN_LOT", 0.01),
                "grade": "B",
                "allow_c_scalps": False,
                "setup": 0.62,
                "readiness": 0.58,
                "professional_conviction": 0.30,
                "market_score": 0.45,
                "market_conviction": 0.30,
                "session_trade": 0.40,
                "session_scalp": 0.55,
                "cooldown": 3,
                "daily_loss_cap_pct": 0.03,
                "max_daily_losses": 1,
                "max_consecutive_losses": 1,
                "loss_cooldown_minutes": 360,
                "max_active_trades_total": 1,
            }
        elif equity < 100:
            profile = {
                "name": "small",
                "volume": env_float("DYNAMIC_SMALL_TRADE_VOLUME", 0.001),
                "max_auto_min_lot": env_float("DYNAMIC_SMALL_MAX_AUTO_MIN_LOT", 0.01),
                "grade": "B",
                "allow_c_scalps": False,
                "setup": 0.60,
                "readiness": 0.56,
                "professional_conviction": 0.28,
                "market_score": 0.44,
                "market_conviction": 0.30,
                "session_trade": 0.38,
                "session_scalp": 0.52,
                "cooldown": 3,
                "daily_loss_cap_pct": 0.04,
                "max_daily_losses": 1,
                "max_consecutive_losses": 1,
                "loss_cooldown_minutes": 240,
                "max_active_trades_total": 1,
            }
        elif equity < 500:
            profile = {
                "name": "standard",
                "volume": env_float("DYNAMIC_STANDARD_TRADE_VOLUME", 0.01),
                "max_auto_min_lot": env_float("DYNAMIC_STANDARD_MAX_AUTO_MIN_LOT", 0.02),
                "grade": "B",
                "allow_c_scalps": False,
                "setup": 0.56,
                "readiness": 0.54,
                "professional_conviction": 0.25,
                "market_score": 0.42,
                "market_conviction": 0.30,
                "session_trade": 0.35,
                "session_scalp": 0.45,
                "cooldown": 3,
                "daily_loss_cap_pct": 0.05,
                "max_daily_losses": 2,
                "max_consecutive_losses": 1,
                "loss_cooldown_minutes": 120,
                "max_active_trades_total": 1,
            }
        else:
            profile = {
                "name": "large",
                "volume": env_float("DYNAMIC_LARGE_TRADE_VOLUME", 0.02),
                "max_auto_min_lot": env_float("DYNAMIC_LARGE_MAX_AUTO_MIN_LOT", 0.05),
                "grade": "B",
                "allow_c_scalps": False,
                "setup": 0.62,
                "readiness": 0.60,
                "professional_conviction": 0.30,
                "market_score": 0.45,
                "market_conviction": 0.35,
                "session_trade": 0.40,
                "session_scalp": 0.55,
                "cooldown": 3,
                "daily_loss_cap_pct": 0.05,
                "max_daily_losses": 2,
                "max_consecutive_losses": 2,
                "loss_cooldown_minutes": 60,
                "max_active_trades_total": 2,
            }

        self.account_profile_name = profile["name"]
        self.account_profile_equity = equity
        self.dynamic_profile_last_applied_at = datetime.now().isoformat()
        self.volume = max(0.001, profile["volume"])
        self.max_auto_min_lot = max(0.001, profile["max_auto_min_lot"])
        self.position_sizing_mode = "fixed"
        self.min_execution_grade = profile["grade"]
        self.allow_c_scalps = bool(profile.get("allow_c_scalps", False))
        self.min_professional_score = profile["setup"]
        self.min_professional_conviction = profile["professional_conviction"]
        self.min_trade_readiness_score = profile["readiness"]
        self.market_execution_score_threshold = profile["market_score"]
        self.market_execution_conviction_threshold = profile["market_conviction"]
        self.min_session_score_for_trade = profile["session_trade"]
        self.min_session_score_for_scalp = profile["session_scalp"]
        self.trade_cooldown_minutes = profile["cooldown"]
        self.daily_loss_cap_pct = profile["daily_loss_cap_pct"]
        try:
            self.max_daily_losses = max(0, min(100, int(os.getenv("MAX_DAILY_LOSSES", profile["max_daily_losses"]))))
        except Exception:
            self.max_daily_losses = profile["max_daily_losses"]
        try:
            self.max_consecutive_losses = max(0, min(100, int(os.getenv("MAX_CONSECUTIVE_LOSSES", profile["max_consecutive_losses"]))))
        except Exception:
            self.max_consecutive_losses = profile["max_consecutive_losses"]
        self.loss_cooldown_minutes = profile["loss_cooldown_minutes"]
        try:
            configured_total = int(os.getenv("MAX_ACTIVE_TRADES_TOTAL", profile["max_active_trades_total"]))
        except Exception:
            configured_total = profile["max_active_trades_total"]
        self.max_active_trades_total = max(1, min(20, configured_total))
        self.armed_required_scans = 1
        self.armed_require_structure = False
        self.broker_min_lot_fallback_enabled = True
        self._apply_small_account_overlay(equity)

    def _get_symbol_info(self, symbol: str):
        return self.mt5.get_symbol_info(symbol)

    def _get_pip_value(self, symbol: str):
        """Return the value of one pip for 1 lot."""
        info = self._get_symbol_info(symbol)
        if not info:
            return None

        digits = getattr(info, "digits", None)
        if digits is None:
            return None

        pip_size = 0.0001 if digits > 3 else 0.01
        tick_value = (
            getattr(info, "trade_tick_value", None)
            or getattr(info, "trade_tick_value_profit", None)
            or getattr(info, "trade_tick_value_loss", None)
        )
        tick_size = getattr(info, "trade_tick_size", None) or getattr(info, "point", None)

        if tick_value and tick_size and tick_value > 0 and tick_size > 0:
            return float(tick_value) * (pip_size / float(tick_size))

        contract_size = getattr(info, "trade_contract_size", None)
        if not contract_size or contract_size <= 0:
            logger.warning(f"Cannot calculate pip value for {symbol}: missing tick value and contract size")
            return None

        tick = None
        try:
            tick = self.mt5.get_symbol_tick(symbol)
        except Exception:
            tick = None

        price = None
        if tick:
            bid = getattr(tick, "bid", None)
            ask = getattr(tick, "ask", None)
            if bid and ask:
                price = (float(bid) + float(ask)) / 2
            elif bid:
                price = float(bid)
            elif ask:
                price = float(ask)

        clean_symbol = "".join(ch for ch in str(symbol).upper() if ch.isalpha())
        base = clean_symbol[:3]
        quote = clean_symbol[3:6]
        account = self.mt5.get_account_info() or {}
        account_currency = str(account.get("currency") or "USD").upper()

        # Fallback for common USD-denominated symbols. Broker tick metadata is preferred above.
        if quote == account_currency:
            return float(contract_size) * pip_size
        if base == account_currency and price and price > 0:
            return (float(contract_size) * pip_size) / price
        if symbol.upper().startswith("XAU") and quote == account_currency:
            return float(contract_size) * pip_size

        logger.warning(
            f"Cannot accurately calculate pip value for {symbol}: no broker tick value and no {account_currency} conversion path"
        )
        return None

    def _has_hit_daily_profit_cap(self) -> bool:
        """Check if daily profit cap has been reached."""
        try:
            account = self.mt5.get_account_info()
            if not account:
                return False
            
            current_equity = account.get("equity", 0)
            
            # Initialize daily start equity if not set
            if self.daily_start_equity is None:
                self.daily_start_equity = current_equity
                return False
            
            # Check if it's a new day (equity reset or significant change)
            if current_equity < self.daily_start_equity * 0.8:  # Reset if equity dropped >20%
                self.daily_start_equity = current_equity
                return False
            
            daily_profit_pct = (current_equity - self.daily_start_equity) / self.daily_start_equity
            
            if daily_profit_pct >= self.daily_profit_cap:
                logger.info(f"Daily profit cap reached: {daily_profit_pct*100:.2f}% >= {self.daily_profit_cap*100:.1f}%")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error checking daily profit cap: {e}")
            return False

    def _round_lot(self, volume: float, lot_step: float) -> float:
        """Round a lot size down to the broker's volume step."""
        try:
            step = float(lot_step or 0.01)
            if step <= 0:
                step = 0.01
            steps = int(float(volume) / step)
            rounded = steps * step
            return round(max(step, rounded), 8)
        except Exception:
            return round(float(volume or self.volume), 2)

    def _round_symbol_lot(self, symbol: str, volume: float) -> float:
        """Round volume using the symbol's broker lot settings."""
        try:
            info = self._get_symbol_info(symbol)
            if not info:
                return self._round_lot(volume, 0.001)
            min_lot = float(getattr(info, "volume_min", 0.001) or 0.001)
            max_lot = float(getattr(info, "volume_max", 100) or 100)
            lot_step = float(getattr(info, "volume_step", 0.001) or 0.001)
            rounded = self._round_lot(volume, lot_step)
            return max(min_lot, min(max_lot, rounded))
        except Exception:
            return self._round_lot(volume, 0.001)

    def _get_symbol_min_lot(self, symbol: str) -> float:
        try:
            info = self._get_symbol_info(symbol)
            if not info:
                return 0.01
            return float(getattr(info, "volume_min", 0.001) or 0.001)
        except Exception:
            return 0.001

    def _calculate_volume(self, symbol: str, entry: float, sl: float) -> float:
        """Return the configured fixed lot size, rounded to the broker symbol step."""
        info = self._get_symbol_info(symbol)
        if info:
            min_lot = float(getattr(info, "volume_min", 0.001) or 0.001)
            max_lot = float(getattr(info, "volume_max", 100) or 100)
            lot_step = float(getattr(info, "volume_step", 0.001) or 0.001)
            if self.volume < min_lot:
                if self.broker_min_lot_fallback_enabled and min_lot <= self.max_auto_min_lot:
                    logger.warning(
                        f"Fixed lot {self.volume} is below broker minimum {min_lot} for {symbol}; using broker minimum"
                    )
                    requested_volume = min_lot
                else:
                    logger.error(
                        f"Fixed lot {self.volume} is below broker minimum {min_lot} for {symbol}; rejecting trade"
                    )
                    return 0.0
            else:
                requested_volume = self.volume
            volume = min(max_lot, self._round_lot(requested_volume, lot_step))
        else:
            volume = self._round_lot(self.volume, 0.001)
        logger.debug(f"Fixed lot sizing for {symbol}: volume={volume:.3f}")
        return volume

    def _calculate_risk_amount(self, symbol: str, entry: float, sl: float, volume: float) -> float:
        """Calculate the dollar risk amount for a given trade."""
        pip_value = self._get_pip_value(symbol)
        if pip_value is None:
            return 0.0

        info = self._get_symbol_info(symbol)
        if not info:
            return 0.0

        digits = getattr(info, "digits", 5)
        pip_size = 0.0001 if digits > 3 else 0.01
        stop_pips = abs(entry - sl) / pip_size if pip_size else 0
        return stop_pips * pip_value * volume

    def _get_pip_size(self, symbol: str):
        """Return pip size for the symbol (e.g. 0.0001 for EURUSD, 0.01 for JPY pairs)."""
        info = self._get_symbol_info(symbol)
        if not info:
            return None
        digits = getattr(info, "digits", None)
        if digits is None:
            return None
        return 0.0001 if digits > 3 else 0.01

    def _calculate_exposure(self):
        """Estimate current risk exposure of open trades."""
        exposure, _ = self._calculate_exposure_details()
        return exposure

    def _calculate_exposure_details(self):
        """Estimate current risk exposure and return per-position details."""
        positions = self.mt5.get_positions() or []
        exposure = 0.0
        details = []
        for pos in positions:
            symbol = pos.get("symbol")
            entry = pos.get("entry")
            sl = pos.get("sl")
            volume = pos.get("volume")
            if not symbol or entry is None or sl is None or volume is None:
                continue
            risk_amount = self._calculate_risk_amount(symbol, entry, sl, volume)
            exposure += risk_amount
            details.append({
                "symbol": symbol,
                "ticket": pos.get("ticket"),
                "type": pos.get("type"),
                "volume": volume,
                "entry": entry,
                "sl": sl,
                "risk": risk_amount,
            })
        return exposure, details

    def _format_exposure_details(self, details):
        if not details:
            return "no open positions with SL risk"
        return "; ".join(
            f"{item.get('symbol')} {item.get('type')} vol={float(item.get('volume') or 0):.2f} "
            f"risk=${float(item.get('risk') or 0):.2f}"
            for item in details
        )

    def _can_trade(self):
        """CRITICAL FIX: Determine if new trades are allowed with equity validation"""
        # Check cooldown
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds() / 60
            logger.info(f"In cooldown. {remaining:.0f} minutes remaining")
            return False, f"In cooldown ({remaining:.0f}m remaining)"

        # Global trade interval disabled: rely on symbol-specific cooldown and exposure checks only

        # Get equity
        equity = self._get_equity()
        
        # CRITICAL FIX: Handle None and check > 0
        if equity is None:
            logger.error("Cannot get account equity")
            return False, "Cannot get account equity"
        
        if equity <= 0:
            logger.critical(f"🚨 ACCOUNT LIQUIDATED! Equity: {equity}. Stopping engine.")
            self.is_running = False  # Stop immediately
            # Log liquidation event
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "LIQUIDATION_ALERT",
                "equity": equity,
            })
            return False, "Account liquidated - stopping engine"

        loss_brake = self._get_loss_brake_state(equity)
        if loss_brake.get("blocked"):
            reason = loss_brake.get("reason", "Daily loss brake active")
            logger.warning(f"Loss brake blocking new trade: {reason}")
            return False, reason

        active_total = len(self.active_trades or {})
        try:
            broker_positions = self.mt5.get_positions() or []
            active_total = max(active_total, len(broker_positions))
        except Exception:
            pass
        if self.max_active_trades_total > 0 and active_total >= self.max_active_trades_total:
            return False, f"Max total active trades reached ({active_total}/{self.max_active_trades_total})"

        # Check exposure
        current_exposure, exposure_details = self._calculate_exposure_details()
        max_exposure = equity * self.max_exposure_pct
        
        if current_exposure >= max_exposure:
            logger.info(
                f"Max exposure reached. Current: ${current_exposure:.2f}, Max: ${max_exposure:.2f}. "
                f"Details: {self._format_exposure_details(exposure_details)}"
            )
            return False, f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})"

        return True, "OK"

    def _can_place_pending_orders(self):
        """Check if the bot can place or refresh pending orders without blocking from market-only intervals."""
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds() / 60
            logger.info(f"Pending order blocked by cooldown. {remaining:.0f} minutes remaining")
            return False, f"Cooldown active ({remaining:.0f}m remaining)"

        equity = self._get_equity()
        if equity is None:
            logger.error("Cannot get account equity for pending orders")
            return False, "Cannot get account equity"
        if equity <= 0:
            logger.critical(f"🚨 ACCOUNT LIQUIDATED! Equity: {equity}. Stopping engine.")
            self.is_running = False
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "LIQUIDATION_ALERT",
                "equity": equity,
            })
            return False, "Account liquidated - stopping engine"

        loss_brake = self._get_loss_brake_state(equity)
        if loss_brake.get("blocked"):
            reason = loss_brake.get("reason", "Daily loss brake active")
            logger.warning(f"Loss brake blocking pending orders: {reason}")
            return False, reason

        active_total = len(self.active_trades or {})
        try:
            broker_positions = self.mt5.get_positions() or []
            active_total = max(active_total, len(broker_positions))
        except Exception:
            pass
        if self.max_active_trades_total > 0 and active_total >= self.max_active_trades_total:
            return False, f"Max total active trades reached ({active_total}/{self.max_active_trades_total})"

        current_exposure, exposure_details = self._calculate_exposure_details()
        max_exposure = equity * self.max_exposure_pct
        if current_exposure >= max_exposure:
            logger.info(
                f"Pending order blocked: max exposure reached. Current: ${current_exposure:.2f}, Max: ${max_exposure:.2f}. "
                f"Details: {self._format_exposure_details(exposure_details)}"
            )
            return False, f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})"

        return True, "OK"

    # ========== GLOBAL TRADE REGISTRY METHODS ===========

    def _check_signal_lockout(
        self,
        symbol: str,
        is_addon: bool = False,
        strategy: str | None = None,
        signal: dict | None = None,
        ensemble_decision: dict | None = None,
    ) -> tuple[bool, str]:
        """Check if a symbol is locked out from new trades due to active positions or cooldown.

        News ladder add-ons are attached to an existing active trade and should not be blocked by the
        symbol-level trade slot limit.
        """
        if is_addon or (strategy and strategy == "news_ladder"):
            return True, "Addon bypass lockout"

        if not self.signal_lockout_enabled:
            return True, "Lockout disabled"

        with self._trades_lock:
            registry_key = self._trade_registry_key(symbol)
            registry = self.trade_registry.get(registry_key, {
                "active_trades": 0,
                "last_trade_time": None,
                "cooldown_until": None,
                "shock_until": None,
                "shock_direction": None,
            })

            # Check active trades limit
            if registry["active_trades"] >= self.max_trades_per_symbol:
                return False, f"Max trades reached ({registry['active_trades']}/{self.max_trades_per_symbol})"

            shock_until = registry.get("shock_until")
            signal_action = str((signal or {}).get("action") or "").upper()
            shock_direction = str(registry.get("shock_direction") or "").upper()
            if self.reversal_shock_guard_enabled and shock_until and datetime.now() < shock_until:
                remaining = (shock_until - datetime.now()).total_seconds() / 60
                if not signal_action or not shock_direction or signal_action == shock_direction:
                    return False, (
                        f"Reversal shock guard active for {shock_direction or 'same direction'} "
                        f"({remaining:.1f}m remaining after adverse exit)"
                    )

            # Check cooldown period
            if registry["cooldown_until"] and datetime.now() < registry["cooldown_until"]:
                remaining = (registry["cooldown_until"] - datetime.now()).total_seconds() / 60
                override_ok, override_reason = self._cooldown_override_allowed(signal, ensemble_decision)
                if override_ok:
                    self.logger._save_log({
                        "timestamp": datetime.now().isoformat(),
                        "event": "COOLDOWN_OVERRIDE",
                        "symbol": symbol,
                        "remaining_minutes": round(remaining, 2),
                        "reason": override_reason,
                    })
                    self.add_logic(symbol, f"Cooldown override allowed: {override_reason}", level="info")
                    return True, f"Cooldown override: {override_reason}"
                return False, f"Cooldown active ({remaining:.1f}m remaining)"

            return True, "OK"

    def _trade_registry_key(self, symbol: str) -> str:
        """Return the existing registry key for a symbol, allowing broker suffixes."""
        for tracked_symbol in self.trade_registry.keys():
            if self._symbols_match(tracked_symbol, symbol):
                return tracked_symbol
        return symbol

    def _cooldown_override_allowed(self, signal: dict | None, ensemble_decision: dict | None = None) -> tuple[bool, str]:
        """Allow only exceptional fresh-structure setups through symbol cooldown."""
        if not self.cooldown_override_enabled:
            return False, "Cooldown override disabled"
        if not signal:
            return False, "No signal supplied for cooldown override"

        setup = signal.get("setup_score") or {}
        grade = str(setup.get("grade") or "D").upper()
        grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
        required_rank = grade_rank.get(self.cooldown_override_min_grade, 4)
        if grade_rank.get(grade, 1) < required_rank:
            return False, f"Grade {grade} below override grade {self.cooldown_override_min_grade}"

        score = float(setup.get("score") or signal.get("confluence_score") or 0.0)
        if score < self.cooldown_override_min_score:
            return False, f"Score {score:.2f} below override {self.cooldown_override_min_score:.2f}"

        conviction = float((ensemble_decision or {}).get("conviction") or signal.get("conviction") or 0.0)
        if conviction < self.cooldown_override_min_conviction:
            return False, f"Conviction {conviction:.2f} below override {self.cooldown_override_min_conviction:.2f}"

        if self.cooldown_override_require_spread_safe:
            spread_ok, spread_reason = self._get_spread_safety(signal)
            if not spread_ok:
                return False, spread_reason
        else:
            spread_reason = "spread override check disabled"

        drift_ok, drift_reason = self._is_price_near_entry(signal)
        if not drift_ok:
            return False, drift_reason

        if self.cooldown_override_require_new_structure:
            components = setup.get("components") or []
            passed = {c.get("key") for c in components if c.get("passed")}
            has_structure = bool({"liquidity_sweep", "mss", "displacement"}.intersection(passed))
            archetype = str(setup.get("archetype") or "")
            if not has_structure or archetype == "Context Watch":
                return False, "No fresh liquidity/MSS/displacement structure for override"

        return True, f"Grade {grade}, score={score:.2f}, conviction={conviction:.2f}, {spread_reason}, {drift_reason}"

    def _activate_reversal_shock_guard(self, symbol: str, action: str | None, reason: str, r_value=None, profit=None):
        """Block same-direction re-entry after the market invalidates a trade."""
        if not self.reversal_shock_guard_enabled or not symbol:
            return

        minutes = self.reversal_shock_xau_cooldown_minutes if self._symbol_profile_name(symbol) == "XAU" else self.reversal_shock_cooldown_minutes
        shock_until = datetime.now() + timedelta(minutes=minutes)
        action = str(action or "").upper()

        with self._trades_lock:
            registry = self.trade_registry.setdefault(symbol, {
                "active_trades": 0,
                "last_trade_time": None,
                "cooldown_until": None,
            })
            registry["shock_until"] = shock_until
            registry["shock_direction"] = action
            registry["last_shock_reason"] = reason
            registry["last_shock_r"] = r_value
            registry["last_shock_profit"] = profit

        self.logger._save_log({
            "timestamp": datetime.now().isoformat(),
            "event": "REVERSAL_SHOCK_GUARD",
            "symbol": symbol,
            "action": action,
            "cooldown_minutes": minutes,
            "shock_until": shock_until.isoformat(),
            "reason": reason,
            "r": r_value,
            "profit": profit,
        })
        self.add_logic(symbol, f"Reversal shock guard active for {action or 'same direction'} {minutes}m: {reason}", level="warning")

    def _open_position_for_symbol(self, symbol: str):
        """Return the currently open MT5 position for a symbol, if present."""
        try:
            positions = self.mt5.get_positions() or []
        except Exception:
            return None
        return next((pos for pos in positions if self._symbols_match(pos.get("symbol"), symbol)), None)

    def _apply_opposing_signal_profit_exit(self, signal: dict, ensemble_decision: dict | None, setup_value: float) -> bool:
        """Close a profitable active trade when a qualified opposite signal appears.

        This is a defensive exit only. It does not flip into the new signal; normal lockout and
        cooldown handling decide whether a future trade is allowed after MT5 confirms closure.
        """
        if not self.opposing_signal_profit_exit_enabled:
            return False

        symbol = signal.get("symbol")
        new_action = str(signal.get("action") or "").upper()
        trade = self.active_trades.get(symbol) if symbol else None
        if not symbol or not trade or new_action not in ["BUY", "SELL"]:
            return False

        old_action = str(trade.get("action") or "").upper()
        if old_action not in ["BUY", "SELL"] or old_action == new_action:
            return False

        pos = self._open_position_for_symbol(symbol)
        if not pos:
            return False

        r_now = self._position_r_multiple(pos)
        if r_now is None or r_now < self.opposing_signal_min_r:
            return False

        setup = signal.get("setup_score") or {}
        effective_score = max(float(setup.get("score") or 0.0), float(setup_value or 0.0))
        if effective_score < self.opposing_signal_min_score:
            return False

        qualified, qualification = self._qualify_signal_stage(signal, ensemble_decision or {}, effective_score)
        if not qualified:
            return False

        self._set_reversal_breakeven_at_entry(pos, "OPPOSING_SIGNAL_REVERSAL")

        if self._close_position_fraction(pos, 1.0, "OPPOSING_SIGNAL_PROFIT_EXIT"):
            trade["opposing_signal_exit"] = {
                "at": datetime.now().isoformat(),
                "old_action": old_action,
                "new_action": new_action,
                "r": r_now,
                "setup_score": effective_score,
                "qualification": qualification,
            }
            self._activate_reversal_shock_guard(
                symbol,
                old_action,
                f"qualified {new_action} signal appeared while {old_action} trade was in profit",
                r_value=r_now,
                profit=pos.get("profit"),
            )
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "OPPOSING_SIGNAL_PROFIT_EXIT",
                "symbol": symbol,
                "closed_action": old_action,
                "opposing_action": new_action,
                "r": r_now,
                "profit": pos.get("profit"),
                "setup_score": effective_score,
                "qualification": qualification,
            })
            self.add_logic(
                symbol,
                f"Closed profitable {old_action}: qualified {new_action} reversal signal at {r_now:.2f}R",
                level="warning",
            )
            return True

        return False

    def _should_use_market_execution(self, signal: dict, scalp_data: dict, ensemble_decision: dict = None) -> bool:
        """Determine whether a signal should be executed as a market trade."""
        score = float(scalp_data.get("score", 0.0)) if scalp_data else 0.0
        conviction = float(ensemble_decision.get("conviction", 0.0)) if ensemble_decision else 0.0
        confluence = float(signal.get("confluence_score", 0.0))
        setup_score = float((signal.get("setup_score") or {}).get("score", 0.0))

        if signal.get("early_entry") and setup_score >= self.early_entry_min_score:
            return True
        if setup_score >= max(0.55, self.early_entry_min_score):
            return True
        if confluence >= 0.70:
            return True
        if score >= self.market_execution_score_threshold:
            return True
        if score >= self.market_execution_score_threshold + 0.10:
            return True
        if conviction >= self.market_execution_conviction_threshold and score >= max(0.45, self.market_execution_score_threshold - 0.10):
            return True
        if conviction >= self.market_execution_conviction_threshold + 0.10 and score >= 0.40:
            return True
        if confluence >= 0.55 and score >= 0.45:
            return True
        return False

    def _refresh_signal_for_market_execution(self, signal: dict) -> tuple[bool, str]:
        """Rebuild market order levels around the current tick while preserving signal R:R."""
        symbol = signal.get("symbol")
        action = str(signal.get("action", "")).upper()
        entry = signal.get("entry")
        sl = signal.get("sl")
        tp = signal.get("tp")

        if not symbol or action not in ["BUY", "SELL"] or entry is None or sl is None or tp is None:
            return False, "Cannot refresh market levels; missing symbol/action/entry/SL/TP"

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            return False, f"Cannot refresh market levels; no tick for {symbol}"

        price = float(tick.ask if action == "BUY" else tick.bid)
        risk_distance = abs(float(entry) - float(sl))
        reward_distance = abs(float(tp) - float(entry))
        if risk_distance <= 0 or reward_distance <= 0:
            return False, "Cannot refresh market levels; invalid original risk/reward distance"

        original_entry = float(entry)
        if action == "BUY":
            signal["entry"] = price
            signal["sl"] = price - risk_distance
            signal["tp"] = price + reward_distance
        else:
            signal["entry"] = price
            signal["sl"] = price + risk_distance
            signal["tp"] = price - reward_distance

        signal["market_refresh"] = {
            "original_entry": original_entry,
            "price": price,
            "risk_distance": risk_distance,
            "reward_distance": reward_distance,
        }
        return True, f"Market levels refreshed from {original_entry:.5f} to {price:.5f}"

    def _register_trade_open(self, symbol: str):
        """Register a new trade opening in the global registry."""
        with self._trades_lock:
            registry_key = self._trade_registry_key(symbol)
            if registry_key not in self.trade_registry:
                self.trade_registry[registry_key] = {
                    "active_trades": 0,
                    "last_trade_time": None,
                    "cooldown_until": None,
                    "shock_until": None,
                    "shock_direction": None,
                }

            registry = self.trade_registry[registry_key]
            registry["active_trades"] += 1
            registry["last_trade_time"] = datetime.now()

            self.add_logic(symbol, f"Trade registered in Global Registry (active: {registry['active_trades']})", level="info")

    def _register_trade_close(self, symbol: str):
        """Register a trade closing in the global registry."""
        with self._trades_lock:
            registry_key = self._trade_registry_key(symbol)
            if registry_key in self.trade_registry:
                registry = self.trade_registry[registry_key]
                if registry["active_trades"] > 0:
                    registry["active_trades"] -= 1

                    # Set cooldown if this was the last active trade
                    if registry["active_trades"] == 0 and self.trade_cooldown_minutes > 0:
                        registry["cooldown_until"] = datetime.now() + timedelta(minutes=self.trade_cooldown_minutes)
                        self.add_logic(symbol, f"Cooldown activated ({self.trade_cooldown_minutes}m)", level="info")

                    self.add_logic(symbol, f"Trade removed from Global Registry (active: {registry['active_trades']})", level="info")

    def _get_registry_status(self, symbol: str = None) -> dict:
        """Get the current status of the trade registry for dashboard display."""
        with self._trades_lock:
            if symbol:
                registry = self.trade_registry.get(symbol, {
                    "active_trades": 0,
                    "last_trade_time": None,
                    "cooldown_until": None,
                    "shock_until": None,
                    "shock_direction": None,
                })
                return {
                    "symbol": symbol,
                    "active_trades": registry["active_trades"],
                    "max_trades": self.max_trades_per_symbol,
                    "cooldown_active": registry["cooldown_until"] is not None and datetime.now() < registry["cooldown_until"],
                    "cooldown_remaining": None if not registry["cooldown_until"] else
                        max(0, (registry["cooldown_until"] - datetime.now()).total_seconds() / 60),
                    "shock_active": registry.get("shock_until") is not None and datetime.now() < registry.get("shock_until"),
                    "shock_remaining": None if not registry.get("shock_until") else
                        max(0, (registry["shock_until"] - datetime.now()).total_seconds() / 60),
                    "shock_direction": registry.get("shock_direction"),
                    "last_trade": registry["last_trade_time"].isoformat() if registry["last_trade_time"] else None
                }
            else:
                # Return status for all symbols
                return {
                    symbol: self._get_registry_status(symbol)
                    for symbol in self.symbols
                }

    def _calculate_expected_r(self, signal: dict):
        """Estimate R-multiple (R = reward/risk) for a given signal."""
        entry = signal.get("entry")
        sl = signal.get("sl")
        tp = signal.get("tp")
        action = str(signal.get("action", "")).upper()

        if entry is None or sl is None or tp is None or action not in ["BUY", "SELL"]:
            return None

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0:
            return None
        return reward / risk

    def _env_key_for_symbol(self, prefix: str, symbol: str) -> str:
        normalized = "".join(ch for ch in str(symbol or "").upper() if ch.isalnum())
        return f"{prefix}_{normalized}"

    def _env_float(self, key: str, default: float) -> float:
        try:
            return float(self._read_env_value(key, str(default)))
        except Exception:
            return default

    def _env_bool(self, key: str, default: bool = False) -> bool:
        value = self._read_env_value(key, "") if hasattr(self, "_read_env_value") else os.getenv(key)
        if value is None:
            return default
        if str(value).strip() == "":
            return default
        return str(value).strip().lower() in ["1", "true", "yes", "on"]

    def _instrument_class(self, symbol: str | None) -> str:
        normalized = "".join(ch for ch in str(symbol or "").upper() if ch.isalnum())
        if not normalized:
            return "OTHER"

        stock_symbols = {
            s.strip().upper()
            for s in os.getenv("STOCK_SYMBOLS", "AAPL,MSFT,TSLA,NVDA,AMZN,META,GOOGL,GOOG,NFLX").split(",")
            if s.strip()
        }
        if any(token in normalized for token in ["XAU", "XAG", "GOLD", "SILVER"]):
            return "METAL"
        if any(token in normalized for token in ["BTC", "ETH", "LTC", "XRP", "DOGE", "SOL"]):
            return "CRYPTO"
        if any(token in normalized for token in ["US30", "SPX", "SP500", "NAS", "NDX", "DAX", "GER40", "UK100", "JP225"]):
            return "INDEX"
        if normalized in stock_symbols:
            return "STOCK"

        currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
        base = normalized[:3]
        quote = normalized[3:6]
        if len(normalized) >= 6 and base in currencies and quote in currencies:
            return "FOREX"
        return "OTHER"

    def _instrument_profile(self, symbol: str | None) -> dict:
        asset_class = self._instrument_class(symbol)
        profiles = {
            "FOREX": {
                "asset_class": "FOREX",
                "min_profit_pips": self._env_float("MIN_PROFIT_PIPS_FOREX", self._env_float("MIN_PROFIT_PIPS_FX", 1.5)),
                "max_entry_drift_pips": self._env_float("MAX_ENTRY_DRIFT_PIPS_FOREX", self.max_entry_drift_pips),
                "max_spread_pips": self._env_float("MAX_SPREAD_PIPS_FOREX", 2.5),
                "min_setup_score": self._env_float("MIN_SETUP_SCORE_FOREX", self.min_professional_score),
                "min_conviction": self._env_float("MIN_CONVICTION_FOREX", self.min_professional_conviction),
                "min_session_score": self._env_float("MIN_SESSION_SCORE_FOREX", self.min_session_score_for_trade),
                "min_grade": os.getenv("MIN_EXECUTION_GRADE_FOREX", self.min_execution_grade).strip().upper() or self.min_execution_grade,
                "block_scalps": self._env_bool("BLOCK_FOREX_SCALPS", False),
            },
            "METAL": {
                "asset_class": "METAL",
                "min_profit_pips": self._env_float("MIN_PROFIT_PIPS_METAL", self._env_float("MIN_PROFIT_PIPS_XAU", 20)),
                "max_entry_drift_pips": self._env_float("MAX_ENTRY_DRIFT_PIPS_METAL", self._env_float("MAX_ENTRY_DRIFT_PIPS_XAU", 50)),
                "max_spread_pips": self._env_float("MAX_SPREAD_PIPS_METAL", 35),
                "min_setup_score": self._env_float("MIN_SETUP_SCORE_METAL", self.min_professional_score),
                "min_conviction": self._env_float("MIN_CONVICTION_METAL", self.min_professional_conviction),
                "min_session_score": self._env_float("MIN_SESSION_SCORE_METAL", self.min_session_score_for_trade),
                "min_grade": os.getenv("MIN_EXECUTION_GRADE_METAL", self.min_execution_grade).strip().upper() or self.min_execution_grade,
                "block_scalps": self._env_bool("BLOCK_METAL_SCALPS", False),
            },
            "STOCK": {
                "asset_class": "STOCK",
                "min_profit_pips": self._env_float("MIN_PROFIT_PIPS_STOCK", 20),
                "max_entry_drift_pips": self._env_float("MAX_ENTRY_DRIFT_PIPS_STOCK", 10),
                "max_spread_pips": self._env_float("MAX_SPREAD_PIPS_STOCK", 5),
                "min_setup_score": self._env_float("MIN_SETUP_SCORE_STOCK", 0.70),
                "min_conviction": self._env_float("MIN_CONVICTION_STOCK", 0.45),
                "min_session_score": self._env_float("MIN_SESSION_SCORE_STOCK", 0.60),
                "min_grade": os.getenv("MIN_EXECUTION_GRADE_STOCK", "A").strip().upper() or "A",
                "block_scalps": self._env_bool("BLOCK_STOCK_SCALPS", True),
            },
            "INDEX": {
                "asset_class": "INDEX",
                "min_profit_pips": self._env_float("MIN_PROFIT_PIPS_INDEX", 25),
                "max_entry_drift_pips": self._env_float("MAX_ENTRY_DRIFT_PIPS_INDEX", 15),
                "max_spread_pips": self._env_float("MAX_SPREAD_PIPS_INDEX", 8),
                "min_setup_score": self._env_float("MIN_SETUP_SCORE_INDEX", 0.68),
                "min_conviction": self._env_float("MIN_CONVICTION_INDEX", 0.42),
                "min_session_score": self._env_float("MIN_SESSION_SCORE_INDEX", 0.55),
                "min_grade": os.getenv("MIN_EXECUTION_GRADE_INDEX", "B").strip().upper() or "B",
                "block_scalps": self._env_bool("BLOCK_INDEX_SCALPS", False),
            },
            "CRYPTO": {
                "asset_class": "CRYPTO",
                "min_profit_pips": self._env_float("MIN_PROFIT_PIPS_CRYPTO", 50),
                "max_entry_drift_pips": self._env_float("MAX_ENTRY_DRIFT_PIPS_CRYPTO", 25),
                "max_spread_pips": self._env_float("MAX_SPREAD_PIPS_CRYPTO", 10),
                "min_setup_score": self._env_float("MIN_SETUP_SCORE_CRYPTO", 0.72),
                "min_conviction": self._env_float("MIN_CONVICTION_CRYPTO", 0.48),
                "min_session_score": self._env_float("MIN_SESSION_SCORE_CRYPTO", 0.30),
                "min_grade": os.getenv("MIN_EXECUTION_GRADE_CRYPTO", "A").strip().upper() or "A",
                "block_scalps": self._env_bool("BLOCK_CRYPTO_SCALPS", True),
            },
        }
        profile = profiles.get(asset_class, profiles["FOREX"]).copy()
        if not getattr(self, "instrument_profiles_enabled", True):
            profile["asset_class"] = "DISABLED"
        return profile

    def _get_symbol_min_profit_pips(self, symbol: str, signal: dict | None = None) -> float:
        override = os.getenv(self._env_key_for_symbol("MIN_PROFIT_PIPS", symbol))
        if override is not None:
            try:
                return float(override)
            except Exception:
                pass

        if getattr(self, "instrument_profiles_enabled", True):
            profile = self._instrument_profile(symbol)
            if profile.get("asset_class") != "DISABLED":
                return float(profile.get("min_profit_pips", self.min_profit_pips))

        clean_symbol = str(symbol or "").upper()
        if "XAU" in clean_symbol or "GOLD" in clean_symbol:
            return float(os.getenv("MIN_PROFIT_PIPS_XAU", 30))
        if clean_symbol.startswith("NZDUSD") or clean_symbol.startswith("AUDUSD") or clean_symbol.startswith("USDCAD"):
            return float(os.getenv("MIN_PROFIT_PIPS_FX", 2))
        if clean_symbol.endswith("JPY"):
            return float(os.getenv("MIN_PROFIT_PIPS_JPY", 1))
        if signal and self._is_scalp_signal(signal):
            return float(os.getenv("MIN_PROFIT_PIPS_SCALP", 2))
        return self.min_profit_pips

    def _get_symbol_max_entry_drift_pips(self, symbol: str) -> float:
        override = os.getenv(self._env_key_for_symbol("MAX_ENTRY_DRIFT_PIPS", symbol))
        if override is not None:
            try:
                return float(override)
            except Exception:
                pass

        if getattr(self, "instrument_profiles_enabled", True):
            profile = self._instrument_profile(symbol)
            if profile.get("asset_class") != "DISABLED":
                return float(profile.get("max_entry_drift_pips", self.max_entry_drift_pips))

        clean_symbol = str(symbol or "").upper()
        if "XAU" in clean_symbol or "GOLD" in clean_symbol:
            return float(os.getenv("MAX_ENTRY_DRIFT_PIPS_XAU", 250))
        return self.max_entry_drift_pips

    def _is_scalp_signal(self, signal: dict) -> bool:
        style = str(signal.get("trade_style", "")).lower()
        scalp = signal.get("scalp_potential") or {}
        label = str(scalp.get("label", "")).lower()
        return "scalp" in style or "scalp" in label

    def _signal_horizon_name(self, signal: dict | None) -> str:
        if not signal:
            return "INTRADAY"
        if self._is_scalp_signal(signal):
            return "SCALP"
        horizon = signal.get("trade_horizon")
        if isinstance(horizon, dict):
            value = horizon.get("type") or horizon.get("name") or horizon.get("label")
        else:
            value = horizon
        return self._trade_horizon_profile_name(value or signal.get("trade_style"))

    def _horizon_execution_profile(self, signal: dict | None) -> dict:
        horizon = self._signal_horizon_name(signal)
        profiles = {
            "SCALP": {
                "name": "SCALP",
                "conviction_threshold": self._env_float("SCALP_EXECUTION_CONVICTION_THRESHOLD", 0.28),
                "setup_score_threshold": self._env_float("SCALP_EXECUTION_SETUP_SCORE_THRESHOLD", 0.52),
                "archetype_score_threshold": self._env_float("SCALP_EXECUTION_ARCHETYPE_SCORE_THRESHOLD", 0.55),
                "min_professional_score": self._env_float("SCALP_MIN_PROFESSIONAL_SETUP_SCORE", 0.58),
                "min_professional_conviction": self._env_float("SCALP_MIN_PROFESSIONAL_CONVICTION", 0.28),
                "min_session_score": self._env_float("SCALP_MIN_SESSION_SCORE", self.min_session_score_for_scalp),
                "min_expected_r": self._env_float("MIN_EXPECTED_R_SCALP", self.min_expected_r_scalp),
                "target_r": self._env_float("TAKE_PROFIT_R_MULTIPLIER_SCALP", self.take_profit_r_multiplier_scalp),
                "require_hard_structure": self._env_bool("SCALP_REQUIRE_HARD_STRUCTURE", True),
                "require_htf": self._env_bool("SCALP_REQUIRE_HTF", False),
                "allow_c_grade": self._env_bool("SCALP_ALLOW_C_GRADE", False),
            },
            "INTRADAY": {
                "name": "INTRADAY",
                "conviction_threshold": self._env_float("INTRADAY_EXECUTION_CONVICTION_THRESHOLD", self.execution_conviction_threshold),
                "setup_score_threshold": self._env_float("INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD", self.execution_setup_score_threshold),
                "archetype_score_threshold": self._env_float("INTRADAY_EXECUTION_ARCHETYPE_SCORE_THRESHOLD", self.execution_archetype_score_threshold),
                "min_professional_score": self._env_float("INTRADAY_MIN_PROFESSIONAL_SETUP_SCORE", self.min_professional_score),
                "min_professional_conviction": self._env_float("INTRADAY_MIN_PROFESSIONAL_CONVICTION", self.min_professional_conviction),
                "min_session_score": self._env_float("INTRADAY_MIN_SESSION_SCORE", self.min_session_score_for_trade),
                "min_expected_r": self._env_float("MIN_EXPECTED_R_INTRADAY", self.min_expected_r),
                "target_r": self._env_float("TAKE_PROFIT_R_MULTIPLIER_INTRADAY", self.take_profit_r_multiplier),
                "require_hard_structure": self._env_bool("INTRADAY_REQUIRE_HARD_STRUCTURE", False),
                "require_htf": self._env_bool("INTRADAY_REQUIRE_HTF", False),
                "allow_c_grade": self._env_bool("INTRADAY_ALLOW_C_GRADE", False),
            },
            "SWING": {
                "name": "SWING",
                "conviction_threshold": self._env_float("SWING_EXECUTION_CONVICTION_THRESHOLD", 0.42),
                "setup_score_threshold": self._env_float("SWING_EXECUTION_SETUP_SCORE_THRESHOLD", 0.68),
                "archetype_score_threshold": self._env_float("SWING_EXECUTION_ARCHETYPE_SCORE_THRESHOLD", 0.66),
                "min_professional_score": self._env_float("SWING_MIN_PROFESSIONAL_SETUP_SCORE", 0.68),
                "min_professional_conviction": self._env_float("SWING_MIN_PROFESSIONAL_CONVICTION", 0.40),
                "min_session_score": self._env_float("SWING_MIN_SESSION_SCORE", 0.30),
                "min_expected_r": self._env_float("MIN_EXPECTED_R_SWING", 1.50),
                "target_r": self._env_float("TAKE_PROFIT_R_MULTIPLIER_SWING", 2.50),
                "require_hard_structure": self._env_bool("SWING_REQUIRE_HARD_STRUCTURE", True),
                "require_htf": self._env_bool("SWING_REQUIRE_HTF", True),
                "allow_c_grade": self._env_bool("SWING_ALLOW_C_GRADE", False),
            },
        }
        return profiles.get(horizon, profiles["INTRADAY"])

    def _get_required_r(self, signal: dict) -> float:
        return float(self._horizon_execution_profile(signal).get("min_expected_r", self.min_expected_r))

    def _get_target_r(self, signal: dict) -> float:
        return float(self._horizon_execution_profile(signal).get("target_r", self.take_profit_r_multiplier))

    def _normalize_signal_levels_to_rr(self, signal: dict) -> tuple[bool, str]:
        """Keep strategy SL, then derive TP from the configured target R multiple."""
        action = str(signal.get("action", "")).upper()
        entry = signal.get("entry")
        sl = signal.get("sl")
        if action not in ["BUY", "SELL"] or entry is None or sl is None:
            return False, "Cannot calculate TP/SL; missing action, entry, or SL"

        entry = float(entry)
        sl = float(sl)
        risk_distance = abs(entry - sl)
        if risk_distance <= 0:
            return False, "Cannot calculate TP; invalid SL distance"
        if action == "BUY" and sl >= entry:
            return False, "Cannot calculate BUY TP; SL must be below entry"
        if action == "SELL" and sl <= entry:
            return False, "Cannot calculate SELL TP; SL must be above entry"

        target_r = self._get_target_r(signal)
        tp = entry + (risk_distance * target_r) if action == "BUY" else entry - (risk_distance * target_r)
        original_tp = signal.get("tp")
        signal["entry"] = entry
        signal["sl"] = sl
        signal["tp"] = tp
        signal["target_r"] = target_r
        signal["rr_normalized"] = True
        signal["original_tp"] = original_tp
        return True, f"TP/SL normalized to {target_r:.2f}R"

    def _get_spread_safety(self, signal: dict) -> tuple[bool, str]:
        spread = signal.get("spread_safety")
        if not spread and signal.get("setup_score"):
            spread = signal["setup_score"].get("spread")
        if not spread:
            return True, "Spread data unavailable"
        symbol = signal.get("symbol")
        if getattr(self, "instrument_profiles_enabled", True):
            profile = self._instrument_profile(symbol)
            max_spread = profile.get("max_spread_pips")
            spread_pips = spread.get("spread_pips")
            if max_spread is not None and spread_pips is not None:
                try:
                    spread_value = float(spread_pips)
                    max_spread_value = float(max_spread)
                    if spread_value > max_spread_value:
                        asset_class = profile.get("asset_class", "instrument")
                        return False, f"{asset_class} spread {spread_value:.2f} pips > max {max_spread_value:.2f}"
                    return True, f"{profile.get('asset_class', 'Instrument')} spread {spread_value:.2f} pips <= max {max_spread_value:.2f}"
                except Exception:
                    pass
        if spread.get("safe") is False:
            return False, spread.get("description", "Spread unsafe")
        return True, spread.get("description", "Spread safe")

    def _is_price_near_entry(self, signal: dict) -> tuple[bool, str]:
        entry = signal.get("entry")
        sl = signal.get("sl")
        current = signal.get("current_price")
        symbol = signal.get("symbol")
        if current is None and symbol:
            tick = self.mt5.get_symbol_tick(symbol)
            if tick:
                current = getattr(tick, "ask", None) if signal.get("action") == "BUY" else getattr(tick, "bid", None)

        if entry is None or sl is None or current is None:
            return True, "No current price drift check"

        risk_distance = abs(entry - sl)
        if risk_distance <= 0:
            return False, "Invalid entry drift risk distance"

        drift = abs(float(current) - float(entry))
        pip_size = self._get_pip_size(symbol) if symbol else None
        pip_drift_limit = (pip_size or 0) * self._get_symbol_max_entry_drift_pips(symbol)
        max_drift = max(risk_distance * self.max_entry_drift_pct, pip_drift_limit)
        if drift > max_drift:
            return False, f"Price drift too large ({drift:.5f} > {max_drift:.5f})"
        return True, f"Price drift acceptable ({drift:.5f} <= {max_drift:.5f})"

    def _execution_gate(self, signal: dict, ensemble_decision: dict, setup_value: float) -> tuple[bool, str]:
        ensemble_conviction = float((ensemble_decision or {}).get("conviction", 0.0) or 0.0)
        signal_conviction = float(signal.get("conviction", 0.0) or 0.0)
        scalp_conviction = float((signal.get("scalp_potential") or {}).get("conviction", 0.0) or 0.0)
        conviction_sources = {
            "ensemble": ensemble_conviction,
            "signal": signal_conviction,
            "scalp": scalp_conviction,
            "setup": float(setup_value or 0.0),
        }
        conviction_source, conviction = max(conviction_sources.items(), key=lambda item: item[1])
        scalp = self._is_scalp_signal(signal)
        scalp_score = float((signal.get("scalp_potential") or {}).get("score", 0.0))
        setup = signal.get("setup_score") or {}
        archetype = setup.get("archetype")
        horizon_profile = self._horizon_execution_profile(signal)
        horizon_name = horizon_profile.get("name", "INTRADAY")
        spread_ok, spread_reason = self._get_spread_safety(signal)
        drift_ok, drift_reason = self._is_price_near_entry(signal)

        if not spread_ok:
            return False, spread_reason
        if not drift_ok:
            return False, drift_reason
        _, passed = self._component_state(signal)
        if horizon_profile.get("require_hard_structure") and not self._signal_has_hard_structure(signal):
            return False, f"{horizon_name} execution requires hard structure"
        if horizon_profile.get("require_htf") and "htf_bias" not in passed:
            return False, f"{horizon_name} execution requires higher-timeframe alignment"

        conviction_threshold = float(horizon_profile.get("conviction_threshold", self.execution_conviction_threshold))
        setup_threshold = float(horizon_profile.get("setup_score_threshold", self.execution_setup_score_threshold))
        archetype_threshold = float(horizon_profile.get("archetype_score_threshold", self.execution_archetype_score_threshold))

        if conviction >= conviction_threshold:
            return True, f"{horizon_name} execution approved by {conviction_source} conviction {conviction:.3f}"
        if setup_value >= setup_threshold:
            return True, f"{horizon_name} execution approved by setup score {setup_value:.3f}"
        if archetype and archetype != "Context Watch" and setup_value >= archetype_threshold:
            return True, f"{horizon_name} execution approved by {archetype} archetype ({setup_value:.3f})"
        if horizon_name == "SCALP" and conviction >= max(0.25, conviction_threshold - 0.10):
            return True, f"SCALP execution approved by {conviction_source} conviction {conviction:.3f}"
        if horizon_name == "SCALP" and scalp_score >= 0.78:
            return True, f"SCALP execution approved by scalp score {scalp_score:.3f}"

        return False, (
            f"{horizon_name} execution gate failed: conviction={conviction:.3f}/{conviction_threshold:.3f}, "
            f"setup_score={setup_value:.3f}, scalp_score={scalp_score:.3f}, {spread_reason}, {drift_reason}"
        )

    def _component_state(self, signal: dict) -> tuple[dict, set]:
        setup = signal.get("setup_score") or {}
        components = setup.get("components") or []
        component_map = {c.get("key"): c for c in components if c.get("key")}
        passed = {key for key, component in component_map.items() if component.get("passed")}
        return component_map, passed

    def _passed_archetype_keys(self, signal: dict) -> set:
        setup = signal.get("setup_score") or {}
        return {
            item.get("key")
            for item in setup.get("archetypes", []) or []
            if item.get("key") and item.get("passed")
        }

    def _event_direction_aligned(self, signal: dict) -> bool:
        news_move = signal.get("news_move") or (signal.get("setup_score") or {}).get("news_move") or {}
        direction = news_move.get("direction")
        action = str(signal.get("action") or "").upper()
        aligned_action = "BUY" if direction == "Bullish" else "SELL" if direction == "Bearish" else None
        return bool(aligned_action and action == aligned_action)

    def _mtf_execution_allowed(self, signal: dict) -> tuple[bool, str, float]:
        if not getattr(self, "mtf_execution_gate_enabled", True):
            return True, "MTF gate disabled", 0.5
        mtf = signal.get("multi_timeframe") or (signal.get("setup_score") or {}).get("multi_timeframe") or {}
        if not mtf:
            return True, "MTF unavailable", 0.5
        try:
            score = float(mtf.get("score", 0.5))
        except Exception:
            score = 0.5
        symbol = signal.get("symbol")
        asset_class = self._instrument_class(symbol) if getattr(self, "instrument_profiles_enabled", True) else "DEFAULT"
        min_score = self.min_mtf_execution_score_metal if asset_class == "METAL" else self.min_mtf_execution_score
        conflicting = mtf.get("conflicting") or []
        aligned = mtf.get("aligned") or []
        if score < min_score:
            return False, f"MTF gate: score {score:.2f} below {asset_class} minimum {min_score:.2f} ({mtf.get('reason', 'multi-timeframe conflict')})", score
        if asset_class == "METAL" and conflicting and not aligned:
            return False, f"MTF gate: all available timeframes conflict for metal setup ({', '.join(conflicting)})", score
        return True, f"MTF gate passed: {score:.2f}", score

    def _qualify_signal_stage(self, signal: dict, ensemble_decision: dict, setup_value: float) -> tuple[bool, dict]:
        """Promote discovered signals to qualified only when their archetype has the right proof."""
        setup = signal.get("setup_score") or {}
        grade = str(setup.get("grade") or "D").upper()
        archetype = str(setup.get("archetype") or "Context Watch")
        archetype_key = str(setup.get("archetype_key") or "").lower()
        component_map, passed = self._component_state(signal)
        passed_archetypes = self._passed_archetype_keys(signal)
        if not archetype_key:
            archetype_key = next(iter(passed_archetypes), "")

        def has(*keys):
            return any(key in passed for key in keys)

        primary_structure = has("liquidity_sweep", "mss", "displacement", "ob_fvg", "false_move")
        context_structure = (
            has("htf_bias")
            and has("spread")
            and has("premium_discount", "displacement", "session")
            and setup_value >= self.execution_setup_score_threshold
        )

        checks = {
            "Sweep Reversal": has("liquidity_sweep") and has("displacement", "premium_discount", "false_move"),
            "Structure Continuation": has("mss") and has("htf_bias", "displacement"),
            "Order Block Mitigation": has("ob_fvg") and has("premium_discount", "htf_bias"),
            "FVG Momentum": has("displacement") and has("htf_bias", "premium_discount"),
            "Scalp Retest": has("spread") and primary_structure,
            "False Move Reversal": has("false_move") and has("liquidity_sweep", "displacement"),
            "Post-News Retest": (
                has("spread")
                and self._event_direction_aligned(signal)
                and has("mss", "displacement")
            ),
        }
        archetype_ok = checks.get(archetype, False)
        if archetype_key and archetype_key in passed_archetypes and archetype != "Post-News Retest":
            archetype_ok = archetype_ok or primary_structure

        ensemble_conviction = float((ensemble_decision or {}).get("conviction", 0.0) or 0.0)
        signal_conviction = float(signal.get("conviction", 0.0) or 0.0)
        effective_conviction = max(ensemble_conviction, signal_conviction, setup_value)
        qualified = bool(archetype_ok or context_structure)

        blockers = []
        if archetype == "Context Watch":
            blockers.append("context watch")
        if not primary_structure and not context_structure:
            blockers.append("no executable structure")
        if archetype == "Post-News Retest" and not self._event_direction_aligned(signal):
            blockers.append("event direction not aligned")
        if effective_conviction < self.min_professional_conviction:
            blockers.append("low conviction")
        if setup_value < self.execution_setup_score_threshold and grade not in {"A", "B"}:
            blockers.append("low setup score")

        if blockers:
            qualified = False

        return qualified, {
            "stage": "QUALIFIED" if qualified else "DISCOVERED",
            "grade": grade,
            "archetype": archetype,
            "setup_score": round(setup_value, 3),
            "effective_conviction": round(effective_conviction, 3),
            "passed_components": sorted(passed),
            "missing": blockers,
            "reason": (
                f"{archetype} qualified with {', '.join(sorted(passed))}"
                if qualified
                else f"{archetype} not qualified: {', '.join(blockers) or 'insufficient proof'}"
            ),
        }

    def _compute_trade_readiness(self, signal: dict, ensemble_decision: dict, setup_value: float) -> dict:
        component_map, passed = self._component_state(signal)
        session = signal.get("session_bias") or (signal.get("setup_score") or {}).get("session_bias") or {}
        session_score = float(session.get("score", 0.0) or 0.0)
        expected_r = self._calculate_expected_r(signal) or 0.0
        spread_ok, spread_reason = self._get_spread_safety(signal)
        drift_ok, drift_reason = self._is_price_near_entry(signal)
        mtf_ok, mtf_reason, mtf_score = self._mtf_execution_allowed(signal)

        primary_structure = bool({"liquidity_sweep", "mss", "displacement", "ob_fvg", "false_move"}.intersection(passed))
        context_structure = "htf_bias" in passed and bool({"premium_discount", "displacement", "session"}.intersection(passed))
        structure_score = 1.0 if primary_structure else 0.65 if context_structure else 0.0
        execution_score = (0.5 if spread_ok else 0.0) + (0.5 if drift_ok else 0.0)
        risk_score = min(1.0, max(0.0, expected_r / 2.0))
        final_score = (
            setup_value * 0.32
            + execution_score * 0.25
            + risk_score * 0.15
            + session_score * 0.10
            + structure_score * 0.08
            + mtf_score * 0.10
        )
        if not mtf_ok:
            final_score = min(final_score, self.min_trade_readiness_score - 0.01)

        return {
            "stage": "EXECUTABLE" if final_score >= self.min_trade_readiness_score else "QUALIFIED",
            "score": round(final_score, 3),
            "threshold": round(self.min_trade_readiness_score, 3),
            "setup_score": round(setup_value, 3),
            "execution_score": round(execution_score, 3),
            "risk_score": round(risk_score, 3),
            "session_score": round(session_score, 3),
            "structure_score": round(structure_score, 3),
            "mtf_score": round(mtf_score, 3),
            "mtf_ok": mtf_ok,
            "mtf_reason": mtf_reason,
            "expected_r": round(expected_r, 3),
            "spread_ok": spread_ok,
            "spread_reason": spread_reason,
            "drift_ok": drift_ok,
            "drift_reason": drift_reason,
        }

    def _professional_execution_gate(self, signal: dict, ensemble_decision: dict, setup_value: float) -> tuple[bool, str]:
        """Final discretionary-style filter: dashboard may watch C/D setups, execution only takes clean ones."""
        if not self.professional_gate_enabled:
            return True, "Professional gate disabled"

        setup = signal.get("setup_score") or {}
        grade = str(setup.get("grade") or "D").upper()
        archetype = str(setup.get("archetype") or "Context Watch")
        session = signal.get("session_bias") or setup.get("session_bias") or {}
        session_score = float(session.get("score", 0.0) or 0.0)
        scalp = self._is_scalp_signal(signal)
        scalp_score = float((signal.get("scalp_potential") or {}).get("score", 0.0))
        ensemble_conviction = float((ensemble_decision or {}).get("conviction", 0.0) or 0.0)
        signal_conviction = float(signal.get("conviction", 0.0) or 0.0)
        scalp_conviction = float((signal.get("scalp_potential") or {}).get("conviction", 0.0) or 0.0)
        conviction_sources = {
            "ensemble": ensemble_conviction,
            "signal": signal_conviction,
            "scalp": scalp_conviction,
            "setup": float(setup_value or 0.0),
        }
        conviction_source, conviction = max(conviction_sources.items(), key=lambda item: item[1])
        components = setup.get("components") or []
        passed = {c.get("key") for c in components if c.get("passed")}
        structural_keys = {"liquidity_sweep", "mss", "displacement", "ob_fvg", "false_move"}
        structural_pass = bool(structural_keys.intersection(passed))
        hard_structure_pass = self._signal_has_hard_structure(signal)
        archetype_threshold = float(getattr(self, "execution_archetype_score_threshold", 0.58))
        execution_conviction_threshold = float(getattr(self, "execution_conviction_threshold", 0.35))
        c_grade_structure_ok = (
            self.allow_c_scalps
            and grade == "C"
            and setup_value >= max(self.min_professional_score, archetype_threshold)
            and conviction >= max(self.min_professional_conviction, execution_conviction_threshold)
            and session_score >= self.min_session_score_for_trade
            and structural_pass
        )

        grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
        instrument_profile = self._instrument_profile(signal.get("symbol")) if getattr(self, "instrument_profiles_enabled", True) else {}
        asset_class = instrument_profile.get("asset_class", "DEFAULT")
        horizon_profile = self._horizon_execution_profile(signal)
        horizon_name = horizon_profile.get("name", "INTRADAY")
        instrument_min_grade = instrument_profile.get("min_grade", self.min_execution_grade)
        required_grade = instrument_min_grade if grade_rank.get(instrument_min_grade, 0) > grade_rank.get(self.min_execution_grade, 3) else self.min_execution_grade
        required_rank = grade_rank.get(required_grade, 3)
        grade_ok = grade_rank.get(grade, 1) >= required_rank
        c_scalp_ok = (
            self.allow_c_scalps
            and grade == "C"
            and scalp
            and scalp_score >= 0.75
            and session_score >= self.min_session_score_for_scalp
            and structural_pass
        )
        if asset_class in ["STOCK", "CRYPTO"]:
            c_scalp_ok = False
            c_grade_structure_ok = False
        if not horizon_profile.get("allow_c_grade", False):
            c_scalp_ok = False
            c_grade_structure_ok = False

        if self.block_context_watch_trades and archetype == "Context Watch":
            return False, "Professional gate: Context Watch is watch-only"
        if instrument_profile.get("block_scalps") and scalp:
            return False, f"Professional gate: {asset_class} scalp execution disabled"
        if not structural_pass:
            return False, "Professional gate: no liquidity sweep, MSS/BOS, displacement, OB/FVG, or false-move structure"
        if scalp and not hard_structure_pass:
            return False, "Professional gate: scalp needs MSS/BOS, displacement, OB/FVG, or confirmed false-move reclaim"
        if horizon_profile.get("require_hard_structure") and not hard_structure_pass:
            return False, f"Professional gate: {horizon_name} needs hard structure"
        if horizon_profile.get("require_htf") and "htf_bias" not in passed:
            return False, f"Professional gate: {horizon_name} needs higher-timeframe alignment"
        if not (grade_ok or c_scalp_ok or c_grade_structure_ok):
            c_details = ""
            if grade == "C":
                c_details = (
                    f" (C override needs allow={self.allow_c_scalps}, scalp={scalp}, "
                    f"scalp_score={scalp_score:.2f}, setup={setup_value:.2f}, "
                    f"conviction={conviction:.2f} {conviction_source}, session={session_score:.2f})"
                )
            return False, f"Professional gate: Grade {grade} below {asset_class} execution grade {required_grade}{c_details}"
        instrument_min_score = float(instrument_profile.get("min_setup_score", self.min_professional_score) or self.min_professional_score)
        instrument_min_conviction = float(instrument_profile.get("min_conviction", self.min_professional_conviction) or self.min_professional_conviction)
        instrument_min_session = float(instrument_profile.get("min_session_score", self.min_session_score_for_trade) or self.min_session_score_for_trade)
        horizon_min_score = float(horizon_profile.get("min_professional_score", self.min_professional_score) or self.min_professional_score)
        horizon_min_conviction = float(horizon_profile.get("min_professional_conviction", self.min_professional_conviction) or self.min_professional_conviction)
        horizon_min_session = float(horizon_profile.get("min_session_score", self.min_session_score_for_trade) or self.min_session_score_for_trade)
        required_score = max(instrument_min_score, horizon_min_score)
        required_conviction = max(instrument_min_conviction, horizon_min_conviction)
        required_session = max(instrument_min_session, horizon_min_session)
        if setup_value < required_score and not (c_scalp_ok or c_grade_structure_ok):
            return False, f"Professional gate: {asset_class}/{horizon_name} setup score {setup_value:.2f} < {required_score:.2f}"
        if conviction < required_conviction and grade != "A":
            return False, (
                f"Professional gate: {asset_class}/{horizon_name} conviction {conviction:.2f} "
                f"({conviction_source}) < {required_conviction:.2f}"
            )
        if session_score < required_session and grade != "A":
            return False, f"Professional gate: {asset_class}/{horizon_name} weak session score {session_score:.2f}"
        if setup_value < self.min_professional_score and not (c_scalp_ok or c_grade_structure_ok):
            return False, f"Professional gate: setup score {setup_value:.2f} < {self.min_professional_score:.2f}"
        if conviction < self.min_professional_conviction and grade != "A":
            return False, (
                f"Professional gate: conviction {conviction:.2f} "
                f"({conviction_source}) < {self.min_professional_conviction:.2f}"
            )
        if session_score < self.min_session_score_for_trade and grade != "A":
            return False, f"Professional gate: weak session score {session_score:.2f}"
        if scalp and session_score < self.min_session_score_for_scalp and grade != "A":
            return False, f"Professional gate: scalp blocked outside liquid session ({session_score:.2f})"

        return True, (
            f"Professional gate passed: {asset_class}/{horizon_name} Grade {grade}, {archetype}, "
            f"score={setup_value:.2f}, conviction={conviction:.2f} ({conviction_source}), "
            f"session={session_score:.2f}"
        )

    def _event_execution_gate(self, signal: dict) -> tuple[bool, str]:
        """Block trap-chasing and manage reduced-risk post-news entries."""
        false_move = signal.get("false_move") or (signal.get("setup_score") or {}).get("false_move") or {}
        news_move = signal.get("news_move") or (signal.get("setup_score") or {}).get("news_move") or {}

        if self.false_move_detection_enabled:
            fm_type = false_move.get("type")
            fm_safe = false_move.get("safe", True)
            fm_direction = false_move.get("direction")
            action = str(signal.get("action") or "").upper()
            aligned_action = "BUY" if fm_direction == "Bullish" else "SELL" if fm_direction == "Bearish" else None
            if fm_safe is False:
                is_tradable_reversal = fm_type in ["FAILED_BREAKOUT", "LIQUIDITY_SWEEP_REVERSAL"] and aligned_action and action == aligned_action
                if not is_tradable_reversal:
                    return False, f"False-move gate: unsafe {str(fm_type or 'move').replace('_', ' ').title()} context"
            if fm_type in ["FAILED_BREAKOUT", "LIQUIDITY_SWEEP_REVERSAL"] and aligned_action and action != aligned_action:
                return False, f"False-move gate: signal is chasing against {fm_direction} trap reversal"
            if fm_type in ["REAL_BREAKOUT", "BREAKOUT_UNCONFIRMED"] and not fm_safe:
                return False, f"False-move gate: {fm_type.replace('_', ' ').title()} lacks aligned follow-through"

        if self.news_mode_enabled:
            mode = news_move.get("mode", "NORMAL")
            plan = news_move.get("plan", "NORMAL")
            safe = news_move.get("safe", True)
            if mode == "ACTIVE" and self.news_block_unsafe:
                return False, f"News gate: {plan} - {news_move.get('description', 'unsafe event spike')}"
            if not safe and plan in ["WAIT_SPREAD", "WAIT_RETEST"] and self.news_block_unsafe:
                return False, f"News gate: {news_move.get('description', 'news spread unsafe')}"
            if mode == "FOLLOW_RETEST":
                if not self.news_allow_retest_follow:
                    return False, "News gate: post-news retest entries disabled"
                signal["risk_multiplier"] = min(float(signal.get("risk_multiplier") or 1.0), self.news_risk_multiplier)
                return True, f"News gate: post-news follow allowed at {self.news_risk_multiplier:.0%} risk"

        return True, "False-move/news gate passed"

    def _compute_scalp_potential(self, signal: dict):
        """Compute scalp potential score and classification."""
        from technical_analysis import calculate_scalp_potential

        if not signal or "entry" not in signal:
            return {
                "score": 0.0,
                "label": "Unknown",
                "risk_pips": 0,
                "reward_pips": 0,
                "r_ratio": 0,
            }

        scalp = calculate_scalp_potential(signal)
        return scalp

    def _classify_trade_style(self, signal: dict, scalp_data: dict) -> str:
        """Classify the trade name for logging and analysis."""
        action = signal.get("action", "UNKNOWN").upper()
        label = scalp_data.get("label", "Opportunity") if scalp_data else "Opportunity"

        if signal.get("order_block"):
            style = "Order Block"
        elif signal.get("divergence") and signal["divergence"].get("type") in ["Bullish", "Bearish"]:
            style = "Divergence"
        elif signal.get("structure_break"):
            style = "Structure"
        elif signal.get("liquidity_zone"):
            style = "Liquidity"
        elif "Scalp" in label:
            style = "Scalp"
        elif "Momentum" in label:
            style = "Momentum"
        elif "Trend" in label:
            style = "Trend"
        else:
            style = "Setup"

        if action == "BUY":
            return f"Long {style}"
        if action == "SELL":
            return f"Short {style}"
        return f"{action.title()} {style}"

    def _is_signal_big_enough(self, signal: dict):
        """Check if a signal has enough pip distance to justify a trade."""
        try:
            symbol = signal.get("symbol")
            entry = signal.get("entry")
            sl = signal.get("sl")
            tp = signal.get("tp")
            action = str(signal.get("action", "")).upper()
            pip_size = self._get_pip_size(symbol) if symbol else None

            if not symbol or entry is None or sl is None or tp is None or pip_size is None:
                return False, "Signal missing symbol, entry, SL, TP, or pip size"

            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk <= 0 or reward <= 0:
                return False, "Invalid risk/reward distance"

            reward_pips = reward / pip_size
            risk_pips = risk / pip_size
            expected_r = reward / risk

            min_profit_pips = self._get_symbol_min_profit_pips(symbol, signal)
            min_expected_r = self._get_required_r(signal)
            spread_ok, spread_reason = self._get_spread_safety(signal)

            if not spread_ok:
                return False, spread_reason
            if reward_pips < min_profit_pips:
                return False, f"Reward too small ({reward_pips:.1f}p < {min_profit_pips:.1f}p)"
            if expected_r < min_expected_r:
                return False, f"R:R too low ({expected_r:.2f}R < {min_expected_r:.2f}R)"
            if action == "BUY" and not (sl < entry < tp):
                return False, "BUY levels invalid; expected SL < entry < TP"
            if action == "SELL" and not (tp < entry < sl):
                return False, "SELL levels invalid; expected TP < entry < SL"

            return True, f"Signal accepted ({reward_pips:.1f}p reward, {risk_pips:.1f}p risk, {expected_r:.2f}R, {spread_reason})"
        except Exception as e:
            logger.error(f"Signal size filter error: {e}")
            return False, f"Signal size filter error: {e}"

    def _record_closed_trade(self, symbol: str, profit: float, risk: float, reason: str = "Closed"):
        r = profit / risk if risk and risk != 0 else None
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "TRADE_CLOSED",
            "symbol": symbol,
            "profit": profit,
            "risk": risk,
            "r": r,
            "reason": reason,
        }
        self.logger._save_log(entry)
        self.trade_journal.append(entry)
        try:
            if float(profit or 0) < 0:
                self.daily_loss_count += 1
                self.consecutive_loss_count += 1
                cooldown_minutes = self.loss_cooldown_minutes
                loss_reason = f"Loss cooldown after {self.consecutive_loss_count} consecutive loss"
                if (
                    self.catastrophic_loss_stop_enabled
                    and r is not None
                    and r <= -abs(self.catastrophic_loss_r)
                ):
                    cooldown_minutes = max(cooldown_minutes, self.catastrophic_loss_cooldown_minutes)
                    loss_reason = f"Catastrophic loss stop after {r:.2f}R loss"
                    self.is_running = False
                    self.logger._save_log({
                        "timestamp": datetime.now().isoformat(),
                        "event": "CATASTROPHIC_LOSS_STOP",
                        "symbol": symbol,
                        "r": r,
                        "profit": profit,
                        "cooldown_minutes": cooldown_minutes,
                    })
                if self.daily_loss_brake_enabled and cooldown_minutes > 0:
                    self.cooldown_until = datetime.now() + timedelta(minutes=cooldown_minutes)
                    self.last_loss_brake_reason = loss_reason
            elif float(profit or 0) > 0:
                self.consecutive_loss_count = 0
        except Exception as e:
            logger.error(f"Error updating loss brake counters: {e}")

    def _get_realized_profit_today(self) -> float:
        """Return realized P/L from today's closed trade logs."""
        try:
            logs = self.logger.get_logs()
            return sum(
                float(log.get("profit") or 0)
                for log in logs
                if log.get("event") == "TRADE_CLOSED"
            )
        except Exception as e:
            logger.error(f"Error calculating realized profit: {e}")
            return 0.0

    def _get_loss_brake_state(self, equity: float | None = None) -> dict:
        """Return daily loss brake state from today's closed trade logs."""
        state = {
            "enabled": self.daily_loss_brake_enabled,
            "blocked": False,
            "reason": None,
            "daily_realized_profit": 0.0,
            "daily_loss_count": self.daily_loss_count,
            "consecutive_loss_count": self.consecutive_loss_count,
            "daily_loss_cap_percent": self.daily_loss_cap_pct,
            "max_daily_losses": self.max_daily_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
        }
        if not self.daily_loss_brake_enabled:
            return state

        today = datetime.now().date()
        closed_today = []
        try:
            for log in self.logger.get_logs():
                if log.get("event") != "TRADE_CLOSED":
                    continue
                timestamp = str(log.get("timestamp") or "")
                try:
                    log_date = datetime.fromisoformat(timestamp).date()
                except Exception:
                    log_date = today
                if log_date == today:
                    closed_today.append(log)
        except Exception as e:
            logger.error(f"Error reading logs for loss brake: {e}")

        daily_profit = sum(float(log.get("profit") or 0) for log in closed_today)
        daily_losses = sum(1 for log in closed_today if float(log.get("profit") or 0) < 0)
        consecutive_losses = 0
        for log in reversed(closed_today):
            profit = float(log.get("profit") or 0)
            if profit < 0:
                consecutive_losses += 1
            elif profit > 0:
                break

        self.daily_loss_count = max(self.daily_loss_count, daily_losses)
        self.consecutive_loss_count = max(self.consecutive_loss_count, consecutive_losses)
        state.update({
            "daily_realized_profit": round(daily_profit, 2),
            "daily_loss_count": self.daily_loss_count,
            "consecutive_loss_count": self.consecutive_loss_count,
        })

        base_equity = self.daily_start_equity or self.start_equity or equity
        if base_equity and daily_profit <= -(float(base_equity) * self.daily_loss_cap_pct):
            state["blocked"] = True
            state["reason"] = f"Daily loss cap hit ({daily_profit:.2f})"
        elif self.max_daily_losses > 0 and self.daily_loss_count >= self.max_daily_losses:
            state["blocked"] = True
            state["reason"] = f"Max daily losses hit ({self.daily_loss_count})"
        elif self.max_consecutive_losses > 0 and self.consecutive_loss_count >= self.max_consecutive_losses:
            state["blocked"] = True
            state["reason"] = f"Max consecutive losses hit ({self.consecutive_loss_count})"
        elif self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds() / 60
            state["blocked"] = True
            state["reason"] = f"Loss cooldown active ({remaining:.0f}m remaining)"

        if state["blocked"]:
            self.last_loss_brake_reason = state["reason"]
        return state

    def _compute_bot_score(self, market_open=None):
        """Return a practical readiness score for dashboard monitoring."""
        components = []

        def add(name, value, weight, note):
            value = max(0.0, min(float(value), float(weight)))
            components.append({
                "name": name,
                "value": round(value, 2),
                "weight": weight,
                "pct": round((value / weight) * 100, 1) if weight else 0,
                "note": note,
            })

        connected = bool(getattr(self.mt5, "is_connected", False))
        add("Connection", 12 if connected else 0, 12, "MT5 connected" if connected else "MT5 disconnected")
        add("Runtime", 8 if self.is_running else 0, 8, "Engine running" if self.is_running else "Engine stopped")

        if market_open is None:
            market_open = self._is_market_open()
        add("Market", 5 if market_open else 3, 5, "Market open" if market_open else "Market closed; signal snapshots only")

        if self.last_scan_at:
            age = max(0.0, (datetime.now() - self.last_scan_at).total_seconds())
            expected = max(float(self.scan_interval_seconds or 3) * 3, 15.0)
            freshness = max(0.0, 1.0 - min(age / (expected * 2), 1.0))
            scan_value = 15 * freshness
            scan_note = f"Last scan {int(age)}s ago"
        else:
            scan_value = 3 if not self.is_running else 5
            scan_note = "No scan recorded yet"
        add("Scan Freshness", scan_value, 15, scan_note)

        risk_value = 0
        risk_notes = []
        if self.position_sizing_mode == "fixed":
            risk_value += 7
            risk_notes.append(f"fixed {self.volume:.2f} lots")
        elif 0 < self.risk_pct <= 0.01:
            risk_value += 7
            risk_notes.append("risk <= 1%")
        elif self.risk_pct <= 0.02:
            risk_value += 5
            risk_notes.append("risk <= 2%")
        elif self.risk_pct <= 0.03:
            risk_value += 3
            risk_notes.append("risk elevated")
        else:
            risk_notes.append("risk too high")
        if 0 < self.max_exposure_pct <= 0.05:
            risk_value += 5
            risk_notes.append("exposure guarded")
        elif self.max_exposure_pct <= 0.10:
            risk_value += 3
            risk_notes.append("exposure moderate")
        if self.signal_lockout_enabled and self.max_trades_per_symbol <= 1:
            risk_value += 4
            risk_notes.append("symbol lockout strict")
        elif self.signal_lockout_enabled:
            risk_value += 2
            risk_notes.append("symbol lockout enabled")
        if not self.killed.get("all"):
            risk_value += 2
            risk_notes.append("kill switch ready")
        if self.daily_profit_cap > 0:
            risk_value += 2
            risk_notes.append("daily cap enabled")
        add("Risk Guardrails", risk_value, 20, ", ".join(risk_notes) or "risk config unavailable")

        management_value = 3
        management_notes = ["trailing SL"]
        if self.trailing_tp_enabled:
            management_value += 4
            management_notes.append("trailing TP")
        if self.partial_tp_enabled:
            management_value += 4
            management_notes.append("partial TP")
        if self.reverse_profit_exit_enabled:
            management_value += 4
            management_notes.append("reverse exit")
        if self.professional_gate_enabled:
            management_value += 5
            management_notes.append("professional gate")
        add("Trade Management", management_value, 20, ", ".join(management_notes))

        candidates = list(self.future_trades[-30:]) + list(self.recent_signals[-20:])
        best_score = 0.0
        best_grade = "-"
        for signal in candidates:
            setup = signal.get("setup_score") or {}
            score = float(setup.get("score") or signal.get("confluence_score") or signal.get("conviction") or 0.0)
            if score > best_score:
                best_score = score
                best_grade = str(setup.get("grade") or "-")
        if candidates:
            signal_value = 20 * max(0.0, min(best_score, 1.0))
            signal_note = f"Best candidate {best_score:.2f} grade {best_grade}"
        else:
            signal_value = 6
            signal_note = "No current candidates"
        add("Signal Quality", signal_value, 20, signal_note)

        total = round(sum(c["value"] for c in components), 1)
        if total >= 85:
            grade, label = "A", "Operationally strong"
        elif total >= 72:
            grade, label = "B", "Tradeable with discipline"
        elif total >= 58:
            grade, label = "C", "Watch carefully"
        elif total >= 40:
            grade, label = "D", "Weak readiness"
        else:
            grade, label = "F", "Do not rely on automation"

        return {
            "score": total,
            "grade": grade,
            "label": label,
            "components": components,
            "summary": f"{grade} ({total:.1f}/100) - {label}",
        }

    def get_enriched_positions(self):
        """Return MT5 positions joined with active trade state for the dashboard."""
        positions = self.mt5.get_positions() or []
        enriched = []
        for pos in positions:
            item = dict(pos)
            symbol = item.get("symbol")
            trade = self.active_trades.get(symbol, {}) if symbol else {}
            profile = self._management_profile(symbol, trade=trade)
            item["trade_state"] = {
                "status": "ACTIVE" if symbol in self.active_trades else "EXTERNAL",
                "trade_style": trade.get("trade_style"),
                "trade_horizon": trade.get("trade_horizon"),
                "symbol_profile": profile.get("symbol_profile"),
                "horizon_profile": profile.get("horizon_profile"),
                "management_profile": profile.get("name"),
                "partial_tp_taken": bool(trade.get("partial_tp_taken")),
                "partial_tp_lock_sl": trade.get("partial_tp_lock_sl"),
                "partial_runner_tp": trade.get("partial_runner_tp"),
                "partial_runner_tp_at": trade.get("partial_runner_tp_at"),
                "opposing_signal_exit": trade.get("opposing_signal_exit"),
                "reverse_exit_done": bool(trade.get("reverse_exit_done")),
                "news_ladder_count": len(trade.get("news_ladder_addons") or []),
                "news_move": trade.get("news_move"),
                "false_move": trade.get("false_move"),
                "max_favorable_r": trade.get("max_favorable_r"),
                "max_favorable_profit": trade.get("max_favorable_profit"),
                "opened_at": trade.get("opened_at"),
                "risk": trade.get("risk"),
            }
            try:
                r_now = self._position_r_multiple(item)
                item["r_multiple"] = round(r_now, 3) if r_now is not None else None
            except Exception:
                item["r_multiple"] = None
            enriched.append(item)
        return enriched

    def _base_symbol_profile(self) -> dict:
        """Return the global trade-management settings as the default symbol profile."""
        return {
            "name": "DEFAULT",
            "trailing_stop_trigger_pct": getattr(self, "trailing_stop_trigger_pct", 0.55),
            "trailing_stop_lock_pips": getattr(self, "trailing_stop_lock_pips", 10.0),
            "trailing_stop_step_pct": getattr(self, "trailing_stop_step_pct", 0.50),
            "trailing_stop_min_step_pips": getattr(self, "trailing_stop_min_step_pips", 5.0),
            "partial_tp_trigger_r": getattr(self, "partial_tp_trigger_r", 0.75),
            "partial_tp_lock_pips": getattr(self, "partial_tp_lock_pips", 10.0),
            "reverse_profit_min_r": getattr(self, "reverse_profit_min_r", 1.20),
            "reverse_profit_giveback_pct": getattr(self, "reverse_profit_giveback_pct", 0.45),
            "reverse_profit_close_pct": getattr(self, "reverse_profit_close_pct", 0.5),
            "reverse_after_partial_lock_r": getattr(self, "reverse_after_partial_lock_r", 0.20),
        }

    def _symbol_profile_overrides(self) -> dict:
        """Small first-pass overrides for instruments that need wider management."""
        return {
            "JPY": {
                "name": "JPY",
                "trailing_stop_trigger_pct": 0.60,
                "trailing_stop_lock_pips": 10.0,
                "trailing_stop_step_pct": 0.55,
                "trailing_stop_min_step_pips": 5.0,
                "partial_tp_trigger_r": 0.85,
                "reverse_profit_min_r": 1.35,
                "reverse_profit_giveback_pct": 0.48,
            },
            "XAU": {
                "name": "XAU",
                "trailing_stop_trigger_pct": 0.65,
                "trailing_stop_lock_pips": 10.0,
                "trailing_stop_step_pct": 0.65,
                "trailing_stop_min_step_pips": 10.0,
                "partial_tp_trigger_r": 0.90,
                "reverse_profit_min_r": 1.50,
                "reverse_profit_giveback_pct": 0.50,
                "reverse_after_partial_lock_r": 0.25,
            },
        }

    def _symbol_profile_name(self, symbol: str | None) -> str:
        if not getattr(self, "symbol_profiles_enabled", True):
            return "DEFAULT"
        normalized = (symbol or "").upper()
        if "XAU" in normalized or "GOLD" in normalized:
            return "XAU"
        if "JPY" in normalized:
            return "JPY"
        return "DEFAULT"

    def _symbol_profile(self, symbol: str | None) -> dict:
        base = self._base_symbol_profile()
        profile_name = self._symbol_profile_name(symbol)
        if profile_name == "DEFAULT":
            return base
        profile = dict(base)
        profile.update(self._symbol_profile_overrides().get(profile_name, {}))
        return profile

    def _instrument_management_overrides(self, symbol: str | None) -> dict:
        if not getattr(self, "instrument_profiles_enabled", True):
            return {"name": "DISABLED"}
        asset_class = self._instrument_class(symbol)
        profiles = {
            "FOREX": {
                "name": "FOREX",
                "partial_tp_trigger_r": self._env_float("PARTIAL_TP_TRIGGER_R_FOREX", 0.70),
                "trailing_stop_trigger_pct": self._env_float("TRAILING_STOP_TRIGGER_PCT_FOREX", 0.55),
                "trailing_stop_step_pct": self._env_float("TRAILING_STOP_STEP_PCT_FOREX", 0.50),
                "max_adverse_r": self._env_float("MAX_ADVERSE_R_FOREX", 0.45),
            },
            "METAL": {
                "name": "METAL",
                "partial_tp_trigger_r": self._env_float("PARTIAL_TP_TRIGGER_R_METAL", 0.80),
                "trailing_stop_trigger_pct": self._env_float("TRAILING_STOP_TRIGGER_PCT_METAL", 0.65),
                "trailing_stop_step_pct": self._env_float("TRAILING_STOP_STEP_PCT_METAL", 0.65),
                "trailing_stop_min_step_pips": self._env_float("TRAILING_STOP_MIN_STEP_PIPS_METAL", 10.0),
                "reverse_profit_min_r": self._env_float("REVERSE_PROFIT_MIN_R_METAL", 1.50),
                "reverse_profit_giveback_pct": self._env_float("REVERSE_PROFIT_GIVEBACK_PCT_METAL", 0.50),
                "max_adverse_r": self._env_float("MAX_ADVERSE_R_METAL", 0.60),
            },
            "STOCK": {
                "name": "STOCK",
                "partial_tp_trigger_r": self._env_float("PARTIAL_TP_TRIGGER_R_STOCK", 1.00),
                "trailing_stop_trigger_pct": self._env_float("TRAILING_STOP_TRIGGER_PCT_STOCK", 0.75),
                "trailing_stop_step_pct": self._env_float("TRAILING_STOP_STEP_PCT_STOCK", 0.70),
                "reverse_profit_min_r": self._env_float("REVERSE_PROFIT_MIN_R_STOCK", 1.80),
                "reverse_profit_giveback_pct": self._env_float("REVERSE_PROFIT_GIVEBACK_PCT_STOCK", 0.55),
                "max_adverse_r": self._env_float("MAX_ADVERSE_R_STOCK", 0.80),
                "allow_news_ladder": False,
            },
            "INDEX": {
                "name": "INDEX",
                "partial_tp_trigger_r": self._env_float("PARTIAL_TP_TRIGGER_R_INDEX", 0.90),
                "trailing_stop_trigger_pct": self._env_float("TRAILING_STOP_TRIGGER_PCT_INDEX", 0.70),
                "trailing_stop_step_pct": self._env_float("TRAILING_STOP_STEP_PCT_INDEX", 0.65),
                "max_adverse_r": self._env_float("MAX_ADVERSE_R_INDEX", 0.70),
            },
            "CRYPTO": {
                "name": "CRYPTO",
                "partial_tp_trigger_r": self._env_float("PARTIAL_TP_TRIGGER_R_CRYPTO", 1.10),
                "trailing_stop_trigger_pct": self._env_float("TRAILING_STOP_TRIGGER_PCT_CRYPTO", 0.80),
                "trailing_stop_step_pct": self._env_float("TRAILING_STOP_STEP_PCT_CRYPTO", 0.75),
                "max_adverse_r": self._env_float("MAX_ADVERSE_R_CRYPTO", 0.90),
                "allow_news_ladder": False,
            },
        }
        return profiles.get(asset_class, {"name": asset_class})

    def _configured_symbol_profiles(self) -> dict:
        """Expose profile assignments for configured symbols without adding UI logic here."""
        return {
            symbol: {
                **self._symbol_profile(symbol),
                "instrument_class": self._instrument_class(symbol),
                "instrument_execution": self._instrument_profile(symbol),
            }
            for symbol in self.symbols
        }

    def _horizon_value(self, source: dict | None) -> str:
        if not source:
            return ""
        horizon = source.get("trade_horizon")
        if isinstance(horizon, dict):
            value = horizon.get("type") or horizon.get("name") or horizon.get("label")
            if value:
                return str(value)
        elif horizon:
            return str(horizon)
        return str(source.get("trade_style") or "")

    def _trade_horizon_profile_name(self, horizon: str | None) -> str:
        normalized = str(horizon or "").upper()
        if "SCALP" in normalized:
            return "SCALP"
        if "SWING" in normalized:
            return "SWING"
        return "INTRADAY"

    def _trade_horizon_profile(self, horizon: str | None) -> dict:
        if not getattr(self, "trade_horizon_profiles_enabled", True):
            return {"name": "DISABLED"}

        profile_name = self._trade_horizon_profile_name(horizon)
        enabled = {
            "SCALP": getattr(self, "scalp_profile_enabled", True),
            "INTRADAY": getattr(self, "intraday_profile_enabled", True),
            "SWING": getattr(self, "swing_profile_enabled", True),
        }
        if not enabled.get(profile_name, True):
            return {"name": "DISABLED"}

        profiles = {
            "SCALP": {
                "name": "SCALP",
                "partial_tp_trigger_r": 0.60,
                "reverse_profit_min_r": 1.00,
                "reverse_profit_giveback_pct": 0.35,
                "reverse_after_partial_lock_r": 0.12,
                "trailing_stop_trigger_pct": 0.50,
                "trailing_stop_step_pct": 0.45,
                "trailing_stop_min_step_pips": 3.0,
                "max_adverse_r": 0.45,
                "allow_news_ladder": False,
            },
            "INTRADAY": {
                "name": "INTRADAY",
                "partial_tp_trigger_r": 0.75,
                "reverse_profit_min_r": 1.20,
                "reverse_profit_giveback_pct": 0.45,
                "trailing_stop_trigger_pct": 0.55,
                "trailing_stop_step_pct": 0.50,
                "trailing_stop_min_step_pips": 5.0,
                "max_adverse_r": 0.60,
                "allow_news_ladder": True,
            },
            "SWING": {
                "name": "SWING",
                "partial_tp_trigger_r": 1.00,
                "reverse_profit_min_r": 1.80,
                "reverse_profit_giveback_pct": 0.55,
                "reverse_after_partial_lock_r": 0.30,
                "trailing_stop_trigger_pct": 0.70,
                "trailing_stop_step_pct": 0.70,
                "trailing_stop_min_step_pips": 8.0,
                "max_adverse_r": 0.80,
                "allow_news_ladder": False,
            },
        }
        return profiles.get(profile_name, profiles["INTRADAY"])

    def _management_profile(self, symbol: str | None, trade: dict | None = None, signal: dict | None = None) -> dict:
        symbol_profile = self._symbol_profile(symbol)
        instrument_profile = self._instrument_management_overrides(symbol)
        horizon_source = self._horizon_value(trade) or self._horizon_value(signal)
        horizon_profile = self._trade_horizon_profile(horizon_source)

        merged = dict(symbol_profile)
        symbol_name = symbol_profile.get("name", "DEFAULT")
        instrument_name = instrument_profile.get("name", "DISABLED")
        if instrument_name != "DISABLED":
            merged.update({k: v for k, v in instrument_profile.items() if k != "name"})
        horizon_name = horizon_profile.get("name", "DISABLED")
        if horizon_name != "DISABLED":
            merged.update({k: v for k, v in horizon_profile.items() if k != "name"})
        name_parts = [symbol_name]
        if instrument_name != "DISABLED":
            name_parts.append(instrument_name)
        if horizon_name != "DISABLED":
            name_parts.append(horizon_name)
        merged["name"] = "+".join(name_parts)
        merged["symbol_profile"] = symbol_name
        merged["instrument_profile"] = instrument_name
        merged["horizon_profile"] = horizon_name
        merged["horizon_profile_mode"] = getattr(self, "horizon_profile_mode", "exit_only")
        return merged

    def _configured_management_profiles(self) -> dict:
        return {
            symbol: {
                "SCALP": self._management_profile(symbol, trade={"trade_horizon": {"type": "SCALP"}}),
                "INTRADAY": self._management_profile(symbol, trade={"trade_horizon": {"type": "INTRADAY"}}),
                "SWING": self._management_profile(symbol, trade={"trade_horizon": {"type": "SWING"}}),
            }
            for symbol in self.symbols
        }

    def _manage_pending_orders(self):
        """Manage pending orders (Set and Forget feature)."""
        try:
            can_trade, reason = self._can_place_pending_orders()
            if not can_trade:
                logger.info(f"Skipping pending order placement: {reason}")
                return

            # Scan for high-probability zones and place pending orders
            placed = self.pending_order_manager.scan_and_place_pending_orders(
                self.symbols,
                volume_func=self._calculate_volume,
                rr_ratio=self.take_profit_r_multiplier,
                max_orders=1,
                signal_guard=self._pending_signal_execution_allowed,
                signal_mark=self._mark_pending_signal_execution,
            )
            
            if placed:
                logger.info(f"Placed {len(placed)} pending orders")
            
            # Monitor existing pending orders
            updates = self.pending_order_manager.monitor_pending_orders()
            if updates:
                for symbol, status in updates.items():
                    if status["status"] == "FILLED_OR_CANCELLED":
                        logger.info(f"Pending order for {symbol} was filled or cancelled")
        
        except Exception as e:
            logger.error(f"Error managing pending orders: {e}")

    def _manage_conditional_watchlist(self):
        """Manage conditional watchlist (Smart Watchlist feature)."""
        try:
            # Process watchlist through phases
            updates = self.conditional_watchlist_manager.process_watchlist()
            
            if updates:
                for symbol, update in updates.items():
                    logger.info(f"Watchlist update {symbol}: {update}")
            
            # Check for symbols ready for execution (Phase 3 complete)
            ready_symbols = self.conditional_watchlist_manager.get_ready_for_execution()
            
            for ready in ready_symbols:
                symbol = ready["symbol"]
                
                # Skip if already in trade or trading disabled
                if symbol in self.active_trades or self.killed.get(symbol) or self.killed.get("all"):
                    continue
                
                # Can we trade?
                can_trade, reason = self._can_trade()
                if not can_trade:
                    self.log_rejection(symbol, f"Watchlist blocked: {reason}")
                    continue
                
                # Calculate volume for this trade
                extreme_fvg = ready["extreme_fvg"]
                volume = self._calculate_volume(symbol, extreme_fvg["entry"], extreme_fvg["sl"])
                if volume <= 0:
                    self.log_rejection(symbol, f"Watchlist blocked: invalid fixed lot size for {symbol}")
                    continue
                
                # Place the conditional order
                order_result = self.conditional_watchlist_manager.place_conditional_order(symbol, volume)
                
                if order_result:
                    logger.info(f"Conditional order placed for {symbol}: {order_result}")
                    # Reset the watchlist symbol for next cycle
                    self.conditional_watchlist_manager.reset_symbol(symbol)
        
        except Exception as e:
            logger.error(f"Error managing conditional watchlist: {e}")

    def start(self):
        """Start the trading loop"""
        self.is_running = True
        logger.info("Trading engine started")
        
        # Initialize conditional watchlist if enabled
        if self.features.get("conditional_watchlist"):
            self.conditional_watchlist_manager.initialize_watchlist(self.symbols)
        
        while self.is_running:
            try:
                if not self.mt5.ensure_connected():
                    self.add_logic("SYSTEM", "MT5 reconnect pending; pausing scan cycle", level="warning")
                    time.sleep(max(self.engine_loop_sleep_seconds, 5))
                    continue

                self._apply_dynamic_account_profile()
                self.scan_and_trade()
                
                # Handle pending orders (Set and Forget)
                if self.features.get("pending_orders"):
                    self._manage_pending_orders()
                
                # Handle conditional watchlist (Smart Watchlist)
                if self.features.get("conditional_watchlist"):
                    self._manage_conditional_watchlist()
                
                self.check_positions()
                time.sleep(self.engine_loop_sleep_seconds)
            except Exception as e:
                logger.error(f"Engine error: {e}")
                time.sleep(self.engine_loop_sleep_seconds)

    def stop(self):
        """Stop the trading loop"""
        self.is_running = False
        logger.info("Trading engine stopped")

    def _is_market_open(self):
        """Check if forex markets are currently open"""
        from datetime import datetime
        now = datetime.utcnow()
        weekday = now.weekday()  # 0=Monday, 6=Sunday
        
        # Forex markets: Sunday 17:00 UTC to Friday 17:00 UTC
        if weekday == 6:  # Sunday
            return now.hour >= 17
        elif weekday >= 0 and weekday <= 4:  # Monday-Friday
            return True
        elif weekday == 5:  # Saturday
            return False
        else:
            return False

    def _current_scan_candle_key(self):
        """Return a stable key for the active scan candle window."""
        minutes = max(1, self.scan_timeframe_minutes)
        now = datetime.utcnow().replace(second=0, microsecond=0)
        floored_minute = (now.minute // minutes) * minutes
        candle = now.replace(minute=floored_minute)
        return candle.isoformat()

    def _seconds_until_next_scan(self):
        if self.scan_on_new_candle:
            minutes = max(1, self.scan_timeframe_minutes)
            now = datetime.utcnow()
            floored_minute = (now.minute // minutes) * minutes
            candle = now.replace(minute=floored_minute, second=0, microsecond=0)
            next_candle = candle + timedelta(minutes=minutes)
            return max(1, int((next_candle - now).total_seconds()))

        if self.last_scan_at is None:
            return 1
        elapsed = (datetime.utcnow() - self.last_scan_at).total_seconds()
        return max(1, int(self.scan_interval_seconds - elapsed))

    def _should_scan_now(self):
        if self.scan_on_new_candle:
            candle_key = self._current_scan_candle_key()
            if candle_key == self.last_scan_candle_key:
                self.next_scan_at = datetime.utcnow() + timedelta(seconds=self._seconds_until_next_scan())
                return False
            self.last_scan_candle_key = candle_key
            self.last_scan_at = datetime.utcnow()
            self.next_scan_at = self.last_scan_at + timedelta(seconds=self._seconds_until_next_scan())
            return True

        now = datetime.utcnow()
        if self.last_scan_at is None or (now - self.last_scan_at).total_seconds() >= self.scan_interval_seconds:
            self.last_scan_at = now
            self.next_scan_at = now + timedelta(seconds=max(1, self.scan_interval_seconds))
            return True
        self.next_scan_at = now + timedelta(seconds=self._seconds_until_next_scan())
        return False

    def _signal_key(self, signal):
        symbol = signal.get("symbol")
        action = signal.get("action")
        entry = signal.get("entry")
        sl = signal.get("sl")
        tp = signal.get("tp")
        nature = signal.get("nature")
        return (
            symbol,
            action,
            round(float(entry or 0), 5),
            round(float(sl or 0), 5),
            round(float(tp or 0), 5),
            nature,
        )

    def _signal_execution_key(self, signal: dict) -> tuple:
        """Return a stable key for one actionable setup.

        The key is intentionally coarser than order ticket data: repeated scans of the same setup
        should map to the same key, so the bot cannot place both a pending order and a market order
        from the same signal.
        """
        symbol = str(signal.get("symbol") or "").upper()
        action = str(signal.get("action") or "").upper()
        setup = signal.get("setup_score") or {}
        archetype = str(setup.get("archetype") or signal.get("trade_style") or signal.get("nature") or "")
        horizon = signal.get("trade_horizon") or {}
        horizon_type = str(horizon.get("type") or signal.get("trade_type") or "")

        def rounded_price(value):
            try:
                return round(float(value or 0), 5)
            except Exception:
                return 0.0

        candle_key = signal.get("candle_key") or signal.get("bar_time") or signal.get("time") or self._current_scan_candle_key()
        return (
            symbol,
            action,
            archetype,
            horizon_type,
            rounded_price(signal.get("entry")),
            rounded_price(signal.get("sl")),
            rounded_price(signal.get("tp")),
            str(candle_key),
        )

    def _prune_signal_execution_ledger(self):
        now = datetime.utcnow()
        ttl = max(60, int(getattr(self, "signal_execution_ttl_seconds", 900)))
        self._signal_execution_ledger = {
            key: value
            for key, value in self._signal_execution_ledger.items()
            if (now - value.get("at", now)).total_seconds() <= ttl
            or value.get("state") in {"PENDING_PLACED", "MARKET_ENTERED"}
        }

    def _reserve_signal_execution(self, signal: dict, route: str) -> tuple[bool, str, tuple | None]:
        key = self._signal_execution_key(signal)
        symbol = signal.get("symbol")
        now = datetime.utcnow()
        with self._trades_lock:
            self._prune_signal_execution_ledger()
            existing = self._signal_execution_ledger.get(key)
            if existing and existing.get("state") in {"RESERVED", "PENDING_PLACED", "MARKET_ENTERED"}:
                return False, f"same signal already acted on via {existing.get('route')}", key
            self._signal_execution_ledger[key] = {
                "state": "RESERVED",
                "route": route,
                "symbol": symbol,
                "action": signal.get("action"),
                "at": now,
            }
        return True, "reserved", key

    def _commit_signal_execution(self, key, state: str, order_id=None):
        if not key:
            return
        with self._trades_lock:
            entry = self._signal_execution_ledger.get(key, {})
            entry.update({
                "state": state,
                "order_id": order_id,
                "at": datetime.utcnow(),
            })
            self._signal_execution_ledger[key] = entry

    def _release_signal_execution(self, key, reason: str = "released"):
        if not key:
            return
        with self._trades_lock:
            entry = self._signal_execution_ledger.get(key)
            if entry and entry.get("state") == "RESERVED":
                entry["state"] = "FAILED"
                entry["reason"] = reason
                entry["at"] = datetime.utcnow()

    def _signal_from_pending_zone(self, symbol: str, zone: dict) -> dict:
        return {
            "symbol": symbol,
            "action": zone.get("action"),
            "entry": zone.get("entry"),
            "sl": zone.get("sl"),
            "tp": zone.get("tp"),
            "nature": zone.get("type"),
            "bar_time": zone.get("bar_time"),
        }

    def _pending_signal_execution_allowed(self, symbol: str, zone: dict) -> bool:
        signal = self._signal_from_pending_zone(symbol, zone)
        lockout_ok, lockout_reason = self._check_signal_lockout(symbol, signal=signal)
        if not lockout_ok:
            self.add_logic(symbol, f"Pending order skipped by symbol lockout: {lockout_reason}", level="warning")
            return False

        broker_guard_ok, broker_guard_reason = self._guard_same_symbol_broker_exposure(signal)
        if not broker_guard_ok:
            self.add_logic(symbol, f"Pending order skipped by broker exposure guard: {broker_guard_reason}", level="warning")
            return False

        allowed, reason, key = self._reserve_signal_execution(signal, "pending_scan")
        zone["_signal_execution_key"] = key
        if not allowed:
            self.add_logic(symbol, f"Pending order skipped: {reason}", level="warning")
            return False
        return True

    def _mark_pending_signal_execution(self, symbol: str, zone: dict, ticket=None, success: bool = True):
        key = zone.get("_signal_execution_key")
        if success:
            self._commit_signal_execution(key, "PENDING_PLACED", ticket)
        else:
            self._release_signal_execution(key, "pending order failed")

    def _read_env_value(self, key: str, default: str = "") -> str:
        raw = os.getenv(key, default)
        try:
            env_path = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as env_file:
                    for line in env_file:
                        stripped = line.strip()
                        if stripped.startswith(f"{key}="):
                            raw = stripped.split("=", 1)[1].strip()
        except Exception:
            pass
        return str(raw or "")

    def _refresh_execution_symbols_from_env(self) -> list:
        raw = self._read_env_value("EXECUTION_SYMBOLS", "")
        refreshed = [
            s.strip().upper()
            for s in str(raw or "").split(",")
            if s.strip()
        ]
        if refreshed != getattr(self, "execution_symbols", []):
            self.execution_symbols = refreshed
        return refreshed

    def _refresh_scan_symbols_from_env(self) -> list:
        raw = self._read_env_value("TRADING_SYMBOLS", ",".join(self.symbols))
        refreshed = [
            s.strip()
            for s in str(raw or "").split(",")
            if s.strip()
        ]
        if refreshed and refreshed != getattr(self, "symbols", []):
            self.symbols = refreshed
        return self.symbols

    def _symbol_allowed_for_execution(self, symbol: str) -> tuple[bool, str]:
        """Allow broad scanning while limiting live MT5 execution to a curated symbol set."""
        if self.small_account_active:
            instrument_class = self._instrument_class(symbol)
            if instrument_class == "METAL" and not self.small_account_allow_metals:
                return False, f"Small Account Mode blocks metal execution for {symbol}"
            if instrument_class == "CRYPTO" and not self.small_account_allow_crypto:
                return False, f"Small Account Mode blocks crypto execution for {symbol}"
            if instrument_class == "STOCK" and not self.small_account_allow_stocks:
                return False, f"Small Account Mode blocks stock execution for {symbol}"

        execution_symbols = self._refresh_execution_symbols_from_env()
        if not execution_symbols:
            return True, "No execution-symbol allowlist configured"
        if any(self._symbols_match(symbol, allowed) for allowed in execution_symbols):
            return True, "Symbol is on execution allowlist"
        return False, f"{symbol} is watch-only; not in EXECUTION_SYMBOLS ({','.join(execution_symbols)})"

    def _armed_signal_key(self, signal: dict) -> str:
        setup = signal.get("setup_score") or {}
        symbol = self._normalize_symbol_key(signal.get("symbol"))
        action = str(signal.get("action") or "").upper()
        archetype = str(setup.get("archetype") or signal.get("nature") or "setup").upper()
        return f"{symbol}|{action}|{archetype}"

    def _cleanup_armed_signals(self):
        now = datetime.utcnow()
        self.armed_signals = {
            key: value
            for key, value in self.armed_signals.items()
            if (now - value.get("last_seen", now)).total_seconds() <= self.armed_ttl_seconds
        }

    def _armed_confirmation_allowed(self, signal: dict, setup_value: float) -> tuple[bool, str]:
        """Require a setup to persist across scans before execution."""
        if not self.armed_confirmation_enabled:
            return True, "Armed confirmation disabled"

        symbol = signal.get("symbol")
        if setup_value < self.armed_min_score:
            return False, f"Armed confirmation: setup score {setup_value:.2f} < {self.armed_min_score:.2f}"
        if self.armed_require_structure and not self._signal_has_hard_structure(signal):
            return False, "Armed confirmation: missing hard structure"

        spread_ok, spread_reason = self._get_spread_safety(signal)
        drift_ok, drift_reason = self._is_price_near_entry(signal)
        if not spread_ok:
            return False, f"Armed confirmation: {spread_reason}"
        if not drift_ok:
            return False, f"Armed confirmation: {drift_reason}"

        if self.armed_required_scans <= 1:
            return True, "Armed confirmation passed immediately"

        self._cleanup_armed_signals()
        key = self._armed_signal_key(signal)
        now = datetime.utcnow()
        armed = self.armed_signals.get(key)
        if not armed:
            self.armed_signals[key] = {
                "first_seen": now,
                "last_seen": now,
                "seen_count": 1,
                "setup_score": setup_value,
            }
            return False, f"Setup armed for confirmation scan 1/{self.armed_required_scans}"

        armed["last_seen"] = now
        armed["seen_count"] = int(armed.get("seen_count") or 0) + 1
        armed["setup_score"] = max(float(armed.get("setup_score") or 0.0), setup_value)
        if armed["seen_count"] < self.armed_required_scans:
            return False, f"Setup armed for confirmation scan {armed['seen_count']}/{self.armed_required_scans}"

        self.armed_signals.pop(key, None)
        return True, f"Armed confirmation passed after {armed['seen_count']} scans"

    def _should_log_signal(self, signal):
        """Suppress repeated log spam for the same signal inside the cooldown window."""
        key = self._signal_key(signal)
        now = datetime.utcnow()
        last_seen = self._signal_log_cache.get(key)
        self._signal_log_cache = {
            k: v for k, v in self._signal_log_cache.items()
            if (now - v).total_seconds() <= self.duplicate_signal_cooldown_seconds
        }
        if last_seen and (now - last_seen).total_seconds() < self.duplicate_signal_cooldown_seconds:
            return False
        self._signal_log_cache[key] = now
        return True

    def scan_and_trade(self):
        """Scan for FVG signals and execute trades"""
        try:
            if not self._should_scan_now():
                return

            scan_symbols_list = self._refresh_scan_symbols_from_env()

            # Check if markets are open
            if not self._is_market_open():
                self.add_logic(None, "Markets closed; scanning for signal snapshots only", level="info")
                # Still scan for signals to show in UI, but don't trade
                signals = scan_symbols(scan_symbols_list, self.timeframe)
                self.last_scan_signal_count = len(signals)
                for signal in signals:
                    self.recent_signals.append(signal)
                self.recent_signals = self.recent_signals[-20:]
                return

            signals = scan_symbols(scan_symbols_list, self.timeframe)
            self.last_scan_signal_count = len(signals)
            if not signals:
                self.add_logic(None, "No FVG signals detected this cycle", level="info")

            # store recent signals (keep last 20)
            for signal in signals:
                self.recent_signals.append(signal)
            self.recent_signals = self.recent_signals[-20:]

            for signal in signals:
                symbol = signal.get("symbol")

                # Timestamp and classification events
                signal = {
                    **signal,
                    "timestamp": datetime.now().isoformat(),
                }

                # Compute scalp potential rating and store signal history
                scalp_data = self._compute_scalp_potential(signal)
                signal["scalp_potential"] = scalp_data
                setup_score = signal.get("setup_score") or {}
                setup_value = float(setup_score.get("score", 0.0))
                self.signal_history.append(signal)
                self.signal_history = self.signal_history[-200:]
                
                # Enhanced future trades with institutional context
                conviction_score = int(max(scalp_data['score'], setup_value, float(signal.get("confluence_score", 0.0))) * 100)
                setup_name = (
                    f"Grade {setup_score.get('grade')} Early Entry"
                    if signal.get("early_entry")
                    else "Institutional Sweep" if setup_value >= 0.70
                    else "Order Block Mitigation" if scalp_data['score'] >= 0.5
                    else "FVG Re-entry"
                )
                trigger = f"Wait for {signal.get('nature').split()[0]} confirmation at {signal.get('entry'):.5f}"
                
                trade_style = self._classify_trade_style(signal, scalp_data)
                signal["trade_style"] = trade_style
                trade_horizon = signal.get("trade_horizon") or {}
                false_move = signal.get("false_move") or {}
                news_move = signal.get("news_move") or {}
                event_tags = []
                if false_move.get("type") and false_move.get("type") not in ["UNKNOWN", "RANGE"]:
                    event_tags.append(false_move.get("type").replace("_", " ").title())
                if news_move.get("mode") and news_move.get("mode") != "NORMAL":
                    event_tags.append(f"News {news_move.get('mode').replace('_', ' ').title()}")

                future_trade = {
                    **signal,
                    "setup_name": setup_name,
                    "conviction_score": conviction_score,
                    "trigger": trigger,
                    "trade_style": trade_style,
                    "trade_horizon": trade_horizon,
                    "phase": "Monitoring" if conviction_score < 70 else "Ready",
                    "criteria": f"Conviction {conviction_score}% | {trade_horizon.get('type', 'INTRADAY')} | {scalp_data['label']}" + (f" | {' | '.join(event_tags)}" if event_tags else ""),
                    "action_needed": (
                        "Wait for news retest/spread normalisation"
                        if news_move.get("plan") in ["WAIT_RETEST", "WAIT_SPREAD"]
                        else "Fade failed breakout" if false_move.get("type") in ["FAILED_BREAKOUT", "LIQUIDITY_SWEEP_REVERSAL"]
                        else "Execute on M5 Shift" if conviction_score >= 80
                        else "Hunting FVG Fill"
                    )
                }
                self.future_trades.append(future_trade)
                self.future_trades = self.future_trades[-200:]

                # Log the signal for UI and analytics without spamming repeated identical setups.
                should_log_signal = self._should_log_signal(signal)
                if should_log_signal:
                    self.logger.log_signal(signal)
                context_reason = f"{setup_score.get('archetype', 'Structure')} identified with market context"
                if setup_value >= self.early_entry_min_score:
                    context_reason += f" | Early Score {setup_value:.2f} ({setup_score.get('summary', 'composite setup')})"
                if scalp_data['score'] >= 0.7:
                    context_reason += " | High Scalp Conviction"
                elif scalp_data['score'] >= 0.5:
                    context_reason += " | Momentum Setup detected"
                else:
                    context_reason += " | Trend Opportunity zone"
                if should_log_signal:
                    self.add_logic(symbol, f"Structure setup: {signal.get('nature')} ({scalp_data['label']}|score={scalp_data['score']}) - {context_reason}")
                    if trade_horizon:
                        self.add_logic(symbol, f"Trade horizon: {trade_horizon.get('type')} ({trade_horizon.get('hold_time')}) - {trade_horizon.get('reason')}")

                # Validate rules and compute war room decision
                trade_approved = False
                ensemble_decision = {}
                if self.features.get("war_room", True):
                    analytic_result = self.analytic_engine.evaluate_setup(symbol, signal)
                    mtf_details = analytic_result.get("multi_timeframe") or {}
                    if mtf_details:
                        signal["multi_timeframe"] = mtf_details
                        self.add_logic(
                            symbol,
                            f"MTF analytic: {mtf_details.get('score', 0):.2f} - {mtf_details.get('reason', 'multi-timeframe context')}",
                            level="info",
                        )
                    predictive_result = self.predictive_engine.predict_probability(symbol)
                    ensemble_decision = self.ensemble_decision.make_decision(
                        analytic_result, predictive_result, signal
                    )

                    conviction = ensemble_decision.get("conviction", 0.5)
                    decision = ensemble_decision.get("decision", "WAIT")
                    confluence_score = float(signal.get("confluence_score", 0.0))
                    setup_archetype = str((signal.get("setup_score") or {}).get("archetype") or "Context Watch")
                    hard_structure_ok = self._signal_has_hard_structure(signal)
                    horizon_profile = self._horizon_execution_profile(signal)
                    horizon_conviction = float(horizon_profile.get("conviction_threshold", self.execution_conviction_threshold))
                    horizon_setup = float(horizon_profile.get("setup_score_threshold", self.execution_setup_score_threshold))
                    promotable_structure = hard_structure_ok and setup_archetype != "Context Watch"

                    # Use configurable conviction threshold (lowered to 0.60)
                    if conviction < self.conviction_threshold:
                        decision = "WAIT"

                    # Structured scalp override only. Context-only scalp ideas stay watchlist.
                    if (
                        decision != "TRADE"
                        and promotable_structure
                        and scalp_data.get("score", 0) >= 0.65
                        and conviction >= max(0.22, horizon_conviction - 0.08)
                    ):
                        decision = "TRADE"
                        ensemble_decision["reasoning"] = ensemble_decision.get("reasoning", "") + " | Structured Scalp Override"

                    # Allow moderate conviction only when setup quality and structure agree.
                    if (
                        decision != "TRADE"
                        and promotable_structure
                        and conviction >= max(0.25, horizon_conviction - 0.05)
                        and setup_value >= max(0.45, horizon_setup - 0.08)
                    ):
                        decision = "TRADE"
                        ensemble_decision["reasoning"] = ensemble_decision.get("reasoning", "") + " | Structured Moderate Confidence"

                    # Early entry for high-confluence setups even if FVG is not perfect yet
                    if decision != "TRADE" and promotable_structure and confluence_score >= 0.60 and conviction >= 0.25:
                        decision = "TRADE"
                        ensemble_decision["reasoning"] = ensemble_decision.get("reasoning", "") + " | Confluence Override"

                    # High probability signal entry
                    if decision != "TRADE" and promotable_structure and scalp_data.get("score", 0) >= 0.75 and conviction >= 0.25:
                        decision = "TRADE"
                        ensemble_decision["reasoning"] = ensemble_decision.get("reasoning", "") + " | Aggressive High-Probability Entry"

                    # Early-entry composite setup override.
                    if (
                        self.early_entry_enabled
                        and decision != "TRADE"
                        and setup_value >= self.early_entry_min_score
                    ):
                        decision = "TRADE"
                        ensemble_decision["reasoning"] = ensemble_decision.get("reasoning", "") + f" | Early Score Override {setup_value:.2f}"

                    if decision == "TRADE":
                        trade_approved = True
                        self.add_logic(symbol, f"War Room WATCHLIST approved; conviction={conviction:.3f}", level="info")
                        logger.info(f"War Room WATCHLIST approved setup for {symbol}: Conviction {conviction:.3f}")
                    else:
                        rejection_reason = ensemble_decision.get("reasoning", "Low conviction")
                        self.log_rejection(symbol, f"War Room: {rejection_reason}")
                        self.add_logic(symbol, f"War Room DECLINED ({rejection_reason}); conviction={conviction:.3f}", level="warning")
                        # Fall back to traditional validation

                if self._apply_opposing_signal_profit_exit(signal, ensemble_decision, setup_value):
                    continue

                # Fallback to traditional validation if war room declined or disabled
                if not trade_approved:
                    valid, reason = validate_trade(symbol, {**self.rule_config, **{"action": signal.get("action")}})
                    if not valid:
                        # More lenient fallback - allow trades with good scalp potential
                        if scalp_data.get("score", 0) >= 0.6:
                            self.add_logic(symbol, f"Scalp potential strong, overriding validation: {reason}", level="info")
                            trade_approved = True
                        elif self.early_entry_enabled and setup_value >= self.early_entry_min_score:
                            self.add_logic(symbol, f"Composite early-entry score strong ({setup_value:.2f}), overriding validation: {reason}", level="info")
                            trade_approved = True
                        elif scalp_data.get("score", 0) >= 0.4 and "stale" not in reason.lower():
                            # Allow trades with moderate scalp potential if data is fresh
                            self.add_logic(symbol, f"Moderate scalp potential, allowing trade despite: {reason}", level="info")
                            trade_approved = True
                        else:
                            self.log_rejection(symbol, f"Validation failed: {reason}")
                            continue
                    else:
                        trade_approved = True
                        self.add_logic(symbol, f"Traditional validation PASSED", level="info")

                # If trade is approved, proceed with execution
                if not trade_approved:
                    continue

                rr_ok, rr_reason = self._normalize_signal_levels_to_rr(signal)
                if not rr_ok:
                    self.log_rejection(symbol, rr_reason)
                    self.add_logic(symbol, f"Signal rejected by TP/SL calculator: {rr_reason}", level="warning")
                    continue
                self.add_logic(symbol, rr_reason, level="info")

                # Check daily profit cap
                if self._has_hit_daily_profit_cap():
                    self.add_logic(symbol, f"Daily profit cap reached ({self.daily_profit_cap*100:.1f}%), stopping trading for today", level="warning")
                    self.is_running = False  # Stop the bot
                    continue

                # Reject signals with too-small profit potential
                big_enough, size_reason = self._is_signal_big_enough(signal)
                if not big_enough:
                    self.log_rejection(symbol, size_reason, {"stage": "SIZE_FILTER", "setup_score": setup_score})
                    self.add_logic(symbol, f"Signal rejected by size filter: {size_reason}", level="warning")
                    continue

                qualified, qualification = self._qualify_signal_stage(signal, ensemble_decision, setup_value)
                signal["decision_stage"] = qualification.get("stage")
                signal["qualification"] = qualification
                if not qualified:
                    self.log_rejection(symbol, qualification["reason"], {
                        "stage": "QUALIFICATION",
                        "setup_score": setup_score,
                        "qualification": qualification,
                    })
                    self.add_logic(symbol, f"Signal held at discovery stage: {qualification['reason']}", level="warning")
                    continue
                self.add_logic(symbol, f"Signal qualified: {qualification['reason']}", level="info")

                use_market_execution = self._should_use_market_execution(signal, scalp_data, ensemble_decision)
                signal["execution_type"] = "market" if use_market_execution else "pending"
                if use_market_execution:
                    refreshed, refresh_reason = self._refresh_signal_for_market_execution(signal)
                    if not refreshed:
                        self.log_rejection(symbol, refresh_reason, {"stage": "MARKET_REFRESH", "setup_score": setup_score})
                        self.add_logic(symbol, f"Signal rejected before gate checks: {refresh_reason}", level="warning")
                        continue
                    self.add_logic(symbol, refresh_reason, level="info")
                    rr_ok, rr_reason = self._normalize_signal_levels_to_rr(signal)
                    if not rr_ok:
                        self.log_rejection(symbol, rr_reason, {"stage": "RR_NORMALIZATION", "setup_score": setup_score})
                        self.add_logic(symbol, f"Signal rejected after market refresh: {rr_reason}", level="warning")
                        continue
                    self.add_logic(symbol, rr_reason, level="info")

                readiness = self._compute_trade_readiness(signal, ensemble_decision, setup_value)
                signal["trade_readiness"] = readiness
                if readiness["score"] < readiness["threshold"]:
                    reason = f"Trade readiness {readiness['score']:.2f} < {readiness['threshold']:.2f}"
                    self.log_rejection(symbol, reason, {
                        "stage": "READINESS",
                        "setup_score": setup_score,
                        "qualification": qualification,
                        "trade_readiness": readiness,
                    })
                    self.add_logic(symbol, f"Signal not executable yet: {reason}", level="warning")
                    continue
                self.add_logic(symbol, f"Trade readiness passed: {readiness['score']:.2f}/{readiness['threshold']:.2f}", level="info")

                execution_ok, execution_reason = self._execution_gate(signal, ensemble_decision, setup_value)
                if not execution_ok:
                    self.log_rejection(symbol, execution_reason, {
                        "stage": "EXECUTION_GATE",
                        "setup_score": setup_score,
                        "qualification": qualification,
                        "trade_readiness": readiness,
                    })
                    self.add_logic(symbol, f"Signal rejected by execution gate: {execution_reason}", level="warning")
                    continue
                self.add_logic(symbol, execution_reason, level="info")

                professional_ok, professional_reason = self._professional_execution_gate(signal, ensemble_decision, setup_value)
                if not professional_ok:
                    self.log_rejection(symbol, professional_reason, {
                        "stage": "PROFESSIONAL_GATE",
                        "setup_score": setup_score,
                        "qualification": qualification,
                        "trade_readiness": readiness,
                    })
                    self.add_logic(symbol, f"Signal held as watch-only: {professional_reason}", level="warning")
                    continue
                self.add_logic(symbol, professional_reason, level="info")

                event_ok, event_reason = self._event_execution_gate(signal)
                if not event_ok:
                    self.log_rejection(symbol, event_reason, {
                        "stage": "EVENT_GATE",
                        "setup_score": setup_score,
                        "qualification": qualification,
                        "trade_readiness": readiness,
                    })
                    self.add_logic(symbol, f"Signal held by event/trap gate: {event_reason}", level="warning")
                    continue
                self.add_logic(symbol, event_reason, level="info")

                # Determine whether this signal is favorable (pass checks)
                can_trade, can_trade_reason = self._can_trade()
                status = "ready"
                status_reason = None
                if self.killed.get("all") or self.killed.get(symbol):
                    status = "killed"
                    status_reason = "Kill switch active"
                elif symbol in self.active_trades:
                    status = "active"
                    status_reason = "Already in trade"
                elif not can_trade:
                    status = "blocked"
                    status_reason = can_trade_reason

                expected_r = self._calculate_expected_r(signal)
                self.add_logic(symbol, f"Signal evaluation: status={status}, reason={status_reason or 'none'}, expected_r={expected_r if expected_r is not None else 'N/A'}")
                self.favorable_signals.append({
                    **signal,
                    "status": status,
                    "status_reason": status_reason,
                    "expected_r": expected_r,
                })
                self.favorable_signals = self.favorable_signals[-20:]

                execution_symbol_ok, execution_symbol_reason = self._symbol_allowed_for_execution(symbol)
                if not execution_symbol_ok:
                    self.log_rejection(symbol, execution_symbol_reason, {"stage": "EXECUTION_SYMBOLS"})
                    self.add_logic(symbol, f"Signal held as watch-only: {execution_symbol_reason}", level="warning")
                    self.favorable_signals[-1]["status"] = "watch_only"
                    self.favorable_signals[-1]["status_reason"] = execution_symbol_reason
                    continue

                armed_ok, armed_reason = self._armed_confirmation_allowed(signal, setup_value)
                if not armed_ok:
                    self.log_rejection(symbol, armed_reason, {"stage": "ARMED_CONFIRMATION"})
                    self.add_logic(symbol, armed_reason, level="info")
                    self.favorable_signals[-1]["status"] = "armed"
                    self.favorable_signals[-1]["status_reason"] = armed_reason
                    continue
                self.add_logic(symbol, armed_reason, level="info")
                self.favorable_signals[-1]["armed_confirmation"] = armed_reason

                # ========== SIGNAL LOCKOUT CHECK ==========
                # Check if this symbol is locked out from new trades
                lockout_check, lockout_reason = self._check_signal_lockout(
                    symbol,
                    signal=signal,
                    ensemble_decision=ensemble_decision,
                )
                if not lockout_check:
                    self.log_rejection(symbol, f"Signal Lockout: {lockout_reason}")
                    self.add_logic(symbol, f"Signal rejected by lockout system: {lockout_reason}", level="warning")
                    # Update favorable signals with lockout status
                    self.favorable_signals[-1]["status"] = "locked"
                    self.favorable_signals[-1]["status_reason"] = lockout_reason
                    continue

                if status != "ready":
                    if status == "blocked":
                        self.log_rejection(symbol, status_reason)
                    continue

                use_market_execution = signal.get("execution_type") == "market"
                if use_market_execution:
                    self.add_logic(symbol, "High-probability market execution selected", level="info")

                # Check if we have enough funds for this trade
                entry = signal.get("entry")
                sl = signal.get("sl")
                volume = self._calculate_volume(symbol, entry, sl)
                if volume <= 0:
                    self.log_rejection(symbol, f"Invalid fixed lot size for {symbol}; check TRADE_VOLUME and broker minimum")
                    self.add_logic(symbol, "Signal rejected by lot-size guard: fixed lot is below broker minimum or invalid", level="warning")
                    continue
                risk_multiplier = float(signal.get("risk_multiplier") or 1.0)
                if risk_multiplier < 1.0:
                    reduced_volume = volume * risk_multiplier
                    if reduced_volume < self._get_symbol_min_lot(symbol):
                        self.log_rejection(symbol, f"Reduced event volume below broker minimum for {symbol}")
                        self.add_logic(symbol, "Signal rejected: event risk reduction would round back up to minimum lot", level="warning")
                        continue
                    volume = self._round_symbol_lot(symbol, reduced_volume)
                    self.add_logic(symbol, f"Risk reduced for event mode: volume multiplier {risk_multiplier:.0%}", level="info")

                # Verify funds with favorable trade priority
                # If funds insufficient, skip and let favorable trades attempt first on next scan
                can_trade_check, trade_reason = self._can_trade()
                if not can_trade_check:
                    self.log_rejection(symbol, f"Insufficient funds - {trade_reason}")
                    continue

                broker_guard_ok, broker_guard_reason = self._guard_same_symbol_broker_exposure(signal)
                if not broker_guard_ok:
                    self.log_rejection(symbol, f"Broker exposure guard: {broker_guard_reason}")
                    self.add_logic(symbol, f"Signal rejected by broker exposure guard: {broker_guard_reason}", level="warning")
                    self.favorable_signals[-1]["status"] = "broker_locked"
                    self.favorable_signals[-1]["status_reason"] = broker_guard_reason
                    continue

                self.execute_trade(signal, volume, use_market_execution=use_market_execution)
        except Exception as e:
            logger.exception(f"Scan error: {e}")

    def execute_trade(self, signal, volume: float, use_market_execution: bool = False):
        """Execute a trade based on FVG signal."""
        execution_key = None
        self.last_execution_error = None
        try:
            symbol = signal["symbol"]
            action = signal["action"]
            entry = signal["entry"]
            sl = signal["sl"]
            tp = signal["tp"]
            execution_type = "market" if use_market_execution else "pending"

            event_ok, event_reason = self._event_execution_gate(signal)
            if not event_ok:
                self.log_rejection(symbol, event_reason)
                self.add_logic(symbol, f"Trade execution blocked by final event gate: {event_reason}", level="warning")
                self.last_execution_error = event_reason
                return

            reserved, reserve_reason, execution_key = self._reserve_signal_execution(signal, execution_type)
            if not reserved:
                self.log_rejection(symbol, f"Signal execution ledger: {reserve_reason}")
                self.add_logic(symbol, f"Trade execution blocked: {reserve_reason}", level="warning")
                self.last_execution_error = reserve_reason
                return

            lockout_ok, lockout_reason = self._check_signal_lockout(symbol, signal=signal)
            if not lockout_ok:
                self.log_rejection(symbol, f"Execution lockout: {lockout_reason}")
                self.add_logic(symbol, f"Trade execution blocked by symbol lockout: {lockout_reason}", level="warning")
                self._release_signal_execution(execution_key, lockout_reason)
                self.last_execution_error = lockout_reason
                return

            broker_guard_ok, broker_guard_reason = self._guard_same_symbol_broker_exposure(signal)
            if not broker_guard_ok:
                self.log_rejection(symbol, f"Broker exposure guard: {broker_guard_reason}")
                self.add_logic(symbol, f"Trade execution blocked by broker exposure guard: {broker_guard_reason}", level="warning")
                self._release_signal_execution(execution_key, broker_guard_reason)
                self.last_execution_error = broker_guard_reason
                return

            if use_market_execution:
                tick = self.mt5.get_symbol_tick(symbol)
                if tick is None:
                    logger.error(f"Failed to retrieve tick data for market execution: {symbol}")
                    self._release_signal_execution(execution_key, "missing tick data")
                    self.last_execution_error = "Missing tick data"
                    return
                price = float(tick.ask if action == "BUY" else tick.bid)
                if action == "BUY":
                    order_id = self.mt5.place_buy_order(symbol, volume, price, sl, tp)
                else:
                    order_id = self.mt5.place_sell_order(symbol, volume, price, sl, tp)
            else:
                if action == "BUY":
                    order_id = self.mt5.place_buy_limit_order(symbol, volume, entry, sl, tp)
                else:
                    order_id = self.mt5.place_sell_limit_order(symbol, volume, entry, sl, tp)

            if order_id:
                risk_amount = self._calculate_risk_amount(symbol, entry, sl, volume)
                self._commit_signal_execution(
                    execution_key,
                    "MARKET_ENTERED" if use_market_execution else "PENDING_PLACED",
                    order_id,
                )
                trade_style = signal.get("trade_style", "Setup")
                self.add_logic(symbol, f"Trade executed ({execution_type}) {trade_style} {action} @ {entry} SL={sl} TP={tp} vol={volume:.2f} risk=${risk_amount:.2f}", level="info")
                self.active_trades[symbol] = {
                    "order_id": order_id,
                    "signal_execution_key": execution_key,
                    "action": action,
                    "entry": entry,
                    "sl": sl,
                    "original_sl": sl,
                    "tp": tp,
                    "volume": volume,
                    "risk": risk_amount,
                    "opened_at": datetime.now().isoformat(),
                    "type": execution_type,
                    "trade_style": trade_style,
                    "trade_horizon": signal.get("trade_horizon"),
                    "symbol_profile": self._symbol_profile(symbol).get("name"),
                    "management_profile": self._management_profile(symbol, signal=signal).get("name"),
                    "risk_multiplier": signal.get("risk_multiplier", 1.0),
                    "false_move": signal.get("false_move"),
                    "news_move": signal.get("news_move"),
                    "initial_volume": volume,
                    "news_ladder_addons": [],
                    "partial_tp_taken": False,
                    "reverse_exit_done": False,
                    "max_favorable_r": 0.0,
                    "max_favorable_profit": 0.0,
                    "max_favorable_price": entry,
                }
                self.logger.log_trade({
                    "symbol": symbol,
                    "action": action,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "volume": volume,
                    "risk": risk_amount,
                    "order_id": order_id,
                    "type": execution_type,
                    "trade_style": trade_style,
                    "trade_horizon": signal.get("trade_horizon"),
                    "risk_multiplier": signal.get("risk_multiplier", 1.0),
                    "false_move": signal.get("false_move"),
                    "news_move": signal.get("news_move"),
                })
                logger.info(f"{execution_type.capitalize()} order placed: {symbol} {action} (vol={volume:.2f}, risk=${risk_amount:.2f})")
                self._register_trade_open(symbol)
                if self.favorable_signals:
                    self.favorable_signals[-1]["status"] = "executed"
                    self.favorable_signals[-1]["status_reason"] = f"MT5 order placed: {order_id}"
                    self.favorable_signals[-1]["execution_attempted"] = True
                    self.favorable_signals[-1]["order_id"] = order_id
            else:
                self._release_signal_execution(execution_key, "order failed")
                order_error = getattr(self.mt5, "last_order_error", None) or "MT5 returned no order id"
                if self.favorable_signals:
                    self.favorable_signals[-1]["status"] = "mt5_failed"
                    self.favorable_signals[-1]["status_reason"] = order_error
                    self.favorable_signals[-1]["execution_attempted"] = True
                self.add_logic(symbol, f"Trade execution failed: {order_error}", level="warning")
                self.last_execution_error = order_error
                logger.error(f"Failed to place {action} order: {symbol} - {order_error}")
        except Exception as e:
            self._release_signal_execution(execution_key, str(e))
            self.last_execution_error = str(e)
            logger.error(f"Trade execution error: {e}")

    def check_positions(self):
        """Monitor active positions and pending orders, apply trade management rules"""
        try:
            if not self.mt5.ensure_connected():
                self.add_logic("SYSTEM", "Position check skipped: MT5 reconnect pending", level="warning")
                return

            positions = self.mt5.get_positions() or []
            pending_orders = self.mt5.get_pending_orders() or []
            
            current_symbols = {p["symbol"] for p in positions}
            pending_symbols = {o["symbol"] for o in pending_orders}
            position_tickets = {p.get("ticket") for p in positions}
            pending_tickets = {o.get("ticket") for o in pending_orders}

            # Check for filled pending orders (now positions)
            for symbol in list(self.active_trades.keys()):
                trade = self.active_trades[symbol]
                if trade.get("type") == "pending":
                    ticket = trade.get("order_id")
                    if ticket in position_tickets:
                        # Pending order was filled - update to position
                        self.add_logic(symbol, f"Pending order filled - now active position", level="info")
                        trade["type"] = "position"
                        trade["filled_at"] = datetime.now().isoformat()
                        logger.info(f"Pending order filled for {symbol}, now monitoring as position")
                    elif ticket not in pending_tickets:
                        # Pending order was cancelled or expired
                        pending_info = self.pending_order_manager.pending_orders.get(symbol)
                        reason = "Cancelled or expired"
                        if pending_info and pending_info.get("placed_at"):
                            try:
                                placed_time = datetime.fromisoformat(pending_info["placed_at"])
                                age_hours = (datetime.now() - placed_time).total_seconds() / 3600
                                if age_hours >= 24:
                                    reason = "Expired"
                                else:
                                    reason = "Cancelled"
                            except Exception:
                                reason = "Cancelled or expired"

                        self.add_logic(symbol, f"Pending order {reason} - ticket {ticket} no longer exists in MT5", level="warning")
                        self.active_trades.pop(symbol, None)
                        self.pending_order_manager.pending_orders.pop(symbol, None)
                        self._record_closed_trade(symbol, 0, trade.get("risk"), reason)
                        continue
            
            # Update profit/loss for active positions
            for position in positions:
                symbol = position.get("symbol")
                tracked_symbol = next(
                    (tracked for tracked in self.active_trades if self._symbols_match(symbol, tracked)),
                    None,
                )
                if tracked_symbol:
                    profit = position.get("profit", 0)
                    self.last_known_profit[tracked_symbol] = profit
            
            # Detect closed positions
            for symbol in list(self.active_trades.keys()):
                symbol_has_position = any(self._symbols_match(live_symbol, symbol) for live_symbol in current_symbols)
                symbol_has_pending = any(self._symbols_match(pending_symbol, symbol) for pending_symbol in pending_symbols)
                if not symbol_has_position and not symbol_has_pending:
                    # Trade was closed or cancelled
                    trade = self.active_trades.pop(symbol, None)
                    if trade:
                        # Try to get profit from last known position
                        profit = self.last_known_profit.get(symbol, 0)
                        risk = trade.get("risk") if isinstance(trade, dict) else None
                        reason = "TP/SL Hit" if profit != 0 else "Cancelled"
                        if profit < 0:
                            self._activate_reversal_shock_guard(
                                symbol,
                                trade.get("action"),
                                f"closed loss: {reason}",
                                profit=profit,
                            )
                        self._register_trade_close(symbol)
                        self._record_closed_trade(symbol, profit, risk, reason)
                        self.last_known_profit.pop(symbol, None)
                        self.add_logic(symbol, f"Position closed: {reason}, P&L=${profit:.2f}", level="info")

            # Update current profit tracking for open positions
            for pos in positions:
                symbol = pos.get("symbol")
                if not symbol:
                    continue
                tracked_symbol = next(
                    (tracked for tracked in self.active_trades if self._symbols_match(symbol, tracked)),
                    symbol,
                )
                self.last_known_profit[tracked_symbol] = pos.get("profit")
                if tracked_symbol in self.active_trades:
                    if symbol != tracked_symbol:
                        pos = dict(pos)
                        pos["symbol"] = tracked_symbol
                    logger.info(f"{tracked_symbol}: P&L = {pos.get('profit')}")
                    self._track_favorable_excursion(pos)
                    self._apply_first_profit_breakeven(pos)
                    self._apply_breakeven_protection(pos)
                    self._apply_partial_tp_protection(pos)
                    if self._apply_max_adverse_exit(pos):
                        continue
                    if self._apply_partial_take_profit(pos):
                        continue
                    self._apply_news_ladder(pos)
                    if self._apply_reverse_profit_exit(pos):
                        continue
                    self._apply_trailing_stop(pos)
                    self._apply_trailing_take_profit(pos)
        except Exception as e:
            logger.error(f"Position check error: {e}")

    def _position_r_multiple(self, pos) -> float | None:
        """Return current open profit in R based on price movement versus initial stop."""
        try:
            side = pos.get("type")
            symbol = pos.get("symbol")
            trade = self.active_trades.get(symbol, {}) if symbol else {}
            entry = float(pos.get("entry"))
            current = float(pos.get("current"))
            sl = float(trade.get("original_sl") or pos.get("sl"))
            risk_distance = abs(entry - sl)
            if risk_distance <= 0:
                return None
            move = current - entry if side == "BUY" else entry - current
            return move / risk_distance
        except Exception:
            return None

    def _track_favorable_excursion(self, pos):
        """Track max open profit so reversals from green can be handled before breakeven."""
        symbol = pos.get("symbol")
        if symbol not in self.active_trades:
            return
        trade = self.active_trades[symbol]
        r_now = self._position_r_multiple(pos)
        profit_now = float(pos.get("profit") or 0)
        if r_now is None:
            return

        if r_now > float(trade.get("max_favorable_r") or 0):
            trade["max_favorable_r"] = r_now
            trade["max_favorable_price"] = pos.get("current")
        if profit_now > float(trade.get("max_favorable_profit") or 0):
            trade["max_favorable_profit"] = profit_now

    def _close_position_fraction(self, pos, fraction: float, reason: str) -> bool:
        ticket = pos.get("ticket")
        symbol = pos.get("symbol")
        volume = float(pos.get("volume") or 0)
        if not ticket or not symbol or volume <= 0:
            return False

        fraction = max(0.0, min(1.0, float(fraction or 0)))
        close_volume = None if fraction >= 0.999 else volume * fraction
        success = self.mt5.close_position_volume(ticket, close_volume, comment=reason)
        if success:
            self.add_logic(symbol, f"{reason.replace('_', ' ').title()}: closed {'all' if close_volume is None else f'{close_volume:.2f} lots'}", level="info")
            logger.info(f"{symbol}: {reason} closed volume={close_volume or volume}")
        return success

    def _current_position_for_symbol(self, symbol: str):
        """Return the freshest open position snapshot for a symbol."""
        try:
            for position in self.mt5.get_positions() or []:
                if self._symbols_match(position.get("symbol"), symbol):
                    return position
        except Exception as exc:
            logger.warning(f"{symbol}: Failed to refresh position snapshot: {exc}")
        return None

    def _normalize_symbol_key(self, symbol: str | None) -> str:
        """Normalize configured and broker symbols so suffixes do not bypass locks."""
        return "".join(ch for ch in str(symbol or "").upper() if ch.isalnum())

    def _symbols_match(self, broker_symbol: str | None, configured_symbol: str | None) -> bool:
        """Treat USDCHF, USDCHFm, and USDCHF.pro as the same lockout symbol."""
        broker_key = self._normalize_symbol_key(broker_symbol)
        configured_key = self._normalize_symbol_key(configured_symbol)
        if not broker_key or not configured_key:
            return False
        return broker_key == configured_key or broker_key.startswith(configured_key) or configured_key.startswith(broker_key)

    def _live_positions_for_symbol(self, symbol: str) -> list[dict]:
        """Return all live broker positions for a symbol, independent of engine memory."""
        if not symbol:
            return []
        try:
            return [
                position
                for position in (self.mt5.get_positions() or [])
                if self._symbols_match(position.get("symbol"), symbol)
            ]
        except Exception as exc:
            logger.warning(f"{symbol}: Failed to check live broker positions: {exc}")
            return []

    def _live_pending_orders_for_symbol(self, symbol: str) -> list[dict]:
        """Return all live broker pending orders for a symbol."""
        if not symbol:
            return []
        try:
            return [
                order
                for order in (self.mt5.get_pending_orders() or [])
                if self._symbols_match(order.get("symbol"), symbol)
            ]
        except Exception as exc:
            logger.warning(f"{symbol}: Failed to check live pending orders: {exc}")
            return []

    def _pending_order_direction(self, order: dict) -> str | None:
        order_type = str(order.get("type") or "").upper()
        if order_type.startswith("BUY"):
            return "BUY"
        if order_type.startswith("SELL"):
            return "SELL"
        return None

    def _guard_same_symbol_broker_exposure(self, signal: dict) -> tuple[bool, str]:
        """Block duplicate or opposite same-symbol exposure before placing any order."""
        symbol = signal.get("symbol")
        action = signal.get("action")
        if not symbol or action not in ["BUY", "SELL"]:
            return False, "missing symbol/action"

        pending_orders = self._live_pending_orders_for_symbol(symbol)
        same_pending = [
            order
            for order in pending_orders
            if self._pending_order_direction(order) == action
        ]
        opposite_pending = [
            order
            for order in pending_orders
            if self._pending_order_direction(order) not in [None, action]
        ]

        if same_pending:
            return False, f"same-symbol duplicate pending {action} order exists"

        if opposite_pending:
            failed = []
            for order in opposite_pending:
                ticket = order.get("ticket")
                if ticket and not self.mt5.cancel_order(ticket):
                    failed.append(ticket)
            if failed:
                return False, f"opposite pending order cancel failed: {failed}"
            self.add_logic(
                symbol,
                "Opposite pending order cancelled; waiting for next scan before new entry",
                level="warning",
            )
            return False, "opposite pending order cancelled; re-entry delayed"

        live_positions = self._live_positions_for_symbol(symbol)
        if not live_positions:
            return True, "no live same-symbol exposure"

        same_side = [pos for pos in live_positions if pos.get("type") == action]
        opposite_side = [pos for pos in live_positions if pos.get("type") != action]

        if same_side:
            return False, f"same-symbol duplicate {action} position exists"

        if not opposite_side:
            return True, "no opposite same-symbol exposure"

        total_profit = sum(float(pos.get("profit") or 0) for pos in opposite_side)
        signal_score = float(
            (signal.get("setup_score") or {}).get("score")
            or signal.get("confluence_score")
            or signal.get("conviction")
            or 0
        )
        reversal_confirmed = self._signal_has_hard_structure(signal) and signal_score >= 0.70

        if total_profit > 0 and reversal_confirmed:
            for pos in opposite_side:
                self._set_reversal_breakeven_at_entry(pos, "OPPOSITE_SIGNAL_LOCKOUT")
                ticket = pos.get("ticket")
                if ticket and not self.mt5.close_position(ticket):
                    return False, f"opposite {pos.get('type')} position close failed"
            self._register_trade_close(symbol)
            self._activate_reversal_shock_guard(
                symbol,
                action,
                "opposite signal closed profitable same-symbol exposure",
                profit=total_profit,
            )
            self.add_logic(
                symbol,
                "Opposite signal closed profitable same-symbol position; waiting for next scan before re-entry",
                level="warning",
            )
            return False, "opposite same-symbol position closed; re-entry delayed"

        return False, f"opposite same-symbol {opposite_side[0].get('type')} position exists"

    def _set_reversal_breakeven_at_entry(self, pos: dict, reason: str) -> bool:
        """On reversal risk, move SL to exact entry before any close/flip logic."""
        if not self.reversal_breakeven_at_entry_enabled:
            return False
        symbol = pos.get("symbol")
        ticket = pos.get("ticket")
        side = pos.get("type")
        entry = pos.get("entry")
        current_sl = pos.get("sl")
        if not ticket or not symbol or side not in ["BUY", "SELL"] or entry is None:
            return False

        try:
            entry = float(entry)
            current_sl = float(current_sl) if current_sl is not None else None
            if current_sl is not None:
                if side == "BUY" and current_sl >= entry:
                    return False
                if side == "SELL" and current_sl <= entry:
                    return False
        except Exception:
            return False

        if self.mt5.modify_position_sl(ticket, symbol, entry):
            trade = self.active_trades.get(symbol)
            if trade:
                trade["sl"] = entry
                trade["reversal_breakeven_sl"] = entry
                trade["reversal_breakeven_reason"] = reason
                trade["reversal_breakeven_at"] = datetime.now().isoformat()
            self.add_logic(symbol, f"Reversal protection: SL moved to breakeven at entry {entry:.5f}", level="warning")
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "REVERSAL_BREAKEVEN_AT_ENTRY",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "reason": reason,
            })
            return True

        self.add_logic(symbol, f"Reversal breakeven requested at entry {entry:.5f}, but broker rejected SL update", level="warning")
        return False

    def _signal_has_hard_structure(self, signal: dict) -> bool:
        """Require real structure, not context-only score, for aggressive execution paths."""
        setup = signal.get("setup_score") or {}
        components = setup.get("components") or []
        passed_keys = {
            str(component.get("key") or "").lower()
            for component in components
            if component.get("passed")
        }
        if passed_keys.intersection({"mss", "ob_fvg", "displacement", "false_move"}):
            return True
        if signal.get("market_structure_shift") or signal.get("order_block") or signal.get("gap_zone"):
            return True
        false_move = signal.get("false_move") or {}
        if str(false_move.get("type") or "").upper() in {"LIQUIDITY_SWEEP_REVERSAL", "FAILED_BREAKOUT"}:
            return True
        return False

    def _symbol_min_stop_distance(self, symbol: str) -> float:
        """Return broker minimum stop distance in price units, with a small buffer."""
        try:
            info = self._get_symbol_info(symbol)
            if not info:
                return 0.0
            point = float(getattr(info, "point", 0) or 0)
            stops_level = float(getattr(info, "trade_stops_level", 0) or 0)
            freeze_level = float(getattr(info, "trade_freeze_level", 0) or 0)
            level = max(stops_level, freeze_level)
            if point <= 0 or level <= 0:
                return 0.0
            return point * (level + 2)
        except Exception:
            return 0.0

    def _normalize_protective_sl(self, pos: dict, desired_sl: float | None) -> float | None:
        """Keep a protective SL on the correct side of current price and broker stop levels."""
        if desired_sl is None:
            return None
        symbol = pos.get("symbol")
        side = pos.get("type")
        entry = pos.get("entry")
        current = pos.get("current")
        if not symbol or side not in ["BUY", "SELL"] or entry is None or current is None:
            return desired_sl

        try:
            entry = float(entry)
            current = float(current)
            desired_sl = float(desired_sl)
            pip_size = self._get_pip_size(symbol) or 0.0
            min_distance = max(self._symbol_min_stop_distance(symbol), pip_size)
            if side == "BUY":
                return max(entry, min(desired_sl, current - min_distance))
            return min(entry, max(desired_sl, current + min_distance))
        except Exception:
            return desired_sl

    def _apply_partial_tp_protection(self, pos: dict) -> bool:
        """Retry and improve the post-partial protective SL as price gives room."""
        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not symbol or not trade or not trade.get("partial_tp_taken"):
            return False

        profile = self._management_profile(symbol, trade=trade)
        side = pos.get("type")
        current_sl = pos.get("sl")
        desired_sl = self._partial_tp_lock_sl(pos, profile)
        desired_sl = self._normalize_protective_sl(pos, desired_sl)
        if desired_sl is None or side not in ["BUY", "SELL"]:
            return False

        ticket = pos.get("ticket")
        if not ticket:
            return False

        try:
            current_sl = float(current_sl) if current_sl is not None else None
            desired_sl = float(desired_sl)
            pip_size = self._get_pip_size(symbol) or 0.0
            min_improvement = max(pip_size * 0.25, 0.0)
            already_applied = bool(trade.get("partial_tp_sl_applied"))

            if current_sl is not None:
                if side == "BUY":
                    if current_sl >= desired_sl - min_improvement:
                        trade["partial_tp_lock_sl"] = current_sl
                        trade["partial_tp_sl_applied"] = True
                        return False
                    if already_applied and desired_sl <= current_sl + min_improvement:
                        return False
                else:
                    if current_sl <= desired_sl + min_improvement:
                        trade["partial_tp_lock_sl"] = current_sl
                        trade["partial_tp_sl_applied"] = True
                        return False
                    if already_applied and desired_sl >= current_sl - min_improvement:
                        return False
        except Exception:
            pass

        if self.mt5.modify_position_sl(ticket, symbol, desired_sl):
            trade["sl"] = desired_sl
            trade["partial_tp_lock_sl"] = desired_sl
            trade["partial_tp_sl_applied"] = True
            trade["partial_tp_sl_applied_at"] = datetime.now().isoformat()
            self.add_logic(symbol, f"Partial TP protection active: SL locked at {desired_sl:.5f}", level="info")
            self.logger._save_log({
                "timestamp": trade["partial_tp_sl_applied_at"],
                "event": "PARTIAL_TP_SL_LOCK",
                "symbol": symbol,
                "ticket": ticket,
                "sl": desired_sl,
                "profile": profile.get("name"),
            })
            return True

        trade["partial_tp_sl_retry_at"] = datetime.now().isoformat()
        self.add_logic(symbol, f"Partial TP protection pending: broker rejected SL {desired_sl:.5f}; will retry", level="warning")
        return False

    def _apply_breakeven_protection(self, pos: dict) -> bool:
        """Move SL to breakeven once the trade reaches the configured R trigger."""
        if not self.breakeven_protection_enabled:
            return False

        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol) if symbol else None
        if not symbol or not trade or trade.get("breakeven_sl_applied"):
            return False

        r_now = self._position_r_multiple(pos)
        if r_now is None or r_now < self.breakeven_trigger_r:
            return False

        ticket = pos.get("ticket")
        side = pos.get("type")
        entry = pos.get("entry")
        current_sl = pos.get("sl")
        if not ticket or side not in ["BUY", "SELL"] or entry is None:
            return False

        try:
            entry = float(entry)
            pip_size = self._get_pip_size(symbol) or 0.0
            lock_distance = max(0.0, self.breakeven_lock_pips) * pip_size
            desired_sl = entry + lock_distance if side == "BUY" else entry - lock_distance
            desired_sl = self._normalize_protective_sl(pos, desired_sl)
            if desired_sl is None:
                return False
            current_sl = float(current_sl) if current_sl is not None else None
            if current_sl is not None:
                if side == "BUY" and current_sl >= desired_sl:
                    trade["breakeven_sl_applied"] = True
                    return False
                if side == "SELL" and current_sl <= desired_sl:
                    trade["breakeven_sl_applied"] = True
                    return False
        except Exception:
            return False

        if self.mt5.modify_position_sl(ticket, symbol, desired_sl):
            trade["sl"] = desired_sl
            trade["breakeven_sl"] = desired_sl
            trade["breakeven_sl_applied"] = True
            trade["breakeven_sl_applied_at"] = datetime.now().isoformat()
            self.add_logic(symbol, f"Breakeven protection active: SL moved to {desired_sl:.5f} at {r_now:.2f}R", level="info")
            self.logger._save_log({
                "timestamp": trade["breakeven_sl_applied_at"],
                "event": "BREAKEVEN_SL_LOCK",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "sl": desired_sl,
                "r": r_now,
                "trigger_r": self.breakeven_trigger_r,
                "lock_pips": self.breakeven_lock_pips,
            })
            return True
        return False

    def _apply_first_profit_breakeven(self, pos: dict) -> bool:
        """Move SL to entry as soon as a trade first proves minimally profitable."""
        if not getattr(self, "first_profit_breakeven_enabled", True):
            return False

        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol) if symbol else None
        if not symbol or not trade or trade.get("first_profit_be_applied"):
            return False

        r_now = self._position_r_multiple(pos)
        horizon = self._trade_horizon_profile_name(self._horizon_value(trade))
        trigger_r = (
            self.first_profit_breakeven_trigger_r_scalp
            if horizon == "SCALP"
            else self.first_profit_breakeven_trigger_r
        )
        if r_now is None or r_now < trigger_r:
            return False

        ticket = pos.get("ticket")
        side = pos.get("type")
        entry = pos.get("entry")
        current_sl = pos.get("sl")
        if not ticket or side not in ["BUY", "SELL"] or entry is None:
            return False

        try:
            entry = float(entry)
            desired_sl = self._normalize_protective_sl(pos, entry)
            if desired_sl is None:
                return False
            current_sl = float(current_sl) if current_sl is not None else None
            if current_sl is not None:
                if side == "BUY" and current_sl >= desired_sl:
                    trade["first_profit_be_applied"] = True
                    return False
                if side == "SELL" and current_sl <= desired_sl:
                    trade["first_profit_be_applied"] = True
                    return False
        except Exception:
            return False

        if self.mt5.modify_position_sl(ticket, symbol, desired_sl):
            applied_at = datetime.now().isoformat()
            trade["sl"] = desired_sl
            trade["first_profit_be_sl"] = desired_sl
            trade["first_profit_be_applied"] = True
            trade["first_profit_be_applied_at"] = applied_at
            self.add_logic(symbol, f"First-profit breakeven active: SL moved to entry {desired_sl:.5f} at {r_now:.2f}R", level="info")
            self.logger._save_log({
                "timestamp": applied_at,
                "event": "FIRST_PROFIT_BREAKEVEN",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "sl": desired_sl,
                "r": r_now,
                "trigger_r": trigger_r,
                "horizon_profile": horizon,
            })
            return True
        return False

    def _apply_partial_take_profit(self, pos):
        """Bank part of the position once the trade reaches the configured R target."""
        if not self.partial_tp_enabled:
            return False
        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not trade or trade.get("partial_tp_taken"):
            return False

        profile = self._management_profile(symbol, trade=trade)
        r_now = self._position_r_multiple(pos)
        partial_trigger_r = float(profile.get("partial_tp_trigger_r", self.partial_tp_trigger_r))
        if r_now is None or r_now < partial_trigger_r:
            return False

        if self._close_position_fraction(pos, self.partial_tp_close_pct, "PARTIAL_TP"):
            trade["partial_tp_taken"] = True
            trade["partial_tp_at"] = datetime.now().isoformat()
            trade["partial_tp_r"] = r_now
            trade["partial_tp_profit"] = pos.get("profit")
            symbol = pos.get("symbol")
            remaining_pos = self._current_position_for_symbol(symbol) or pos
            lock_sl = self._partial_tp_lock_sl(remaining_pos, profile)
            lock_sl = self._normalize_protective_sl(remaining_pos, lock_sl)
            trade["partial_tp_lock_sl"] = lock_sl
            trade["partial_tp_sl_applied"] = False
            if lock_sl is not None and symbol:
                self._apply_partial_tp_protection(remaining_pos)
            new_tp = self._extend_runner_tp_after_partial(remaining_pos, trade, profile)
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "PARTIAL_TP",
                "symbol": symbol,
                "r": r_now,
                "profile": profile.get("name"),
                "symbol_profile": profile.get("symbol_profile"),
                "horizon_profile": profile.get("horizon_profile"),
                "trigger_r": partial_trigger_r,
                "profit": pos.get("profit"),
                "close_pct": self.partial_tp_close_pct,
                "lock_pips": profile.get("partial_tp_lock_pips", self.partial_tp_lock_pips),
                "lock_sl": lock_sl,
                "sl_applied": bool(trade.get("partial_tp_sl_applied")),
                "remaining_volume": remaining_pos.get("volume"),
                "runner_tp": new_tp,
            })
            return True
        return False

    def _partial_tp_lock_sl(self, pos: dict, profile: dict):
        """Return SL for post-partial protection without forcing a too-tight stop."""
        symbol = pos.get("symbol")
        side = pos.get("type")
        entry = pos.get("entry")
        current = pos.get("current")
        if not symbol or side not in ["BUY", "SELL"] or entry is None or current is None:
            return None

        try:
            entry = float(entry)
            current = float(current)
            pip_size = self._get_pip_size(symbol)
            if not pip_size:
                return entry

            lock_pips = max(0.0, float(profile.get("partial_tp_lock_pips", self.partial_tp_lock_pips)))
            lock_distance = lock_pips * pip_size
            if lock_distance <= 0:
                return entry

            if side == "BUY":
                return entry + lock_distance

            return entry - lock_distance
        except Exception:
            return None

    def _extend_runner_tp_after_partial(self, pos: dict, trade: dict, profile: dict):
        """Immediately assign a fresh TP for the remaining runner after partial profit."""
        if not (self.trailing_tp_enabled and self.partial_tp_extend_enabled):
            return None

        ticket = pos.get("ticket")
        symbol = pos.get("symbol")
        side = pos.get("type") or trade.get("action")
        entry = pos.get("entry")
        current_tp = pos.get("tp") or trade.get("tp")
        if not ticket or not symbol or side not in ["BUY", "SELL"] or entry is None or current_tp is None:
            return None

        try:
            entry = float(entry)
            current_tp = float(current_tp)
            tp_distance = abs(current_tp - entry)
            if tp_distance <= 0:
                return None

            extend_pct = float(profile.get("partial_tp_extend_pct", self.partial_tp_extend_pct))
            if extend_pct <= 0:
                return None

            extension = tp_distance * extend_pct
            new_tp = current_tp + extension if side == "BUY" else current_tp - extension
            if self.mt5.modify_position_tp(ticket, symbol, new_tp):
                trade["tp"] = new_tp
                trade["partial_runner_tp"] = new_tp
                trade["partial_runner_tp_at"] = datetime.now().isoformat()
                trade["last_tp_extended_at"] = datetime.now().isoformat()
                self.logger._save_log({
                    "timestamp": datetime.now().isoformat(),
                    "event": "PARTIAL_TP_RUNNER_TP",
                    "symbol": symbol,
                    "side": side,
                    "old_tp": current_tp,
                    "new_tp": new_tp,
                    "extension_pct": extend_pct,
                    "profile": profile.get("name"),
                    "symbol_profile": profile.get("symbol_profile"),
                    "horizon_profile": profile.get("horizon_profile"),
                })
                self.add_logic(symbol, f"Partial TP runner target extended to {new_tp:.5f}", level="info")
                return new_tp
        except Exception as e:
            self.add_logic(symbol, f"Partial TP runner target extension failed: {e}", level="warning")
        return None

    def _apply_max_adverse_exit(self, pos):
        """Cut bad trades before the full stop when adverse movement reaches a hard R limit."""
        if not self.max_adverse_exit_enabled:
            return False
        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not trade or trade.get("max_adverse_exit_done"):
            return False

        profile = self._management_profile(symbol, trade=trade)
        r_now = self._position_r_multiple(pos)
        max_adverse_r = float(profile.get("max_adverse_r", self.max_adverse_r))
        if r_now is None or r_now > -abs(max_adverse_r):
            return False

        if self._close_position_fraction(pos, 1.0, "MAX_ADVERSE_EXIT"):
            trade["max_adverse_exit_done"] = True
            trade["max_adverse_exit_at"] = datetime.now().isoformat()
            trade["max_adverse_exit_r"] = r_now
            self._activate_reversal_shock_guard(
                symbol,
                trade.get("action") or pos.get("type"),
                "max adverse exit",
                r_value=r_now,
                profit=pos.get("profit"),
            )
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "MAX_ADVERSE_EXIT",
                "symbol": symbol,
                "r": r_now,
                "profile": profile.get("name"),
                "symbol_profile": profile.get("symbol_profile"),
                "horizon_profile": profile.get("horizon_profile"),
                "threshold_r": -abs(max_adverse_r),
                "profit": pos.get("profit"),
            })
            return True
        return False

    def _apply_news_ladder(self, pos):
        """Add controlled follow-up positions only after a news move confirms in profit."""
        if not (self.news_mode_enabled and self.news_ladder_enabled):
            return

        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not symbol or not trade:
            return
        profile = self._management_profile(symbol, trade=trade)
        if profile.get("allow_news_ladder") is False:
            return
        if self.killed.get("all") or self.killed.get(symbol):
            return

        addons = trade.setdefault("news_ladder_addons", [])
        if len(addons) >= self.news_ladder_max_addons:
            return

        r_now = self._position_r_multiple(pos)
        if r_now is None:
            return
        if r_now < self.news_ladder_min_r and not trade.get("partial_tp_taken"):
            return

        last_addon_at = trade.get("last_news_ladder_at")
        if last_addon_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(last_addon_at)).total_seconds()
                if elapsed < self.news_ladder_cooldown_seconds:
                    return
            except Exception:
                pass

        try:
            from technical_analysis import detect_news_move, detect_spread_safety
            news_move = detect_news_move(symbol, self.timeframe)
            spread = detect_spread_safety(symbol)
        except Exception as e:
            self.add_logic(symbol, f"News ladder check failed: {e}", level="warning")
            return

        trade_news = trade.get("news_move") or {}
        mode = news_move.get("mode") or trade_news.get("mode") or "NORMAL"
        if mode not in ["FOLLOW_RETEST", "NORMAL"]:
            return
        if spread.get("safe") is False or news_move.get("safe") is False:
            self.add_logic(symbol, f"News ladder waiting: {news_move.get('description', 'spread/event state not safe')}", level="warning")
            return

        side = str(pos.get("type") or trade.get("action") or "").upper()
        if side not in ["BUY", "SELL"]:
            return

        current_volume = float(pos.get("volume") or trade.get("initial_volume") or 0)
        base_volume = float(trade.get("initial_volume") or current_volume or self.volume)
        raw_addon_volume = min(base_volume, current_volume) * self.news_ladder_volume_pct
        if raw_addon_volume < self._get_symbol_min_lot(symbol):
            self.add_logic(symbol, "News ladder skipped: add-on volume would round up to broker minimum", level="warning")
            return
        addon_volume = self._round_symbol_lot(symbol, raw_addon_volume)
        if addon_volume <= 0:
            return

        can_trade, reason = self._can_trade()
        if not can_trade:
            self.add_logic(symbol, f"News ladder blocked: {reason}", level="warning")
            return

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.add_logic(symbol, "News ladder blocked: no tick data", level="warning")
            return

        entry = float(tick.ask if side == "BUY" else tick.bid)
        sl = trade.get("sl") or pos.get("sl")
        tp = trade.get("tp") or pos.get("tp")
        if sl is None or tp is None:
            self.add_logic(symbol, "News ladder blocked: missing SL/TP", level="warning")
            return

        if side == "BUY":
            order_id = self.mt5.place_buy_order(symbol, addon_volume, entry, sl, tp)
        else:
            order_id = self.mt5.place_sell_order(symbol, addon_volume, entry, sl, tp)

        if not order_id:
            order_error = getattr(self.mt5, "last_order_error", None) or "MT5 returned no order id"
            self.add_logic(symbol, f"News ladder add-on failed: {order_error}", level="warning")
            return

        risk_amount = self._calculate_risk_amount(symbol, entry, sl, addon_volume)
        addon = {
            "timestamp": datetime.now().isoformat(),
            "order_id": order_id,
            "action": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "volume": addon_volume,
            "risk": risk_amount,
            "r_trigger": r_now,
            "news_mode": mode,
        }
        addons.append(addon)
        trade["last_news_ladder_at"] = addon["timestamp"]
        trade["risk"] = float(trade.get("risk") or 0) + risk_amount
        trade["volume"] = float(trade.get("volume") or 0) + addon_volume
        self.add_logic(symbol, f"News ladder add-on placed #{len(addons)} at {r_now:.2f}R vol={addon_volume:.2f}", level="info")
        self.logger._save_log({
            "timestamp": addon["timestamp"],
            "event": "NEWS_LADDER_ADDON",
            "symbol": symbol,
            "order_id": order_id,
            "action": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "volume": addon_volume,
            "risk": risk_amount,
            "r_trigger": r_now,
            "news_mode": mode,
        })

    def _apply_reverse_profit_exit(self, pos):
        """Close green trades when price reverses sharply from max favorable excursion."""
        if not self.reverse_profit_exit_enabled:
            return False
        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not trade or trade.get("reverse_exit_done"):
            return False

        r_now = self._position_r_multiple(pos)
        if r_now is None:
            return False
        profile = self._management_profile(symbol, trade=trade)
        max_r = float(trade.get("max_favorable_r") or 0)
        reverse_min_r = float(profile.get("reverse_profit_min_r", self.reverse_profit_min_r))
        if max_r < reverse_min_r:
            return False

        giveback = max_r - r_now
        giveback_pct = float(profile.get("reverse_profit_giveback_pct", self.reverse_profit_giveback_pct))
        giveback_trigger = max_r * max(0.0, min(1.0, giveback_pct))
        partial_taken = bool(trade.get("partial_tp_taken"))
        partial_lock_r = float(profile.get("reverse_after_partial_lock_r", self.reverse_after_partial_lock_r))
        near_breakeven_after_partial = partial_taken and r_now <= partial_lock_r and r_now > 0
        reversed_from_peak = r_now > 0 and giveback >= giveback_trigger

        if not (reversed_from_peak or near_breakeven_after_partial):
            return False

        profile_close_pct = float(profile.get("reverse_profit_close_pct", self.reverse_profit_close_pct))
        close_pct = profile_close_pct if partial_taken else min(profile_close_pct, 0.5)
        self._set_reversal_breakeven_at_entry(pos, "REVERSE_PROFIT_EXIT")
        if self._close_position_fraction(pos, close_pct, "REVERSE_PROFIT_EXIT"):
            trade["reverse_exit_done"] = True
            trade["reverse_exit_at"] = datetime.now().isoformat()
            trade["reverse_exit_r"] = r_now
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "REVERSE_PROFIT_EXIT",
                "symbol": symbol,
                "r": r_now,
                "max_r": max_r,
                "giveback_r": giveback,
                "profile": profile.get("name"),
                "symbol_profile": profile.get("symbol_profile"),
                "horizon_profile": profile.get("horizon_profile"),
                "trigger_r": reverse_min_r,
                "giveback_pct": giveback_pct,
                "close_pct": close_pct,
                "profit": pos.get("profit"),
            })
            return True
        return False

    def _apply_trailing_stop(self, pos):
        """Lock profit after trigger, then step-trail as price keeps moving favorably."""
        try:
            ticket = pos.get("ticket")
            symbol = pos.get("symbol")
            side = pos.get("type")
            entry = pos.get("entry")
            current = pos.get("current")
            sl = pos.get("sl")
            tp = pos.get("tp")

            if not ticket or not symbol or entry is None or current is None or sl is None or tp is None:
                return

            pip_size = self._get_pip_size(symbol)
            if not pip_size:
                return

            tp_distance = abs(tp - entry)
            if tp_distance <= 0:
                return

            trade = self.active_trades.get(symbol, {})
            profile = self._management_profile(symbol, trade=trade)
            trigger_pct = float(profile.get("trailing_stop_trigger_pct", self.trailing_stop_trigger_pct))
            lock_pips = float(profile.get("trailing_stop_lock_pips", self.trailing_stop_lock_pips))
            step_pct = float(profile.get("trailing_stop_step_pct", self.trailing_stop_step_pct))
            min_step_pips = float(profile.get("trailing_stop_min_step_pips", self.trailing_stop_min_step_pips))

            break_even_threshold = entry + (tp_distance * trigger_pct) if side == "BUY" else entry - (tp_distance * trigger_pct)
            lock_distance = max(0.0, lock_pips) * pip_size
            min_step = max(0.0, min_step_pips) * pip_size
            trail_gap = tp_distance * max(0.0, step_pct)

            if side == "BUY":
                if current < break_even_threshold:
                    return
                profit_lock_sl = entry + lock_distance
                step_trail_sl = current - trail_gap
                new_sl = max(profit_lock_sl, step_trail_sl)
                new_sl = min(new_sl, current - pip_size)
                if new_sl > sl + min_step:
                    if self.mt5.modify_position_sl(ticket, symbol, new_sl):
                        if symbol in self.active_trades:
                            self.active_trades[symbol]["sl"] = new_sl
                        logger.info(f"Trailing stop[{profile.get('name')}]: Locked {symbol} BUY profit at {new_sl:.5f}")
            elif side == "SELL":
                if current > break_even_threshold:
                    return
                profit_lock_sl = entry - lock_distance
                step_trail_sl = current + trail_gap
                new_sl = min(profit_lock_sl, step_trail_sl)
                new_sl = max(new_sl, current + pip_size)
                if new_sl < sl - min_step:
                    if self.mt5.modify_position_sl(ticket, symbol, new_sl):
                        if symbol in self.active_trades:
                            self.active_trades[symbol]["sl"] = new_sl
                        logger.info(f"Trailing stop[{profile.get('name')}]: Locked {symbol} SELL profit at {new_sl:.5f}")
        except Exception as e:
            logger.error(f"Trailing stop error: {e}")

    def _apply_trailing_take_profit(self, pos):
        """Extend take-profit when price reaches a configured percentage of the current TP distance."""
        if not self.trailing_tp_enabled:
            return
        try:
            ticket = pos.get("ticket")
            symbol = pos.get("symbol")
            side = pos.get("type")
            entry = pos.get("entry")
            current = pos.get("current")
            tp = pos.get("tp")

            if not ticket or not symbol or entry is None or current is None or tp is None:
                return

            trade_state = self.active_trades.get(symbol, {})
            last_extended_at = trade_state.get("last_tp_extended_at")
            if last_extended_at:
                try:
                    elapsed = (datetime.now() - datetime.fromisoformat(last_extended_at)).total_seconds()
                    if elapsed < self.trailing_tp_cooldown_seconds:
                        return
                except Exception:
                    pass

            tp_distance = abs(tp - entry)
            if tp_distance <= 0:
                return

            if side == "BUY":
                trigger = entry + (tp_distance * self.trailing_tp_trigger_pct)
                if current < trigger:
                    return
                new_tp = tp + (tp_distance * self.trailing_tp_extension_pct)
            elif side == "SELL":
                trigger = entry - (tp_distance * self.trailing_tp_trigger_pct)
                if current > trigger:
                    return
                new_tp = tp - (tp_distance * self.trailing_tp_extension_pct)
            else:
                return

            if self.mt5.modify_position_tp(ticket, symbol, new_tp):
                if symbol in self.active_trades:
                    self.active_trades[symbol]["tp"] = new_tp
                    self.active_trades[symbol]["last_trailed_tp"] = new_tp
                    self.active_trades[symbol]["last_tp_extended_at"] = datetime.now().isoformat()
                self.add_logic(symbol, f"Trailing TP extended to {new_tp:.5f}", level="info")
        except Exception as e:
            logger.error(f"Trailing take-profit error: {e}")

    def get_status(self):
        """Get bot status"""
        try:
            account = self.mt5.get_account_info()
            positions = self.get_enriched_positions() or []
            market_open = self._is_market_open()
            bot_score = self._compute_bot_score(market_open=market_open)
            current_open_risk, open_risk_details = self._calculate_exposure_details()

            equity = account.get("equity") if account else None
            max_open_risk = equity * self.max_exposure_pct if equity is not None else None
            open_risk_pct = (current_open_risk / equity) if equity else None
            if equity is not None:
                if self.start_equity is None:
                    self.start_equity = equity
                if self.peak_equity is None or equity > self.peak_equity:
                    self.peak_equity = equity

            daily_profit = None
            floating_drawdown = None
            floating_profit = sum(float(pos.get("profit") or 0) for pos in positions)
            realized_profit = self._get_realized_profit_today()
            net_profit = realized_profit + floating_profit
            loss_brake = self._get_loss_brake_state(equity)
            if equity is not None and self.start_equity is not None:
                daily_profit = equity - self.start_equity
            if equity is not None and self.peak_equity is not None:
                floating_drawdown = max(0.0, self.peak_equity - equity)

            return {
                "running": self.is_running,
                "connected": self.mt5.is_connected,
                "market_open": market_open,
                "bot_score": bot_score,
                "symbols": self._refresh_scan_symbols_from_env(),
                "volume": self.volume,
                "position_sizing_mode": self.position_sizing_mode,
                "account_profile": {
                    "enabled": self.dynamic_account_profile_enabled,
                    "name": self.account_profile_name,
                    "equity": self.account_profile_equity,
                    "last_applied_at": self.dynamic_profile_last_applied_at,
                    "volume": self.volume,
                    "min_execution_grade": self.min_execution_grade,
                    "min_trade_readiness_score": self.min_trade_readiness_score,
                    "min_professional_setup_score": self.min_professional_score,
                    "market_execution_score_threshold": self.market_execution_score_threshold,
                    "max_auto_min_lot": self.max_auto_min_lot,
                    "small_account_mode": {
                        "enabled": self.small_account_mode_enabled,
                        "active": self.small_account_active,
                        "threshold": self.small_account_threshold,
                        "allow_metals": self.small_account_allow_metals,
                        "allow_crypto": self.small_account_allow_crypto,
                        "allow_stocks": self.small_account_allow_stocks,
                    },
                },
                "account": account,
                "balance": account.get("balance") if account else None,
                "equity": equity,
                "free_margin": account.get("free_margin") if account else None,
                "margin_level": account.get("margin_level") if account else None,
                "daily_profit": daily_profit,
                "floating_profit": floating_profit,
                "realized_profit": realized_profit,
                "net_profit": net_profit,
                "floating_drawdown": floating_drawdown,
                "current_open_risk": current_open_risk,
                "max_open_risk": max_open_risk,
                "open_risk_pct": open_risk_pct,
                "max_open_risk_pct": self.max_exposure_pct,
                "open_risk_details": open_risk_details,
                "loss_brake": loss_brake,
                "positions": positions,
                "active_trades": len(self.active_trades),
                "max_active_trades_total": self.max_active_trades_total,
                "scan": {
                    "interval_seconds": self.scan_interval_seconds,
                    "engine_loop_sleep_seconds": self.engine_loop_sleep_seconds,
                    "on_new_candle": self.scan_on_new_candle,
                    "timeframe_minutes": self.scan_timeframe_minutes,
                    "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
                    "next_scan_at": self.next_scan_at.isoformat() if self.next_scan_at else None,
                    "seconds_until_next_scan": self._seconds_until_next_scan(),
                    "last_signal_count": self.last_scan_signal_count,
                    "duplicate_signal_cooldown_seconds": self.duplicate_signal_cooldown_seconds,
                    "trade_cooldown_minutes": self.trade_cooldown_minutes,
                    "cooldown_override_enabled": self.cooldown_override_enabled,
                    "cooldown_override_min_grade": self.cooldown_override_min_grade,
                    "cooldown_override_min_score": self.cooldown_override_min_score,
                    "cooldown_override_min_conviction": self.cooldown_override_min_conviction,
                "max_active_trades_total": self.max_active_trades_total,
                "max_trades_per_symbol": self.max_trades_per_symbol,
                "early_entry_enabled": self.early_entry_enabled,
                "early_entry_min_score": self.early_entry_min_score,
                "reversal_shock_guard": self.reversal_shock_guard_enabled,
                "reversal_shock_cooldown_minutes": self.reversal_shock_cooldown_minutes,
                "reversal_shock_xau_cooldown_minutes": self.reversal_shock_xau_cooldown_minutes,
                "opposing_signal_profit_exit": self.opposing_signal_profit_exit_enabled,
                "opposing_signal_min_r": self.opposing_signal_min_r,
                "opposing_signal_min_score": self.opposing_signal_min_score,
                "execution_symbols": self._refresh_execution_symbols_from_env(),
                "armed_confirmation_enabled": self.armed_confirmation_enabled,
                "armed_required_scans": self.armed_required_scans,
                "armed_ttl_seconds": self.armed_ttl_seconds,
                "armed_min_score": self.armed_min_score,
                "armed_active_count": len(self.armed_signals),
            },
                "trade_management": {
                    "trailing_sl": True,
                    "trailing_sl_trigger_pct": self.trailing_stop_trigger_pct,
                    "trailing_sl_lock_pips": self.trailing_stop_lock_pips,
                    "trailing_sl_step_pct": self.trailing_stop_step_pct,
                    "trailing_sl_min_step_pips": self.trailing_stop_min_step_pips,
                    "symbol_profiles_enabled": self.symbol_profiles_enabled,
                    "instrument_profiles_enabled": self.instrument_profiles_enabled,
                    "symbol_profiles": self._configured_symbol_profiles(),
                    "trade_horizon_profiles": self.trade_horizon_profiles_enabled,
                    "horizon_profile_mode": self.horizon_profile_mode,
                    "scalp_profile": self.scalp_profile_enabled,
                    "intraday_profile": self.intraday_profile_enabled,
                    "swing_profile": self.swing_profile_enabled,
                    "management_profiles": self._configured_management_profiles(),
                    "trailing_tp": self.trailing_tp_enabled,
                    "trailing_tp_trigger_pct": self.trailing_tp_trigger_pct,
                    "trailing_tp_extension_pct": self.trailing_tp_extension_pct,
                    "trailing_tp_cooldown_seconds": self.trailing_tp_cooldown_seconds,
                    "partial_tp_extend": self.partial_tp_extend_enabled,
                    "partial_tp_extend_pct": self.partial_tp_extend_pct,
                    "partial_tp": self.partial_tp_enabled,
                    "partial_tp_trigger_r": self.partial_tp_trigger_r,
                    "partial_tp_close_pct": self.partial_tp_close_pct,
                    "partial_tp_lock_pips": self.partial_tp_lock_pips,
                    "first_profit_breakeven": self.first_profit_breakeven_enabled,
                    "first_profit_breakeven_trigger_r": self.first_profit_breakeven_trigger_r,
                    "first_profit_breakeven_trigger_r_scalp": self.first_profit_breakeven_trigger_r_scalp,
                    "max_adverse_exit": self.max_adverse_exit_enabled,
                    "max_adverse_r": self.max_adverse_r,
                    "reverse_profit_exit": self.reverse_profit_exit_enabled,
                    "reverse_profit_min_r": self.reverse_profit_min_r,
                    "reverse_profit_giveback_pct": self.reverse_profit_giveback_pct,
                    "reverse_profit_close_pct": self.reverse_profit_close_pct,
                    "reverse_after_partial_lock_r": self.reverse_after_partial_lock_r,
                    "opposing_signal_profit_exit": self.opposing_signal_profit_exit_enabled,
                    "opposing_signal_min_r": self.opposing_signal_min_r,
                    "opposing_signal_min_score": self.opposing_signal_min_score,
                    "professional_gate": self.professional_gate_enabled,
                    "ict_mode": self.ict_mode_enabled,
                    "ict_min_setup_score": self.ict_min_setup_score,
                    "ict_min_confluence": self.ict_min_confluence,
                    "min_execution_grade": self.min_execution_grade,
                    "min_professional_setup_score": self.min_professional_score,
                    "min_professional_conviction": self.min_professional_conviction,
                    "min_trade_readiness_score": self.min_trade_readiness_score,
                    "false_move_detection": self.false_move_detection_enabled,
                    "news_mode": self.news_mode_enabled,
                    "news_block_unsafe": self.news_block_unsafe,
                    "news_risk_multiplier": self.news_risk_multiplier,
                    "news_ladder": self.news_ladder_enabled,
                    "news_ladder_max_addons": self.news_ladder_max_addons,
                    "news_ladder_min_r": self.news_ladder_min_r,
                    "news_ladder_volume_pct": self.news_ladder_volume_pct,
                    "news_ladder_cooldown_seconds": self.news_ladder_cooldown_seconds,
                },
                "logic_feed": self.logic_feed[-20:],
                "signal_history": self.signal_history[-50:],
                "future_trades": self.future_trades[-50:],
            }
        except Exception as e:
            logger.error(f"Status error: {e}")
            return None


# Global engine instance
engine = None


def start_engine():
    """Initialize and start the engine"""
    global engine
    if engine is None:
        engine = TradingEngine()
        if engine.connect():
            thread = threading.Thread(target=engine.start, daemon=True)
            thread.start()
            return True
    return False


def stop_engine():
    """Stop the engine"""
    global engine
    if engine:
        engine.stop()
        engine.disconnect()
        return True
    return False


def get_engine():
    """Get the current engine instance"""
    global engine
    return engine
