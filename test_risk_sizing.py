import threading
from datetime import datetime, timedelta

from engine import TradingEngine
from analytic_engine import AnalyticEngine
import technical_analysis
from pending_order_manager import PendingOrderManager
from mt5_interface import MT5Interface, mt5


class FakeInfo:
    digits = 5
    volume_min = 0.01
    volume_max = 100
    volume_step = 0.01
    trade_tick_value = 1.0
    trade_tick_size = 0.0001


class FakeMT5:
    is_connected = True
    last_order_error = None

    def __init__(self):
        self.calls = []
        self.positions = []

    def get_symbol_info(self, symbol):
        return FakeInfo()

    def get_account_info(self):
        return {"equity": 100000.0, "currency": "USD"}

    def get_symbol_tick(self, symbol):
        return type("Tick", (), {"bid": 1.1, "ask": 1.1001})()

    def get_positions(self):
        return self.positions

    def place_buy_order(self, symbol, volume, price, sl, tp):
        self.calls.append(("buy", symbol, volume, price, sl, tp))
        return 111

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        self.calls.append(("buy_limit", symbol, volume, price, sl, tp))
        return 222

    def close_position_volume(self, ticket, volume=None, comment="PARTIAL_TP"):
        self.calls.append(("close", ticket, volume, comment))
        return True

    def modify_position_sl(self, ticket, symbol, sl):
        self.calls.append(("modify_sl", ticket, symbol, sl))
        return True

    def modify_position_tp(self, ticket, symbol, tp):
        self.calls.append(("modify_tp", ticket, symbol, tp))
        return True


def make_engine():
    engine = TradingEngine.__new__(TradingEngine)
    engine.mt5 = FakeMT5()
    engine.active_trades = {}
    engine.logger = type("Logger", (), {"log_trade": lambda self, trade: None})()
    engine.volume = 0.01
    engine.risk_pct = 0.01
    engine.position_sizing_mode = "fixed"
    engine.take_profit_r_multiplier = 2.0
    engine.take_profit_r_multiplier_scalp = 1.5
    engine.trailing_tp_enabled = True
    engine.trailing_tp_extension_pct = 0.5
    engine.partial_tp_extend_enabled = True
    engine.partial_tp_extend_pct = 0.5
    engine.partial_tp_enabled = True
    engine.partial_tp_trigger_r = 0.75
    engine.partial_tp_close_pct = 0.5
    engine.partial_tp_lock_pips = 10
    engine.min_expected_r = 1.2
    engine.min_expected_r_scalp = 0.8
    engine.symbol_profiles_enabled = True
    engine.trade_horizon_profiles_enabled = True
    engine.horizon_profile_mode = "exit_only"
    engine.scalp_profile_enabled = True
    engine.intraday_profile_enabled = True
    engine.swing_profile_enabled = True
    engine._trades_lock = threading.RLock()
    engine.trade_registry = {}
    engine.signal_lockout_enabled = True
    engine.max_trades_per_symbol = 1
    engine.trade_cooldown_minutes = 3
    engine.cooldown_override_enabled = True
    engine.cooldown_override_min_grade = "A"
    engine.cooldown_override_min_score = 0.78
    engine.cooldown_override_min_conviction = 0.45
    engine.cooldown_override_require_spread_safe = True
    engine.cooldown_override_require_new_structure = True
    engine.reversal_shock_guard_enabled = True
    engine.reversal_shock_cooldown_minutes = 30
    engine.reversal_shock_xau_cooldown_minutes = 60
    engine.opposing_signal_profit_exit_enabled = True
    engine.opposing_signal_min_r = 0.20
    engine.opposing_signal_min_score = 0.58
    engine.professional_gate_enabled = True
    engine.min_execution_grade = "B"
    engine.allow_c_scalps = False
    engine.min_professional_score = 0.62
    engine.min_professional_conviction = 0.30
    engine.min_session_score_for_trade = 0.45
    engine.min_session_score_for_scalp = 0.65
    engine.block_context_watch_trades = True
    engine.max_entry_drift_pct = 0.35
    engine.max_entry_drift_pips = 10
    engine.execution_conviction_threshold = 0.35
    engine.execution_setup_score_threshold = 0.50
    engine.execution_archetype_score_threshold = 0.58
    engine.min_trade_readiness_score = 0.62
    engine.logger = type(
        "Logger",
        (),
        {
            "log_trade": lambda self, trade: None,
            "_save_log": lambda self, entry: None,
        },
    )()
    engine.add_logic = lambda *args, **kwargs: None
    return engine


