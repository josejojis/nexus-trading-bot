import importlib
import sys
import types


def _stub_dependencies():
    mt5_module = types.ModuleType("MetaTrader5")
    mt5_module.initialize = lambda *args, **kwargs: True
    mt5_module.shutdown = lambda *args, **kwargs: None
    mt5_module.account_info = lambda *args, **kwargs: None
    mt5_module.symbol_info = lambda *args, **kwargs: None
    mt5_module.symbol_info_tick = lambda *args, **kwargs: None
    mt5_module.symbols_get = lambda *args, **kwargs: []
    mt5_module.ORDER_FILLING_IOC = 0
    mt5_module.ACCOUNT_TRADE_MODE_DEMO = 0
    mt5_module.ACCOUNT_TRADE_MODE_CONTEST = 1
    mt5_module.ACCOUNT_TRADE_MODE_REAL = 2
    sys.modules["MetaTrader5"] = mt5_module

    class DummyMT5Interface:
        is_connected = False

        def __init__(self, *args, **kwargs):
            pass

        def ensure_connected(self, *args, **kwargs):
            return True

        def get_account_info(self, *args, **kwargs):
            return None

        def get_symbols(self, *args, **kwargs):
            return []

        def get_positions(self, *args, **kwargs):
            return []

        def get_symbol_tick(self, *args, **kwargs):
            return None

        def place_buy_order(self, *args, **kwargs):
            return None

        def place_sell_order(self, *args, **kwargs):
            return None

        def modify_position_sl(self, *args, **kwargs):
            return True

        def modify_position_tp(self, *args, **kwargs):
            return True

        def get_symbol_info(self, *args, **kwargs):
            return None

    class DummyAnalyticEngine:
        def __init__(self, *args, **kwargs):
            pass

    # Predictive engine removed from runtime path; tests can stub behavior if needed
    class DummyPredictiveEngine:
        def __init__(self, *args, **kwargs):
            pass

    class DummyEnsembleDecision:
        def __init__(self, *args, **kwargs):
            pass

    class DummyTradeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def _save_log(self, *args, **kwargs):
            return None

    class DummyPendingOrderManager:
        def __init__(self, *args, **kwargs):
            pass

        def get_pending_orders_summary(self, *args, **kwargs):
            return []

    class DummyConditionalWatchlistManager:
        def __init__(self, *args, **kwargs):
            pass

    class DummyBibleLogic:
        @staticmethod
        def validate_trade(*args, **kwargs):
            return True

    class DummyTechnicalAnalysis:
        @staticmethod
        def scan_symbols(*args, **kwargs):
            return []

        @staticmethod
        def detect_forex_pattern(*args, **kwargs):
            return None

    stub_names = {
        "mt5_interface": DummyMT5Interface,
        "analytic_engine": DummyAnalyticEngine,
        "predictive_engine": DummyPredictiveEngine,
        "ensemble_decision": DummyEnsembleDecision,
        "trade_logger": DummyTradeLogger,
        "pending_order_manager": DummyPendingOrderManager,
        "conditional_watchlist_manager": DummyConditionalWatchlistManager,
        "bible_logic": DummyBibleLogic,
        "technical_analysis": DummyTechnicalAnalysis,
    }
    for name, cls in stub_names.items():
        module = types.ModuleType(name)
        if name == "mt5_interface":
            module.MT5Interface = cls
        elif name == "analytic_engine":
            module.AnalyticEngine = cls
        elif name == "predictive_engine":
            module.PredictiveEngine = cls
        elif name == "ensemble_decision":
            module.EnsembleDecision = cls
        elif name == "trade_logger":
            module.TradeLogger = cls
        elif name == "pending_order_manager":
            module.PendingOrderManager = cls
        elif name == "conditional_watchlist_manager":
            module.ConditionalWatchlistManager = cls
        elif name == "bible_logic":
            module.validate_trade = DummyBibleLogic.validate_trade
        elif name == "technical_analysis":
            module.scan_symbols = DummyTechnicalAnalysis.scan_symbols
            module.detect_forex_pattern = DummyTechnicalAnalysis.detect_forex_pattern
        sys.modules[name] = module


