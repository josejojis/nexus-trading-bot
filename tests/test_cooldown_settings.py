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
            return False

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
            return False

        def modify_position_tp(self, *args, **kwargs):
            return False

        def get_symbol_info(self, *args, **kwargs):
            return None

    class DummyAnalyticEngine:
        def __init__(self, *args, **kwargs):
            pass

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
        setattr(module, name.split(".")[-1].replace("-", "_").upper(), cls)
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
        sys.modules[name] = module


def test_cooldown_values_respect_env(monkeypatch):
    _stub_dependencies()
    monkeypatch.setenv("LOSS_COOLDOWN_MINUTES", "3")
    monkeypatch.setenv("CATASTROPHIC_LOSS_COOLDOWN_MINUTES", "15")

    import engine
    importlib.reload(engine)

    instance = engine.TradingEngine()

    assert instance.loss_cooldown_minutes == 3
    assert instance.catastrophic_loss_cooldown_minutes == 15


def test_best_trading_window_and_breakeven_defaults(monkeypatch):
    _stub_dependencies()
    monkeypatch.setenv("BREAKEVEN_LOCK_PIPS", "2.0")

    import engine
    importlib.reload(engine)

    instance = engine.TradingEngine()

    assert instance.breakeven_lock_pips == 2.0

    window = instance._get_best_trading_window()
    assert window["current_session"] in instance.sessions
    assert window["score"] >= 0.0
    assert "recommendation" in window
