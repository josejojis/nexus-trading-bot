"""Flask Dashboard API"""

import json
import logging
import os
import time
import threading
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO

from engine import TradingEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

# Configure logging with visible format
log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
log_format = logging.Formatter(
    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Remove any existing handlers
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add console handler with formatter
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(log_format)
root_logger.addHandler(console_handler)

# Set werkzeug to WARNING to reduce noise
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("NEXUS TRADING BOT - Starting Application")
logger.info("=" * 60)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*')

# Global engine instance (shared state between API and background loop)
engine: Optional[TradingEngine] = None
_realtime_thread: Optional[threading.Thread] = None
_engine_thread: Optional[threading.Thread] = None
_backtest_thread: Optional[threading.Thread] = None
_engine_lock = threading.RLock()  # CRITICAL: Prevent race conditions on global engine
_backtest_lock = threading.RLock()
_status_cache_lock = threading.RLock()
_status_cache = {
    "payload": None,
    "updated_at": 0.0,
}
# Advice/predictive endpoints removed: predictive engine logic disabled
_backtest_status = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "message": None,
    "error": None,
    "report_path": os.getenv("BACKTEST_REPORT_PATH", "backtest_report.json"),
}
_STATUS_CACHE_SECONDS = float(os.getenv("DASHBOARD_STATUS_CACHE_SECONDS", "2.0"))


# CRITICAL FIX: Input validation for API endpoints
def validate_float_param(value, param_name: str, min_val=None, max_val=None):
    """CRITICAL FIX: Safely validate float parameters"""
    try:
        val = float(value)
        if min_val is not None and val < min_val:
            return None, f"{param_name} must be >= {min_val}, got {val}"
        if max_val is not None and val > max_val:
            return None, f"{param_name} must be <= {max_val}, got {val}"
        return val, None
    except (TypeError, ValueError):
        return None, f"{param_name} must be a valid number"


def validate_symbols_param(value):
    """CRITICAL FIX: Safely validate and sanitize symbols"""
    if isinstance(value, str):
        symbols = [s.strip().upper() for s in value.split(",") if s.strip()]
    elif isinstance(value, list):
        symbols = [str(s).strip().upper() for s in value if s]
    else:
        return None, "Symbols must be string or array"
    
    if not symbols:
        return None, "Symbols cannot be empty"
    return symbols, None


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ["1", "true", "yes", "on"]


def _safe_backtest_snapshot():
    with _backtest_lock:
        return dict(_backtest_status)


def _run_backtest_background(symbols, bars, lookahead, min_samples, report_path, retrain_on_complete=False):
    with _backtest_lock:
        _backtest_status.update({
            "running": True,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "message": "Backtest running",
            "error": None,
            "report_path": report_path,
        })

    try:
        import offline_backtest

        # Predictive engine removed — run backtest using neutral probabilities
        engine_for_backtest = None
        report = offline_backtest.backtest_symbols(
            engine_for_backtest,
            symbols,
            bars=bars,
            lookahead=lookahead,
            min_samples=min_samples,
        )

        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

        with _backtest_lock:
            _backtest_status.update({
                "running": False,
                "finished_at": datetime.utcnow().isoformat(),
                "message": "Backtest completed successfully",
                "error": None,
            })
    except Exception as exc:
        with _backtest_lock:
            _backtest_status.update({
                "running": False,
                "finished_at": datetime.utcnow().isoformat(),
                "message": "Backtest failed",
                "error": str(exc),
            })


def read_env_file(path=ENV_PATH):
    env_vars = {}
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key] = val
    return env_vars


def parse_env_editor(text):
    env_vars = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            env_vars[key] = value.strip()
    return env_vars


def safe_env_for_dashboard(env_vars):
    secret_fragments = ("PASSWORD", "TOKEN", "SECRET", "WEBHOOK", "KEY")
    return {
        key: value
        for key, value in sorted((env_vars or {}).items())
        if not any(fragment in key.upper() for fragment in secret_fragments)
    }


@app.route("/")
def index():
    app_js_path = os.path.join(app.static_folder, "js", "app.js")
    app_css_path = os.path.join(app.static_folder, "css", "style.css")
    try:
        app_js_version = int(os.path.getmtime(app_js_path))
    except Exception:
        app_js_version = 1
    try:
        app_css_version = int(os.path.getmtime(app_css_path))
    except Exception:
        app_css_version = 1
    ui_version = max(app_js_version, app_css_version)
    return render_template(
        "index.html",
        app_js_version=app_js_version,
        app_css_version=app_css_version,
        ui_version=ui_version,
    )


@app.route("/api/bot/start", methods=["POST"])
def api_start():
    global engine, _engine_thread
    with _engine_lock:  # CRITICAL: Prevent race condition on engine creation
        payload = request.json or {}
        success, message = _start_engine(payload)
        if success:
            return jsonify({"status": "success", "message": "Bot started"})

        status_code = 400 if message and message.startswith("Invalid") else 500
        return jsonify({"status": "error", "message": message or "Failed to start bot"}), status_code


@app.route("/api/bot/stop", methods=["POST"])
def api_stop():
    with _engine_lock:  # CRITICAL: Prevent race condition on engine shutdown
        success, message = _stop_engine()
        if success:
            return jsonify({"status": "success", "message": "Bot stopped"})
        return jsonify({"status": "error", "message": message or "Bot not running"}), 400


@app.route("/api/bot/retrain", methods=["POST"])
@app.route("/api/bot/retrain", methods=["POST"])
def api_retrain():
    # Predictive retraining removed from this deployment.
    return jsonify({"status": "error", "message": "Predictive engine removed"}), 410


@app.route("/api/telegram/test", methods=["POST"])
def api_send_telegram_test():
    global engine
    payload = request.json or {}
    message = str(payload.get("message") or "🧪 Telegram test from trading bot").strip()

    if engine is None:
        return jsonify({"status": "error", "message": "Bot engine is not running"}), 400

    ok = engine.send_telegram_message(message)
    if ok:
        return jsonify({"status": "success", "message": "Telegram message sent"})
    return jsonify({"status": "error", "message": "Telegram message not sent"}), 400


@app.route("/api/telegram/command", methods=["POST"])
def api_handle_telegram_command():
    global engine
    payload = request.json or {}
    text = str(payload.get("text") or "").strip()
    chat_id = payload.get("chat_id")

    if engine is None:
        return jsonify({"status": "error", "message": "Bot engine is not running"}), 400

    ok = engine._handle_telegram_command(text, chat_id)
    if ok:
        return jsonify({"status": "success", "message": "Telegram command handled"})
    return jsonify({"status": "error", "message": "Telegram command failed"}), 400


@app.route("/api/advice", methods=["GET"])
@app.route("/api/advice", methods=["GET"])
def api_advice_removed():
    return jsonify({"status": "error", "message": "Predictive advice API removed"}), 410


@app.route("/api/advice/batch", methods=["GET", "POST"])
@app.route("/api/advice/batch", methods=["GET", "POST"])
def api_advice_batch_removed():
    return jsonify({"status": "error", "message": "Predictive advice API removed"}), 410


@app.route("/api/advice/history", methods=["GET"])
@app.route("/api/advice/history", methods=["GET"])
def api_advice_history_removed():
    return jsonify({"status": "error", "message": "Predictive advice API removed"}), 410


def _build_logs_payload(active_engine):
    """Return dashboard logs grouped by how the UI consumes them."""
    if not active_engine:
        return {"rejections": [], "trades": [], "signals": [], "future_trades": [], "diagnostics": []}

    raw_logs = []
    if hasattr(active_engine, "logger"):
        raw_logs = active_engine.logger.get_logs(limit=500)

    rejection_logs = list(getattr(active_engine, "rejection_logs", []) or [])
    persisted_rejections = [entry for entry in raw_logs if entry.get("event") == "SIGNAL_REJECTED"]
    seen_rejections = {
        (entry.get("timestamp"), entry.get("symbol"), entry.get("reason"))
        for entry in rejection_logs
    }
    for entry in persisted_rejections:
        key = (entry.get("timestamp"), entry.get("symbol"), entry.get("reason"))
        if key not in seen_rejections:
            rejection_logs.append(entry)
            seen_rejections.add(key)

    trade_events = {
        "TRADE_EXECUTED",
        "TRADE_CLOSED",
        "PARTIAL_TP",
        "PARTIAL_TP_SL_LOCK",
        "PARTIAL_TP_RUNNER_TP",
        "MAX_ADVERSE_EXIT",
        "NEWS_LADDER_ADDON",
        "REVERSAL_SHOCK_GUARD",
        "OPPOSING_SIGNAL_PROFIT_EXIT",
    }
    trades = [entry for entry in raw_logs if entry.get("event") in trade_events]

    signal_history = list(getattr(active_engine, "signal_history", []) or [])
    persisted_signals = [entry for entry in raw_logs if entry.get("event") == "FVG_DETECTED"]
    signals = (signal_history + persisted_signals)[-100:]

    logic_feed = list(getattr(active_engine, "logic_feed", []) or [])
    diagnostics = [
        {
            "timestamp": item.get("timestamp"),
            "event": "LOGIC_WARNING" if item.get("level") == "warning" else "LOGIC_NOTE",
            "symbol": item.get("symbol"),
            "reason": item.get("message"),
        }
        for item in logic_feed[-50:]
        if item.get("level") in {"warning", "error"} or "No FVG signals" not in str(item.get("message", ""))
    ][-50:]

    if signals and not rejection_logs and not trades:
        latest = signals[-1]
        diagnostics.append({
            "timestamp": latest.get("timestamp"),
            "event": "SCAN_DIAGNOSTIC",
            "symbol": latest.get("symbol"),
            "reason": "Setup detected, but no rejection or execution was recorded yet. If this repeats, check the engine console for scan exceptions.",
        })

    return {
        "rejections": rejection_logs[-80:],
        "trades": trades[-250:],
        "signals": signals[-100:],
        "future_trades": list(getattr(active_engine, "future_trades", []) or [])[-50:],
        "diagnostics": diagnostics[-50:],
    }


def _safe_engine_snapshot(active_engine):
    """Return a cached heavy engine snapshot so dashboard polling cannot overload MT5."""
    now = time.time()
    with _status_cache_lock:
        cached = _status_cache.get("payload")
        updated_at = float(_status_cache.get("updated_at") or 0)
        if cached is not None and (now - updated_at) < _STATUS_CACHE_SECONDS:
            return cached

    if not active_engine:
        payload = {
            "status": {},
            "positions": [],
            "logs": {"rejections": [], "trades": [], "signals": [], "future_trades": [], "diagnostics": []},
            "stats": {},
        }
    else:
        status = {}
        positions = []
        logs = {}
        stats = {}
        try:
            status = active_engine.get_status() or {}
        except Exception as exc:
            logger.warning("Status snapshot failed: %s", exc)
        try:
            positions = active_engine.get_enriched_positions() if hasattr(active_engine, "get_enriched_positions") else active_engine.mt5.get_positions()
        except Exception as exc:
            logger.warning("Position snapshot failed: %s", exc)
            positions = []
        try:
            logs = _build_logs_payload(active_engine)
        except Exception as exc:
            logger.warning("Logs snapshot failed: %s", exc)
            logs = {"rejections": [], "trades": [], "signals": [], "future_trades": [], "diagnostics": []}
        try:
            stats = active_engine.logger.get_stats() if hasattr(active_engine, "logger") else {}
        except Exception as exc:
            logger.warning("Stats snapshot failed: %s", exc)
            stats = {}
        payload = {
            "status": status,
            "positions": positions or [],
            "logs": logs,
            "stats": stats,
        }

    with _status_cache_lock:
        _status_cache["payload"] = payload
        _status_cache["updated_at"] = time.time()
    return payload


def _build_realtime_payload():
    global engine
    active_engine = engine
    if not active_engine:
        return None

    snapshot = _safe_engine_snapshot(active_engine)
    status = dict(snapshot.get("status") or {})
    positions = snapshot.get("positions") or []
    status["active_trades"] = len(positions)
    status["engine_active_trades"] = len(getattr(active_engine, "active_trades", {}) or {})

    pending = []
    try:
        pending = active_engine.pending_order_manager.get_pending_orders_summary() if hasattr(active_engine, 'pending_order_manager') else []
    except Exception:
        pending = []

    return {
        "status": status,
        "signals": {
            "recent": getattr(active_engine, "recent_signals", []) or [],
            "favorable": getattr(active_engine, "favorable_signals", []) or [],
        },
        "pending_orders": pending,
        "positions": positions,
        "logs": snapshot.get("logs") or {},
        "stats": snapshot.get("stats") or {},
    }


def _start_realtime_thread():
    global _realtime_thread

    if _realtime_thread and _realtime_thread.is_alive():
        return

    def _realtime_worker():
        import time
        interval_seconds = float(os.getenv("DASHBOARD_REALTIME_INTERVAL_SECONDS", "2.5"))

        while True:
            time.sleep(interval_seconds)
            payload = _build_realtime_payload()
            if payload:
                try:
                    socketio.emit('dashboard_update', payload, namespace='/')
                except Exception as e:
                    logger.warning(f"SocketIO emit failed: {e}")

    _realtime_thread = threading.Thread(target=_realtime_worker, daemon=True)
    _realtime_thread.start()


def _start_engine(payload):
    global engine, _engine_thread

    if engine and engine.is_running:
        return False, "Bot already running"

    try:
        engine = TradingEngine()
        engine.rule_config = {"ema": False, "volume": False, "po3": False}

        if "symbols" in payload:
            symbols, error = validate_symbols_param(payload["symbols"])
            if error:
                engine = None
                return False, f"Invalid symbols: {error}"
            engine.symbols = symbols

        if "volume" in payload:
            volume, error = validate_float_param(payload["volume"], "volume", min_val=0.001, max_val=10)
            if error:
                engine = None
                return False, error
            engine.volume = volume

        sizing_mode = None
        if "POSITION_SIZING_MODE" in payload and payload.get("POSITION_SIZING_MODE") is not None:
            sizing_mode = payload.get("POSITION_SIZING_MODE")
        elif "position_sizing_mode" in payload and payload.get("position_sizing_mode") is not None:
            sizing_mode = payload.get("position_sizing_mode")
        elif os.getenv("POSITION_SIZING_MODE") is not None:
            sizing_mode = os.getenv("POSITION_SIZING_MODE")
        else:
            sizing_mode = "fixed"
        try:
            mode = str(sizing_mode).strip().lower()
            engine.position_sizing_mode = mode if mode in {"fixed", "dynamic"} else "fixed"
        except Exception:
            engine.position_sizing_mode = "fixed"

        risk_value = None
        if "RISK_PER_TRADE_PCT" in payload and payload.get("RISK_PER_TRADE_PCT") is not None:
            risk_value = payload.get("RISK_PER_TRADE_PCT")
        elif "risk_pct" in payload and payload.get("risk_pct") is not None:
            risk_value = payload.get("risk_pct")
        elif os.getenv("RISK_PER_TRADE_PCT") is not None:
            risk_value = os.getenv("RISK_PER_TRADE_PCT")
        else:
            risk_value = "0.01"
        try:
            risk_pct = float(risk_value)
            if risk_pct <= 0:
                risk_pct = 0.01
            elif risk_pct > 0.10:
                risk_pct = 0.10
            engine.risk_pct = risk_pct
        except Exception:
            engine.risk_pct = 0.01

        if "max_exposure_pct" in payload:
            exposure, error = validate_float_param(payload["max_exposure_pct"], "max_exposure_pct", min_val=0.01, max_val=1)
            if error:
                engine = None
                return False, error
            engine.max_exposure_pct = exposure

        if not engine.connect():
            engine = None
            return False, "MT5 connection failed"

        _engine_thread = threading.Thread(target=engine.start, daemon=True)
        _engine_thread.start()
        _start_realtime_thread()
        logger.info("Bot started via API")
        return True, None
    except Exception as e:
        logger.error(f"Error initializing engine: {e}", exc_info=True)
        engine = None
        return False, str(e)


def _stop_engine():
    global engine, _engine_thread

    if not engine:
        return False, "Bot not running"

    try:
        engine.stop()
        engine.disconnect()
        if _engine_thread and _engine_thread.is_alive():
            _engine_thread.join(timeout=5)
        engine = None
        _engine_thread = None
        logger.info("Bot stopped via API")
        return True, None
    except Exception as e:
        logger.error(f"Error stopping engine: {e}", exc_info=True)
        return False, str(e)


@app.route("/api/bot/status", methods=["GET"])
def api_status():
    try:
        active_engine = engine
        if active_engine:
            snapshot = _safe_engine_snapshot(active_engine)
            status = snapshot.get("status") or {}
            positions = snapshot.get("positions") or []
            live_active_trades = len(positions)
            try:
                pending_orders = (
                    active_engine.pending_order_manager.get_pending_orders_summary()
                    if hasattr(active_engine, "pending_order_manager")
                    else []
                )
            except Exception:
                pending_orders = []
            return jsonify({
                "running": status.get("running", False),
                "connected": status.get("connected", False),
                "market_open": status.get("market_open"),
                "bot_score": status.get("bot_score"),
                "balance": status.get("balance"),
                "equity": status.get("equity"),
                "free_margin": status.get("free_margin"),
                "margin_level": status.get("margin_level"),
                "best_trading_window": status.get("best_trading_window"),
                "daily_profit": status.get("daily_profit"),
                "floating_profit": status.get("floating_profit"),
                "realized_profit": status.get("realized_profit"),
                "net_profit": status.get("net_profit"),
                "floating_drawdown": status.get("floating_drawdown"),
                "current_open_risk": status.get("current_open_risk"),
                "max_open_risk": status.get("max_open_risk"),
                "open_risk_pct": status.get("open_risk_pct"),
                "max_open_risk_pct": status.get("max_open_risk_pct"),
                "open_risk_details": status.get("open_risk_details", []),
                "symbols": status.get("symbols", []),
                "volume": status.get("volume"),
                "position_sizing_mode": status.get("position_sizing_mode"),
                "account_profile": status.get("account_profile") or {},
                "account": status.get("account") or {},
                "active_trades": live_active_trades,
                "engine_active_trades": status.get("active_trades", 0),
                "logic_feed": status.get("logic_feed", []),
                "scan": status.get("scan", {}),
                "trade_management": status.get("trade_management", {}),
                "positions": positions,
                "pending_orders": pending_orders,
                "logs": snapshot.get("logs") or {},
                "stats": snapshot.get("stats") or {},
            })

        return jsonify({
            "running": False,
            "connected": False,
            "market_open": False,
            "bot_score": {
                "score": 25,
                "grade": "F",
                "label": "Engine stopped",
                "components": [],
                "summary": "F (25/100) - Engine stopped",
            },
            "equity": None,
            "active_trades": 0,
            "scan": {
                "interval_seconds": int(os.getenv("SCAN_INTERVAL_SECONDS", 3)),
                "engine_loop_sleep_seconds": float(os.getenv("ENGINE_LOOP_SLEEP_SECONDS", 3)),
                "on_new_candle": os.getenv("SCAN_ON_NEW_CANDLE", "false").lower() in ["true", "1", "yes"],
                "timeframe_minutes": int(os.getenv("SCAN_TIMEFRAME_MINUTES", 5)),
                "last_scan_at": None,
                "next_scan_at": None,
                "seconds_until_next_scan": None,
                "last_signal_count": 0,
                "duplicate_signal_cooldown_seconds": int(os.getenv("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", 0)),
                "trade_cooldown_minutes": int(os.getenv("TRADE_COOLDOWN_MINUTES", 0)),
                "max_trades_per_symbol": int(os.getenv("MAX_TRADES_PER_SYMBOL", 1)),
                "early_entry_enabled": os.getenv("FEATURE_EARLY_ENTRY", "true").lower() in ["true", "1", "yes"],
                "early_entry_min_score": float(os.getenv("EARLY_ENTRY_MIN_SCORE", 0.55)),
            },
            "trade_management": {},
        })
    except Exception as e:
        return jsonify({"running": False, "connected": False, "equity": None, "error": str(e)}), 500