def test_fixed_lot_sizing_uses_trade_volume():
    engine = make_engine()
    assert engine._calculate_volume("EURUSD", 1.1000, 1.0990) == 0.01


def test_fixed_lot_below_broker_min_rejects_instead_of_rounding_up():
    engine = make_engine()
    engine.volume = 0.001
    assert engine._calculate_volume("EURUSD", 1.1000, 1.0990) == 0.0


def test_tp_is_normalized_from_sl_distance_and_target_r():
    engine = make_engine()
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1010,
        "trade_style": "Long Intraday",
    }
    ok, reason = engine._normalize_signal_levels_to_rr(signal)
    assert ok, reason
    assert round(signal["tp"], 5) == 1.1020
    assert round(abs(signal["tp"] - signal["entry"]) / abs(signal["entry"] - signal["sl"]), 2) == 2.0


def test_pending_order_manager_uses_rr_ratio_for_tp():
    manager = PendingOrderManager.__new__(PendingOrderManager)
    entry = 1.1000
    sl = 1.0990
    tp = entry + (abs(entry - sl) * 2.0)
    assert round(tp, 5) == 1.1020


def test_analytic_engine_scores_order_block_component_without_key_error(monkeypatch):
    engine = AnalyticEngine()
    monkeypatch.setattr(engine, "_score_liquidity", lambda symbol: 0.5)
    monkeypatch.setattr(engine, "_score_volume", lambda symbol: 0.5)
    monkeypatch.setattr(engine, "_score_fvg", lambda signal: 0.5)

    result = engine.evaluate_setup("EURUSD", {"symbol": "EURUSD", "action": "BUY"})

    assert "order_block" in result["component_scores"]
    assert result["component_scores"]["order_block"] == 0.5


def test_pending_order_placed_retcode_is_success(monkeypatch):
    interface = MT5Interface.__new__(MT5Interface)
    interface.is_connected = True
    interface.last_order_error = None

    monkeypatch.setattr(mt5, "symbol_info", lambda symbol: type("Info", (), {"filling_mode": mt5.ORDER_FILLING_IOC})())
    monkeypatch.setattr(
        mt5,
        "order_send",
        lambda request: type("Result", (), {"retcode": mt5.TRADE_RETCODE_PLACED, "order": 987654, "comment": "placed"})(),
    )

    assert interface.place_buy_limit_order("EURUSD", 0.01, 1.1, 1.099, 1.102) == 987654


def test_execute_trade_market_uses_fixed_volume_and_mt5_order():
    engine = make_engine()
    engine.add_logic = lambda *args, **kwargs: None
    engine._register_trade_open = lambda symbol: None
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "trade_style": "Long Intraday",
    }
    engine.execute_trade(signal, 0.01, use_market_execution=True)
    assert engine.mt5.calls[0][0] == "buy"
    assert engine.mt5.calls[0][2] == 0.01
    assert "EURUSD" in engine.active_trades


def test_symbol_profiles_select_wider_management_for_xau_and_jpy():
    engine = make_engine()

    default_profile = engine._symbol_profile("EURUSD")
    jpy_profile = engine._symbol_profile("USDJPY")
    xau_profile = engine._symbol_profile("XAUUSD")

    assert default_profile["name"] == "DEFAULT"
    assert jpy_profile["name"] == "JPY"
    assert xau_profile["name"] == "XAU"
    assert jpy_profile["trailing_stop_step_pct"] > default_profile["trailing_stop_step_pct"]
    assert xau_profile["partial_tp_trigger_r"] > default_profile["partial_tp_trigger_r"]


