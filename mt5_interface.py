"""
MT5 Interface - Connection and Order Management
"""
import logging
import time
import MetaTrader5 as mt5
from dotenv import load_dotenv
import os

load_dotenv()
logger = logging.getLogger(__name__)


class MT5Interface:
    def __init__(self):
        self.is_connected = False
        self.last_order_error = None
        self.account = os.getenv("MT5_ACCOUNT")
        self.password = os.getenv("MT5_PASSWORD")
        self.server = os.getenv("MT5_SERVER")
        self._connection_retry_count = 0

    def _trade_mode_label(self, trade_mode):
        mode_map = {
            getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0): "DEMO",
            getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1): "CONTEST",
            getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2): "REAL",
        }
        return mode_map.get(trade_mode, f"UNKNOWN({trade_mode})")

    def _account_info_payload(self, info):
        if info is None:
            return None
        trade_mode = getattr(info, "trade_mode", None)
        return {
            "login": getattr(info, "login", None),
            "server": getattr(info, "server", None),
            "company": getattr(info, "company", None),
            "name": getattr(info, "name", None),
            "trade_mode": trade_mode,
            "trade_mode_label": self._trade_mode_label(trade_mode),
            "leverage": getattr(info, "leverage", None),
            "balance": info.balance,
            "equity": info.equity,
            "free_margin": info.margin_free,
            "margin_level": info.margin_level,
            "currency": info.currency,
        }

    def _get_filling_modes(self, symbol: str):
        """Return candidate MT5 order filling policies for a symbol.

        MT5 exposes symbol_info.filling_mode as broker metadata that can behave
        like a bitmask on some servers. Trade requests, however, require a
        concrete ORDER_FILLING_* value in the type_filling field.
        """
        candidates = []

        def add(mode):
            if mode is not None and mode not in candidates:
                candidates.append(mode)

        try:
            info = mt5.symbol_info(symbol)
            if info is None:
                return [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]

            raw_mode = getattr(info, "filling_mode", None)
            if raw_mode is not None:
                # Common MT5 bitmask flags: FOK=1, IOC=2. RETURN may not be
                # represented in the symbol mask but is still useful as a final
                # fallback for some pending/market-execution brokers.
                if raw_mode & 2:
                    add(mt5.ORDER_FILLING_IOC)
                if raw_mode & 1:
                    add(mt5.ORDER_FILLING_FOK)
                if raw_mode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
                    add(raw_mode)

            add(mt5.ORDER_FILLING_IOC)
            add(mt5.ORDER_FILLING_FOK)
            add(mt5.ORDER_FILLING_RETURN)
            return candidates
        except Exception:
            return [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]

    def _is_invalid_filling_result(self, result) -> bool:
        if result is None:
            return False
        invalid_fill_code = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", None)
        if invalid_fill_code is not None and getattr(result, "retcode", None) == invalid_fill_code:
            return True
        comment = str(getattr(result, "comment", "") or "").lower()
        return "filling" in comment or "unsupported filling" in comment

    def _send_order_with_filling_fallback(self, request: dict, symbol: str):
        """Send an MT5 order request, retrying alternate filling policies."""
        last_result = None
        for filling in self._get_filling_modes(symbol):
            trial = dict(request)
            trial["type_filling"] = filling
            trial.pop("filling", None)
            result = mt5.order_send(trial)
            last_result = result
            if result is None:
                return None
            if not self._is_invalid_filling_result(result):
                return result
            logger.warning(
                "Order rejected for %s with filling mode %s: [%s] %s; trying next mode",
                symbol,
                filling,
                getattr(result, "retcode", None),
                getattr(result, "comment", ""),
            )
        return last_result

    def get_symbol_info(self, symbol: str):
        try:
            return mt5.symbol_info(symbol)
        except Exception as e:
            logger.error(f"Error fetching symbol info for {symbol}: {e}")
            return None

    def get_symbol_tick(self, symbol: str):
        try:
            return mt5.symbol_info_tick(symbol)
        except Exception as e:
            logger.error(f"Error fetching tick data for {symbol}: {e}")
            return None

    def get_symbols(self):
        """Get all available symbols from MT5"""
        try:
            return mt5.symbols_get()
        except Exception as e:
            logger.error(f"Error fetching symbols: {e}")
            return []

    def connect(self):
        """CRITICAL FIX: Connect to MT5 with automatic retry logic"""
        max_retries = 5
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Connecting to MT5 (attempt {attempt+1}/{max_retries})...")
                
                if not mt5.initialize():
                    logger.error(f"MT5 initialization failed (attempt {attempt+1})")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 10)  # Exponential backoff, max 10s
                    continue
                
                # Try to get account info to verify connection
                account_info = mt5.account_info()
                if account_info is None:
                    logger.error(f"Failed to get account info (attempt {attempt+1})")
                    mt5.shutdown()
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 10)
                    continue
                
                self.is_connected = True
                self._connection_retry_count = 0
                account = self._account_info_payload(account_info) or {}
                logger.info(
                    "Connected to MT5 account=%s mode=%s server=%s company=%s leverage=1:%s balance=%.2f equity=%.2f %s",
                    account.get("login"),
                    account.get("trade_mode_label"),
                    account.get("server"),
                    account.get("company"),
                    account.get("leverage"),
                    float(account.get("balance") or 0),
                    float(account.get("equity") or 0),
                    account.get("currency") or "",
                )
                return True
                
            except Exception as e:
                logger.error(f"MT5 connection error (attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10)
        
        logger.critical(f"🚨 Failed to connect to MT5 after {max_retries} attempts")
        self.is_connected = False
        return False

    def ensure_connected(self):
        """CRITICAL FIX: Check connection and reconnect if needed"""
        if not self.is_connected:
            logger.warning("MT5 connection flag is down, attempting to reconnect...")
            return self.connect()
        try:
            if mt5.account_info() is None:
                logger.warning("MT5 heartbeat failed, reconnecting...")
                self.is_connected = False
                mt5.shutdown()
                return self.connect()
        except Exception as e:
            logger.warning(f"MT5 heartbeat exception, reconnecting: {e}")
            self.is_connected = False
            try:
                mt5.shutdown()
            except Exception:
                pass
            return self.connect()
        return True

    def disconnect(self):
        try:
            if self.is_connected:
                mt5.shutdown()
                self.is_connected = False
                logger.info("Disconnected from MT5")
        except Exception as e:
            logger.error(f"Error disconnecting MT5: {e}")

    def get_account_info(self):
        try:
            if not self.ensure_connected():
                return None
            info = mt5.account_info()
            if info is None:
                return None
            return self._account_info_payload(info)
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return None

    def get_positions(self):
        try:
            if not self.ensure_connected():
                return []
            positions = mt5.positions_get()
            if positions is None:
                return []
            return [
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "entry": p.price_open,
                    "current": p.price_current,
                    "profit": p.profit,
                    "sl": getattr(p, "sl", None),
                    "tp": getattr(p, "tp", None),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    def place_buy_order(self, symbol, volume, price, sl, tp):
        """CRITICAL FIX: Place BUY market order with comprehensive error handling"""
        try:
            self.last_order_error = None
            if not self.ensure_connected():
                self.last_order_error = "MT5 not connected"
                logger.error(self.last_order_error)
                return None
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY,
                "price": price,
                "sl": sl,
                "tp": tp,
                "comment": "FVG_BUY",
            }
            
            result = self._send_order_with_filling_fallback(request, symbol)
            
            # CRITICAL FIX: Handle all error codes
            if result is None:
                self.last_order_error = f"BUY order failed for {symbol}: No response from MT5"
                logger.error(self.last_order_error)
                return None
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✓ BUY order placed: {symbol} (vol={volume}, price={price:.5f}, SL={sl:.5f}, TP={tp:.5f})")
                return result.order
            
            # Handle specific error codes
            error_messages = {
                mt5.TRADE_RETCODE_NO_MONEY: "Insufficient margin/funds",
                mt5.TRADE_RETCODE_INVALID_VOLUME: "Invalid volume for symbol",
                mt5.TRADE_RETCODE_MARKET_CLOSED: "Market is closed",
                mt5.TRADE_RETCODE_PRICE_CHANGED: "Price changed before execution",
                mt5.TRADE_RETCODE_INVALID_EXPIRATION: "Invalid order expiration",
                mt5.TRADE_RETCODE_ORDER_CHANGED: "Order was changed",
                mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Too many requests to MT5",
                mt5.TRADE_RETCODE_NO_CHANGES: "No changes to apply",
                mt5.TRADE_RETCODE_TRADE_DISABLED: "Trading is disabled",
            }
            
            error_msg = error_messages.get(result.retcode, result.comment or f"Unknown error {result.retcode}")
            self.last_order_error = f"BUY order failed for {symbol}: [{result.retcode}] {error_msg}"
            logger.error(f"🚨 BUY order failed for {symbol}: [{result.retcode}] {error_msg}")
            
            return None
        except Exception as e:
            self.last_order_error = f"Exception placing buy order for {symbol}: {e}"
            logger.error(f"Exception placing buy order for {symbol}: {e}", exc_info=True)
            return None

    def place_sell_order(self, symbol, volume, price, sl, tp):
        """CRITICAL FIX: Place SELL market order with comprehensive error handling"""
        try:
            self.last_order_error = None
            if not self.ensure_connected():
                self.last_order_error = "MT5 not connected"
                logger.error(self.last_order_error)
                return None
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_SELL,
                "price": price,
                "sl": sl,
                "tp": tp,
                "comment": "FVG_SELL",
            }
            
            result = self._send_order_with_filling_fallback(request, symbol)
            
            # CRITICAL FIX: Handle all error codes
            if result is None:
                self.last_order_error = f"SELL order failed for {symbol}: No response from MT5"
                logger.error(self.last_order_error)
                return None
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✓ SELL order placed: {symbol} (vol={volume}, price={price:.5f}, SL={sl:.5f}, TP={tp:.5f})")
                return result.order
            
            # Handle specific error codes
            error_messages = {
                mt5.TRADE_RETCODE_NO_MONEY: "Insufficient margin/funds",
                mt5.TRADE_RETCODE_INVALID_VOLUME: "Invalid volume for symbol",
                mt5.TRADE_RETCODE_MARKET_CLOSED: "Market is closed",
                mt5.TRADE_RETCODE_PRICE_CHANGED: "Price changed before execution",
                mt5.TRADE_RETCODE_INVALID_EXPIRATION: "Invalid order expiration",
                mt5.TRADE_RETCODE_ORDER_CHANGED: "Order was changed",
                mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Too many requests to MT5",
                mt5.TRADE_RETCODE_NO_CHANGES: "No changes to apply",
                mt5.TRADE_RETCODE_TRADE_DISABLED: "Trading is disabled",
            }
            
            error_msg = error_messages.get(result.retcode, result.comment or f"Unknown error {result.retcode}")
            self.last_order_error = f"SELL order failed for {symbol}: [{result.retcode}] {error_msg}"
            logger.error(f"🚨 SELL order failed for {symbol}: [{result.retcode}] {error_msg}")
            
            return None
        except Exception as e:
            self.last_order_error = f"Exception placing sell order for {symbol}: {e}"
            logger.error(f"Exception placing sell order for {symbol}: {e}", exc_info=True)
            return None

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        """Place a pending BUY_LIMIT order (automatically executed when price hits entry)."""
        try:
            self.last_order_error = None
            if not self.ensure_connected():
                self.last_order_error = "MT5 not connected"
                return None
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY_LIMIT,
                "price": price,
                "sl": sl,
                "tp": tp,
                "comment": "PENDING_BUY_LIMIT_FVG",
            }
            result = self._send_order_with_filling_fallback(request, symbol)
            if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                logger.info(f"BUY_LIMIT pending order placed: {symbol} at {price:.5f}")
                return result.order
            self.last_order_error = f"BUY_LIMIT order failed for {symbol}: [{result.retcode}] {result.comment}"
            logger.error(f"BUY_LIMIT order failed: {result.comment}")
            return None
        except Exception as e:
            self.last_order_error = f"Error placing buy limit order for {symbol}: {e}"
            logger.error(f"Error placing buy limit order: {e}")
            return None

    def place_sell_limit_order(self, symbol, volume, price, sl, tp):
        """Place a pending SELL_LIMIT order (automatically executed when price hits entry)."""
        try:
            self.last_order_error = None
            if not self.ensure_connected():
                self.last_order_error = "MT5 not connected"
                return None
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_SELL_LIMIT,
                "price": price,
                "sl": sl,
                "tp": tp,
                "comment": "PENDING_SELL_LIMIT_FVG",
            }
            result = self._send_order_with_filling_fallback(request, symbol)
            if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                logger.info(f"SELL_LIMIT pending order placed: {symbol} at {price:.5f}")
                return result.order
            self.last_order_error = f"SELL_LIMIT order failed for {symbol}: [{result.retcode}] {result.comment}"
            logger.error(f"SELL_LIMIT order failed: {result.comment}")
            return None
        except Exception as e:
            self.last_order_error = f"Error placing sell limit order for {symbol}: {e}"
            logger.error(f"Error placing sell limit order: {e}")
            return None

    def get_pending_orders(self):
        """Retrieve all pending orders from MT5."""
        try:
            if not self.ensure_connected():
                return []
            orders = mt5.orders_get()
            if orders is None:
                return []
            pending = []
            for o in orders:
                volume = getattr(o, 'volume', None)
                if volume is None:
                    volume = getattr(o, 'volume_current', None)
                if volume is None:
                    volume = getattr(o, 'volume_initial', None)

                pending.append({
                    "ticket": o.ticket,
                    "symbol": o.symbol,
                    "type": self._order_type_to_string(o.type),
                    "volume": volume,
                    "price": o.price_open,
                    "sl": o.sl,
                    "tp": o.tp,
                    "time_setup": o.time_setup,
                    "comment": o.comment,
                })
            return pending
        except Exception as e:
            logger.error(f"Error getting pending orders: {e}")
            return []

    def cancel_order(self, ticket: int) -> bool:
        """Cancel a pending order by ticket number."""
        try:
            if not self.ensure_connected():
                return False
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": ticket,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Pending order {ticket} cancelled")
                return True
            logger.error(f"Failed to cancel order {ticket}: {result.comment}")
            return False
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    def _order_type_to_string(self, order_type):
        """Convert MT5 order type constant to string."""
        order_types = {
            mt5.ORDER_TYPE_BUY: "BUY",
            mt5.ORDER_TYPE_SELL: "SELL",
            mt5.ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT",
            mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
            mt5.ORDER_TYPE_BUY_STOP: "BUY_STOP",
            mt5.ORDER_TYPE_SELL_STOP: "SELL_STOP",
        }
        return order_types.get(order_type, "UNKNOWN")

    def modify_position_sltp(self, ticket, symbol, sl=None, tp=None):
        """Modify stop-loss and/or take-profit of an existing position."""
        try:
            current_sl = None
            current_tp = None
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                current_sl = getattr(positions[0], "sl", None)
                current_tp = getattr(positions[0], "tp", None)

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": symbol,
            }
            request["sl"] = sl if sl is not None else current_sl
            request["tp"] = tp if tp is not None else current_tp

            result = mt5.order_send(request)
            if result is None:
                logger.error(f"Failed to update SL/TP for position {ticket}: No response from MT5")
                return False
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Updated SL/TP for position {ticket}: SL={sl}, TP={tp}")
                return True
            logger.error(f"Failed to update SL/TP: {result.comment}")
            return False
        except Exception as e:
            logger.error(f"Error modifying SL/TP: {e}")
            return False

    def modify_position_sl(self, ticket, symbol, sl):
        """Modify the stop-loss of an existing position."""
        return self.modify_position_sltp(ticket, symbol, sl=sl)

    def modify_position_tp(self, ticket, symbol, tp):
        """Modify the take-profit of an existing position."""
        return self.modify_position_sltp(ticket, symbol, tp=tp)

    def _normalize_close_volume(self, symbol, requested_volume, current_volume):
        """Round a partial-close volume to the broker's allowed lot step."""
        try:
            info = mt5.symbol_info(symbol)
            min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
            step = float(getattr(info, "volume_step", 0.01) or 0.01)
            current_volume = float(current_volume or 0)
            requested_volume = min(float(requested_volume or 0), current_volume)
            if requested_volume <= 0:
                return 0
            steps = int(requested_volume / step)
            volume = round(steps * step, 2)
            if volume < min_lot and current_volume >= min_lot:
                volume = min_lot
            if current_volume - volume > 0 and current_volume - volume < min_lot:
                volume = current_volume
            return round(min(volume, current_volume), 2)
        except Exception:
            return round(min(float(requested_volume or 0), float(current_volume or 0)), 2)

    def close_position_volume(self, ticket, volume=None, comment="PARTIAL_TP"):
        """Close all or part of a position by sending the opposite market order."""
        try:
            if not self.ensure_connected():
                logger.error("MT5 not connected")
                return False

            positions = mt5.positions_get()
            if not positions:
                return False
            for pos in positions:
                if pos.ticket != ticket:
                    continue

                close_volume = pos.volume if volume is None else self._normalize_close_volume(pos.symbol, volume, pos.volume)
                if close_volume <= 0:
                    logger.error(f"Invalid close volume for position {ticket}: {close_volume}")
                    return False

                tick = mt5.symbol_info_tick(pos.symbol)
                if tick is None:
                    logger.error(f"Failed to get tick for closing position {ticket} ({pos.symbol})")
                    return False

                close_type = (
                    mt5.ORDER_TYPE_SELL
                    if pos.type == mt5.ORDER_TYPE_BUY
                    else mt5.ORDER_TYPE_BUY
                )
                price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": close_volume,
                    "type": close_type,
                    "position": ticket,
                    "price": price,
                    "deviation": 20,
                    "comment": comment,
                }
                result = self._send_order_with_filling_fallback(request, pos.symbol)
                if result is None:
                    logger.error(f"Failed to close position {ticket}: No response from MT5")
                    return False
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"Closed {close_volume} lots from position {ticket}")
                    return True
                logger.error(f"Failed to close position {ticket}: [{result.retcode}] {result.comment}")
                return False
            return False
        except Exception as e:
            logger.error(f"Error partially closing position: {e}")
            return False

    def close_position(self, ticket):
        try:
            return self.close_position_volume(ticket, volume=None, comment="PANIC_CLOSE")
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False