@app.route("/api/positions", methods=["GET"])
def api_positions():
    try:
        if engine:
            positions = _safe_engine_snapshot(engine).get("positions") or []
            return jsonify({"status": "success", "data": positions})
        return jsonify({"status": "success", "data": [], "message": "Engine not running"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chart-visuals/<symbol>", methods=["GET"])
def api_chart_visuals(symbol):
    try:
        from technical_analysis import build_chart_visuals

        data = build_chart_visuals(symbol)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/signals", methods=["GET"])
def api_signals():
    try:
        if engine:
            return jsonify({
                "status": "success",
                "data": {
                    "recent": engine.recent_signals,
                    "favorable": engine.favorable_signals,
                },
            })
        return jsonify({"status": "success", "data": {"recent": [], "favorable": []}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/signals/execute", methods=["POST"])
def api_execute_signal():
    """Manually execute a current dashboard signal through the engine safety path."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400

        data = request.json or {}
        source = str(data.get("source") or "recent").lower()
        index = data.get("index")
        symbol = str(data.get("symbol") or "").upper().strip()
        action = str(data.get("action") or "").upper().strip()
        route = str(data.get("route") or "market").lower()
        use_market_execution = route != "pending"

        pools = {
            "recent": list(getattr(engine, "recent_signals", []) or []),
            "favorable": list(getattr(engine, "favorable_signals", []) or []),
        }
        signals = pools.get(source, pools["recent"])

        signal = None
        if index is not None:
            try:
                signal = signals[int(index)]
            except Exception:
                signal = None
        if signal is None and symbol:
            for candidate in signals:
                candidate_action = str(candidate.get("action") or candidate.get("type") or "").upper()
                if str(candidate.get("symbol") or "").upper() == symbol and (not action or candidate_action == action):
                    signal = candidate
                    break
        if signal is None and isinstance(data.get("signal"), dict):
            signal = data["signal"]
        if signal is None:
            return jsonify({"status": "error", "message": "Signal no longer available"}), 404

        signal = dict(signal)
        symbol = str(signal.get("symbol") or symbol).upper()
        action = str(signal.get("action") or signal.get("type") or action).upper()
        if "BULL" in action:
            action = "BUY"
        elif "BEAR" in action:
            action = "SELL"
        signal["symbol"] = symbol
        signal["action"] = action
        signal["manual_execution"] = True
        signal["status"] = "manual_requested"
        signal["status_reason"] = "Manual dashboard execution requested"

        if not symbol or action not in {"BUY", "SELL"}:
            return jsonify({"status": "error", "message": "Signal is missing a valid symbol/action"}), 400
        if engine.killed.get("all") or engine.killed.get(symbol):
            return jsonify({"status": "error", "message": f"Kill switch active for {symbol}"}), 409

        symbol_ok, symbol_reason = engine._symbol_allowed_for_execution(symbol)
        if not symbol_ok:
            return jsonify({"status": "error", "message": symbol_reason}), 409

        can_trade, reason = engine._can_trade()
        if not can_trade:
            return jsonify({"status": "error", "message": reason}), 409

        if use_market_execution:
            ok, reason = engine._refresh_signal_for_market_execution(signal)
            if not ok:
                return jsonify({"status": "error", "message": reason}), 400

        ok, reason = engine._normalize_signal_levels_to_rr(signal)
        if not ok:
            return jsonify({"status": "error", "message": reason}), 400
        big_enough, size_reason = engine._is_signal_big_enough(signal)
        if not big_enough:
            return jsonify({"status": "error", "message": size_reason}), 400
        setup_value = float((signal.get("setup_score") or {}).get("score") or signal.get("confluence_score") or signal.get("conviction") or 0.0)
        ensemble_decision = {"conviction": float(signal.get("conviction") or setup_value or 0.0)}
        spread_ok, spread_reason = engine._get_spread_safety(signal)
        if not spread_ok:
            return jsonify({"status": "error", "message": spread_reason}), 409
        engine.add_logic(symbol, "Manual execution bypassed score/professional gates; hard safety checks remain active", level="warning")
        event_ok, event_reason = engine._event_execution_gate(signal)
        if not event_ok:
            return jsonify({"status": "error", "message": event_reason}), 409

        volume = engine._calculate_volume(symbol, signal.get("entry"), signal.get("sl"))
        if not volume or volume <= 0:
            return jsonify({"status": "error", "message": f"Invalid fixed lot size for {symbol}"}), 400

        def pos_value(position, key):
            if isinstance(position, dict):
                return position.get(key)
            return getattr(position, key, None)

        before_orders = set()
        try:
            positions = engine.mt5.get_positions() or []
            before_orders = {str(pos_value(pos, "ticket")) for pos in positions if pos_value(pos, "ticket") is not None}
        except Exception:
            before_orders = set()

        engine.add_logic(symbol, f"Manual execution requested from dashboard ({route})", level="warning")
        engine.execute_trade(signal, volume, use_market_execution=use_market_execution)

        active = engine.active_trades.get(symbol) or {}
        order_id = active.get("order_id")
        if not order_id:
            try:
                positions = engine.mt5.get_positions() or []
                for pos in positions:
                    pos_symbol = pos_value(pos, "symbol")
                    ticket = pos_value(pos, "ticket")
                    if str(pos_symbol).upper() == symbol and str(ticket) not in before_orders:
                        order_id = ticket
                        break
            except Exception:
                pass

        if order_id:
            return jsonify({
                "status": "success",
                "message": f"Manual {action} execution sent for {symbol}",
                "data": {"symbol": symbol, "action": action, "order_id": order_id, "route": route},
            })

        order_error = (
            getattr(engine, "last_execution_error", None)
            or getattr(engine.mt5, "last_order_error", None)
            or "Order was not accepted by MT5 or engine guard"
        )
        return jsonify({"status": "error", "message": order_error}), 409
    except Exception as e:
        logger.exception("Manual signal execution failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/kill", methods=["GET", "POST"])
def api_kill():
    try:
        if not engine:
            return jsonify({"status": "success", "data": {"all": False}, "message": "Engine not running"})
        if request.method == "GET":
            return jsonify({"status": "success", "data": engine.killed})
        # POST to update
        data = request.json or {}
        symbol = data.get("symbol", "all")
        action = data.get("action")
        if action == "disable":
            engine.killed[symbol] = True
        elif action == "enable":
            engine.killed[symbol] = False
        else:
            return jsonify({"status": "error", "message": "invalid action"}), 400
        return jsonify({"status": "success", "data": engine.killed})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    try:
        if engine:
            return jsonify({"status": "success", "data": engine.sessions})
        return jsonify({"status": "error"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
def api_logs():
    try:
        if engine:
            return jsonify({
                "status": "success",
                "data": _safe_engine_snapshot(engine).get("logs") or {},
            })
        return jsonify({"status": "success", "data": {"rejections": [], "trades": [], "signals": [], "future_trades": [], "diagnostics": []}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/backtest-report", methods=["GET"])
def api_backtest_report():
    report_path = os.getenv("BACKTEST_REPORT_PATH", "backtest_report.json")
    try:
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return jsonify({"status": "success", "data": data})
        return jsonify({"status": "success", "data": None, "message": "No backtest report found"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/backtest-status", methods=["GET"])
def api_backtest_status():
    try:
        return jsonify({"status": "success", "data": _safe_backtest_snapshot()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/backtest-run", methods=["POST"])
def api_backtest_run():
    global _backtest_thread
    try:
        if _backtest_thread and _backtest_thread.is_alive():
            return jsonify({"status": "error", "message": "Backtest already running"}), 409

        payload = request.json or {}
        symbols = payload.get("symbols")
        if symbols is not None:
            symbols, error = validate_symbols_param(symbols)
            if error:
                return jsonify({"status": "error", "message": f"Invalid symbols: {error}"}), 400
        else:
            raw_symbols = os.getenv("EXECUTION_SYMBOLS") or os.getenv("TRADING_SYMBOLS") or "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,NZDUSD,EURJPY,GBPJPY,USDCHF,XAUUSD"
            symbols = [symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()]

        def parse_int(value, default):
            try:
                return int(value)
            except Exception:
                return default

        bars = parse_int(payload.get("bars"), int(os.getenv("BACKTEST_BARS", "2000")))
        lookahead = parse_int(payload.get("lookahead"), int(os.getenv("BACKTEST_LOOKAHEAD", "3")))
        min_samples = parse_int(payload.get("min_samples"), int(os.getenv("BACKTEST_MIN_SAMPLES", "200")))
        report_path = payload.get("report") or os.getenv("BACKTEST_REPORT_PATH", "backtest_report.json")
        retrain_on_complete = parse_bool(payload.get("retrain")) if payload.get("retrain") is not None else parse_bool(os.getenv("BACKTEST_RETRAIN_ON_RUN", "false"))

        _backtest_thread = threading.Thread(
            target=_run_backtest_background,
            args=(symbols, bars, lookahead, min_samples, report_path, retrain_on_complete),
            daemon=True,
        )
        _backtest_thread.start()

        return jsonify({
            "status": "success",
            "message": "Backtest started",
            "data": {
                "symbols": symbols,
                "report_path": report_path,
                "retrain_on_complete": retrain_on_complete,
            },
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stats", methods=["GET"])
def api_stats():
    try:
        if engine:
            stats = _safe_engine_snapshot(engine).get("stats") or {}
            return jsonify({"status": "success", "data": stats})
        return jsonify({"status": "success", "data": {}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    try:
        def as_float(value, default=0.0):
            try:
                return float(value)
            except Exception:
                return default

        def to_percent(value, default=0.0):
            value = as_float(value, default)
            return value * 100 if value <= 1 else value

        def from_percent(value, default=0.0):
            value = as_float(value, default)
            return value / 100 if value > 1 else value

        def from_ui_percent(value, default=0.0):
            value = as_float(value, default)
            return value / 100.0 if value > 1 else value

        def normalize_fraction(value, default=0.0):
            value = as_float(value, default)
            return value / 100.0 if value >= 1 else value

        load_dotenv(ENV_PATH, override=True)

        if request.method == "GET":
            # return current config from .env
            current_env = read_env_file(ENV_PATH)
            exposure_value = engine.max_exposure_pct if engine else normalize_fraction(os.getenv("MAX_EXPOSURE_PERCENT", "0.05"), 0.05)
            daily_cap_value = engine.daily_profit_cap if engine else normalize_fraction(os.getenv("DAILY_PROFIT_CAP", "0.02"), 0.02)
            config = {
                "TRADING_SYMBOLS": os.getenv("TRADING_SYMBOLS", ""),
                "EXECUTION_SYMBOLS": os.getenv("EXECUTION_SYMBOLS", ""),
                "TRADE_VOLUME": os.getenv("TRADE_VOLUME", "0.001"),
                "POSITION_SIZING_MODE": engine.position_sizing_mode if engine else os.getenv("POSITION_SIZING_MODE", "fixed"),
                "RISK_PER_TRADE_PCT": getattr(engine, 'risk_pct', float(os.getenv('RISK_PER_TRADE_PCT', 0.01))) if engine else float(os.getenv('RISK_PER_TRADE_PCT', 0.01)),
                "ACCOUNT_PROFILE": {
                    "enabled": engine.dynamic_account_profile_enabled,
                    "name": engine.account_profile_name,
                    "equity": engine.account_profile_equity,
                    "volume": engine.volume,
                } if engine else {},
                "MAX_EXPOSURE_PERCENT": to_percent(exposure_value, 5),
                "MIN_PROFIT_PIPS": os.getenv("MIN_PROFIT_PIPS", "50"),
                "DAILY_PROFIT_CAP": to_percent(daily_cap_value, 2),
                "DAILY_PROFIT_CAP_EXTENSION": to_percent(engine.daily_profit_cap_extension if engine else normalize_fraction(os.getenv("DAILY_PROFIT_CAP_EXTENSION", "0.0"), 0.0), 0.0),
                "FEATURE_DAILY_LOSS_BRAKE": False,
                "DAILY_LOSS_CAP_PERCENT": to_percent(engine.daily_loss_cap_pct if engine else os.getenv("DAILY_LOSS_CAP_PERCENT", "0.05"), 5),
                "MAX_DAILY_LOSSES": engine.max_daily_losses if engine else int(os.getenv("MAX_DAILY_LOSSES", "100")),
                "MAX_CONSECUTIVE_LOSSES": engine.max_consecutive_losses if engine else int(os.getenv("MAX_CONSECUTIVE_LOSSES", "30")),
                "LOSS_COOLDOWN_MINUTES": engine.loss_cooldown_minutes if engine else int(os.getenv("LOSS_COOLDOWN_MINUTES", "0")),
                "MAX_ACTIVE_TRADES_TOTAL": engine.max_active_trades_total if engine else int(os.getenv("MAX_ACTIVE_TRADES_TOTAL", "10")),
                "FEATURE_CATASTROPHIC_LOSS_STOP": engine.catastrophic_loss_stop_enabled if engine else os.getenv("FEATURE_CATASTROPHIC_LOSS_STOP", "true").lower() in ["1", "true", "yes"],
                "CATASTROPHIC_LOSS_R": engine.catastrophic_loss_r if engine else as_float(os.getenv("CATASTROPHIC_LOSS_R", "1.5"), 1.5),
                "CATASTROPHIC_LOSS_COOLDOWN_MINUTES": engine.catastrophic_loss_cooldown_minutes if engine else int(os.getenv("CATASTROPHIC_LOSS_COOLDOWN_MINUTES", "0")),
                "MIN_EXPECTED_R": engine.min_expected_r if engine else as_float(os.getenv("MIN_EXPECTED_R", "1.2"), 1.2),
                "MIN_EXPECTED_R_SCALP": engine.min_expected_r_scalp if engine else as_float(os.getenv("MIN_EXPECTED_R_SCALP", "1.0"), 1.0),
                "MIN_EXPECTED_R_INTRADAY": os.getenv("MIN_EXPECTED_R_INTRADAY", os.getenv("MIN_EXPECTED_R", "1.2")),
                "MIN_EXPECTED_R_SWING": os.getenv("MIN_EXPECTED_R_SWING", "1.5"),
                "TAKE_PROFIT_R_MULTIPLIER": engine.take_profit_r_multiplier if engine else as_float(os.getenv("TAKE_PROFIT_R_MULTIPLIER", "1.8"), 1.8),
                "TAKE_PROFIT_R_MULTIPLIER_SCALP": engine.take_profit_r_multiplier_scalp if engine else as_float(os.getenv("TAKE_PROFIT_R_MULTIPLIER_SCALP", "1.5"), 1.5),
                "TAKE_PROFIT_R_MULTIPLIER_INTRADAY": os.getenv("TAKE_PROFIT_R_MULTIPLIER_INTRADAY", os.getenv("TAKE_PROFIT_R_MULTIPLIER", "1.8")),
                "TAKE_PROFIT_R_MULTIPLIER_SWING": os.getenv("TAKE_PROFIT_R_MULTIPLIER_SWING", "2.5"),
                "EXECUTION_CONVICTION_THRESHOLD": engine.execution_conviction_threshold if engine else as_float(os.getenv("EXECUTION_CONVICTION_THRESHOLD", "0.35"), 0.35),
                "EXECUTION_SETUP_SCORE_THRESHOLD": engine.execution_setup_score_threshold if engine else as_float(os.getenv("EXECUTION_SETUP_SCORE_THRESHOLD", "0.50"), 0.50),
                "MIN_TRADE_READINESS_SCORE": engine.min_trade_readiness_score if engine else as_float(os.getenv("MIN_TRADE_READINESS_SCORE", "0.62"), 0.62),
                "FEATURE_MTF_EXECUTION_GATE": getattr(engine, "mtf_execution_gate_enabled", True) if engine else os.getenv("FEATURE_MTF_EXECUTION_GATE", "true").lower() in ["1", "true", "yes"],
                "MIN_MTF_EXECUTION_SCORE": getattr(engine, "min_mtf_execution_score", as_float(os.getenv("MIN_MTF_EXECUTION_SCORE", "0.30"), 0.30)) if engine else as_float(os.getenv("MIN_MTF_EXECUTION_SCORE", "0.30"), 0.30),
                "MIN_MTF_EXECUTION_SCORE_METAL": getattr(engine, "min_mtf_execution_score_metal", as_float(os.getenv("MIN_MTF_EXECUTION_SCORE_METAL", "0.45"), 0.45)) if engine else as_float(os.getenv("MIN_MTF_EXECUTION_SCORE_METAL", "0.45"), 0.45),
                "MARKET_EXECUTION_SCORE_THRESHOLD": engine.market_execution_score_threshold if engine else as_float(os.getenv("MARKET_EXECUTION_SCORE_THRESHOLD", "0.60"), 0.60),
                "MARKET_EXECUTION_CONVICTION_THRESHOLD": engine.market_execution_conviction_threshold if engine else as_float(os.getenv("MARKET_EXECUTION_CONVICTION_THRESHOLD", "0.35"), 0.35),
                "ANALYTIC_TIMEFRAMES": os.getenv("ANALYTIC_TIMEFRAMES", "M1,M5,M15,H1,H4"),
                "MAX_ENTRY_DRIFT_PIPS": engine.max_entry_drift_pips if engine else as_float(os.getenv("MAX_ENTRY_DRIFT_PIPS", "10"), 10),
                "FEATURE_ICT_MODE": engine.ict_mode_enabled if engine else os.getenv("FEATURE_ICT_MODE", "false").lower() in ["1", "true", "yes"],
                "ICT_MIN_SETUP_SCORE": engine.ict_min_setup_score if engine else as_float(os.getenv("ICT_MIN_SETUP_SCORE", "0.60"), 0.60),
                "ICT_MIN_CONFLUENCE": engine.ict_min_confluence if engine else as_float(os.getenv("ICT_MIN_CONFLUENCE", "0.60"), 0.60),
                "TRAILING_STOP_TRIGGER_PCT": to_percent(engine.trailing_stop_trigger_pct if engine else os.getenv("TRAILING_STOP_TRIGGER_PCT", "0.55"), 55),
                "TRAILING_STOP_LOCK_PIPS": engine.trailing_stop_lock_pips if engine else as_float(os.getenv("TRAILING_STOP_LOCK_PIPS", "10"), 10),
                "TRAILING_STOP_STEP_PCT": to_percent(engine.trailing_stop_step_pct if engine else os.getenv("TRAILING_STOP_STEP_PCT", "0.50"), 50),
                "TRAILING_STOP_MIN_STEP_PIPS": engine.trailing_stop_min_step_pips if engine else as_float(os.getenv("TRAILING_STOP_MIN_STEP_PIPS", "5"), 5),
                "FEATURE_TRAILING_TAKE_PROFIT": engine.trailing_tp_enabled if engine else os.getenv("FEATURE_TRAILING_TAKE_PROFIT", "true").lower() in ["1", "true", "yes"],
                "TRAILING_TP_TRIGGER_PCT": to_percent(engine.trailing_tp_trigger_pct if engine else os.getenv("TRAILING_TP_TRIGGER_PCT", "0.85"), 85),
                "TRAILING_TP_EXTENSION_PCT": to_percent(engine.trailing_tp_extension_pct if engine else os.getenv("TRAILING_TP_EXTENSION_PCT", "0.5"), 50),
                "TRAILING_TP_COOLDOWN_SECONDS": engine.trailing_tp_cooldown_seconds if engine else int(os.getenv("TRAILING_TP_COOLDOWN_SECONDS", "0")),
                "FEATURE_PARTIAL_TP_EXTEND": engine.partial_tp_extend_enabled if engine else os.getenv("FEATURE_PARTIAL_TP_EXTEND", "true").lower() in ["1", "true", "yes"],
                "PARTIAL_TP_EXTEND_PCT": to_percent(engine.partial_tp_extend_pct if engine else os.getenv("PARTIAL_TP_EXTEND_PCT", "0.5"), 50),
                "FEATURE_PARTIAL_TAKE_PROFIT": engine.partial_tp_enabled if engine else os.getenv("FEATURE_PARTIAL_TAKE_PROFIT", "true").lower() in ["1", "true", "yes"],
                "PARTIAL_TP_TRIGGER_R": engine.partial_tp_trigger_r if engine else as_float(os.getenv("PARTIAL_TP_TRIGGER_R", "0.75"), 0.75),
                "PARTIAL_TP_CLOSE_PCT": to_percent(engine.partial_tp_close_pct if engine else os.getenv("PARTIAL_TP_CLOSE_PCT", "0.5"), 50),
                "PARTIAL_TP_LOCK_PIPS": engine.partial_tp_lock_pips if engine else as_float(os.getenv("PARTIAL_TP_LOCK_PIPS", "10"), 10),
                "FEATURE_BREAKEVEN_PROTECTION": engine.breakeven_protection_enabled if engine else os.getenv("FEATURE_BREAKEVEN_PROTECTION", "true").lower() in ["1", "true", "yes"],
                "BREAKEVEN_TRIGGER_R": engine.breakeven_trigger_r if engine else as_float(os.getenv("BREAKEVEN_TRIGGER_R", "0.30"), 0.30),
                "BREAKEVEN_LOCK_PIPS": engine.breakeven_lock_pips if engine else as_float(os.getenv("BREAKEVEN_LOCK_PIPS", "0"), 0),
                "FEATURE_FIRST_PROFIT_BREAKEVEN": getattr(engine, "first_profit_breakeven_enabled", True) if engine else os.getenv("FEATURE_FIRST_PROFIT_BREAKEVEN", "true").lower() in ["1", "true", "yes"],
                "FIRST_PROFIT_BREAKEVEN_TRIGGER_R": getattr(engine, "first_profit_breakeven_trigger_r", as_float(os.getenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", "0.10"), 0.10)) if engine else as_float(os.getenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", "0.10"), 0.10),
                "FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP": getattr(engine, "first_profit_breakeven_trigger_r_scalp", as_float(os.getenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", "0.08"), 0.08)) if engine else as_float(os.getenv("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", "0.08"), 0.08),
                "FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY": engine.reversal_breakeven_at_entry_enabled if engine else os.getenv("FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY", "true").lower() in ["1", "true", "yes"],
                "FEATURE_MAX_ADVERSE_EXIT": engine.max_adverse_exit_enabled if engine else os.getenv("FEATURE_MAX_ADVERSE_EXIT", "true").lower() in ["1", "true", "yes"],
                "MAX_ADVERSE_R": engine.max_adverse_r if engine else as_float(os.getenv("MAX_ADVERSE_R", "0.90"), 0.90),
                "MAX_ADVERSE_R_FOREX": os.getenv("MAX_ADVERSE_R_FOREX", "0.85"),
                "MAX_ADVERSE_R_SCALP": os.getenv("MAX_ADVERSE_R_SCALP", "0.85"),
                "MAX_ADVERSE_R_INTRADAY": os.getenv("MAX_ADVERSE_R_INTRADAY", "0.90"),
                "MAX_ADVERSE_R_METAL": os.getenv("MAX_ADVERSE_R_METAL", "1.00"),
                "MAX_ADVERSE_R_SWING": os.getenv("MAX_ADVERSE_R_SWING", "1.10"),
                "FEATURE_SYMBOL_PROFILES": engine.symbol_profiles_enabled if engine else os.getenv("FEATURE_SYMBOL_PROFILES", "true").lower() in ["1", "true", "yes"],
                "FEATURE_INSTRUMENT_PROFILES": engine.instrument_profiles_enabled if engine else os.getenv("FEATURE_INSTRUMENT_PROFILES", "true").lower() in ["1", "true", "yes"],
                "MIN_PROFIT_PIPS_FOREX": os.getenv("MIN_PROFIT_PIPS_FOREX", os.getenv("MIN_PROFIT_PIPS_FX", "1.5")),
                "MAX_ENTRY_DRIFT_PIPS_FOREX": os.getenv("MAX_ENTRY_DRIFT_PIPS_FOREX", os.getenv("MAX_ENTRY_DRIFT_PIPS", "6")),
                "MAX_SPREAD_PIPS_FOREX": os.getenv("MAX_SPREAD_PIPS_FOREX", "2.5"),
                "MIN_PROFIT_PIPS_METAL": os.getenv("MIN_PROFIT_PIPS_METAL", os.getenv("MIN_PROFIT_PIPS_XAU", "20")),
                "MAX_ENTRY_DRIFT_PIPS_METAL": os.getenv("MAX_ENTRY_DRIFT_PIPS_METAL", os.getenv("MAX_ENTRY_DRIFT_PIPS_XAU", "50")),
                "MAX_SPREAD_PIPS_METAL": os.getenv("MAX_SPREAD_PIPS_METAL", "35"),
                "MIN_PROFIT_PIPS_STOCK": os.getenv("MIN_PROFIT_PIPS_STOCK", "20"),
                "MAX_ENTRY_DRIFT_PIPS_STOCK": os.getenv("MAX_ENTRY_DRIFT_PIPS_STOCK", "10"),
                "MAX_SPREAD_PIPS_STOCK": os.getenv("MAX_SPREAD_PIPS_STOCK", "5"),
                "MIN_SETUP_SCORE_STOCK": os.getenv("MIN_SETUP_SCORE_STOCK", "0.70"),
                "MIN_CONVICTION_STOCK": os.getenv("MIN_CONVICTION_STOCK", "0.45"),
                "MIN_SESSION_SCORE_STOCK": os.getenv("MIN_SESSION_SCORE_STOCK", "0.60"),
                "MIN_EXECUTION_GRADE_STOCK": os.getenv("MIN_EXECUTION_GRADE_STOCK", "A"),
                "BLOCK_STOCK_SCALPS": os.getenv("BLOCK_STOCK_SCALPS", "true").lower() in ["1", "true", "yes"],
                "FEATURE_TRADE_HORIZON_PROFILES": engine.trade_horizon_profiles_enabled if engine else os.getenv("FEATURE_TRADE_HORIZON_PROFILES", "true").lower() in ["1", "true", "yes"],
                "HORIZON_PROFILE_MODE": engine.horizon_profile_mode if engine else os.getenv("HORIZON_PROFILE_MODE", "exit_only"),
                "ENABLE_SCALP_PROFILE": engine.scalp_profile_enabled if engine else os.getenv("ENABLE_SCALP_PROFILE", "true").lower() in ["1", "true", "yes"],
                "ENABLE_INTRADAY_PROFILE": engine.intraday_profile_enabled if engine else os.getenv("ENABLE_INTRADAY_PROFILE", "true").lower() in ["1", "true", "yes"],
                "ENABLE_SWING_PROFILE": engine.swing_profile_enabled if engine else os.getenv("ENABLE_SWING_PROFILE", "true").lower() in ["1", "true", "yes"],
                "SCALP_EXECUTION_CONVICTION_THRESHOLD": os.getenv("SCALP_EXECUTION_CONVICTION_THRESHOLD", "0.28"),
                "SCALP_EXECUTION_SETUP_SCORE_THRESHOLD": os.getenv("SCALP_EXECUTION_SETUP_SCORE_THRESHOLD", "0.52"),
                "SCALP_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": os.getenv("SCALP_EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.55"),
                "SCALP_REQUIRE_HARD_STRUCTURE": os.getenv("SCALP_REQUIRE_HARD_STRUCTURE", "true").lower() in ["1", "true", "yes"],
                "INTRADAY_EXECUTION_CONVICTION_THRESHOLD": os.getenv("INTRADAY_EXECUTION_CONVICTION_THRESHOLD", "0.35"),
                "INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD": os.getenv("INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD", "0.50"),
                "INTRADAY_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": os.getenv("INTRADAY_EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.58"),
                "SWING_EXECUTION_CONVICTION_THRESHOLD": os.getenv("SWING_EXECUTION_CONVICTION_THRESHOLD", "0.42"),
                "SWING_EXECUTION_SETUP_SCORE_THRESHOLD": os.getenv("SWING_EXECUTION_SETUP_SCORE_THRESHOLD", "0.68"),
                "SWING_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": os.getenv("SWING_EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.66"),
                "SWING_REQUIRE_HTF": os.getenv("SWING_REQUIRE_HTF", "true").lower() in ["1", "true", "yes"],
                "SIGNAL_LOCKOUT_ENABLED": engine.signal_lockout_enabled if engine else os.getenv("SIGNAL_LOCKOUT_ENABLED", "true").lower() in ["1", "true", "yes"],
                "MAX_TRADES_PER_SYMBOL": engine.max_trades_per_symbol if engine else int(os.getenv("MAX_TRADES_PER_SYMBOL", "1")),
                "TRADE_COOLDOWN_MINUTES": engine.trade_cooldown_minutes if engine else int(os.getenv("TRADE_COOLDOWN_MINUTES", "0")),
                "FEATURE_COOLDOWN_OVERRIDE": engine.cooldown_override_enabled if engine else os.getenv("FEATURE_COOLDOWN_OVERRIDE", "false").lower() in ["1", "true", "yes"],
                "COOLDOWN_OVERRIDE_MIN_GRADE": engine.cooldown_override_min_grade if engine else os.getenv("COOLDOWN_OVERRIDE_MIN_GRADE", "A"),
                "COOLDOWN_OVERRIDE_MIN_SCORE": engine.cooldown_override_min_score if engine else as_float(os.getenv("COOLDOWN_OVERRIDE_MIN_SCORE", "0.78"), 0.78),
                "COOLDOWN_OVERRIDE_MIN_CONVICTION": engine.cooldown_override_min_conviction if engine else as_float(os.getenv("COOLDOWN_OVERRIDE_MIN_CONVICTION", "0.45"), 0.45),
                "COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE": engine.cooldown_override_require_spread_safe if engine else os.getenv("COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE", "true").lower() in ["1", "true", "yes"],
                "COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE": engine.cooldown_override_require_new_structure if engine else os.getenv("COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE", "true").lower() in ["1", "true", "yes"],
                "NO_REVENGE_COOLDOWN_SECONDS": engine.no_revenge_cooldown if engine else int(os.getenv("NO_REVENGE_COOLDOWN_SECONDS", "0")),
                "FEATURE_REVERSAL_SHOCK_GUARD": engine.reversal_shock_guard_enabled if engine else os.getenv("FEATURE_REVERSAL_SHOCK_GUARD", "false").lower() in ["1", "true", "yes"],
                "REVERSAL_SHOCK_COOLDOWN_MINUTES": engine.reversal_shock_cooldown_minutes if engine else int(os.getenv("REVERSAL_SHOCK_COOLDOWN_MINUTES", "0")),
                "REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES": engine.reversal_shock_xau_cooldown_minutes if engine else int(os.getenv("REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES", "0")),
                "FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT": engine.opposing_signal_profit_exit_enabled if engine else os.getenv("FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT", "true").lower() in ["1", "true", "yes"],
                "OPPOSING_SIGNAL_MIN_R": engine.opposing_signal_min_r if engine else as_float(os.getenv("OPPOSING_SIGNAL_MIN_R", "0.20"), 0.20),
                "OPPOSING_SIGNAL_MIN_SCORE": engine.opposing_signal_min_score if engine else as_float(os.getenv("OPPOSING_SIGNAL_MIN_SCORE", "0.58"), 0.58),
                "FEATURE_PROFESSIONAL_EXECUTION_GATE": engine.professional_gate_enabled if engine else os.getenv("FEATURE_PROFESSIONAL_EXECUTION_GATE", "true").lower() in ["1", "true", "yes"],
                "MIN_EXECUTION_GRADE": engine.min_execution_grade if engine else os.getenv("MIN_EXECUTION_GRADE", "B"),
                "ALLOW_C_GRADE_SCALPS": engine.allow_c_scalps if engine else os.getenv("ALLOW_C_GRADE_SCALPS", "false").lower() in ["1", "true", "yes"],
                "MIN_PROFESSIONAL_SETUP_SCORE": engine.min_professional_score if engine else as_float(os.getenv("MIN_PROFESSIONAL_SETUP_SCORE", "0.62"), 0.62),
                "MIN_PROFESSIONAL_CONVICTION": engine.min_professional_conviction if engine else as_float(os.getenv("MIN_PROFESSIONAL_CONVICTION", "0.30"), 0.30),
                "MIN_SESSION_SCORE_FOR_TRADE": engine.min_session_score_for_trade if engine else as_float(os.getenv("MIN_SESSION_SCORE_FOR_TRADE", "0.40"), 0.40),
                "MIN_SESSION_SCORE_FOR_SCALP": engine.min_session_score_for_scalp if engine else as_float(os.getenv("MIN_SESSION_SCORE_FOR_SCALP", "0.55"), 0.55),
                "BLOCK_CONTEXT_WATCH_TRADES": engine.block_context_watch_trades if engine else os.getenv("BLOCK_CONTEXT_WATCH_TRADES", "true").lower() in ["1", "true", "yes"],
                "FEATURE_EARLY_ENTRY": engine.early_entry_enabled if engine else os.getenv("FEATURE_EARLY_ENTRY", "true").lower() in ["1", "true", "yes"],
                "EARLY_ENTRY_MIN_SCORE": engine.early_entry_min_score if engine else as_float(os.getenv("EARLY_ENTRY_MIN_SCORE", "0.50"), 0.50),
                "EXECUTION_ARCHETYPE_SCORE_THRESHOLD": engine.execution_archetype_score_threshold if engine else as_float(os.getenv("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.58"), 0.58),
                "FEATURE_FALSE_MOVE_DETECTION": engine.false_move_detection_enabled if engine else os.getenv("FEATURE_FALSE_MOVE_DETECTION", "true").lower() in ["1", "true", "yes"],
                "FEATURE_NEWS_MODE": engine.news_mode_enabled if engine else os.getenv("FEATURE_NEWS_MODE", "true").lower() in ["1", "true", "yes"],
                "NEWS_BLOCK_UNSAFE": engine.news_block_unsafe if engine else os.getenv("NEWS_BLOCK_UNSAFE", "true").lower() in ["1", "true", "yes"],
                "NEWS_RISK_MULTIPLIER": to_percent(engine.news_risk_multiplier if engine else os.getenv("NEWS_RISK_MULTIPLIER", "0.35"), 35),
                "NEWS_ALLOW_RETEST_FOLLOW": engine.news_allow_retest_follow if engine else os.getenv("NEWS_ALLOW_RETEST_FOLLOW", "true").lower() in ["1", "true", "yes"],
                "FEATURE_NEWS_LADDER": engine.news_ladder_enabled if engine else os.getenv("FEATURE_NEWS_LADDER", "true").lower() in ["1", "true", "yes"],
                "NEWS_LADDER_MAX_ADDONS": engine.news_ladder_max_addons if engine else int(os.getenv("NEWS_LADDER_MAX_ADDONS", "2")),
                "NEWS_LADDER_MIN_R": engine.news_ladder_min_r if engine else as_float(os.getenv("NEWS_LADDER_MIN_R", "0.55"), 0.55),
                "NEWS_LADDER_VOLUME_PCT": to_percent(engine.news_ladder_volume_pct if engine else os.getenv("NEWS_LADDER_VOLUME_PCT", "0.35"), 35),
                "NEWS_LADDER_COOLDOWN_SECONDS": engine.news_ladder_cooldown_seconds if engine else int(os.getenv("NEWS_LADDER_COOLDOWN_SECONDS", "0")),
                "WAR_ROOM_ENABLED": engine.features.get("war_room", True) if engine else os.getenv("FEATURE_WAR_ROOM", "true").lower() in ["1", "true", "yes"],
                "FEATURE_CURRENCY_BASKET_GUARD": engine.currency_basket_guard_enabled if engine else os.getenv("FEATURE_CURRENCY_BASKET_GUARD", "true").lower() in ["1", "true", "yes"],
                "CURRENCY_BASKET_LIMITS": getattr(engine, "currency_basket_limits", {}) if engine else os.getenv("CURRENCY_BASKET_LIMITS", "USD_SHORT:2,USD_LONG:2,JPY_SHORT:2,JPY_LONG:2"),
                "FEATURE_WEBHOOK_ALERTS": engine.webhook_alerts_enabled if engine else os.getenv("FEATURE_WEBHOOK_ALERTS", "true").lower() in ["1", "true", "yes"],
                "MT5_ACCOUNT": os.getenv("MT5_ACCOUNT", ""),
                "MT5_SERVER": os.getenv("MT5_SERVER", ""),
                "ENV_ALL": safe_env_for_dashboard(current_env),
            }
            return jsonify({"status": "success", "data": config})

        # POST to update .env
        data = request.json or {}
        env_path = ENV_PATH

        # read current .env
        env_vars = read_env_file(env_path)
        if "ENV_ALL" in data:
            env_vars.update(parse_env_editor(data.get("ENV_ALL")))

        # update with posted values
        env_vars["TRADING_SYMBOLS"] = data.get("TRADING_SYMBOLS", env_vars.get("TRADING_SYMBOLS", ""))
        env_vars["EXECUTION_SYMBOLS"] = data.get("EXECUTION_SYMBOLS", env_vars.get("EXECUTION_SYMBOLS", env_vars.get("TRADING_SYMBOLS", "")))
        env_vars["TRADE_VOLUME"] = data.get("TRADE_VOLUME", env_vars.get("TRADE_VOLUME", "0.001"))
        env_vars["POSITION_SIZING_MODE"] = data.get("POSITION_SIZING_MODE", env_vars.get("POSITION_SIZING_MODE", "fixed"))
        env_vars["RISK_PER_TRADE_PCT"] = str(data.get("RISK_PER_TRADE_PCT", env_vars.get("RISK_PER_TRADE_PCT", os.getenv("RISK_PER_TRADE_PCT", "0.01"))))
        env_vars.pop("FEATURE_DYNAMIC_ACCOUNT_PROFILE", None)
        env_vars.pop("FEATURE_SMALL_ACCOUNT_MODE", None)
        env_vars.pop("SMALL_ACCOUNT_EQUITY_THRESHOLD", None)
        env_vars.pop("SMALL_ACCOUNT_TRADE_VOLUME", None)
        env_vars.pop("SMALL_ACCOUNT_MAX_AUTO_MIN_LOT", None)
        env_vars.pop("SMALL_ACCOUNT_MAX_EXPOSURE_PERCENT", None)
        env_vars.pop("SMALL_ACCOUNT_MAX_ACTIVE_TRADES", None)
        env_vars.pop("SMALL_ACCOUNT_ALLOW_METALS", None)
        env_vars.pop("SMALL_ACCOUNT_ALLOW_CRYPTO", None)
        env_vars.pop("SMALL_ACCOUNT_ALLOW_STOCKS", None)
        env_vars.pop("SMALL_ACCOUNT_DISABLE_NEWS_LADDER", None)
        env_vars.pop("SMALL_ACCOUNT_DISABLE_PENDING_ORDERS", None)
        env_vars.pop("RISK_PERCENT", None)
        env_vars["MAX_EXPOSURE_PERCENT"] = str(from_ui_percent(data.get("MAX_EXPOSURE_PERCENT", to_percent(normalize_fraction(env_vars.get("MAX_EXPOSURE_PERCENT", "0.05"), 0.05))), 5.0))
        env_vars["MIN_PROFIT_PIPS"] = data.get("MIN_PROFIT_PIPS", env_vars.get("MIN_PROFIT_PIPS", "50"))
        env_vars["DAILY_PROFIT_CAP"] = str(from_ui_percent(data.get("DAILY_PROFIT_CAP", to_percent(normalize_fraction(env_vars.get("DAILY_PROFIT_CAP", "0.02"), 0.02))), 2.0))
        env_vars["DAILY_PROFIT_CAP_EXTENSION"] = str(from_ui_percent(data.get("DAILY_PROFIT_CAP_EXTENSION", to_percent(normalize_fraction(env_vars.get("DAILY_PROFIT_CAP_EXTENSION", "0.0"), 0.0))), 0.0))
        env_vars["FEATURE_DAILY_LOSS_BRAKE"] = "false"
        env_vars["DAILY_LOSS_CAP_PERCENT"] = str(from_ui_percent(data.get("DAILY_LOSS_CAP_PERCENT", to_percent(normalize_fraction(env_vars.get("DAILY_LOSS_CAP_PERCENT", "0.05"), 0.05))), 5.0))
        env_vars["MAX_DAILY_LOSSES"] = str(data.get("MAX_DAILY_LOSSES", env_vars.get("MAX_DAILY_LOSSES", "100")))
        env_vars["MAX_CONSECUTIVE_LOSSES"] = str(data.get("MAX_CONSECUTIVE_LOSSES", env_vars.get("MAX_CONSECUTIVE_LOSSES", "30")))
        env_vars["LOSS_COOLDOWN_MINUTES"] = str(data.get("LOSS_COOLDOWN_MINUTES", env_vars.get("LOSS_COOLDOWN_MINUTES", "0")))
        env_vars["MAX_ACTIVE_TRADES_TOTAL"] = str(data.get("MAX_ACTIVE_TRADES_TOTAL", env_vars.get("MAX_ACTIVE_TRADES_TOTAL", "10")))
        env_vars["FEATURE_CATASTROPHIC_LOSS_STOP"] = str(data.get("FEATURE_CATASTROPHIC_LOSS_STOP", env_vars.get("FEATURE_CATASTROPHIC_LOSS_STOP", "true"))).lower()
        env_vars["CATASTROPHIC_LOSS_R"] = str(data.get("CATASTROPHIC_LOSS_R", env_vars.get("CATASTROPHIC_LOSS_R", "1.5")))
        env_vars["CATASTROPHIC_LOSS_COOLDOWN_MINUTES"] = str(data.get("CATASTROPHIC_LOSS_COOLDOWN_MINUTES", env_vars.get("CATASTROPHIC_LOSS_COOLDOWN_MINUTES", "0")))
        env_vars["MIN_EXPECTED_R"] = str(data.get("MIN_EXPECTED_R", env_vars.get("MIN_EXPECTED_R", "1.2")))
        env_vars["MIN_EXPECTED_R_SCALP"] = str(data.get("MIN_EXPECTED_R_SCALP", env_vars.get("MIN_EXPECTED_R_SCALP", "1.0")))
        env_vars["MIN_EXPECTED_R_INTRADAY"] = str(data.get("MIN_EXPECTED_R_INTRADAY", env_vars.get("MIN_EXPECTED_R_INTRADAY", env_vars.get("MIN_EXPECTED_R", "1.2"))))
        env_vars["MIN_EXPECTED_R_SWING"] = str(data.get("MIN_EXPECTED_R_SWING", env_vars.get("MIN_EXPECTED_R_SWING", "1.5")))
        env_vars["TAKE_PROFIT_R_MULTIPLIER"] = str(data.get("TAKE_PROFIT_R_MULTIPLIER", env_vars.get("TAKE_PROFIT_R_MULTIPLIER", "1.8")))
        env_vars["TAKE_PROFIT_R_MULTIPLIER_SCALP"] = str(data.get("TAKE_PROFIT_R_MULTIPLIER_SCALP", env_vars.get("TAKE_PROFIT_R_MULTIPLIER_SCALP", "1.5")))
        env_vars["TAKE_PROFIT_R_MULTIPLIER_INTRADAY"] = str(data.get("TAKE_PROFIT_R_MULTIPLIER_INTRADAY", env_vars.get("TAKE_PROFIT_R_MULTIPLIER_INTRADAY", env_vars.get("TAKE_PROFIT_R_MULTIPLIER", "1.8"))))
        env_vars["TAKE_PROFIT_R_MULTIPLIER_SWING"] = str(data.get("TAKE_PROFIT_R_MULTIPLIER_SWING", env_vars.get("TAKE_PROFIT_R_MULTIPLIER_SWING", "2.5")))
        env_vars["EXECUTION_CONVICTION_THRESHOLD"] = str(data.get("EXECUTION_CONVICTION_THRESHOLD", env_vars.get("EXECUTION_CONVICTION_THRESHOLD", "0.35")))
        env_vars["EXECUTION_SETUP_SCORE_THRESHOLD"] = str(data.get("EXECUTION_SETUP_SCORE_THRESHOLD", env_vars.get("EXECUTION_SETUP_SCORE_THRESHOLD", "0.50")))
        env_vars["MIN_TRADE_READINESS_SCORE"] = str(data.get("MIN_TRADE_READINESS_SCORE", env_vars.get("MIN_TRADE_READINESS_SCORE", "0.62")))
        env_vars["FEATURE_MTF_EXECUTION_GATE"] = str(data.get("FEATURE_MTF_EXECUTION_GATE", env_vars.get("FEATURE_MTF_EXECUTION_GATE", "true"))).lower()
        env_vars["MIN_MTF_EXECUTION_SCORE"] = str(data.get("MIN_MTF_EXECUTION_SCORE", env_vars.get("MIN_MTF_EXECUTION_SCORE", "0.30")))
        env_vars["MIN_MTF_EXECUTION_SCORE_METAL"] = str(data.get("MIN_MTF_EXECUTION_SCORE_METAL", env_vars.get("MIN_MTF_EXECUTION_SCORE_METAL", "0.45")))
        env_vars["MARKET_EXECUTION_SCORE_THRESHOLD"] = str(data.get("MARKET_EXECUTION_SCORE_THRESHOLD", env_vars.get("MARKET_EXECUTION_SCORE_THRESHOLD", "0.60")))
        env_vars["MARKET_EXECUTION_CONVICTION_THRESHOLD"] = str(data.get("MARKET_EXECUTION_CONVICTION_THRESHOLD", env_vars.get("MARKET_EXECUTION_CONVICTION_THRESHOLD", "0.35")))
        env_vars["ANALYTIC_TIMEFRAMES"] = str(data.get("ANALYTIC_TIMEFRAMES", env_vars.get("ANALYTIC_TIMEFRAMES", "M1,M5,M15,H1,H4")))
        env_vars["MAX_ENTRY_DRIFT_PIPS"] = str(data.get("MAX_ENTRY_DRIFT_PIPS", env_vars.get("MAX_ENTRY_DRIFT_PIPS", "10")))
        env_vars["FEATURE_ICT_MODE"] = str(data.get("FEATURE_ICT_MODE", env_vars.get("FEATURE_ICT_MODE", "false"))).lower()
        env_vars["ICT_MIN_SETUP_SCORE"] = str(data.get("ICT_MIN_SETUP_SCORE", env_vars.get("ICT_MIN_SETUP_SCORE", "0.60")))
        env_vars["ICT_MIN_CONFLUENCE"] = str(data.get("ICT_MIN_CONFLUENCE", env_vars.get("ICT_MIN_CONFLUENCE", "0.60")))
        env_vars["TRAILING_STOP_TRIGGER_PCT"] = str(from_percent(data.get("TRAILING_STOP_TRIGGER_PCT", env_vars.get("TRAILING_STOP_TRIGGER_PCT", "0.55")), 0.55))
        env_vars["TRAILING_STOP_LOCK_PIPS"] = str(data.get("TRAILING_STOP_LOCK_PIPS", env_vars.get("TRAILING_STOP_LOCK_PIPS", "10")))
        env_vars["TRAILING_STOP_STEP_PCT"] = str(from_percent(data.get("TRAILING_STOP_STEP_PCT", env_vars.get("TRAILING_STOP_STEP_PCT", "0.50")), 0.50))
        env_vars["TRAILING_STOP_MIN_STEP_PIPS"] = str(data.get("TRAILING_STOP_MIN_STEP_PIPS", env_vars.get("TRAILING_STOP_MIN_STEP_PIPS", "5")))
        env_vars["FEATURE_TRAILING_TAKE_PROFIT"] = str(data.get("FEATURE_TRAILING_TAKE_PROFIT", env_vars.get("FEATURE_TRAILING_TAKE_PROFIT", "true"))).lower()
        env_vars["TRAILING_TP_TRIGGER_PCT"] = str(from_percent(data.get("TRAILING_TP_TRIGGER_PCT", env_vars.get("TRAILING_TP_TRIGGER_PCT", "0.85")), 0.85))
        env_vars["TRAILING_TP_EXTENSION_PCT"] = str(from_percent(data.get("TRAILING_TP_EXTENSION_PCT", env_vars.get("TRAILING_TP_EXTENSION_PCT", "0.5")), 0.5))
        env_vars["TRAILING_TP_COOLDOWN_SECONDS"] = str(data.get("TRAILING_TP_COOLDOWN_SECONDS", env_vars.get("TRAILING_TP_COOLDOWN_SECONDS", "0")))
        env_vars["FEATURE_PARTIAL_TP_EXTEND"] = str(data.get("FEATURE_PARTIAL_TP_EXTEND", env_vars.get("FEATURE_PARTIAL_TP_EXTEND", "true"))).lower()
        env_vars["PARTIAL_TP_EXTEND_PCT"] = str(from_percent(data.get("PARTIAL_TP_EXTEND_PCT", env_vars.get("PARTIAL_TP_EXTEND_PCT", "0.5")), 0.5))
        env_vars["FEATURE_PARTIAL_TAKE_PROFIT"] = str(data.get("FEATURE_PARTIAL_TAKE_PROFIT", env_vars.get("FEATURE_PARTIAL_TAKE_PROFIT", "true"))).lower()
        env_vars["PARTIAL_TP_TRIGGER_R"] = str(data.get("PARTIAL_TP_TRIGGER_R", env_vars.get("PARTIAL_TP_TRIGGER_R", "0.75")))
        env_vars["PARTIAL_TP_CLOSE_PCT"] = str(from_percent(data.get("PARTIAL_TP_CLOSE_PCT", env_vars.get("PARTIAL_TP_CLOSE_PCT", "0.5")), 0.5))
        env_vars["PARTIAL_TP_LOCK_PIPS"] = str(data.get("PARTIAL_TP_LOCK_PIPS", env_vars.get("PARTIAL_TP_LOCK_PIPS", "10")))
        env_vars["FEATURE_BREAKEVEN_PROTECTION"] = str(data.get("FEATURE_BREAKEVEN_PROTECTION", env_vars.get("FEATURE_BREAKEVEN_PROTECTION", "true"))).lower()
        env_vars["BREAKEVEN_TRIGGER_R"] = str(data.get("BREAKEVEN_TRIGGER_R", env_vars.get("BREAKEVEN_TRIGGER_R", "0.30")))
        env_vars["BREAKEVEN_LOCK_PIPS"] = str(data.get("BREAKEVEN_LOCK_PIPS", env_vars.get("BREAKEVEN_LOCK_PIPS", "0")))
        env_vars["FEATURE_FIRST_PROFIT_BREAKEVEN"] = str(data.get("FEATURE_FIRST_PROFIT_BREAKEVEN", env_vars.get("FEATURE_FIRST_PROFIT_BREAKEVEN", "true"))).lower()
        env_vars["FIRST_PROFIT_BREAKEVEN_TRIGGER_R"] = str(data.get("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", env_vars.get("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", "0.10")))
        env_vars["FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP"] = str(data.get("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", env_vars.get("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", "0.08")))
        env_vars["FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY"] = str(data.get("FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY", env_vars.get("FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY", "true"))).lower()
        env_vars["FEATURE_MAX_ADVERSE_EXIT"] = str(data.get("FEATURE_MAX_ADVERSE_EXIT", env_vars.get("FEATURE_MAX_ADVERSE_EXIT", "true"))).lower()
        env_vars["MAX_ADVERSE_R"] = str(data.get("MAX_ADVERSE_R", env_vars.get("MAX_ADVERSE_R", "0.90")))
        env_vars["MAX_ADVERSE_R_FOREX"] = str(data.get("MAX_ADVERSE_R_FOREX", env_vars.get("MAX_ADVERSE_R_FOREX", "0.85")))
        env_vars["MAX_ADVERSE_R_SCALP"] = str(data.get("MAX_ADVERSE_R_SCALP", env_vars.get("MAX_ADVERSE_R_SCALP", "0.85")))
        env_vars["MAX_ADVERSE_R_INTRADAY"] = str(data.get("MAX_ADVERSE_R_INTRADAY", env_vars.get("MAX_ADVERSE_R_INTRADAY", "0.90")))
        env_vars["MAX_ADVERSE_R_METAL"] = str(data.get("MAX_ADVERSE_R_METAL", env_vars.get("MAX_ADVERSE_R_METAL", "1.00")))
        env_vars["MAX_ADVERSE_R_SWING"] = str(data.get("MAX_ADVERSE_R_SWING", env_vars.get("MAX_ADVERSE_R_SWING", "1.10")))
        env_vars["FEATURE_SYMBOL_PROFILES"] = str(data.get("FEATURE_SYMBOL_PROFILES", env_vars.get("FEATURE_SYMBOL_PROFILES", "true"))).lower()
        env_vars["FEATURE_INSTRUMENT_PROFILES"] = str(data.get("FEATURE_INSTRUMENT_PROFILES", env_vars.get("FEATURE_INSTRUMENT_PROFILES", "true"))).lower()
        for key, default in {
            "MIN_PROFIT_PIPS_FOREX": env_vars.get("MIN_PROFIT_PIPS_FX", "1.5"),
            "MAX_ENTRY_DRIFT_PIPS_FOREX": env_vars.get("MAX_ENTRY_DRIFT_PIPS", "6"),
            "MAX_SPREAD_PIPS_FOREX": "2.5",
            "MIN_PROFIT_PIPS_METAL": env_vars.get("MIN_PROFIT_PIPS_XAU", "20"),
            "MAX_ENTRY_DRIFT_PIPS_METAL": env_vars.get("MAX_ENTRY_DRIFT_PIPS_XAU", "50"),
            "MAX_SPREAD_PIPS_METAL": "35",
            "MIN_PROFIT_PIPS_STOCK": "20",
            "MAX_ENTRY_DRIFT_PIPS_STOCK": "10",
            "MAX_SPREAD_PIPS_STOCK": "5",
            "MIN_SETUP_SCORE_STOCK": "0.70",
            "MIN_CONVICTION_STOCK": "0.45",
            "MIN_SESSION_SCORE_STOCK": "0.60",
            "MIN_EXECUTION_GRADE_STOCK": "A",
            "BLOCK_STOCK_SCALPS": "true",
        }.items():
            env_vars[key] = str(data.get(key, env_vars.get(key, default))).lower() if key.startswith("BLOCK_") else str(data.get(key, env_vars.get(key, default)))
        env_vars["FEATURE_TRADE_HORIZON_PROFILES"] = str(data.get("FEATURE_TRADE_HORIZON_PROFILES", env_vars.get("FEATURE_TRADE_HORIZON_PROFILES", "true"))).lower()
        env_vars["HORIZON_PROFILE_MODE"] = str(data.get("HORIZON_PROFILE_MODE", env_vars.get("HORIZON_PROFILE_MODE", "exit_only"))).lower()
        env_vars["ENABLE_SCALP_PROFILE"] = str(data.get("ENABLE_SCALP_PROFILE", env_vars.get("ENABLE_SCALP_PROFILE", "true"))).lower()
        env_vars["ENABLE_INTRADAY_PROFILE"] = str(data.get("ENABLE_INTRADAY_PROFILE", env_vars.get("ENABLE_INTRADAY_PROFILE", "true"))).lower()
        env_vars["ENABLE_SWING_PROFILE"] = str(data.get("ENABLE_SWING_PROFILE", env_vars.get("ENABLE_SWING_PROFILE", "true"))).lower()
        for key, default in {
            "SCALP_EXECUTION_CONVICTION_THRESHOLD": "0.28",
            "SCALP_EXECUTION_SETUP_SCORE_THRESHOLD": "0.52",
            "SCALP_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": "0.55",
            "SCALP_MIN_PROFESSIONAL_SETUP_SCORE": "0.58",
            "SCALP_MIN_PROFESSIONAL_CONVICTION": "0.28",
            "SCALP_MIN_SESSION_SCORE": "0.55",
            "SCALP_REQUIRE_HARD_STRUCTURE": "true",
            "SCALP_REQUIRE_HTF": "false",
            "SCALP_ALLOW_C_GRADE": "false",
            "INTRADAY_EXECUTION_CONVICTION_THRESHOLD": "0.35",
            "INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD": "0.50",
            "INTRADAY_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": "0.58",
            "INTRADAY_MIN_PROFESSIONAL_SETUP_SCORE": "0.62",
            "INTRADAY_MIN_PROFESSIONAL_CONVICTION": "0.30",
            "INTRADAY_MIN_SESSION_SCORE": "0.40",
            "INTRADAY_REQUIRE_HARD_STRUCTURE": "false",
            "INTRADAY_REQUIRE_HTF": "false",
            "INTRADAY_ALLOW_C_GRADE": "false",
            "SWING_EXECUTION_CONVICTION_THRESHOLD": "0.42",
            "SWING_EXECUTION_SETUP_SCORE_THRESHOLD": "0.68",
            "SWING_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": "0.66",
            "SWING_MIN_PROFESSIONAL_SETUP_SCORE": "0.68",
            "SWING_MIN_PROFESSIONAL_CONVICTION": "0.40",
            "SWING_MIN_SESSION_SCORE": "0.30",
            "SWING_REQUIRE_HARD_STRUCTURE": "true",
            "SWING_REQUIRE_HTF": "true",
            "SWING_ALLOW_C_GRADE": "false",
        }.items():
            value = data.get(key, env_vars.get(key, default))
            env_vars[key] = str(value).lower() if key.endswith(("REQUIRE_HARD_STRUCTURE", "REQUIRE_HTF", "ALLOW_C_GRADE")) else str(value)
        env_vars["SIGNAL_LOCKOUT_ENABLED"] = str(data.get("SIGNAL_LOCKOUT_ENABLED", env_vars.get("SIGNAL_LOCKOUT_ENABLED", "true"))).lower()
        env_vars["MAX_TRADES_PER_SYMBOL"] = str(data.get("MAX_TRADES_PER_SYMBOL", env_vars.get("MAX_TRADES_PER_SYMBOL", "1")))
        env_vars["TRADE_COOLDOWN_MINUTES"] = str(data.get("TRADE_COOLDOWN_MINUTES", env_vars.get("TRADE_COOLDOWN_MINUTES", "0")))
        env_vars["FEATURE_COOLDOWN_OVERRIDE"] = str(data.get("FEATURE_COOLDOWN_OVERRIDE", env_vars.get("FEATURE_COOLDOWN_OVERRIDE", "false"))).lower()
        env_vars["COOLDOWN_OVERRIDE_MIN_GRADE"] = str(data.get("COOLDOWN_OVERRIDE_MIN_GRADE", env_vars.get("COOLDOWN_OVERRIDE_MIN_GRADE", "A"))).upper()
        env_vars["COOLDOWN_OVERRIDE_MIN_SCORE"] = str(data.get("COOLDOWN_OVERRIDE_MIN_SCORE", env_vars.get("COOLDOWN_OVERRIDE_MIN_SCORE", "0.78")))
        env_vars["COOLDOWN_OVERRIDE_MIN_CONVICTION"] = str(data.get("COOLDOWN_OVERRIDE_MIN_CONVICTION", env_vars.get("COOLDOWN_OVERRIDE_MIN_CONVICTION", "0.45")))
        env_vars["COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE"] = str(data.get("COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE", env_vars.get("COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE", "true"))).lower()
        env_vars["COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE"] = str(data.get("COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE", env_vars.get("COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE", "true"))).lower()
        env_vars["NO_REVENGE_COOLDOWN_SECONDS"] = str(data.get("NO_REVENGE_COOLDOWN_SECONDS", env_vars.get("NO_REVENGE_COOLDOWN_SECONDS", "0")))
        env_vars["FEATURE_REVERSAL_SHOCK_GUARD"] = str(data.get("FEATURE_REVERSAL_SHOCK_GUARD", env_vars.get("FEATURE_REVERSAL_SHOCK_GUARD", "false"))).lower()
        env_vars["REVERSAL_SHOCK_COOLDOWN_MINUTES"] = str(data.get("REVERSAL_SHOCK_COOLDOWN_MINUTES", env_vars.get("REVERSAL_SHOCK_COOLDOWN_MINUTES", "0")))
        env_vars["REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES"] = str(data.get("REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES", env_vars.get("REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES", "0")))
        env_vars["FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT"] = str(data.get("FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT", env_vars.get("FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT", "true"))).lower()
        env_vars["OPPOSING_SIGNAL_MIN_R"] = str(data.get("OPPOSING_SIGNAL_MIN_R", env_vars.get("OPPOSING_SIGNAL_MIN_R", "0.20")))
        env_vars["OPPOSING_SIGNAL_MIN_SCORE"] = str(data.get("OPPOSING_SIGNAL_MIN_SCORE", env_vars.get("OPPOSING_SIGNAL_MIN_SCORE", "0.58")))
        env_vars["FEATURE_PROFESSIONAL_EXECUTION_GATE"] = str(data.get("FEATURE_PROFESSIONAL_EXECUTION_GATE", env_vars.get("FEATURE_PROFESSIONAL_EXECUTION_GATE", "true"))).lower()
        env_vars["MIN_EXECUTION_GRADE"] = str(data.get("MIN_EXECUTION_GRADE", env_vars.get("MIN_EXECUTION_GRADE", "B"))).upper()
        env_vars["ALLOW_C_GRADE_SCALPS"] = str(data.get("ALLOW_C_GRADE_SCALPS", env_vars.get("ALLOW_C_GRADE_SCALPS", "false"))).lower()
        env_vars["MIN_PROFESSIONAL_SETUP_SCORE"] = str(data.get("MIN_PROFESSIONAL_SETUP_SCORE", env_vars.get("MIN_PROFESSIONAL_SETUP_SCORE", "0.62")))
        env_vars["MIN_PROFESSIONAL_CONVICTION"] = str(data.get("MIN_PROFESSIONAL_CONVICTION", env_vars.get("MIN_PROFESSIONAL_CONVICTION", "0.30")))
        env_vars["MIN_SESSION_SCORE_FOR_TRADE"] = str(data.get("MIN_SESSION_SCORE_FOR_TRADE", env_vars.get("MIN_SESSION_SCORE_FOR_TRADE", "0.40")))
        env_vars["MIN_SESSION_SCORE_FOR_SCALP"] = str(data.get("MIN_SESSION_SCORE_FOR_SCALP", env_vars.get("MIN_SESSION_SCORE_FOR_SCALP", "0.55")))
        env_vars["BLOCK_CONTEXT_WATCH_TRADES"] = str(data.get("BLOCK_CONTEXT_WATCH_TRADES", env_vars.get("BLOCK_CONTEXT_WATCH_TRADES", "true"))).lower()
        env_vars["FEATURE_EARLY_ENTRY"] = str(data.get("FEATURE_EARLY_ENTRY", env_vars.get("FEATURE_EARLY_ENTRY", "true"))).lower()
        env_vars["EARLY_ENTRY_MIN_SCORE"] = str(data.get("EARLY_ENTRY_MIN_SCORE", env_vars.get("EARLY_ENTRY_MIN_SCORE", "0.50")))
        env_vars["EXECUTION_ARCHETYPE_SCORE_THRESHOLD"] = str(data.get("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", env_vars.get("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", "0.58")))
        env_vars["FEATURE_FALSE_MOVE_DETECTION"] = str(data.get("FEATURE_FALSE_MOVE_DETECTION", env_vars.get("FEATURE_FALSE_MOVE_DETECTION", "true"))).lower()
        env_vars["FEATURE_NEWS_MODE"] = str(data.get("FEATURE_NEWS_MODE", env_vars.get("FEATURE_NEWS_MODE", "true"))).lower()
        env_vars["NEWS_BLOCK_UNSAFE"] = str(data.get("NEWS_BLOCK_UNSAFE", env_vars.get("NEWS_BLOCK_UNSAFE", "true"))).lower()
        env_vars["NEWS_RISK_MULTIPLIER"] = str(from_percent(data.get("NEWS_RISK_MULTIPLIER", env_vars.get("NEWS_RISK_MULTIPLIER", "0.35")), 0.35))
        env_vars["NEWS_ALLOW_RETEST_FOLLOW"] = str(data.get("NEWS_ALLOW_RETEST_FOLLOW", env_vars.get("NEWS_ALLOW_RETEST_FOLLOW", "true"))).lower()
        env_vars["FEATURE_NEWS_LADDER"] = str(data.get("FEATURE_NEWS_LADDER", env_vars.get("FEATURE_NEWS_LADDER", "true"))).lower()
        env_vars["NEWS_LADDER_MAX_ADDONS"] = str(data.get("NEWS_LADDER_MAX_ADDONS", env_vars.get("NEWS_LADDER_MAX_ADDONS", "2")))
        env_vars["NEWS_LADDER_MIN_R"] = str(data.get("NEWS_LADDER_MIN_R", env_vars.get("NEWS_LADDER_MIN_R", "0.55")))
        env_vars["NEWS_LADDER_VOLUME_PCT"] = str(from_percent(data.get("NEWS_LADDER_VOLUME_PCT", env_vars.get("NEWS_LADDER_VOLUME_PCT", "0.35")), 0.35))
        env_vars["NEWS_LADDER_COOLDOWN_SECONDS"] = str(data.get("NEWS_LADDER_COOLDOWN_SECONDS", env_vars.get("NEWS_LADDER_COOLDOWN_SECONDS", "0")))
        env_vars["FEATURE_WAR_ROOM"] = str(data.get("WAR_ROOM_ENABLED", env_vars.get("FEATURE_WAR_ROOM", "true"))).lower()
        env_vars["FEATURE_CURRENCY_BASKET_GUARD"] = str(data.get("FEATURE_CURRENCY_BASKET_GUARD", env_vars.get("FEATURE_CURRENCY_BASKET_GUARD", "true"))).lower()
        env_vars["CURRENCY_BASKET_LIMITS"] = str(data.get("CURRENCY_BASKET_LIMITS", env_vars.get("CURRENCY_BASKET_LIMITS", "USD_SHORT:2,USD_LONG:2,JPY_SHORT:2,JPY_LONG:2")))
        env_vars["FEATURE_WEBHOOK_ALERTS"] = str(data.get("FEATURE_WEBHOOK_ALERTS", env_vars.get("FEATURE_WEBHOOK_ALERTS", "true"))).lower()
        env_vars["MT5_ACCOUNT"] = data.get("MT5_ACCOUNT", env_vars.get("MT5_ACCOUNT", ""))
        env_vars["MT5_SERVER"] = data.get("MT5_SERVER", env_vars.get("MT5_SERVER", ""))

        # Rule toggles persist to .env
        for obsolete_key in ["RULE_EMA", "RULE_VOLUME", "RULE_PO3"]:
            env_vars.pop(obsolete_key, None)

        # if bot is running, apply new config live
        if engine:
            if "TRADING_SYMBOLS" in data:
                engine.symbols = data["TRADING_SYMBOLS"].split(",") if isinstance(data["TRADING_SYMBOLS"], str) else data["TRADING_SYMBOLS"]
            if "EXECUTION_SYMBOLS" in data:
                engine.execution_symbols = [
                    str(s).strip().upper()
                    for s in (data["EXECUTION_SYMBOLS"].split(",") if isinstance(data["EXECUTION_SYMBOLS"], str) else data["EXECUTION_SYMBOLS"])
                    if str(s).strip()
                ]
            if "TRADE_VOLUME" in data:
                try:
                    engine.volume = float(data["TRADE_VOLUME"])
                except Exception:
                    pass
            if "POSITION_SIZING_MODE" in data:
                try:
                    engine.position_sizing_mode = str(data["POSITION_SIZING_MODE"]).strip().lower() or "fixed"
                except Exception:
                    pass
            if "RISK_PER_TRADE_PCT" in data:
                try:
                    engine.risk_pct = float(data["RISK_PER_TRADE_PCT"])
                except Exception:
                    pass
            if "MAX_EXPOSURE_PERCENT" in data:
                try:
                    engine.max_exposure_pct = from_ui_percent(data["MAX_EXPOSURE_PERCENT"], 5.0)
                except Exception:
                    pass
            if "MIN_PROFIT_PIPS" in data:
                try:
                    engine.min_profit_pips = float(data["MIN_PROFIT_PIPS"])
                except Exception:
                    pass
            if "DAILY_PROFIT_CAP" in data:
                try:
                    engine.daily_profit_cap = from_ui_percent(data["DAILY_PROFIT_CAP"], 2.0)
                except Exception:
                    pass
            if "DAILY_PROFIT_CAP_EXTENSION" in data:
                try:
                    engine.daily_profit_cap_extension = from_ui_percent(data["DAILY_PROFIT_CAP_EXTENSION"], 0.0)
                except Exception:
                    pass
            # Daily loss brake is permanently disabled for this account.
            if "DAILY_LOSS_CAP_PERCENT" in data:
                try:
                    engine.daily_loss_cap_pct = from_ui_percent(data["DAILY_LOSS_CAP_PERCENT"], 5.0)
                except Exception:
                    pass
            if "MAX_DAILY_LOSSES" in data:
                try:
                    engine.max_daily_losses = max(0, min(100, int(data["MAX_DAILY_LOSSES"])))
                except Exception:
                    pass
            if "MAX_CONSECUTIVE_LOSSES" in data:
                try:
                    engine.max_consecutive_losses = max(0, min(100, int(data["MAX_CONSECUTIVE_LOSSES"])))
                except Exception:
                    pass
            if "LOSS_COOLDOWN_MINUTES" in data:
                try:
                    engine.loss_cooldown_minutes = int(data["LOSS_COOLDOWN_MINUTES"])
                except Exception:
                    pass
            if "MAX_ACTIVE_TRADES_TOTAL" in data:
                try:
                    engine.max_active_trades_total = int(data["MAX_ACTIVE_TRADES_TOTAL"])
                except Exception:
                    pass
            if "FEATURE_CATASTROPHIC_LOSS_STOP" in data:
                engine.catastrophic_loss_stop_enabled = parse_bool(data["FEATURE_CATASTROPHIC_LOSS_STOP"])
            if "CATASTROPHIC_LOSS_R" in data:
                try:
                    engine.catastrophic_loss_r = float(data["CATASTROPHIC_LOSS_R"])
                except Exception:
                    pass
            if "CATASTROPHIC_LOSS_COOLDOWN_MINUTES" in data:
                try:
                    engine.catastrophic_loss_cooldown_minutes = int(data["CATASTROPHIC_LOSS_COOLDOWN_MINUTES"])
                except Exception:
                    pass
            if "MIN_EXPECTED_R" in data:
                try:
                    engine.min_expected_r = float(data["MIN_EXPECTED_R"])
                except Exception:
                    pass
            if "MIN_EXPECTED_R_SCALP" in data:
                try:
                    engine.min_expected_r_scalp = float(data["MIN_EXPECTED_R_SCALP"])
                except Exception:
                    pass
            if "TAKE_PROFIT_R_MULTIPLIER" in data:
                try:
                    engine.take_profit_r_multiplier = max(0.1, float(data["TAKE_PROFIT_R_MULTIPLIER"]))
                except Exception:
                    pass
            if "TAKE_PROFIT_R_MULTIPLIER_SCALP" in data:
                try:
                    engine.take_profit_r_multiplier_scalp = max(0.1, float(data["TAKE_PROFIT_R_MULTIPLIER_SCALP"]))
                except Exception:
                    pass
            if "EXECUTION_CONVICTION_THRESHOLD" in data:
                try:
                    engine.execution_conviction_threshold = float(data["EXECUTION_CONVICTION_THRESHOLD"])
                except Exception:
                    pass
            if "EXECUTION_SETUP_SCORE_THRESHOLD" in data:
                try:
                    engine.execution_setup_score_threshold = float(data["EXECUTION_SETUP_SCORE_THRESHOLD"])
                except Exception:
                    pass
            if "MIN_TRADE_READINESS_SCORE" in data:
                try:
                    engine.min_trade_readiness_score = float(data["MIN_TRADE_READINESS_SCORE"])
                except Exception:
                    pass
            if "FEATURE_MTF_EXECUTION_GATE" in data:
                engine.mtf_execution_gate_enabled = parse_bool(data["FEATURE_MTF_EXECUTION_GATE"])
            if "MIN_MTF_EXECUTION_SCORE" in data:
                try:
                    engine.min_mtf_execution_score = float(data["MIN_MTF_EXECUTION_SCORE"])
                except Exception:
                    pass
            if "MIN_MTF_EXECUTION_SCORE_METAL" in data:
                try:
                    engine.min_mtf_execution_score_metal = float(data["MIN_MTF_EXECUTION_SCORE_METAL"])
                except Exception:
                    pass
            if "MARKET_EXECUTION_SCORE_THRESHOLD" in data:
                try:
                    engine.market_execution_score_threshold = float(data["MARKET_EXECUTION_SCORE_THRESHOLD"])
                except Exception:
                    pass
            if "MARKET_EXECUTION_CONVICTION_THRESHOLD" in data:
                try:
                    engine.market_execution_conviction_threshold = float(data["MARKET_EXECUTION_CONVICTION_THRESHOLD"])
                except Exception:
                    pass
            if "MAX_ENTRY_DRIFT_PIPS" in data:
                try:
                    engine.max_entry_drift_pips = float(data["MAX_ENTRY_DRIFT_PIPS"])
                except Exception:
                    pass
            if "FEATURE_ICT_MODE" in data:
                engine.ict_mode_enabled = str(data["FEATURE_ICT_MODE"]).lower() in ["1", "true", "yes", "on"]
            if "ICT_MIN_SETUP_SCORE" in data:
                try:
                    engine.ict_min_setup_score = float(data["ICT_MIN_SETUP_SCORE"])
                except Exception:
                    pass
            if "ICT_MIN_CONFLUENCE" in data:
                try:
                    engine.ict_min_confluence = float(data["ICT_MIN_CONFLUENCE"])
                except Exception:
                    pass
            if "TRAILING_STOP_TRIGGER_PCT" in data:
                try:
                    engine.trailing_stop_trigger_pct = from_percent(data["TRAILING_STOP_TRIGGER_PCT"], 0.55)
                except Exception:
                    pass
            if "TRAILING_STOP_LOCK_PIPS" in data:
                try:
                    engine.trailing_stop_lock_pips = float(data["TRAILING_STOP_LOCK_PIPS"])
                except Exception:
                    pass
            if "TRAILING_STOP_STEP_PCT" in data:
                try:
                    engine.trailing_stop_step_pct = from_percent(data["TRAILING_STOP_STEP_PCT"], 0.40)
                except Exception:
                    pass
            if "TRAILING_STOP_MIN_STEP_PIPS" in data:
                try:
                    engine.trailing_stop_min_step_pips = float(data["TRAILING_STOP_MIN_STEP_PIPS"])
                except Exception:
                    pass
            if "FEATURE_TRAILING_TAKE_PROFIT" in data:
                try:
                    engine.trailing_tp_enabled = parse_bool(data["FEATURE_TRAILING_TAKE_PROFIT"])
                except Exception:
                    pass
            if "TRAILING_TP_TRIGGER_PCT" in data:
                try:
                    engine.trailing_tp_trigger_pct = from_percent(data["TRAILING_TP_TRIGGER_PCT"], 0.85)
                except Exception:
                    pass
            if "TRAILING_TP_EXTENSION_PCT" in data:
                try:
                    engine.trailing_tp_extension_pct = from_percent(data["TRAILING_TP_EXTENSION_PCT"], 0.5)
                except Exception:
                    pass
            if "TRAILING_TP_COOLDOWN_SECONDS" in data:
                try:
                    engine.trailing_tp_cooldown_seconds = int(data["TRAILING_TP_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "FEATURE_PARTIAL_TP_EXTEND" in data:
                engine.partial_tp_extend_enabled = parse_bool(data["FEATURE_PARTIAL_TP_EXTEND"])
            if "PARTIAL_TP_EXTEND_PCT" in data:
                try:
                    engine.partial_tp_extend_pct = from_percent(data["PARTIAL_TP_EXTEND_PCT"], 0.5)
                except Exception:
                    pass
            if "FEATURE_PARTIAL_TAKE_PROFIT" in data:
                engine.partial_tp_enabled = parse_bool(data["FEATURE_PARTIAL_TAKE_PROFIT"])
            if "PARTIAL_TP_TRIGGER_R" in data:
                try:
                    engine.partial_tp_trigger_r = float(data["PARTIAL_TP_TRIGGER_R"])
                except Exception:
                    pass
            if "PARTIAL_TP_CLOSE_PCT" in data:
                try:
                    engine.partial_tp_close_pct = from_percent(data["PARTIAL_TP_CLOSE_PCT"], 0.5)
                except Exception:
                    pass
            if "PARTIAL_TP_LOCK_PIPS" in data:
                try:
                    engine.partial_tp_lock_pips = max(0.0, float(data["PARTIAL_TP_LOCK_PIPS"]))
                except Exception:
                    pass
            if "FEATURE_BREAKEVEN_PROTECTION" in data:
                engine.breakeven_protection_enabled = parse_bool(data["FEATURE_BREAKEVEN_PROTECTION"])
            if "BREAKEVEN_TRIGGER_R" in data:
                try:
                    engine.breakeven_trigger_r = max(0.05, float(data["BREAKEVEN_TRIGGER_R"]))
                except Exception:
                    pass
            if "BREAKEVEN_LOCK_PIPS" in data:
                try:
                    engine.breakeven_lock_pips = max(0.0, float(data["BREAKEVEN_LOCK_PIPS"]))
                except Exception:
                    pass
            if "FEATURE_FIRST_PROFIT_BREAKEVEN" in data:
                engine.first_profit_breakeven_enabled = parse_bool(data["FEATURE_FIRST_PROFIT_BREAKEVEN"])
            if "FIRST_PROFIT_BREAKEVEN_TRIGGER_R" in data:
                try:
                    engine.first_profit_breakeven_trigger_r = max(0.02, float(data["FIRST_PROFIT_BREAKEVEN_TRIGGER_R"]))
                except Exception:
                    pass
            if "FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP" in data:
                try:
                    engine.first_profit_breakeven_trigger_r_scalp = max(0.02, float(data["FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP"]))
                except Exception:
                    pass
            if "FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY" in data:
                engine.reversal_breakeven_at_entry_enabled = parse_bool(data["FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY"])
            if "FEATURE_MAX_ADVERSE_EXIT" in data:
                engine.max_adverse_exit_enabled = parse_bool(data["FEATURE_MAX_ADVERSE_EXIT"])
            if "MAX_ADVERSE_R" in data:
                try:
                    engine.max_adverse_r = max(0.1, min(2.0, float(data["MAX_ADVERSE_R"])))
                except Exception:
                    pass
            if "FEATURE_SYMBOL_PROFILES" in data:
                try:
                    engine.symbol_profiles_enabled = str(data["FEATURE_SYMBOL_PROFILES"]).lower() in ["1", "true", "yes", "on"]
                except Exception:
                    pass
            if "FEATURE_INSTRUMENT_PROFILES" in data:
                try:
                    engine.instrument_profiles_enabled = str(data["FEATURE_INSTRUMENT_PROFILES"]).lower() in ["1", "true", "yes", "on"]
                except Exception:
                    pass
            if "FEATURE_TRADE_HORIZON_PROFILES" in data:
                engine.trade_horizon_profiles_enabled = str(data["FEATURE_TRADE_HORIZON_PROFILES"]).lower() in ["1", "true", "yes", "on"]
            if "HORIZON_PROFILE_MODE" in data:
                engine.horizon_profile_mode = str(data["HORIZON_PROFILE_MODE"]).strip().lower() or "exit_only"
            if "ENABLE_SCALP_PROFILE" in data:
                engine.scalp_profile_enabled = str(data["ENABLE_SCALP_PROFILE"]).lower() in ["1", "true", "yes", "on"]
            if "ENABLE_INTRADAY_PROFILE" in data:
                engine.intraday_profile_enabled = str(data["ENABLE_INTRADAY_PROFILE"]).lower() in ["1", "true", "yes", "on"]
            if "ENABLE_SWING_PROFILE" in data:
                engine.swing_profile_enabled = str(data["ENABLE_SWING_PROFILE"]).lower() in ["1", "true", "yes", "on"]
            if "SIGNAL_LOCKOUT_ENABLED" in data:
                try:
                    engine.signal_lockout_enabled = str(data["SIGNAL_LOCKOUT_ENABLED"]).lower() in ["1", "true", "yes", "on"]
                except Exception:
                    pass
            if "MAX_TRADES_PER_SYMBOL" in data:
                try:
                    engine.max_trades_per_symbol = int(data["MAX_TRADES_PER_SYMBOL"])
                except Exception:
                    pass
            if "TRADE_COOLDOWN_MINUTES" in data:
                try:
                    engine.trade_cooldown_minutes = int(data["TRADE_COOLDOWN_MINUTES"])
                except Exception:
                    pass
            if "FEATURE_COOLDOWN_OVERRIDE" in data:
                engine.cooldown_override_enabled = str(data["FEATURE_COOLDOWN_OVERRIDE"]).lower() in ["1", "true", "yes", "on"]
            if "COOLDOWN_OVERRIDE_MIN_GRADE" in data:
                engine.cooldown_override_min_grade = str(data["COOLDOWN_OVERRIDE_MIN_GRADE"]).strip().upper() or "A"
            if "COOLDOWN_OVERRIDE_MIN_SCORE" in data:
                try:
                    engine.cooldown_override_min_score = float(data["COOLDOWN_OVERRIDE_MIN_SCORE"])
                except Exception:
                    pass
            if "COOLDOWN_OVERRIDE_MIN_CONVICTION" in data:
                try:
                    engine.cooldown_override_min_conviction = float(data["COOLDOWN_OVERRIDE_MIN_CONVICTION"])
                except Exception:
                    pass
            if "COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE" in data:
                engine.cooldown_override_require_spread_safe = str(data["COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE"]).lower() in ["1", "true", "yes", "on"]
            if "COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE" in data:
                engine.cooldown_override_require_new_structure = str(data["COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE"]).lower() in ["1", "true", "yes", "on"]
            if "NO_REVENGE_COOLDOWN_SECONDS" in data:
                try:
                    engine.no_revenge_cooldown = int(data["NO_REVENGE_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "FEATURE_REVERSAL_SHOCK_GUARD" in data:
                engine.reversal_shock_guard_enabled = parse_bool(data["FEATURE_REVERSAL_SHOCK_GUARD"])
            if "REVERSAL_SHOCK_COOLDOWN_MINUTES" in data:
                try:
                    engine.reversal_shock_cooldown_minutes = int(data["REVERSAL_SHOCK_COOLDOWN_MINUTES"])
                except Exception:
                    pass
            if "REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES" in data:
                try:
                    engine.reversal_shock_xau_cooldown_minutes = int(data["REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES"])
                except Exception:
                    pass
            if "FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT" in data:
                engine.opposing_signal_profit_exit_enabled = parse_bool(data["FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT"])
            if "OPPOSING_SIGNAL_MIN_R" in data:
                try:
                    engine.opposing_signal_min_r = max(0.0, float(data["OPPOSING_SIGNAL_MIN_R"]))
                except Exception:
                    pass
            if "OPPOSING_SIGNAL_MIN_SCORE" in data:
                try:
                    engine.opposing_signal_min_score = max(0.0, min(1.0, float(data["OPPOSING_SIGNAL_MIN_SCORE"])))
                except Exception:
                    pass
            if "FEATURE_PROFESSIONAL_EXECUTION_GATE" in data:
                engine.professional_gate_enabled = parse_bool(data["FEATURE_PROFESSIONAL_EXECUTION_GATE"])
            if "MIN_EXECUTION_GRADE" in data:
                engine.min_execution_grade = str(data["MIN_EXECUTION_GRADE"]).upper()
            if "ALLOW_C_GRADE_SCALPS" in data:
                engine.allow_c_scalps = parse_bool(data["ALLOW_C_GRADE_SCALPS"])
            if "MIN_PROFESSIONAL_SETUP_SCORE" in data:
                try:
                    engine.min_professional_score = float(data["MIN_PROFESSIONAL_SETUP_SCORE"])
                except Exception:
                    pass
            if "MIN_PROFESSIONAL_CONVICTION" in data:
                try:
                    engine.min_professional_conviction = float(data["MIN_PROFESSIONAL_CONVICTION"])
                except Exception:
                    pass
            if "MIN_SESSION_SCORE_FOR_TRADE" in data:
                try:
                    engine.min_session_score_for_trade = float(data["MIN_SESSION_SCORE_FOR_TRADE"])
                except Exception:
                    pass
            if "MIN_SESSION_SCORE_FOR_SCALP" in data:
                try:
                    engine.min_session_score_for_scalp = float(data["MIN_SESSION_SCORE_FOR_SCALP"])
                except Exception:
                    pass
            if "BLOCK_CONTEXT_WATCH_TRADES" in data:
                engine.block_context_watch_trades = parse_bool(data["BLOCK_CONTEXT_WATCH_TRADES"])
            if "FEATURE_EARLY_ENTRY" in data:
                engine.early_entry_enabled = parse_bool(data["FEATURE_EARLY_ENTRY"])
            if "EARLY_ENTRY_MIN_SCORE" in data:
                try:
                    engine.early_entry_min_score = float(data["EARLY_ENTRY_MIN_SCORE"])
                except Exception:
                    pass
            if "EXECUTION_ARCHETYPE_SCORE_THRESHOLD" in data:
                try:
                    engine.execution_archetype_score_threshold = float(data["EXECUTION_ARCHETYPE_SCORE_THRESHOLD"])
                except Exception:
                    pass
            if "FEATURE_FALSE_MOVE_DETECTION" in data:
                engine.false_move_detection_enabled = parse_bool(data["FEATURE_FALSE_MOVE_DETECTION"])
            if "FEATURE_NEWS_MODE" in data:
                engine.news_mode_enabled = parse_bool(data["FEATURE_NEWS_MODE"])
            if "NEWS_BLOCK_UNSAFE" in data:
                engine.news_block_unsafe = parse_bool(data["NEWS_BLOCK_UNSAFE"])
            if "NEWS_RISK_MULTIPLIER" in data:
                try:
                    engine.news_risk_multiplier = from_percent(data["NEWS_RISK_MULTIPLIER"], 0.35)
                except Exception:
                    pass
            if "NEWS_ALLOW_RETEST_FOLLOW" in data:
                engine.news_allow_retest_follow = parse_bool(data["NEWS_ALLOW_RETEST_FOLLOW"])
            if "FEATURE_NEWS_LADDER" in data:
                engine.news_ladder_enabled = parse_bool(data["FEATURE_NEWS_LADDER"])
            if "NEWS_LADDER_MAX_ADDONS" in data:
                try:
                    engine.news_ladder_max_addons = int(data["NEWS_LADDER_MAX_ADDONS"])
                except Exception:
                    pass
            if "NEWS_LADDER_MIN_R" in data:
                try:
                    engine.news_ladder_min_r = float(data["NEWS_LADDER_MIN_R"])
                except Exception:
                    pass
            if "NEWS_LADDER_VOLUME_PCT" in data:
                try:
                    engine.news_ladder_volume_pct = from_percent(data["NEWS_LADDER_VOLUME_PCT"], 0.35)
                except Exception:
                    pass
            if "NEWS_LADDER_COOLDOWN_SECONDS" in data:
                try:
                    engine.news_ladder_cooldown_seconds = int(data["NEWS_LADDER_COOLDOWN_SECONDS"])
                except Exception:
                    pass
            if "WAR_ROOM_ENABLED" in data:
                try:
                    engine.features["war_room"] = bool(data["WAR_ROOM_ENABLED"])
                except Exception:
                    pass
            if "FEATURE_CURRENCY_BASKET_GUARD" in data:
                try:
                    engine.currency_basket_guard_enabled = parse_bool(data["FEATURE_CURRENCY_BASKET_GUARD"])
                except Exception:
                    pass
            if "CURRENCY_BASKET_LIMITS" in data:
                try:
                    engine.currency_basket_limits = engine._parse_currency_basket_limits(data["CURRENCY_BASKET_LIMITS"])
                except Exception:
                    pass
            if "FEATURE_WEBHOOK_ALERTS" in data:
                try:
                    engine.webhook_alerts_enabled = parse_bool(data["FEATURE_WEBHOOK_ALERTS"])
                except Exception:
                    pass
            engine.rule_config = {"ema": False, "volume": False, "po3": False}

        # write back to .env
        with open(env_path, "w") as f:
            f.write("# MT5 terminal attachment (optional metadata)\n")
            f.write("# The bot trades the account currently logged in to the local MT5 terminal.\n")
            f.write(f"MT5_ACCOUNT={env_vars.get('MT5_ACCOUNT', '')}\n")
            f.write(f"MT5_PASSWORD={env_vars.get('MT5_PASSWORD', '')}\n")
            f.write(f"MT5_SERVER={env_vars.get('MT5_SERVER', '')}\n\n")
            f.write("# Telegram Notifications (Optional)\n")
            f.write(f"TELEGRAM_BOT_TOKEN={env_vars.get('TELEGRAM_BOT_TOKEN', '')}\n")
            f.write(f"TELEGRAM_CHAT_ID={env_vars.get('TELEGRAM_CHAT_ID', '')}\n\n")
            f.write("# Discord Notifications (Optional)\n")
            f.write(f"DISCORD_WEBHOOK={env_vars.get('DISCORD_WEBHOOK', '')}\n\n")
            f.write("# Risk & Telemetry (Optional)\n")
            f.write(f"FEATURE_CURRENCY_BASKET_GUARD={env_vars.get('FEATURE_CURRENCY_BASKET_GUARD', 'true')}\n")
            f.write(f"CURRENCY_BASKET_LIMITS={env_vars.get('CURRENCY_BASKET_LIMITS', 'USD_SHORT:2,USD_LONG:2,JPY_SHORT:2,JPY_LONG:2')}\n")
            f.write(f"FEATURE_WEBHOOK_ALERTS={env_vars.get('FEATURE_WEBHOOK_ALERTS', 'true')}\n\n")
            f.write("# Trading Settings\n")
            f.write(f"TRADING_SYMBOLS={env_vars['TRADING_SYMBOLS']}\n")
            f.write(f"EXECUTION_SYMBOLS={env_vars.get('EXECUTION_SYMBOLS', env_vars.get('TRADING_SYMBOLS', ''))}\n")
            f.write(f"TIMEFRAME={env_vars.get('TIMEFRAME', 'M5')}\n")
            f.write(f"TRADE_VOLUME={env_vars['TRADE_VOLUME']}\n")
            f.write(f"POSITION_SIZING_MODE={env_vars.get('POSITION_SIZING_MODE','fixed')}\n")
            f.write(f"RISK_PER_TRADE_PCT={env_vars.get('RISK_PER_TRADE_PCT', '0.01')}\n")
            f.write(f"MAX_EXPOSURE_PERCENT={env_vars.get('MAX_EXPOSURE_PERCENT', '5')}\n")
            f.write(f"MIN_PROFIT_PIPS={env_vars.get('MIN_PROFIT_PIPS', '50')}\n")
            f.write(f"DAILY_PROFIT_CAP={env_vars.get('DAILY_PROFIT_CAP', '0.02')}\n")
            f.write(f"DAILY_PROFIT_CAP_EXTENSION={env_vars.get('DAILY_PROFIT_CAP_EXTENSION', '0.0')}\n")
            f.write(f"FEATURE_DAILY_LOSS_BRAKE={env_vars.get('FEATURE_DAILY_LOSS_BRAKE', 'false')}\n")
            f.write(f"DAILY_LOSS_CAP_PERCENT={env_vars.get('DAILY_LOSS_CAP_PERCENT', '0.05')}\n")
            f.write(f"MAX_DAILY_LOSSES={env_vars.get('MAX_DAILY_LOSSES', '100')}\n")
            f.write(f"MAX_CONSECUTIVE_LOSSES={env_vars.get('MAX_CONSECUTIVE_LOSSES', '30')}\n")
            f.write(f"LOSS_COOLDOWN_MINUTES={env_vars.get('LOSS_COOLDOWN_MINUTES', '60')}\n")
            f.write(f"MAX_ACTIVE_TRADES_TOTAL={env_vars.get('MAX_ACTIVE_TRADES_TOTAL', '10')}\n")
            f.write(f"FEATURE_CATASTROPHIC_LOSS_STOP={env_vars.get('FEATURE_CATASTROPHIC_LOSS_STOP', 'true')}\n")
            f.write(f"CATASTROPHIC_LOSS_R={env_vars.get('CATASTROPHIC_LOSS_R', '1.5')}\n")
            f.write(f"CATASTROPHIC_LOSS_COOLDOWN_MINUTES={env_vars.get('CATASTROPHIC_LOSS_COOLDOWN_MINUTES', '360')}\n")
            f.write(f"MIN_EXPECTED_R={env_vars.get('MIN_EXPECTED_R', '1.2')}\n")
            f.write(f"MIN_EXPECTED_R_SCALP={env_vars.get('MIN_EXPECTED_R_SCALP', '0.8')}\n")
            f.write(f"MIN_EXPECTED_R_INTRADAY={env_vars.get('MIN_EXPECTED_R_INTRADAY', env_vars.get('MIN_EXPECTED_R', '1.2'))}\n")
            f.write(f"MIN_EXPECTED_R_SWING={env_vars.get('MIN_EXPECTED_R_SWING', '1.5')}\n")
            f.write(f"TAKE_PROFIT_R_MULTIPLIER={env_vars.get('TAKE_PROFIT_R_MULTIPLIER', '1.8')}\n")
            f.write(f"TAKE_PROFIT_R_MULTIPLIER_SCALP={env_vars.get('TAKE_PROFIT_R_MULTIPLIER_SCALP', '1.5')}\n")
            f.write(f"TAKE_PROFIT_R_MULTIPLIER_INTRADAY={env_vars.get('TAKE_PROFIT_R_MULTIPLIER_INTRADAY', env_vars.get('TAKE_PROFIT_R_MULTIPLIER', '1.8'))}\n")
            f.write(f"TAKE_PROFIT_R_MULTIPLIER_SWING={env_vars.get('TAKE_PROFIT_R_MULTIPLIER_SWING', '2.5')}\n")
            f.write(f"EXECUTION_CONVICTION_THRESHOLD={env_vars.get('EXECUTION_CONVICTION_THRESHOLD', '0.35')}\n")
            f.write(f"EXECUTION_SETUP_SCORE_THRESHOLD={env_vars.get('EXECUTION_SETUP_SCORE_THRESHOLD', '0.50')}\n")
            f.write(f"EXECUTION_ARCHETYPE_SCORE_THRESHOLD={env_vars.get('EXECUTION_ARCHETYPE_SCORE_THRESHOLD', '0.58')}\n")
            f.write(f"ANALYTIC_TIMEFRAMES={env_vars.get('ANALYTIC_TIMEFRAMES', 'M1,M5,M15,H1,H4')}\n")
            for key, default in {
                "SCALP_EXECUTION_CONVICTION_THRESHOLD": "0.28",
                "SCALP_EXECUTION_SETUP_SCORE_THRESHOLD": "0.52",
                "SCALP_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": "0.55",
                "SCALP_MIN_PROFESSIONAL_SETUP_SCORE": "0.58",
                "SCALP_MIN_PROFESSIONAL_CONVICTION": "0.28",
                "SCALP_MIN_SESSION_SCORE": "0.55",
                "SCALP_REQUIRE_HARD_STRUCTURE": "true",
                "SCALP_REQUIRE_HTF": "false",
                "SCALP_ALLOW_C_GRADE": "false",
                "INTRADAY_EXECUTION_CONVICTION_THRESHOLD": "0.35",
                "INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD": "0.50",
                "INTRADAY_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": "0.58",
                "INTRADAY_MIN_PROFESSIONAL_SETUP_SCORE": "0.62",
                "INTRADAY_MIN_PROFESSIONAL_CONVICTION": "0.30",
                "INTRADAY_MIN_SESSION_SCORE": "0.40",
                "INTRADAY_REQUIRE_HARD_STRUCTURE": "false",
                "INTRADAY_REQUIRE_HTF": "false",
                "INTRADAY_ALLOW_C_GRADE": "false",
                "SWING_EXECUTION_CONVICTION_THRESHOLD": "0.42",
                "SWING_EXECUTION_SETUP_SCORE_THRESHOLD": "0.68",
                "SWING_EXECUTION_ARCHETYPE_SCORE_THRESHOLD": "0.66",
                "SWING_MIN_PROFESSIONAL_SETUP_SCORE": "0.68",
                "SWING_MIN_PROFESSIONAL_CONVICTION": "0.40",
                "SWING_MIN_SESSION_SCORE": "0.30",
                "SWING_REQUIRE_HARD_STRUCTURE": "true",
                "SWING_REQUIRE_HTF": "true",
                "SWING_ALLOW_C_GRADE": "false",
            }.items():
                f.write(f"{key}={env_vars.get(key, default)}\n")
            f.write(f"MIN_TRADE_READINESS_SCORE={env_vars.get('MIN_TRADE_READINESS_SCORE', '0.62')}\n")
            f.write(f"FEATURE_MTF_EXECUTION_GATE={env_vars.get('FEATURE_MTF_EXECUTION_GATE', 'true')}\n")
            f.write(f"MIN_MTF_EXECUTION_SCORE={env_vars.get('MIN_MTF_EXECUTION_SCORE', '0.30')}\n")
            f.write(f"MIN_MTF_EXECUTION_SCORE_METAL={env_vars.get('MIN_MTF_EXECUTION_SCORE_METAL', '0.45')}\n")
            f.write(f"MARKET_EXECUTION_SCORE_THRESHOLD={env_vars.get('MARKET_EXECUTION_SCORE_THRESHOLD', '0.60')}\n")
            f.write(f"MARKET_EXECUTION_CONVICTION_THRESHOLD={env_vars.get('MARKET_EXECUTION_CONVICTION_THRESHOLD', '0.35')}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS={env_vars.get('MAX_ENTRY_DRIFT_PIPS', '10')}\n")
            f.write(f"FEATURE_ICT_MODE={env_vars.get('FEATURE_ICT_MODE', 'false')}\n")
            f.write(f"ICT_MIN_SETUP_SCORE={env_vars.get('ICT_MIN_SETUP_SCORE', '0.60')}\n")
            f.write(f"ICT_MIN_CONFLUENCE={env_vars.get('ICT_MIN_CONFLUENCE', '0.60')}\n")
            f.write(f"MIN_PROFIT_PIPS_FX={env_vars.get('MIN_PROFIT_PIPS_FX', '2')}\n")
            f.write(f"MIN_PROFIT_PIPS_JPY={env_vars.get('MIN_PROFIT_PIPS_JPY', '1')}\n")
            f.write(f"MIN_PROFIT_PIPS_USDJPY={env_vars.get('MIN_PROFIT_PIPS_USDJPY', '1')}\n")
            f.write(f"MIN_PROFIT_PIPS_NZDUSD={env_vars.get('MIN_PROFIT_PIPS_NZDUSD', '2')}\n")
            f.write(f"MIN_PROFIT_PIPS_AUDUSD={env_vars.get('MIN_PROFIT_PIPS_AUDUSD', '2')}\n")
            f.write(f"MIN_PROFIT_PIPS_USDCAD={env_vars.get('MIN_PROFIT_PIPS_USDCAD', '1.5')}\n")
            f.write(f"MIN_PROFIT_PIPS_XAU={env_vars.get('MIN_PROFIT_PIPS_XAU', '30')}\n")
            f.write(f"MIN_PROFIT_PIPS_SCALP={env_vars.get('MIN_PROFIT_PIPS_SCALP', '2')}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS_XAU={env_vars.get('MAX_ENTRY_DRIFT_PIPS_XAU', '250')}\n")
            f.write(f"FEATURE_INSTRUMENT_PROFILES={env_vars.get('FEATURE_INSTRUMENT_PROFILES', 'true')}\n")
            f.write(f"MIN_PROFIT_PIPS_FOREX={env_vars.get('MIN_PROFIT_PIPS_FOREX', env_vars.get('MIN_PROFIT_PIPS_FX', '1.5'))}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS_FOREX={env_vars.get('MAX_ENTRY_DRIFT_PIPS_FOREX', env_vars.get('MAX_ENTRY_DRIFT_PIPS', '6'))}\n")
            f.write(f"MAX_SPREAD_PIPS_FOREX={env_vars.get('MAX_SPREAD_PIPS_FOREX', '2.5')}\n")
            f.write(f"MIN_PROFIT_PIPS_METAL={env_vars.get('MIN_PROFIT_PIPS_METAL', env_vars.get('MIN_PROFIT_PIPS_XAU', '20'))}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS_METAL={env_vars.get('MAX_ENTRY_DRIFT_PIPS_METAL', env_vars.get('MAX_ENTRY_DRIFT_PIPS_XAU', '50'))}\n")
            f.write(f"MAX_SPREAD_PIPS_METAL={env_vars.get('MAX_SPREAD_PIPS_METAL', '35')}\n")
            f.write(f"MIN_PROFIT_PIPS_STOCK={env_vars.get('MIN_PROFIT_PIPS_STOCK', '20')}\n")
            f.write(f"MAX_ENTRY_DRIFT_PIPS_STOCK={env_vars.get('MAX_ENTRY_DRIFT_PIPS_STOCK', '10')}\n")
            f.write(f"MAX_SPREAD_PIPS_STOCK={env_vars.get('MAX_SPREAD_PIPS_STOCK', '5')}\n")
            f.write(f"MIN_SETUP_SCORE_STOCK={env_vars.get('MIN_SETUP_SCORE_STOCK', '0.70')}\n")
            f.write(f"MIN_CONVICTION_STOCK={env_vars.get('MIN_CONVICTION_STOCK', '0.45')}\n")
            f.write(f"MIN_SESSION_SCORE_STOCK={env_vars.get('MIN_SESSION_SCORE_STOCK', '0.60')}\n")
            f.write(f"MIN_EXECUTION_GRADE_STOCK={env_vars.get('MIN_EXECUTION_GRADE_STOCK', 'A')}\n")
            f.write(f"BLOCK_STOCK_SCALPS={env_vars.get('BLOCK_STOCK_SCALPS', 'true')}\n")
            f.write(f"TRAILING_STOP_TRIGGER_PCT={env_vars.get('TRAILING_STOP_TRIGGER_PCT', '0.55')}\n")
            f.write(f"TRAILING_STOP_LOCK_PIPS={env_vars.get('TRAILING_STOP_LOCK_PIPS', '10')}\n")
            f.write(f"TRAILING_STOP_STEP_PCT={env_vars.get('TRAILING_STOP_STEP_PCT', '0.50')}\n")
            f.write(f"TRAILING_STOP_MIN_STEP_PIPS={env_vars.get('TRAILING_STOP_MIN_STEP_PIPS', '5')}\n")
            f.write(f"FEATURE_TRAILING_TAKE_PROFIT={env_vars.get('FEATURE_TRAILING_TAKE_PROFIT', 'true')}\n")
            f.write(f"TRAILING_TP_TRIGGER_PCT={env_vars.get('TRAILING_TP_TRIGGER_PCT', '0.85')}\n")
            f.write(f"TRAILING_TP_EXTENSION_PCT={env_vars.get('TRAILING_TP_EXTENSION_PCT', '0.5')}\n")
            f.write(f"TRAILING_TP_COOLDOWN_SECONDS={env_vars.get('TRAILING_TP_COOLDOWN_SECONDS', '0')}\n")
            f.write(f"FEATURE_PARTIAL_TP_EXTEND={env_vars.get('FEATURE_PARTIAL_TP_EXTEND', 'true')}\n")
            f.write(f"PARTIAL_TP_EXTEND_PCT={env_vars.get('PARTIAL_TP_EXTEND_PCT', '0.5')}\n")
            f.write(f"FEATURE_PARTIAL_TAKE_PROFIT={env_vars.get('FEATURE_PARTIAL_TAKE_PROFIT', 'true')}\n")
            f.write(f"PARTIAL_TP_TRIGGER_R={env_vars.get('PARTIAL_TP_TRIGGER_R', '0.75')}\n")
            f.write(f"PARTIAL_TP_CLOSE_PCT={env_vars.get('PARTIAL_TP_CLOSE_PCT', '0.5')}\n")
            f.write(f"PARTIAL_TP_LOCK_PIPS={env_vars.get('PARTIAL_TP_LOCK_PIPS', '10')}\n")
            f.write(f"FEATURE_BREAKEVEN_PROTECTION={env_vars.get('FEATURE_BREAKEVEN_PROTECTION', 'true')}\n")
            f.write(f"BREAKEVEN_TRIGGER_R={env_vars.get('BREAKEVEN_TRIGGER_R', '0.30')}\n")
            f.write(f"BREAKEVEN_LOCK_PIPS={env_vars.get('BREAKEVEN_LOCK_PIPS', '0')}\n")
            f.write(f"FEATURE_FIRST_PROFIT_BREAKEVEN={env_vars.get('FEATURE_FIRST_PROFIT_BREAKEVEN', 'true')}\n")
            f.write(f"FIRST_PROFIT_BREAKEVEN_TRIGGER_R={env_vars.get('FIRST_PROFIT_BREAKEVEN_TRIGGER_R', '0.10')}\n")
            f.write(f"FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP={env_vars.get('FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP', '0.08')}\n")
            f.write(f"FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY={env_vars.get('FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY', 'true')}\n")
            f.write(f"FEATURE_MAX_ADVERSE_EXIT={env_vars.get('FEATURE_MAX_ADVERSE_EXIT', 'true')}\n")
            f.write(f"MAX_ADVERSE_R={env_vars.get('MAX_ADVERSE_R', '0.90')}\n")
            f.write(f"MAX_ADVERSE_R_FOREX={env_vars.get('MAX_ADVERSE_R_FOREX', '0.85')}\n")
            f.write(f"MAX_ADVERSE_R_SCALP={env_vars.get('MAX_ADVERSE_R_SCALP', '0.85')}\n")
            f.write(f"MAX_ADVERSE_R_INTRADAY={env_vars.get('MAX_ADVERSE_R_INTRADAY', '0.90')}\n")
            f.write(f"MAX_ADVERSE_R_METAL={env_vars.get('MAX_ADVERSE_R_METAL', '1.00')}\n")
            f.write(f"MAX_ADVERSE_R_SWING={env_vars.get('MAX_ADVERSE_R_SWING', '1.10')}\n")
            f.write(f"FEATURE_SYMBOL_PROFILES={env_vars.get('FEATURE_SYMBOL_PROFILES', 'true')}\n")
            f.write(f"FEATURE_TRADE_HORIZON_PROFILES={env_vars.get('FEATURE_TRADE_HORIZON_PROFILES', 'true')}\n")
            f.write(f"HORIZON_PROFILE_MODE={env_vars.get('HORIZON_PROFILE_MODE', 'exit_only')}\n")
            f.write(f"ENABLE_SCALP_PROFILE={env_vars.get('ENABLE_SCALP_PROFILE', 'true')}\n")
            f.write(f"ENABLE_INTRADAY_PROFILE={env_vars.get('ENABLE_INTRADAY_PROFILE', 'true')}\n")
            f.write(f"ENABLE_SWING_PROFILE={env_vars.get('ENABLE_SWING_PROFILE', 'true')}\n")
            f.write(f"SIGNAL_LOCKOUT_ENABLED={env_vars.get('SIGNAL_LOCKOUT_ENABLED', 'true')}\n")
            f.write(f"MAX_TRADES_PER_SYMBOL={env_vars.get('MAX_TRADES_PER_SYMBOL', '1')}\n")
            f.write(f"TRADE_COOLDOWN_MINUTES={env_vars.get('TRADE_COOLDOWN_MINUTES', '0')}\n")
            f.write(f"FEATURE_COOLDOWN_OVERRIDE={env_vars.get('FEATURE_COOLDOWN_OVERRIDE', 'false')}\n")
            f.write(f"COOLDOWN_OVERRIDE_MIN_GRADE={env_vars.get('COOLDOWN_OVERRIDE_MIN_GRADE', 'A')}\n")
            f.write(f"COOLDOWN_OVERRIDE_MIN_SCORE={env_vars.get('COOLDOWN_OVERRIDE_MIN_SCORE', '0.78')}\n")
            f.write(f"COOLDOWN_OVERRIDE_MIN_CONVICTION={env_vars.get('COOLDOWN_OVERRIDE_MIN_CONVICTION', '0.45')}\n")
            f.write(f"COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE={env_vars.get('COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE', 'true')}\n")
            f.write(f"COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE={env_vars.get('COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE', 'true')}\n")
            f.write(f"NO_REVENGE_COOLDOWN_SECONDS={env_vars.get('NO_REVENGE_COOLDOWN_SECONDS', '0')}\n")
            f.write(f"FEATURE_REVERSAL_SHOCK_GUARD={env_vars.get('FEATURE_REVERSAL_SHOCK_GUARD', 'false')}\n")
            f.write(f"REVERSAL_SHOCK_COOLDOWN_MINUTES={env_vars.get('REVERSAL_SHOCK_COOLDOWN_MINUTES', '0')}\n")
            f.write(f"REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES={env_vars.get('REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES', '0')}\n")
            f.write(f"FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT={env_vars.get('FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT', 'true')}\n")
            f.write(f"OPPOSING_SIGNAL_MIN_R={env_vars.get('OPPOSING_SIGNAL_MIN_R', '0.20')}\n")
            f.write(f"OPPOSING_SIGNAL_MIN_SCORE={env_vars.get('OPPOSING_SIGNAL_MIN_SCORE', '0.58')}\n")
            f.write(f"FEATURE_PROFESSIONAL_EXECUTION_GATE={env_vars.get('FEATURE_PROFESSIONAL_EXECUTION_GATE', 'true')}\n")
            f.write(f"MIN_EXECUTION_GRADE={env_vars.get('MIN_EXECUTION_GRADE', 'B')}\n")
            f.write(f"ALLOW_C_GRADE_SCALPS={env_vars.get('ALLOW_C_GRADE_SCALPS', 'false')}\n")
            f.write(f"MIN_PROFESSIONAL_SETUP_SCORE={env_vars.get('MIN_PROFESSIONAL_SETUP_SCORE', '0.62')}\n")
            f.write(f"MIN_PROFESSIONAL_CONVICTION={env_vars.get('MIN_PROFESSIONAL_CONVICTION', '0.30')}\n")
            f.write(f"MIN_SESSION_SCORE_FOR_TRADE={env_vars.get('MIN_SESSION_SCORE_FOR_TRADE', '0.40')}\n")
            f.write(f"MIN_SESSION_SCORE_FOR_SCALP={env_vars.get('MIN_SESSION_SCORE_FOR_SCALP', '0.55')}\n")
            f.write(f"BLOCK_CONTEXT_WATCH_TRADES={env_vars.get('BLOCK_CONTEXT_WATCH_TRADES', 'true')}\n")
            f.write(f"BLOCK_ASIA_TRANSITION_SESSIONS={env_vars.get('BLOCK_ASIA_TRANSITION_SESSIONS', 'false')}\n")
            f.write(f"FEATURE_EARLY_ENTRY={env_vars.get('FEATURE_EARLY_ENTRY', 'true')}\n")
            f.write(f"EARLY_ENTRY_MIN_SCORE={env_vars.get('EARLY_ENTRY_MIN_SCORE', '0.50')}\n")
            f.write(f"FEATURE_FALSE_MOVE_DETECTION={env_vars.get('FEATURE_FALSE_MOVE_DETECTION', 'true')}\n")
            f.write(f"FEATURE_NEWS_MODE={env_vars.get('FEATURE_NEWS_MODE', 'true')}\n")
            f.write(f"NEWS_BLOCK_UNSAFE={env_vars.get('NEWS_BLOCK_UNSAFE', 'true')}\n")
            f.write(f"NEWS_RISK_MULTIPLIER={env_vars.get('NEWS_RISK_MULTIPLIER', '0.35')}\n")
            f.write(f"NEWS_ALLOW_RETEST_FOLLOW={env_vars.get('NEWS_ALLOW_RETEST_FOLLOW', 'true')}\n")
            f.write(f"FEATURE_NEWS_LADDER={env_vars.get('FEATURE_NEWS_LADDER', 'true')}\n")
            f.write(f"NEWS_LADDER_MAX_ADDONS={env_vars.get('NEWS_LADDER_MAX_ADDONS', '2')}\n")
            f.write(f"NEWS_LADDER_MIN_R={env_vars.get('NEWS_LADDER_MIN_R', '0.55')}\n")
            f.write(f"NEWS_LADDER_VOLUME_PCT={env_vars.get('NEWS_LADDER_VOLUME_PCT', '0.35')}\n")
            f.write(f"NEWS_LADDER_COOLDOWN_SECONDS={env_vars.get('NEWS_LADDER_COOLDOWN_SECONDS', '0')}\n")
            f.write(f"FEATURE_WAR_ROOM={env_vars.get('FEATURE_WAR_ROOM', 'true')}\n\n")
            f.write("# Saved UI Settings Mirror\n")
            for key in sorted(env_vars.keys()):
                if key in {"MT5_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK", "RULE_EMA", "RULE_VOLUME", "RULE_PO3"}:
                    continue
                f.write(f"{key}={env_vars.get(key, '')}\n")
            f.write("\n")

            f.write("# Logging\n")
            f.write(f"LOG_LEVEL={env_vars.get('LOG_LEVEL', 'INFO')}\n")
            f.flush()
            os.fsync(f.fileno())

        for key, value in env_vars.items():
            os.environ[key] = str(value)

        if engine:
            try:
                engine.symbols = [
                    s.strip().upper()
                    for s in str(env_vars.get("TRADING_SYMBOLS", "")).split(",")
                    if s.strip()
                ] or engine.symbols
                engine.execution_symbols = [
                    s.strip().upper()
                    for s in str(env_vars.get("EXECUTION_SYMBOLS", "")).split(",")
                    if s.strip()
                ]
                engine.volume = float(env_vars.get("TRADE_VOLUME", engine.volume))
                engine.base_trade_volume = engine.volume
                engine.position_sizing_mode = env_vars.get('POSITION_SIZING_MODE', engine.position_sizing_mode)
                try:
                    engine.risk_pct = float(env_vars.get('RISK_PER_TRADE_PCT', getattr(engine, 'risk_pct', 0.01)))
                except Exception:
                    engine.risk_pct = getattr(engine, 'risk_pct', 0.01)
                engine.max_exposure_pct = normalize_fraction(env_vars.get("MAX_EXPOSURE_PERCENT", engine.max_exposure_pct), engine.max_exposure_pct)
                engine.daily_profit_cap = normalize_fraction(env_vars.get("DAILY_PROFIT_CAP", engine.daily_profit_cap), engine.daily_profit_cap)
                engine.daily_profit_cap_extension = normalize_fraction(env_vars.get("DAILY_PROFIT_CAP_EXTENSION", engine.daily_profit_cap_extension), engine.daily_profit_cap_extension)
                engine.daily_loss_brake_enabled = parse_bool(env_vars.get("FEATURE_DAILY_LOSS_BRAKE", engine.daily_loss_brake_enabled))
                engine.daily_loss_cap_pct = normalize_fraction(env_vars.get("DAILY_LOSS_CAP_PERCENT", engine.daily_loss_cap_pct), engine.daily_loss_cap_pct)
                engine.max_daily_losses = max(0, min(100, int(env_vars.get("MAX_DAILY_LOSSES", engine.max_daily_losses))))
                engine.max_consecutive_losses = max(0, min(100, int(env_vars.get("MAX_CONSECUTIVE_LOSSES", engine.max_consecutive_losses))))
                engine.loss_cooldown_minutes = int(env_vars.get("LOSS_COOLDOWN_MINUTES", engine.loss_cooldown_minutes))
                engine.max_active_trades_total = int(env_vars.get("MAX_ACTIVE_TRADES_TOTAL", engine.max_active_trades_total))
                engine.catastrophic_loss_stop_enabled = parse_bool(env_vars.get("FEATURE_CATASTROPHIC_LOSS_STOP", engine.catastrophic_loss_stop_enabled))
                engine.catastrophic_loss_r = float(env_vars.get("CATASTROPHIC_LOSS_R", engine.catastrophic_loss_r))
                engine.catastrophic_loss_cooldown_minutes = int(env_vars.get("CATASTROPHIC_LOSS_COOLDOWN_MINUTES", engine.catastrophic_loss_cooldown_minutes))
                engine.max_trades_per_symbol = int(env_vars.get("MAX_TRADES_PER_SYMBOL", engine.max_trades_per_symbol))
                engine.trade_cooldown_minutes = int(env_vars.get("TRADE_COOLDOWN_MINUTES", engine.trade_cooldown_minutes))
                engine.signal_lockout_enabled = parse_bool(env_vars.get("SIGNAL_LOCKOUT_ENABLED", engine.signal_lockout_enabled))
                engine.min_execution_grade = str(env_vars.get("MIN_EXECUTION_GRADE", engine.min_execution_grade)).upper()
                engine.allow_c_scalps = parse_bool(env_vars.get("ALLOW_C_GRADE_SCALPS", engine.allow_c_scalps))
                engine.min_professional_score = float(env_vars.get("MIN_PROFESSIONAL_SETUP_SCORE", engine.min_professional_score))
                engine.min_professional_conviction = float(env_vars.get("MIN_PROFESSIONAL_CONVICTION", engine.min_professional_conviction))
                engine.min_trade_readiness_score = float(env_vars.get("MIN_TRADE_READINESS_SCORE", engine.min_trade_readiness_score))
                engine.mtf_execution_gate_enabled = parse_bool(env_vars.get("FEATURE_MTF_EXECUTION_GATE", getattr(engine, "mtf_execution_gate_enabled", True)))
                engine.min_mtf_execution_score = float(env_vars.get("MIN_MTF_EXECUTION_SCORE", getattr(engine, "min_mtf_execution_score", 0.30)))
                engine.min_mtf_execution_score_metal = float(env_vars.get("MIN_MTF_EXECUTION_SCORE_METAL", getattr(engine, "min_mtf_execution_score_metal", 0.45)))
                engine.min_session_score_for_trade = float(env_vars.get("MIN_SESSION_SCORE_FOR_TRADE", engine.min_session_score_for_trade))
                engine.min_session_score_for_scalp = float(env_vars.get("MIN_SESSION_SCORE_FOR_SCALP", engine.min_session_score_for_scalp))
                engine.execution_setup_score_threshold = float(env_vars.get("EXECUTION_SETUP_SCORE_THRESHOLD", engine.execution_setup_score_threshold))
                engine.execution_archetype_score_threshold = float(env_vars.get("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", engine.execution_archetype_score_threshold))
                engine.market_execution_score_threshold = float(env_vars.get("MARKET_EXECUTION_SCORE_THRESHOLD", engine.market_execution_score_threshold))
                engine.market_execution_conviction_threshold = float(env_vars.get("MARKET_EXECUTION_CONVICTION_THRESHOLD", engine.market_execution_conviction_threshold))
                engine.execution_conviction_threshold = float(env_vars.get("EXECUTION_CONVICTION_THRESHOLD", engine.execution_conviction_threshold))
                engine.early_entry_enabled = parse_bool(env_vars.get("FEATURE_EARLY_ENTRY", engine.early_entry_enabled))
                engine.early_entry_min_score = float(env_vars.get("EARLY_ENTRY_MIN_SCORE", engine.early_entry_min_score))
                engine.breakeven_protection_enabled = parse_bool(env_vars.get("FEATURE_BREAKEVEN_PROTECTION", engine.breakeven_protection_enabled))
                engine.breakeven_trigger_r = float(env_vars.get("BREAKEVEN_TRIGGER_R", engine.breakeven_trigger_r))
                engine.breakeven_lock_pips = float(env_vars.get("BREAKEVEN_LOCK_PIPS", engine.breakeven_lock_pips))
                engine.first_profit_breakeven_enabled = parse_bool(env_vars.get("FEATURE_FIRST_PROFIT_BREAKEVEN", getattr(engine, "first_profit_breakeven_enabled", True)))
                engine.first_profit_breakeven_trigger_r = float(env_vars.get("FIRST_PROFIT_BREAKEVEN_TRIGGER_R", getattr(engine, "first_profit_breakeven_trigger_r", 0.10)))
                engine.first_profit_breakeven_trigger_r_scalp = float(env_vars.get("FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP", getattr(engine, "first_profit_breakeven_trigger_r_scalp", 0.08)))
                engine.reversal_breakeven_at_entry_enabled = parse_bool(env_vars.get("FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY", engine.reversal_breakeven_at_entry_enabled))
                engine.block_context_watch_trades = parse_bool(env_vars.get("BLOCK_CONTEXT_WATCH_TRADES", engine.block_context_watch_trades))
                engine.armed_confirmation_enabled = parse_bool(env_vars.get("FEATURE_ARMED_CONFIRMATION", engine.armed_confirmation_enabled))
                engine.armed_required_scans = max(1, int(env_vars.get("ARMED_CONFIRMATION_REQUIRED_SCANS", engine.armed_required_scans)))
                engine.armed_min_score = float(env_vars.get("ARMED_CONFIRMATION_MIN_SCORE", engine.armed_min_score))
                engine.armed_require_structure = parse_bool(env_vars.get("ARMED_CONFIRMATION_REQUIRE_STRUCTURE", engine.armed_require_structure))
                engine.ict_mode_enabled = parse_bool(env_vars.get("FEATURE_ICT_MODE", engine.ict_mode_enabled))
                engine.ict_min_setup_score = float(env_vars.get("ICT_MIN_SETUP_SCORE", engine.ict_min_setup_score))
                engine.ict_min_confluence = float(env_vars.get("ICT_MIN_CONFLUENCE", engine.ict_min_confluence))
                engine.instrument_profiles_enabled = parse_bool(env_vars.get("FEATURE_INSTRUMENT_PROFILES", getattr(engine, "instrument_profiles_enabled", True)))
                engine.broker_min_lot_fallback_enabled = parse_bool(env_vars.get("FEATURE_BROKER_MIN_LOT_FALLBACK", engine.broker_min_lot_fallback_enabled))
                engine.max_auto_min_lot = float(env_vars.get("MAX_AUTO_MIN_LOT", engine.max_auto_min_lot))
                engine.pending_orders_enabled = parse_bool(env_vars.get("FEATURE_PENDING_ORDERS", getattr(engine, "pending_orders_enabled", False)))
                engine.news_ladder_enabled = parse_bool(env_vars.get("FEATURE_NEWS_LADDER", engine.news_ladder_enabled))
                engine.rule_config = {"ema": False, "volume": False, "po3": False}
                engine._validate_config()
                engine._apply_dynamic_account_profile()
                logger.info("Applied saved configuration to running engine")
            except Exception as exc:
                logger.warning("Saved config but could not apply all values live: %s", exc)

        def dedupe_env_file(path):
            try:
                with open(path, "r", encoding="utf-8") as env_file:
                    lines = env_file.readlines()

                last_index = {}
                for idx, line in enumerate(lines):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key = stripped.split("=", 1)[0].strip()
                    if key:
                        last_index[key] = idx

                cleaned = []
                for idx, line in enumerate(lines):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        cleaned.append(line)
                        continue
                    key = stripped.split("=", 1)[0].strip()
                    if last_index.get(key) == idx:
                        cleaned.append(line)

                with open(path, "w", encoding="utf-8") as env_file:
                    env_file.writelines(cleaned)
                    env_file.flush()
                    os.fsync(env_file.fileno())
            except Exception as exc:
                logger.warning("Could not dedupe env file %s: %s", path, exc)

        dedupe_env_file(env_path)

        persisted = read_env_file(env_path)
        ignored_verify_keys = {
            "RISK_PERCENT",
            "RULES",
            "RULE_EMA",
            "RULE_VOLUME",
            "RULE_PO3",
            "POSITION_SIZING_MODE",
            "ENV_ALL",
        }
        verify_keys = [
            key
            for key in data.keys()
            if key not in ignored_verify_keys and key in env_vars and key in persisted
        ]

        def equivalent_config_value(expected, actual):
            if actual is None:
                return False
            expected_text = str(expected).strip()
            actual_text = str(actual).strip()
            if expected_text == actual_text:
                return True

            expected_lower = expected_text.lower()
            actual_lower = actual_text.lower()
            bool_map = {
                "true": True,
                "1": True,
                "yes": True,
                "on": True,
                "false": False,
                "0": False,
                "no": False,
                "off": False,
            }
            if expected_lower in bool_map and actual_lower in bool_map:
                return bool_map[expected_lower] == bool_map[actual_lower]

            try:
                return abs(float(expected_text) - float(actual_text)) <= 1e-9
            except Exception:
                return False

        mismatches = {
            key: {"expected": str(env_vars.get(key)), "actual": persisted.get(key)}
            for key in verify_keys
            if not equivalent_config_value(env_vars.get(key), persisted.get(key))
        }
        skipped_verify_keys = [
            key
            for key in data.keys()
            if key not in ignored_verify_keys and key in env_vars and key not in persisted
        ]
        if mismatches:
            logger.error("Config save verification failed for %s: %s", env_path, mismatches)
            return jsonify({
                "status": "success",
                "message": "Config saved",
                "env_path": env_path,
                "mismatches": mismatches,
                "skipped_verify_keys": skipped_verify_keys,
                "verified": True,
            })

        return jsonify({
            "status": "success",
            "message": "Config saved",
            "env_path": env_path,
            "verified": True,
            "skipped_verify_keys": skipped_verify_keys,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/pending-orders", methods=["GET"])
def api_pending_orders():
    """Get summary of all pending orders."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        summary = engine.pending_order_manager.get_pending_orders_summary()
        return jsonify({"status": "success", "data": summary})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/pending-orders/place", methods=["POST"])
def api_place_pending_orders():
    """Manually trigger pending order placement for specified symbols."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        data = request.json or {}
        symbols = data.get("symbols", engine.symbols)
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",")]
        
        placed = engine.pending_order_manager.scan_and_place_pending_orders(
            symbols,
            volume_func=engine._calculate_volume,
            rr_ratio=engine.take_profit_r_multiplier,
            max_orders=1,
            signal_guard=engine._pending_signal_execution_allowed,
            signal_mark=engine._mark_pending_signal_execution,
        )
        
        return jsonify({
            "status": "success",
            "message": f"Placed {len(placed)} pending orders",
            "data": placed
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/pending-orders/<symbol>", methods=["DELETE"])
def api_cancel_pending_order(symbol):
    """Cancel a pending order for a specific symbol."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        success = engine.pending_order_manager.cancel_pending_order(symbol)
        if success:
            return jsonify({"status": "success", "message": f"Cancelled pending order for {symbol}"})
        else:
            return jsonify({"status": "error", "message": f"No pending order found for {symbol}"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/panic-close", methods=["POST"])
def api_panic_close():
    """Close all currently open MT5 positions and stop new execution."""
    with _engine_lock:
        try:
            if not engine:
                return jsonify({"status": "error", "message": "Bot not running"}), 400

            engine.killed["all"] = True
            positions = engine.mt5.get_positions() or []
            closed = []
            failed = []

            for position in positions:
                ticket = position.get("ticket")
                symbol = position.get("symbol")
                if not ticket:
                    failed.append({"symbol": symbol, "reason": "Missing position ticket"})
                    continue

                if engine.mt5.close_position(ticket):
                    closed.append({"ticket": ticket, "symbol": symbol})
                    engine.active_trades.pop(symbol, None)
                    try:
                        engine._register_trade_close(symbol)
                    except Exception:
                        pass
                else:
                    failed.append({"ticket": ticket, "symbol": symbol, "reason": "MT5 close failed"})

            return jsonify({
                "status": "success" if not failed else "partial",
                "message": f"Closed {len(closed)} position(s); {len(failed)} failed",
                "data": {
                    "closed": closed,
                    "failed": failed,
                    "kill_switch": engine.killed,
                },
            })
        except Exception as e:
            logger.error(f"Panic close failed: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/endpoints", methods=["GET"])
def api_endpoints():
    """Return known API endpoints for frontend discovery and diagnostics."""
    try:
        endpoints = [
            {"method": "GET", "path": "/api/bot/status", "description": "Bot running and MT5 status"},
            {"method": "POST", "path": "/api/bot/start", "description": "Start bot with option payload"},
            {"method": "POST", "path": "/api/bot/stop", "description": "Stop bot"},
            {"method": "GET", "path": "/api/positions", "description": "Get open positions"},
            {"method": "GET", "path": "/api/chart-visuals/<symbol>", "description": "Get chart trendlines and support/resistance overlays"},
            {"method": "GET", "path": "/api/signals", "description": "Get recent signals"},
            {"method": "POST", "path": "/api/signals/execute", "description": "Manually execute a current signal through engine safety checks"},
            {"method": "GET", "path": "/api/logs", "description": "Get runtime logs and trades"},
            {"method": "GET", "path": "/api/stats", "description": "Get performance stats"},
            {"method": "GET", "path": "/api/config", "description": "Get config / env values"},
            {"method": "POST", "path": "/api/config", "description": "Update config file"},
            {"method": "GET", "path": "/api/watchlist", "description": "Get watchlist status"},
            {"method": "GET", "path": "/api/pending-orders", "description": "Get pending orders"},
            {"method": "POST", "path": "/api/pending-orders/place", "description": "Trigger pending order placement"},
            {"method": "DELETE", "path": "/api/pending-orders/<symbol>", "description": "Cancel pending order"},
            {"method": "POST", "path": "/api/panic-close", "description": "Close all open positions and enable global kill switch"},
            {"method": "GET", "path": "/api/kill", "description": "Get kill map"},
            {"method": "POST", "path": "/api/kill", "description": "Set kill/enable for symbol"},
            {"method": "GET", "path": "/api/sessions", "description": "Get trading sessions"},
        ]
        return jsonify({"status": "success", "data": endpoints})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist():
    """Get conditional watchlist status and phase information."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        summary = engine.conditional_watchlist_manager.get_watchlist_summary()
        ready = engine.conditional_watchlist_manager.get_ready_for_execution()
        
        return jsonify({
            "status": "success",
            "data": {
                "watchlist": summary,
                "ready_for_execution": ready,
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/watchlist/initialize", methods=["POST"])
def api_initialize_watchlist():
    """Initialize the conditional watchlist for monitoring."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        data = request.json or {}
        symbols = data.get("symbols", engine.symbols)
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",")]
        
        initialized = engine.conditional_watchlist_manager.initialize_watchlist(symbols)
        
        return jsonify({
            "status": "success",
            "message": f"Initialized watchlist for {len(initialized)} symbols",
            "data": initialized
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/watchlist/<symbol>/reset", methods=["POST"])
def api_reset_watchlist(symbol):
    """Reset a symbol in the watchlist back to Phase 1."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        success = engine.conditional_watchlist_manager.reset_symbol(symbol)
        if success:
            return jsonify({"status": "success", "message": f"Reset {symbol} to Phase 1"})
        else:
            return jsonify({"status": "error", "message": f"Symbol {symbol} not in watchlist"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/features", methods=["GET", "POST"])
def api_features():
    """Get or update feature toggles."""
    try:
        if not engine:
            return jsonify({"status": "error", "message": "Bot not running"}), 400
        
        if request.method == "GET":
            return jsonify({
                "status": "success",
                "data": engine.features
            })
        
        # POST to update features
        data = request.json or {}
        for key in ["pending_orders", "conditional_watchlist"]:
            if key in data:
                engine.features[key] = bool(data[key])
        
        return jsonify({
            "status": "success",
            "message": "Features updated",
            "data": engine.features
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    socketio.run(
        app,
        debug=False,
        host="0.0.0.0",
        port=5000,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