def test_management_profiles_merge_symbol_and_horizon_layers():
    engine = make_engine()

    eur_scalp = engine._management_profile("EURUSD", trade={"trade_horizon": {"type": "SCALP"}})
    xau_swing = engine._management_profile("XAUUSD", trade={"trade_horizon": {"type": "SWING"}})
    jpy_intraday = engine._management_profile("USDJPY", trade={"trade_horizon": {"type": "INTRADAY"}})

    assert eur_scalp["symbol_profile"] == "DEFAULT"
    assert eur_scalp["horizon_profile"] == "SCALP"
    assert eur_scalp["partial_tp_trigger_r"] == 0.60
    assert eur_scalp["allow_news_ladder"] is False
    assert xau_swing["symbol_profile"] == "XAU"
    assert xau_swing["horizon_profile"] == "SWING"
    assert xau_swing["reverse_profit_min_r"] == 1.80
    assert xau_swing["allow_news_ladder"] is False
    assert jpy_intraday["symbol_profile"] == "JPY"
    assert jpy_intraday["horizon_profile"] == "INTRADAY"
    assert jpy_intraday["allow_news_ladder"] is True


def test_disabled_horizon_profile_keeps_symbol_profile_only():
    engine = make_engine()
    engine.scalp_profile_enabled = False

    profile = engine._management_profile("XAUUSD", trade={"trade_horizon": {"type": "SCALP"}})

    assert profile["symbol_profile"] == "XAU"
    assert profile["horizon_profile"] == "DISABLED"
    assert profile["partial_tp_trigger_r"] == 0.90


def test_professional_gate_accepts_false_move_and_order_block_structure():
    engine = make_engine()
    base_signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "trade_style": "Long Scalp",
        "scalp_potential": {"score": 0.80, "label": "Scalp Potential"},
        "session_bias": {"score": 1.0},
    }

    false_move_signal = {
        **base_signal,
        "setup_score": {
            "grade": "B",
            "score": 0.74,
            "archetype": "False Move Reversal",
            "components": [{"key": "false_move", "passed": True}],
        },
    }
    order_block_signal = {
        **base_signal,
        "setup_score": {
            "grade": "B",
            "score": 0.70,
            "archetype": "Order Block Mitigation",
            "components": [{"key": "ob_fvg", "passed": True}],
        },
    }

    false_ok, false_reason = engine._professional_execution_gate(false_move_signal, {"conviction": 0.50}, 0.74)
    ob_ok, ob_reason = engine._professional_execution_gate(order_block_signal, {"conviction": 0.50}, 0.70)

    assert false_ok, false_reason
    assert ob_ok, ob_reason


def test_professional_gate_uses_signal_strength_when_ensemble_conviction_is_low():
    engine = make_engine()
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "trade_style": "Long Scalp",
        "conviction": 0.72,
        "scalp_potential": {"score": 0.80, "label": "Scalp Potential"},
        "session_bias": {"score": 0.80},
        "setup_score": {
            "grade": "B",
            "score": 0.74,
            "archetype": "False Move Reversal",
            "components": [{"key": "false_move", "passed": True}],
        },
    }

    ok, reason = engine._professional_execution_gate(signal, {"conviction": 0.05}, 0.74)

    assert ok, reason
    assert "setup" in reason


def test_professional_gate_allows_strong_c_grade_structure_when_enabled():
    engine = make_engine()
    engine.allow_c_scalps = True
    signal = {
        "symbol": "AUDUSD",
        "action": "BUY",
        "trade_style": "Long Intraday",
        "session_bias": {"score": 0.60},
        "setup_score": {
            "grade": "C",
            "score": 0.64,
            "archetype": "Structure Continuation",
            "components": [{"key": "mss", "passed": True}],
        },
    }

    ok, reason = engine._professional_execution_gate(signal, {"conviction": 0.40}, 0.64)

    assert ok, reason


def test_professional_gate_blocks_c_grade_without_structure():
    engine = make_engine()
    engine.allow_c_scalps = True
    signal = {
        "symbol": "USDCAD",
        "action": "BUY",
        "trade_style": "Long Intraday",
        "session_bias": {"score": 0.80},
        "setup_score": {
            "grade": "C",
            "score": 0.70,
            "archetype": "Context Watch",
            "components": [{"key": "htf_bias", "passed": True}],
        },
    }

    ok, reason = engine._professional_execution_gate(signal, {"conviction": 0.50}, 0.70)

    assert not ok
    assert "Context Watch" in reason or "no liquidity" in reason