def _reload_engine(monkeypatch):
    _stub_dependencies()
    monkeypatch.setenv("MIN_PROFIT_PIPS", "10")
    monkeypatch.setenv("MIN_EXPECTED_R", "1.5")
    monkeypatch.setenv("TAKE_PROFIT_R_MULTIPLIER", "2.1")
    monkeypatch.setenv("PARTIAL_TP_TRIGGER_R", "0.80")
    monkeypatch.setenv("PARTIAL_TP_CLOSE_PCT", "0.35")
    monkeypatch.setenv("PARTIAL_TP_LOCK_PIPS", "15")
    monkeypatch.setenv("FEATURE_BREAKEVEN_PROTECTION", "true")
    monkeypatch.setenv("BREAKEVEN_TRIGGER_R", "0.30")
    monkeypatch.setenv("BREAKEVEN_LOCK_PIPS", "5")
    monkeypatch.setenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", "0.15")
    monkeypatch.setenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", "0.08")

    import engine
    return importlib.reload(engine)


def test_signal_size_filter_uses_combined_reward_threshold(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance._get_pip_size = lambda symbol: 1.0
    instance._get_signal_safety_state = lambda signal: {
        "spread_ok": True,
        "spread_reason": "spread ok",
        "drift_ok": True,
        "drift_reason": "drift ok",
    }

    signal_pass = {"symbol": "EURUSD", "entry": 100.0, "sl": 90.0, "tp": 115.0, "action": "BUY"}
    signal_fail = {"symbol": "EURUSD", "entry": 100.0, "sl": 90.0, "tp": 108.0, "action": "BUY"}

    ok_pass, reason_pass = instance._is_signal_big_enough(signal_pass)
    ok_fail, reason_fail = instance._is_signal_big_enough(signal_fail)

    assert ok_pass is True
    assert ok_fail is False
    assert "Reward too small" in reason_fail


def test_execution_gate_blocks_unsafe_safety_state(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance._get_signal_safety_state = lambda signal: {
        "spread_ok": False,
        "spread_reason": "spread too wide",
        "drift_ok": True,
        "drift_reason": "drift ok",
    }
    instance._component_state = lambda signal: ({}, set())
    instance._signal_has_hard_structure = lambda signal: True
    instance._predictive_execution_allowed = lambda signal, ensemble_decision: (True, "ok")

    signal = {
        "action": "BUY",
        "conviction": 0.80,
        "scalp_potential": {"score": 0.0, "conviction": 0.0},
        "setup_score": {"archetype": "Context Watch"},
    }

    ok, reason = instance._execution_gate(signal, {}, 0.80)

    assert ok is False
    assert "spread too wide" in reason


def test_predictive_execution_gate_reports_probability_threshold(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance.predictive_support_required = True
    instance.predictive_engine.model = True

    signal = {"action": "BUY"}
    ok, reason = instance._predictive_execution_allowed(signal, {"predictive_probability": 0.35})

    assert ok is False
    assert "Predictive model too bearish" in reason
    assert "threshold=0.620" in reason


def test_predictive_execution_threshold_is_lower_for_strong_pairs(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance.predictive_support_required = True
    instance.predictive_engine.model = True

    signal = {"symbol": "GBPUSD", "action": "BUY"}
    ok, reason = instance._predictive_execution_allowed(signal, {"predictive_probability": 0.56})

    assert ok is True
    assert "threshold=0.550" in reason


def test_partial_take_profit_takes_fraction_and_locks_sl(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance._get_pip_size = lambda symbol: 1.0
    instance._management_profile = lambda symbol, trade=None: {
        "name": "INTRADAY",
        "partial_tp_trigger_r": 0.80,
        "partial_tp_lock_pips": 15.0,
        "partial_tp_extend_pct": 0.25,
    }
    instance._position_r_multiple = lambda pos: 0.90
    instance._normalize_protective_sl = lambda pos, desired_sl: desired_sl
    instance._close_position_fraction = lambda pos, pct, reason: True
    instance._current_position_for_symbol = lambda symbol: {"symbol": symbol, "volume": 0.5}
    instance._apply_partial_tp_protection = lambda remaining_pos: True
    instance._extend_runner_tp_after_partial = lambda remaining_pos, trade, profile: 115.0
    instance.active_trades = {"EURUSD": {"action": "BUY"}}

    pos = {"symbol": "EURUSD", "type": "BUY", "entry": 100.0, "current": 110.0, "sl": 95.0, "tp": 110.0, "volume": 1.0, "profit": 30.0}

    ok = instance._apply_partial_take_profit(pos)

    assert ok is True
    trade = instance.active_trades["EURUSD"]
    assert trade["partial_tp_taken"] is True
    assert trade["partial_tp_r"] == 0.90


def test_breakeven_protection_moves_stop_to_lock_level(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance._get_pip_size = lambda symbol: 1.0
    instance._position_r_multiple = lambda pos: 0.35
    instance._normalize_protective_sl = lambda pos, desired_sl: desired_sl
    instance.breakeven_trigger_r = 0.10
    instance.active_trades = {"EURUSD": {"breakeven_sl_applied": False}}
    instance.mt5 = types.SimpleNamespace(modify_position_sl=lambda *args, **kwargs: True)

    pos = {"symbol": "EURUSD", "type": "BUY", "entry": 100.0, "sl": 95.0, "ticket": 1, "current": 103.0}

    ok = instance._apply_breakeven_protection(pos)

    assert ok is True
    trade = instance.active_trades["EURUSD"]
    assert trade["breakeven_sl_applied"] is True
    assert trade["breakeven_sl"] == 105.0


def test_enriched_positions_surface_execution_mode(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance.active_trades = {
        "EURUSD": {
            "action": "BUY",
            "entry": 1.1000,
            "sl": 1.0950,
            "tp": 1.1100,
            "volume": 0.5,
            "risk": 10.0,
            "execution_type": "market",
            "trade_style": "Setup",
            "trade_horizon": {"type": "INTRADAY"},
        }
    }
    instance.mt5.get_positions = lambda: [{"symbol": "EURUSD", "type": "BUY", "entry": 1.1000, "sl": 1.0950, "tp": 1.1100, "profit": 3.5}]

    positions = instance.get_enriched_positions()

    assert positions[0]["trade_state"]["execution_type"] == "market"
    assert positions[0]["trade_state"]["entry_mode"] == "market"


def test_currency_basket_guard_blocks_overloaded_currency_exposure(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance.currency_basket_guard_enabled = True
    instance.currency_basket_limits = {"USD_SHORT": 2}
    instance.active_trades = {
        "EURUSD": {"symbol": "EURUSD", "action": "BUY"},
        "GBPUSD": {"symbol": "GBPUSD", "action": "BUY"},
    }

    ok, reason = instance._guard_currency_basket_exposure({"symbol": "AUDUSD", "action": "BUY"})

    assert ok is False
    assert "USD_SHORT" in reason


def test_runtime_alert_helper_posts_to_discord_webhook(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance.webhook_alerts_enabled = True
    instance.discord_webhook_url = "https://example.test/hook"
    captured = {}

    class DummyResponse:
        status = 200

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["data"] = request.data
        return DummyResponse()

    monkeypatch.setattr(engine_module.urllib.request, "urlopen", fake_urlopen)

    ok = instance._send_runtime_alert("entry", "EURUSD", {"action": "BUY", "volume": 0.01})

    assert ok is True
    assert captured["url"] == "https://example.test/hook"
    assert b"EURUSD" in captured["data"]


def test_send_telegram_message_posts_to_bot_api(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    instance.telegram_bot_token = "123:ABC"
    instance.telegram_chat_id = "456"
    captured = {}

    class DummyResponse:
        status = 200

    def fake_urlopen(url, timeout=5):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(engine_module.urllib.request, "urlopen", fake_urlopen)

    ok = instance.send_telegram_message("hello from bot")

    assert ok is True
    assert "sendMessage" in captured["url"]
    assert "chat_id=456" in captured["url"]
    assert "hello+from+bot" in captured["url"]


def test_parse_telegram_command_extracts_trade_request(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()

    parsed = instance._parse_telegram_command("/buy EURUSD 0.01")

    assert parsed["kind"] == "trade"
    assert parsed["action"] == "BUY"
    assert parsed["symbol"] == "EURUSD"
    assert parsed["volume"] == 0.01


def test_handle_telegram_command_returns_help_message(monkeypatch):
    engine_module = _reload_engine(monkeypatch)
    instance = engine_module.TradingEngine()
    sent = {}

    def fake_send(text, **kwargs):
        sent["text"] = text
        return True

    instance.send_telegram_message = fake_send

    ok = instance._handle_telegram_command("/help", 123)

    assert ok is True
    assert "✨ Telegram Command Deck" in sent["text"]