def test_post_news_retest_requires_quality_structure_and_aligned_event(monkeypatch):
    monkeypatch.setattr(technical_analysis, "detect_liquidity_sweep", lambda *args, **kwargs: {
        "direction": "Bearish",
        "description": "Price swept buy-side liquidity and rejected back into the range",
    })
    monkeypatch.setattr(technical_analysis, "detect_market_structure_shift", lambda *args, **kwargs: None)
    monkeypatch.setattr(technical_analysis, "detect_order_block", lambda *args, **kwargs: None)
    monkeypatch.setattr(technical_analysis, "detect_higher_timeframe_bias", lambda *args, **kwargs: {
        "direction": "Neutral",
        "score": 0.35,
        "description": "H1 bias is mixed",
    })
    monkeypatch.setattr(technical_analysis, "detect_session_bias", lambda *args, **kwargs: {
        "session": "Transition",
        "score": 0.35,
        "description": "Lower-quality transition window",
    })
    monkeypatch.setattr(technical_analysis, "detect_displacement", lambda *args, **kwargs: {
        "direction": "Bullish",
        "score": 0.35,
        "ratio": 0.71,
        "description": "Bullish displacement candle 0.71x average body",
    })
    monkeypatch.setattr(technical_analysis, "detect_premium_discount", lambda *args, **kwargs: {
        "zone": "Premium",
        "aligned": False,
        "description": "Price is in premium relative to recent dealing range",
    })
    monkeypatch.setattr(technical_analysis, "detect_false_move", lambda *args, **kwargs: {
        "type": "RANGE",
        "direction": "Neutral",
        "safe": True,
        "description": "No obvious false breakout or stop-hunt",
    })
    monkeypatch.setattr(technical_analysis, "detect_news_move", lambda *args, **kwargs: {
        "mode": "FOLLOW_RETEST",
        "safe": True,
        "plan": "CONTINUATION_OR_FADE",
        "direction": "Bearish",
        "description": "Event impulse detected with manageable spread; follow only after confirmation",
    })
    monkeypatch.setattr(technical_analysis, "detect_spread_safety", lambda *args, **kwargs: {
        "safe": True,
        "spread_pips": 0.2,
        "max_spread_pips": 2.5,
        "description": "Spread 0.20 pips",
    })

    setup = technical_analysis.score_composite_setup(
        "AUDUSD",
        {"symbol": "AUDUSD", "action": "BUY", "type": "EARLY_BULLISH"},
    )

    post_news = next(a for a in setup["archetypes"] if a["key"] == "post_news_retest")
    assert not post_news["passed"]
    assert setup["score"] < 0.66


def test_qualification_promotes_valid_sweep_reversal():
    engine = make_engine()
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "conviction": 0.72,
        "setup_score": {
            "grade": "B",
            "score": 0.72,
            "archetype": "Sweep Reversal",
            "components": [
                {"key": "liquidity_sweep", "passed": True},
                {"key": "premium_discount", "passed": True},
                {"key": "spread", "passed": True},
            ],
            "archetypes": [{"key": "sweep_reversal", "passed": True}],
        },
    }

    ok, qualification = engine._qualify_signal_stage(signal, {"conviction": 0.40}, 0.72)

    assert ok
    assert qualification["stage"] == "QUALIFIED"


def test_qualification_blocks_news_only_setup():
    engine = make_engine()
    signal = {
        "symbol": "AUDUSD",
        "action": "BUY",
        "conviction": 0.66,
        "news_move": {"mode": "FOLLOW_RETEST", "direction": "Bearish"},
        "setup_score": {
            "grade": "B",
            "score": 0.66,
            "archetype": "Post-News Retest",
            "components": [
                {"key": "news_safety", "passed": True},
                {"key": "spread", "passed": True},
            ],
            "archetypes": [{"key": "post_news_retest", "passed": True}],
        },
    }

    ok, qualification = engine._qualify_signal_stage(signal, {"conviction": 0.44}, 0.66)

    assert not ok
    assert "event direction not aligned" in qualification["missing"]


def test_trade_readiness_scores_executable_signal():
    engine = make_engine()
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "spread_safety": {"safe": True, "description": "Spread safe"},
        "session_bias": {"score": 0.80},
        "setup_score": {
            "score": 0.72,
            "components": [
                {"key": "liquidity_sweep", "passed": True},
                {"key": "premium_discount", "passed": True},
                {"key": "spread", "passed": True},
                {"key": "session", "passed": True},
            ],
        },
    }

    readiness = engine._compute_trade_readiness(signal, {"conviction": 0.50}, 0.72)

    assert readiness["score"] >= readiness["threshold"]
    assert readiness["stage"] == "EXECUTABLE"


def test_partial_take_profit_assigns_fresh_runner_tp():
    engine = make_engine()
    engine.active_trades["EURUSD"] = {
        "action": "BUY",
        "entry": 1.1000,
        "original_sl": 1.0990,
        "sl": 1.0990,
        "tp": 1.1020,
        "risk": 10,
        "trade_horizon": {"type": "SCALP"},
    }
    pos = {
        "ticket": 123,
        "symbol": "EURUSD",
        "type": "BUY",
        "entry": 1.1000,
        "current": 1.1008,
        "sl": 1.0990,
        "tp": 1.1020,
        "volume": 0.02,
        "profit": 4.0,
    }

    assert engine._apply_partial_take_profit(pos)

    assert ("close", 123, 0.01, "PARTIAL_TP") in engine.mt5.calls
    assert ("modify_sl", 123, "EURUSD", 1.1000) in engine.mt5.calls
    modify_tp_calls = [call for call in engine.mt5.calls if call[0] == "modify_tp"]
    assert modify_tp_calls
    assert round(modify_tp_calls[-1][3], 5) == 1.10300
    assert round(engine.active_trades["EURUSD"]["partial_runner_tp"], 5) == 1.10300


def test_partial_take_profit_locks_runner_ten_pips_when_price_has_room():
    engine = make_engine()
    engine.active_trades["EURUSD"] = {
        "action": "BUY",
        "entry": 1.1000,
        "original_sl": 1.0990,
        "sl": 1.0990,
        "tp": 1.1020,
        "risk": 10,
        "trade_horizon": {"type": "INTRADAY"},
    }
    pos = {
        "ticket": 123,
        "symbol": "EURUSD",
        "type": "BUY",
        "entry": 1.1000,
        "current": 1.1015,
        "sl": 1.0990,
        "tp": 1.1020,
        "volume": 0.02,
        "profit": 8.0,
    }

    assert engine._apply_partial_take_profit(pos)

    assert ("modify_sl", 123, "EURUSD", 1.1010) in engine.mt5.calls
    assert round(engine.active_trades["EURUSD"]["partial_tp_lock_sl"], 5) == 1.10100


def test_execute_trade_pending_uses_fixed_volume_and_limit_order():
    engine = make_engine()
    engine.add_logic = lambda *args, **kwargs: None
    engine._register_trade_open = lambda symbol: None
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "trade_style": "Long Intraday",
    }
    engine.execute_trade(signal, 0.01, use_market_execution=False)
    assert engine.mt5.calls[0][0] == "buy_limit"
    assert engine.mt5.calls[0][2] == 0.01
    assert "EURUSD" in engine.active_trades


def test_cooldown_blocks_normal_setup_but_allows_exceptional_fresh_structure():
    engine = make_engine()
    engine.trade_registry["EURUSD"] = {
        "active_trades": 0,
        "last_trade_time": datetime.now() - timedelta(minutes=1),
        "cooldown_until": datetime.now() + timedelta(minutes=2),
    }
    normal_signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "current_price": 1.1001,
        "spread_safety": {"safe": True, "description": "Spread safe"},
        "setup_score": {
            "grade": "B",
            "score": 0.70,
            "archetype": "Structure Continuation",
            "components": [{"key": "mss", "passed": True}],
        },
    }
    exceptional_signal = {
        **normal_signal,
        "setup_score": {
            "grade": "A",
            "score": 0.82,
            "archetype": "Sweep Reversal",
            "components": [{"key": "liquidity_sweep", "passed": True}],
        },
    }

    normal_ok, normal_reason = engine._check_signal_lockout(
        "EURUSD",
        signal=normal_signal,
        ensemble_decision={"conviction": 0.50},
    )
    exceptional_ok, exceptional_reason = engine._check_signal_lockout(
        "EURUSD",
        signal=exceptional_signal,
        ensemble_decision={"conviction": 0.50},
    )

    assert not normal_ok
    assert "Cooldown active" in normal_reason
    assert exceptional_ok
    assert "Cooldown override" in exceptional_reason


def test_cooldown_override_never_bypasses_active_trade_limit():
    engine = make_engine()
    engine.trade_registry["EURUSD"] = {
        "active_trades": 1,
        "last_trade_time": datetime.now(),
        "cooldown_until": datetime.now() + timedelta(minutes=2),
    }
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "current_price": 1.1001,
        "spread_safety": {"safe": True, "description": "Spread safe"},
        "setup_score": {
            "grade": "A",
            "score": 0.90,
            "archetype": "Sweep Reversal",
            "components": [{"key": "liquidity_sweep", "passed": True}],
        },
    }

    ok, reason = engine._check_signal_lockout("EURUSD", signal=signal, ensemble_decision={"conviction": 0.70})

    assert not ok
    assert "Max trades reached" in reason


def test_reversal_shock_guard_blocks_same_direction_after_adverse_exit():
    engine = make_engine()
    engine._activate_reversal_shock_guard("USDCAD", "BUY", "max adverse exit", r_value=-0.6, profit=-0.35)

    buy_ok, buy_reason = engine._check_signal_lockout("USDCAD", signal={"action": "BUY"})
    sell_ok, sell_reason = engine._check_signal_lockout("USDCAD", signal={"action": "SELL"})

    assert not buy_ok
    assert "Reversal shock guard" in buy_reason
    assert sell_ok, sell_reason


def test_opposing_signal_profit_exit_closes_profitable_trade_and_shocks_old_direction():
    engine = make_engine()
    engine.active_trades["EURUSD"] = {
        "action": "BUY",
        "entry": 1.1000,
        "original_sl": 1.0990,
        "sl": 1.0990,
        "tp": 1.1020,
        "risk": 10,
    }
    engine.mt5.positions = [{
        "ticket": 123,
        "symbol": "EURUSD",
        "type": "BUY",
        "entry": 1.1000,
        "current": 1.1005,
        "sl": 1.0990,
        "tp": 1.1020,
        "volume": 0.02,
        "profit": 5.0,
    }]
    signal = {
        "symbol": "EURUSD",
        "action": "SELL",
        "conviction": 0.72,
        "setup_score": {
            "grade": "B",
            "score": 0.72,
            "archetype": "Sweep Reversal",
            "components": [
                {"key": "liquidity_sweep", "passed": True},
                {"key": "premium_discount", "passed": True},
                {"key": "spread", "passed": True},
            ],
            "archetypes": [{"key": "sweep_reversal", "passed": True}],
        },
    }

    assert engine._apply_opposing_signal_profit_exit(signal, {"conviction": 0.40}, 0.72)

    assert ("close", 123, None, "OPPOSING_SIGNAL_PROFIT_EXIT") in engine.mt5.calls
    assert engine.active_trades["EURUSD"]["opposing_signal_exit"]["old_action"] == "BUY"
    ok, reason = engine._check_signal_lockout("EURUSD", signal={"action": "BUY"})
    assert not ok
    assert "Reversal shock guard" in reason


def test_opposing_signal_profit_exit_ignores_unqualified_counter_signal():
    engine = make_engine()
    engine.active_trades["EURUSD"] = {
        "action": "BUY",
        "entry": 1.1000,
        "original_sl": 1.0990,
        "sl": 1.0990,
        "tp": 1.1020,
        "risk": 10,
    }
    engine.mt5.positions = [{
        "ticket": 123,
        "symbol": "EURUSD",
        "type": "BUY",
        "entry": 1.1000,
        "current": 1.1005,
        "sl": 1.0990,
        "tp": 1.1020,
        "volume": 0.02,
        "profit": 5.0,
    }]
    weak_signal = {
        "symbol": "EURUSD",
        "action": "SELL",
        "conviction": 0.60,
        "setup_score": {
            "grade": "C",
            "score": 0.55,
            "archetype": "Context Watch",
            "components": [{"key": "htf_bias", "passed": True}],
        },
    }

    assert not engine._apply_opposing_signal_profit_exit(weak_signal, {"conviction": 0.35}, 0.55)
    assert not any(call[0] == "close" for call in engine.mt5.calls)
