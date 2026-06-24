from flask import Flask, request, jsonify
import os
import json
import time
import math
import requests
import threading
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)

TRADERSPOST_WEBHOOK = os.getenv("TRADERSPOST_WEBHOOK", "")
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
STATE_FILE = os.getenv("STATE_FILE", "portfolio_state.json")
# إذا كنت تستخدم Railway Volume اجعلها مثلاً:
# STATE_FILE=/data/portfolio_state.json
ALLOW_ORPHAN_EXIT_WITH_SIGNAL_QUANTITY = os.getenv("ALLOW_ORPHAN_EXIT_WITH_SIGNAL_QUANTITY", "true").lower() == "true"
PARTIAL_SELL_MIN_FULL_EXIT_QTY = int(os.getenv("PARTIAL_SELL_MIN_FULL_EXIT_QTY", "4"))
PARTIAL_TAKE_PROFIT_PERCENT = float(os.getenv("PARTIAL_TAKE_PROFIT_PERCENT", "0.30"))
SCALE_OUT_PERCENT = float(os.getenv("SCALE_OUT_PERCENT", "0.20"))

# V2.6 Pending Orders + Production Safety
ALLOW_TEST_TICKERS = os.getenv("ALLOW_TEST_TICKERS", "false").lower() == "true"
TEST_TICKER_PREFIXES = tuple(
    x.strip().upper()
    for x in os.getenv("TEST_TICKER_PREFIXES", "TEST").split(",")
    if x.strip()
)

# V2.4 Auto Exit Manager
AUTO_EXIT_MANAGER_ENABLED = os.getenv("AUTO_EXIT_MANAGER_ENABLED", "true").lower() == "true"
AUTO_EXIT_FULL_ON_STOP_LOSS = os.getenv("AUTO_EXIT_FULL_ON_STOP_LOSS", "true").lower() == "true"
AUTO_EXIT_FULL_ON_TAKE_PROFIT = os.getenv("AUTO_EXIT_FULL_ON_TAKE_PROFIT", "true").lower() == "true"
DEFAULT_STOP_LOSS_PERCENT = float(os.getenv("DEFAULT_STOP_LOSS_PERCENT", "0") or 0)
DEFAULT_TAKE_PROFIT_PERCENT = float(os.getenv("DEFAULT_TAKE_PROFIT_PERCENT", "0") or 0)

# =========================================================
# PORTFOLIO MANAGER V2.1
# Dynamic Sizing + Session Risk Manager
# + High Quality Near-Close Exception
# + IBKR Fee Guard
# =========================================================

AVAILABLE_CASH = float(os.getenv("AVAILABLE_CASH", "0") or 0)
CASH_USAGE_PERCENT = float(os.getenv("CASH_USAGE_PERCENT", "0.50") or 0.50)
MIN_TRADE_VALUE = float(os.getenv("MIN_TRADE_VALUE", "20") or 20)
USE_DYNAMIC_POSITION_SIZING = os.getenv("USE_DYNAMIC_POSITION_SIZING", "true").lower() == "true"
SAFETY_BUFFER_PERCENT = float(os.getenv("SAFETY_BUFFER_PERCENT", "0.05") or 0.05)

# TradingView safety fields final guard
RESPECT_TRADINGVIEW_SAFETY_FLAGS = os.getenv("RESPECT_TRADINGVIEW_SAFETY_FLAGS", "true").lower() == "true"
BLOCK_BUY_IF_MARKET_UNSAFE = os.getenv("BLOCK_BUY_IF_MARKET_UNSAFE", "true").lower() == "true"
BLOCK_BUY_IF_STOCK_UNSAFE = os.getenv("BLOCK_BUY_IF_STOCK_UNSAFE", "true").lower() == "true"
BLOCK_BUY_IF_TIME_NOT_ALLOWED = os.getenv("BLOCK_BUY_IF_TIME_NOT_ALLOWED", "true").lower() == "true"

# Session risk manager
SESSION_RISK_MANAGER_ENABLED = os.getenv("SESSION_RISK_MANAGER_ENABLED", "true").lower() == "true"
BLOCK_BUY_NEAR_SESSION_END = os.getenv("BLOCK_BUY_NEAR_SESSION_END", "true").lower() == "true"
FORCE_CLOSE_BEFORE_SESSION_END = os.getenv("FORCE_CLOSE_BEFORE_SESSION_END", "true").lower() == "true"
SESSION_CLOSE_BUFFER_MINUTES = int(os.getenv("SESSION_CLOSE_BUFFER_MINUTES", "5"))
SESSION_CHECK_INTERVAL_SECONDS = int(os.getenv("SESSION_CHECK_INTERVAL_SECONDS", "30"))

ALLOW_HOLD_BETWEEN_SESSIONS = os.getenv("ALLOW_HOLD_BETWEEN_SESSIONS", "false").lower() == "true"
ALLOW_OVERNIGHT_HOLD = os.getenv("ALLOW_OVERNIGHT_HOLD", "false").lower() == "true"

# High quality exception near session close
ALLOW_HIGH_QUALITY_NEAR_CLOSE_ENTRY = os.getenv("ALLOW_HIGH_QUALITY_NEAR_CLOSE_ENTRY", "true").lower() == "true"
HIGH_QUALITY_MIN_SCORE = float(os.getenv("HIGH_QUALITY_MIN_SCORE", "90"))
HIGH_QUALITY_MIN_ALPHA_SCORE = float(os.getenv("HIGH_QUALITY_MIN_ALPHA_SCORE", "80"))
HIGH_QUALITY_MIN_REL_VOLUME = float(os.getenv("HIGH_QUALITY_MIN_REL_VOLUME", "1.5"))
HIGH_QUALITY_REQUIRE_TV_TIME_ALLOWED = os.getenv("HIGH_QUALITY_REQUIRE_TV_TIME_ALLOWED", "true").lower() == "true"
HIGH_QUALITY_REQUIRE_TV_MARKET_SAFE = os.getenv("HIGH_QUALITY_REQUIRE_TV_MARKET_SAFE", "true").lower() == "true"
HIGH_QUALITY_REQUIRE_TV_STOCK_SAFE = os.getenv("HIGH_QUALITY_REQUIRE_TV_STOCK_SAFE", "true").lower() == "true"

# IBKR / Derayah Pro commission guard
IBKR_FEE_PER_SHARE = float(os.getenv("IBKR_FEE_PER_SHARE", "0.005"))
IBKR_MIN_FEE_PER_ORDER = float(os.getenv("IBKR_MIN_FEE_PER_ORDER", "1.00"))
MIN_PROFIT_FEE_MULTIPLIER = float(os.getenv("MIN_PROFIT_FEE_MULTIPLIER", "2.0"))
ENFORCE_IBKR_FEE_GUARD = os.getenv("ENFORCE_IBKR_FEE_GUARD", "true").lower() == "true"

MARKET_TIMEZONE = os.getenv("MARKET_TIMEZONE", "America/New_York")
PREMARKET_START = os.getenv("PREMARKET_START", "04:00")
REGULAR_START = os.getenv("REGULAR_START", "09:30")
REGULAR_END = os.getenv("REGULAR_END", "16:00")
AFTERHOURS_END = os.getenv("AFTERHOURS_END", "20:00")
OVERNIGHT_END = os.getenv("OVERNIGHT_END", "04:00")


def now_ts():
    return int(time.time())


def safe_float(value, default=0.0):
    try:
        if value in [None, "", "None"]:
            return default
        return float(str(value).replace(",", "").replace("%", "").replace("$", ""))
    except Exception:
        return default


def safe_int(value, default=1):
    try:
        if value in [None, "", "None"]:
            return default
        return int(float(value))
    except Exception:
        return default


def is_test_ticker(ticker):
    ticker = str(ticker or "").upper().strip()
    return bool(ticker) and any(ticker.startswith(prefix) for prefix in TEST_TICKER_PREFIXES)


def parse_hhmm(value, fallback="00:00"):
    try:
        h, m = str(value).split(":")
        return dtime(int(h), int(m))
    except Exception:
        h, m = str(fallback).split(":")
        return dtime(int(h), int(m))


def market_now():
    try:
        return datetime.now(ZoneInfo(MARKET_TIMEZONE))
    except Exception:
        return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))


def minutes_until_time(now_dt, target_time):
    target = now_dt.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if target < now_dt:
        target = target + timedelta(days=1)
    return (target - now_dt).total_seconds() / 60.0


def get_session_info():
    now = market_now()
    current = now.time()

    pre_start = parse_hhmm(PREMARKET_START, "04:00")
    reg_start = parse_hhmm(REGULAR_START, "09:30")
    reg_end = parse_hhmm(REGULAR_END, "16:00")
    after_end = parse_hhmm(AFTERHOURS_END, "20:00")
    overnight_end = parse_hhmm(OVERNIGHT_END, "04:00")

    is_weekend = now.weekday() >= 5

    if pre_start <= current < reg_start:
        session = "premarket"
        session_end = reg_start
    elif reg_start <= current < reg_end:
        session = "regular"
        session_end = reg_end
    elif reg_end <= current < after_end:
        session = "afterhours"
        session_end = after_end
    else:
        session = "overnight"
        session_end = overnight_end

    minutes_left = minutes_until_time(now, session_end)

    return {
        "session": session,
        "date": now.strftime("%Y-%m-%d"),
        "now_ny": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "is_weekend": is_weekend,
        "session_end": session_end.strftime("%H:%M"),
        "minutes_to_session_end": round(minutes_left, 2),
        "near_session_end": minutes_left <= SESSION_CLOSE_BUFFER_MINUTES,
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"positions": {}, "pending_orders": {}, "closed_sessions": {}, "history": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}

    state.setdefault("positions", {})
    state.setdefault("pending_orders", {})
    state.setdefault("closed_sessions", {})
    state.setdefault("history", [])
    return state


def save_state(state):
    # مهم على Railway: إذا استخدمت Volume مثل /data، ننشئ المجلد تلقائياً.
    parent = os.path.dirname(os.path.abspath(STATE_FILE))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    tmp_file = STATE_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, STATE_FILE)


def add_history(state, event, details=None):
    state.setdefault("history", [])
    info = get_session_info()
    state["history"].append({
        "ts": now_ts(),
        "date": info["date"],
        "session": info["session"],
        "event": event,
        "details": details or {},
    })
    if len(state["history"]) > 1000:
        state["history"] = state["history"][-1000:]


def send_to_traderspost(payload):
    if not TRADERSPOST_WEBHOOK:
        print("Missing TRADERSPOST_WEBHOOK", flush=True)
        return False

    r = requests.post(TRADERSPOST_WEBHOOK, json=payload, timeout=15)
    print("TradersPost:", r.status_code, r.text[:500], flush=True)
    return r.status_code in [200, 201, 202]


def get_trade_price(data):
    order_type = str(data.get("orderType", "market")).lower()

    if order_type == "limit":
        price = safe_float(data.get("limitPrice"), 0)
        if price > 0:
            return price

    for key in ["signalPrice", "price", "close", "current_price", "lastPrice"]:
        price = safe_float(data.get(key), 0)
        if price > 0:
            return price

    return 0


def get_take_profit_price(data):
    extras = get_extras(data)
    tp = safe_float(extras.get("takeProfitPrice"), 0)
    if tp > 0:
        return tp
    return safe_float(data.get("takeProfitPrice"), 0)


def get_stop_loss_price(data):
    extras = get_extras(data)
    sl = safe_float(extras.get("stopLossPrice"), 0)
    if sl > 0:
        return sl
    return safe_float(data.get("stopLossPrice"), 0)


def derive_default_exit_prices(entry_price):
    stop_loss = 0.0
    take_profit = 0.0

    if entry_price > 0 and DEFAULT_STOP_LOSS_PERCENT > 0:
        stop_loss = entry_price * (1 - DEFAULT_STOP_LOSS_PERCENT)

    if entry_price > 0 and DEFAULT_TAKE_PROFIT_PERCENT > 0:
        take_profit = entry_price * (1 + DEFAULT_TAKE_PROFIT_PERCENT)

    return round(stop_loss, 4), round(take_profit, 4)


def estimate_round_trip_fee(quantity):
    qty = max(safe_float(quantity, 0), 0)
    one_way = max(qty * IBKR_FEE_PER_SHARE, IBKR_MIN_FEE_PER_ORDER)
    return one_way * 2


def validate_expected_profit_after_fees(data, final_quantity):
    """
    يمنع الصفقة إذا الربح المتوقع إلى TP لا يغطي عمولة IBKR/Derayah Pro.
    يستخدم takeProfitPrice القادم من TradingView extras.
    """
    if not ENFORCE_IBKR_FEE_GUARD:
        return True, {
            "enabled": False,
            "reason": "IBKR fee guard disabled"
        }

    entry_price = get_trade_price(data)
    take_profit = get_take_profit_price(data)

    if entry_price <= 0 or take_profit <= 0 or final_quantity <= 0:
        # إذا لا يوجد TP واضح لا نرفض كل الصفقات؛ فقط نسجل أن الفحص غير مكتمل.
        return True, {
            "enabled": True,
            "passed": True,
            "warning": "Missing entry or takeProfitPrice; fee guard skipped",
            "entry_price": entry_price,
            "take_profit": take_profit,
            "quantity": final_quantity
        }

    expected_gross_profit = max((take_profit - entry_price) * final_quantity, 0)
    estimated_fees = estimate_round_trip_fee(final_quantity)
    required_profit = estimated_fees * MIN_PROFIT_FEE_MULTIPLIER

    passed = expected_gross_profit >= required_profit

    return passed, {
        "enabled": True,
        "passed": passed,
        "entry_price": round(entry_price, 4),
        "take_profit": round(take_profit, 4),
        "quantity": final_quantity,
        "expected_gross_profit": round(expected_gross_profit, 4),
        "estimated_round_trip_fees": round(estimated_fees, 4),
        "required_profit": round(required_profit, 4),
        "min_profit_fee_multiplier": MIN_PROFIT_FEE_MULTIPLIER
    }


def get_position_reserved_value(position):
    qty = safe_float(position.get("quantity"), 0)
    price = safe_float(position.get("limit_price") or position.get("entry_signal_price") or position.get("entry_price"), 0)
    return max(qty * price, 0)


def get_reserved_cash(positions, pending_orders=None):
    total = 0.0
    for _, pos in (positions or {}).items():
        if isinstance(pos, dict):
            total += get_position_reserved_value(pos)

    # Pending limit orders reserve cash too because IBKR may fill them later.
    for _, order in (pending_orders or {}).items():
        if isinstance(order, dict):
            total += get_position_reserved_value(order)

    return total


def get_effective_available_cash(state=None):
    """
    Returns the cash value Portfolio Manager should use for sizing.

    Priority:
    1) Runtime/persisted cash saved by /update_cash in portfolio_state.json
    2) Railway AVAILABLE_CASH environment variable

    This lets you update cash without changing Railway variables or redeploying.
    """
    try:
        if state is None:
            state = load_state()
        cash_state = state.get("cash", {}) if isinstance(state, dict) else {}
        synced_cash = safe_float(cash_state.get("available_cash"), -1)
        if synced_cash >= 0:
            return synced_cash
    except Exception:
        pass
    return AVAILABLE_CASH


def get_available_cash_after_reserved(positions, state=None):
    base_cash = get_effective_available_cash(state)
    pending_orders = {}
    if isinstance(state, dict):
        pending_orders = state.get("pending_orders", {})
    reserved = get_reserved_cash(positions, pending_orders)
    remaining = max(base_cash - reserved, 0)
    return remaining, reserved


def calculate_dynamic_quantity(data, positions, state=None):
    original_quantity = safe_int(data.get("quantity", 1), 1)
    price = get_trade_price(data)
    effective_available_cash = get_effective_available_cash(state)

    if not USE_DYNAMIC_POSITION_SIZING:
        return original_quantity, {
            "mode": "fixed_quantity_from_tradingview",
            "price_used": price,
            "quantity": original_quantity
        }

    available_after_reserved, reserved_cash = get_available_cash_after_reserved(positions, state)

    if effective_available_cash <= 0:
        return 0, {
            "mode": "dynamic_position_sizing_v2_1",
            "blocked": True,
            "reason": "AVAILABLE_CASH is not configured or <= 0",
            "available_cash": effective_available_cash,
            "price_used": price
        }

    if price <= 0:
        return 0, {
            "mode": "dynamic_position_sizing_v2_1",
            "blocked": True,
            "reason": "Missing valid price for sizing",
            "available_cash": effective_available_cash,
            "reserved_cash": round(reserved_cash, 2),
            "available_after_reserved": round(available_after_reserved, 2),
            "price_used": price
        }

    pending_orders = state.get("pending_orders", {}) if isinstance(state, dict) else {}
    open_positions = len(positions)
    pending_count = len(pending_orders)
    occupied_slots = open_positions + pending_count
    remaining_slots = max(MAX_POSITIONS - occupied_slots, 0)

    if remaining_slots <= 0:
        return 0, {
            "mode": "dynamic_position_sizing_v2_1",
            "blocked": True,
            "reason": "MAX_POSITIONS reached",
            "available_cash": effective_available_cash,
            "reserved_cash": round(reserved_cash, 2),
            "open_positions": open_positions,
            "pending_orders": pending_count,
            "occupied_slots": occupied_slots,
            "max_positions": MAX_POSITIONS,
            "price_used": price
        }

    usable_cash = available_after_reserved * CASH_USAGE_PERCENT
    usable_cash = usable_cash * (1 - SAFETY_BUFFER_PERCENT)

    max_affordable_quantity = int(math.floor(usable_cash / price))

    # V2.5 SAFETY FIX:
    # Portfolio Manager must be the only authority for BUY sizing.
    # TradingView may send a large "quantity" based on strategy equity/backtest logic.
    # For BUY orders we intentionally ignore TradingView quantity completely and
    # calculate the order size only from available cash, reserved cash, cash usage,
    # and safety buffer. SELL/EXIT quantity handling remains unchanged elsewhere.
    raw_quantity = data.get("quantity")
    tradingview_quantity_ignored = raw_quantity not in [None, "", "None"]

    final_quantity = max_affordable_quantity
    has_explicit_quantity = False
    sizing_quantity_mode = "portfolio_manager_dynamic_cash_only"

    trade_value = final_quantity * price

    if final_quantity < 1:
        return 0, {
            "mode": "dynamic_position_sizing_v2_1",
            "blocked": True,
            "reason": "Not enough cash to buy at least 1 share after reserved cash",
            "available_cash": effective_available_cash,
            "reserved_cash": round(reserved_cash, 2),
            "available_after_reserved": round(available_after_reserved, 2),
            "usable_cash": round(usable_cash, 2),
            "price_used": price,
            "original_quantity": original_quantity,
            "has_explicit_quantity": has_explicit_quantity,
            "tradingview_quantity_ignored": tradingview_quantity_ignored,
            "quantity_mode": sizing_quantity_mode,
            "max_affordable_quantity": max_affordable_quantity
        }

    if trade_value < MIN_TRADE_VALUE:
        return 0, {
            "mode": "dynamic_position_sizing_v2_1",
            "blocked": True,
            "reason": "Trade value below MIN_TRADE_VALUE",
            "available_cash": effective_available_cash,
            "reserved_cash": round(reserved_cash, 2),
            "available_after_reserved": round(available_after_reserved, 2),
            "usable_cash": round(usable_cash, 2),
            "price_used": price,
            "quantity": final_quantity,
            "original_quantity": original_quantity,
            "has_explicit_quantity": has_explicit_quantity,
            "tradingview_quantity_ignored": tradingview_quantity_ignored,
            "quantity_mode": sizing_quantity_mode,
            "max_affordable_quantity": max_affordable_quantity,
            "trade_value": round(trade_value, 2),
            "min_trade_value": MIN_TRADE_VALUE
        }

    return final_quantity, {
        "mode": "dynamic_position_sizing_v2_1",
        "available_cash": effective_available_cash,
        "reserved_cash": round(reserved_cash, 2),
        "available_after_reserved": round(available_after_reserved, 2),
        "usable_cash": round(usable_cash, 2),
        "cash_usage_percent": CASH_USAGE_PERCENT,
        "safety_buffer_percent": SAFETY_BUFFER_PERCENT,
        "open_positions": open_positions,
        "pending_orders": pending_count,
        "occupied_slots": occupied_slots,
        "remaining_slots": remaining_slots,
        "price_used": price,
        "original_quantity": original_quantity,
        "has_explicit_quantity": has_explicit_quantity,
        "tradingview_quantity_ignored": tradingview_quantity_ignored,
        "quantity_mode": sizing_quantity_mode,
        "max_affordable_quantity": max_affordable_quantity,
        "final_quantity": final_quantity,
        "estimated_trade_value": round(trade_value, 2)
    }


def get_extras(data):
    extras = data.get("extras") or {}
    return extras if isinstance(extras, dict) else {}


def is_false_value(value):
    if isinstance(value, bool):
        return value is False
    return str(value).lower() in ["false", "0", "no"]


def is_true_value(value):
    if isinstance(value, bool):
        return value is True
    return str(value).lower() in ["true", "1", "yes"]


def validate_buy_against_tradingview_flags(data):
    if not RESPECT_TRADINGVIEW_SAFETY_FLAGS:
        return True, []

    extras = get_extras(data)
    reasons = []

    if BLOCK_BUY_IF_MARKET_UNSAFE and "marketSafe" in extras and is_false_value(extras.get("marketSafe")):
        reasons.append("TradingView marketSafe=false")

    if BLOCK_BUY_IF_STOCK_UNSAFE and "stockSafe" in extras and is_false_value(extras.get("stockSafe")):
        reasons.append("TradingView stockSafe=false")

    if BLOCK_BUY_IF_TIME_NOT_ALLOWED and "timeAllowed" in extras and is_false_value(extras.get("timeAllowed")):
        reasons.append("TradingView timeAllowed=false")

    return len(reasons) == 0, reasons


def is_high_quality_near_close_exception(data):
    """
    يسمح بالدخول قرب نهاية الجلسة إذا كانت الصفقة قوية فعلاً
    وبنفس منطق السكربت: score/alphaScore/timeAllowed/marketSafe/stockSafe.
    """
    if not ALLOW_HIGH_QUALITY_NEAR_CLOSE_ENTRY:
        return False, ["High quality near-close exception disabled"]

    extras = get_extras(data)
    score = safe_float(data.get("score"), 0)
    alpha = safe_float(data.get("alphaScore"), 0)
    rel_volume = safe_float(data.get("relVolume"), 0)

    reasons = []

    if score < HIGH_QUALITY_MIN_SCORE:
        reasons.append(f"score {score} < {HIGH_QUALITY_MIN_SCORE}")

    if alpha < HIGH_QUALITY_MIN_ALPHA_SCORE:
        reasons.append(f"alphaScore {alpha} < {HIGH_QUALITY_MIN_ALPHA_SCORE}")

    if rel_volume < HIGH_QUALITY_MIN_REL_VOLUME:
        reasons.append(f"relVolume {rel_volume} < {HIGH_QUALITY_MIN_REL_VOLUME}")

    if HIGH_QUALITY_REQUIRE_TV_TIME_ALLOWED and not is_true_value(extras.get("timeAllowed")):
        reasons.append("timeAllowed is not true")

    if HIGH_QUALITY_REQUIRE_TV_MARKET_SAFE and not is_true_value(extras.get("marketSafe")):
        reasons.append("marketSafe is not true")

    if HIGH_QUALITY_REQUIRE_TV_STOCK_SAFE and not is_true_value(extras.get("stockSafe")):
        reasons.append("stockSafe is not true")

    return len(reasons) == 0, reasons


def validate_buy_against_session_rules(data):
    if not SESSION_RISK_MANAGER_ENABLED:
        return True, [], {"session_guard": "disabled"}

    info = get_session_info()
    reasons = []
    meta = {"session": info, "near_close_exception": False}

    if info.get("is_weekend"):
        reasons.append("Weekend blocked")

    if BLOCK_BUY_NEAR_SESSION_END and info.get("near_session_end"):
        exception_ok, exception_reasons = is_high_quality_near_close_exception(data)
        if exception_ok:
            meta["near_close_exception"] = True
            meta["near_close_exception_reason"] = "High quality signal allowed near session end"
        else:
            reasons.append(
                f"Near {info['session']} session end: {info['minutes_to_session_end']} minutes left"
            )
            meta["near_close_exception_reject_reasons"] = exception_reasons

    if info["session"] == "overnight" and not ALLOW_OVERNIGHT_HOLD:
        extras = get_extras(data)
        if not is_true_value(extras.get("timeAllowed")):
            reasons.append("Overnight buy blocked unless TradingView explicitly allows it")

    return len(reasons) == 0, reasons, meta


def build_buy_payload(data, ticker, quantity, order_type):
    payload = {
        "ticker": ticker,
        "action": "buy",
        "quantity": quantity,
        "orderType": order_type
    }

    if order_type == "limit" and data.get("limitPrice"):
        payload["limitPrice"] = data.get("limitPrice")

    return payload


def build_sell_payload(ticker, quantity):
    return {
        "ticker": ticker,
        "action": "sell",
        "quantity": quantity,
        "orderType": "market"
    }


def get_exit_reason(data):
    extras = get_extras(data)
    return str(extras.get("reason") or data.get("reason") or "tradingview_exit").lower().strip()


def calculate_exit_quantity(position, incoming_quantity, reason):
    """
    يحول أوامر TradingView إلى كمية بيع مناسبة حسب الكمية الحقيقية في Railway.
    - partial_take_profit: بيع 30%
    - scale_out: بيع 20%
    - dynamic_exit / session_clearance / exit: بيع كامل
    إذا الكمية صغيرة أقل من PARTIAL_SELL_MIN_FULL_EXIT_QTY نحول البيع الجزئي إلى خروج كامل
    لأن الأسهم لا تقبل كسوراً في هذا المسار غالباً.
    """
    local_qty = safe_int((position or {}).get("quantity", 0), 0)
    source_qty = local_qty if local_qty > 0 else safe_int(incoming_quantity, 0)
    if source_qty <= 0:
        return 0, {"reason": reason, "mode": "no_quantity"}

    partial_reasons = ["partial_take_profit", "short_partial_take_profit"]
    scale_reasons = ["scale_out", "short_scale_out"]

    if reason in partial_reasons:
        if source_qty < PARTIAL_SELL_MIN_FULL_EXIT_QTY:
            return source_qty, {"reason": reason, "mode": "partial_converted_to_full_exit_small_qty", "source_qty": source_qty}
        qty = max(1, int(math.floor(source_qty * PARTIAL_TAKE_PROFIT_PERCENT)))
        return min(qty, source_qty), {"reason": reason, "mode": "partial_take_profit", "percent": PARTIAL_TAKE_PROFIT_PERCENT, "source_qty": source_qty}

    if reason in scale_reasons:
        if source_qty < PARTIAL_SELL_MIN_FULL_EXIT_QTY:
            return source_qty, {"reason": reason, "mode": "scale_converted_to_full_exit_small_qty", "source_qty": source_qty}
        qty = max(1, int(math.floor(source_qty * SCALE_OUT_PERCENT)))
        return min(qty, source_qty), {"reason": reason, "mode": "scale_out", "percent": SCALE_OUT_PERCENT, "source_qty": source_qty}

    return source_qty, {"reason": reason, "mode": "full_exit", "source_qty": source_qty}


def update_position_after_sell(positions, ticker, sold_quantity):
    if ticker not in positions:
        return positions, 0

    current_qty = safe_int(positions[ticker].get("quantity", 0), 0)
    remaining_qty = max(current_qty - safe_int(sold_quantity, 0), 0)

    if remaining_qty <= 0:
        positions.pop(ticker, None)
    else:
        positions[ticker]["quantity"] = remaining_qty
        positions[ticker]["updated_at"] = time.time()
        positions[ticker]["last_partial_exit_at"] = time.time()

    return positions, remaining_qty


def should_force_close_now():
    if not SESSION_RISK_MANAGER_ENABLED or not FORCE_CLOSE_BEFORE_SESSION_END:
        return False, "disabled"

    info = get_session_info()

    if not info.get("near_session_end"):
        return False, "not_near_session_end"

    if ALLOW_HOLD_BETWEEN_SESSIONS:
        return False, "holding_between_sessions_allowed"

    if info["session"] == "overnight" and ALLOW_OVERNIGHT_HOLD:
        return False, "overnight_holding_allowed"

    return True, f"near_{info['session']}_end"


def force_close_positions(reason="session_force_close"):
    state = load_state()
    positions = state.get("positions", {})
    pending_orders = state.get("pending_orders", {})
    if not positions:
        return {"ok": True, "closed": 0, "reason": "no_positions"}

    info = get_session_info()
    close_key = f"{info['date']}:{info['session']}:{reason}"
    if state.setdefault("closed_sessions", {}).get(close_key):
        return {"ok": True, "closed": 0, "reason": "already_processed", "close_key": close_key}

    closed = []
    failed = []

    for ticker, pos in list(positions.items()):
        qty = safe_int(pos.get("quantity", 0), 0)
        if qty <= 0:
            continue

        payload = build_sell_payload(ticker, qty)
        ok = send_to_traderspost(payload)

        if ok:
            closed.append({"ticker": ticker, "quantity": qty})
            positions.pop(ticker, None)
        else:
            failed.append({"ticker": ticker, "quantity": qty})

    state["positions"] = positions
    state["closed_sessions"][close_key] = now_ts()
    add_history(state, "FORCE_SESSION_CLOSE", {
        "reason": reason,
        "closed": closed,
        "failed": failed,
        "session": info
    })
    save_state(state)

    return {"ok": len(failed) == 0, "closed": closed, "failed": failed, "close_key": close_key}


def session_risk_worker():
    print("Session Risk Manager started", flush=True)
    while True:
        try:
            should_close, reason = should_force_close_now()
            if should_close:
                result = force_close_positions(reason=reason)
                print("Session Risk Manager:", result, flush=True)
        except Exception as e:
            print("Session Risk Manager error:", e, flush=True)
        time.sleep(SESSION_CHECK_INTERVAL_SECONDS)


def get_price_update_value(data):
    for key in ["current_price", "currentPrice", "lastPrice", "price", "close", "signalPrice", "limitPrice"]:
        price = safe_float(data.get(key), 0)
        if price > 0:
            return price
    return 0


def evaluate_auto_exit_for_ticker(ticker, current_price, reason_source="price_update"):
    if not AUTO_EXIT_MANAGER_ENABLED:
        return {"ok": True, "decision": "auto_exit_disabled", "ticker": ticker}

    state = load_state()
    positions = state.get("positions", {})
    position = positions.get(ticker)

    if not position:
        return {"ok": True, "decision": "no_local_position", "ticker": ticker, "current_price": current_price}

    qty = safe_int(position.get("quantity", 0), 0)
    if qty <= 0:
        return {"ok": True, "decision": "invalid_local_quantity", "ticker": ticker, "quantity": qty}

    entry_price = safe_float(position.get("entry_price") or position.get("entry_signal_price"), 0)
    stop_loss = safe_float(position.get("stop_loss_price"), 0)
    take_profit = safe_float(position.get("take_profit_price"), 0)

    # Fallback optional percentage exits if no explicit SL/TP exists.
    default_sl, default_tp = derive_default_exit_prices(entry_price)
    if stop_loss <= 0:
        stop_loss = default_sl
    if take_profit <= 0:
        take_profit = default_tp

    trigger_reason = None
    exit_mode = None

    if stop_loss > 0 and current_price <= stop_loss:
        trigger_reason = "stop_loss"
        exit_mode = "full_exit" if AUTO_EXIT_FULL_ON_STOP_LOSS else "partial_take_profit"
    elif take_profit > 0 and current_price >= take_profit:
        trigger_reason = "take_profit"
        exit_mode = "full_exit" if AUTO_EXIT_FULL_ON_TAKE_PROFIT else "partial_take_profit"

    if not trigger_reason:
        return {
            "ok": True,
            "decision": "hold",
            "ticker": ticker,
            "current_price": current_price,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss,
            "take_profit_price": take_profit,
            "quantity": qty
        }

    if exit_mode == "full_exit":
        exit_quantity = qty
        exit_calc = {"reason": trigger_reason, "mode": "auto_full_exit", "source_qty": qty}
    else:
        exit_quantity, exit_calc = calculate_exit_quantity(position, qty, "partial_take_profit")
        exit_calc["auto_trigger_reason"] = trigger_reason

    if exit_quantity <= 0:
        return {"ok": True, "decision": "auto_exit_zero_quantity", "ticker": ticker, "exit_calc": exit_calc}

    payload = build_sell_payload(ticker, exit_quantity)
    ok = send_to_traderspost(payload)

    if ok:
        positions, remaining_qty = update_position_after_sell(positions, ticker, exit_quantity)
        state["positions"] = positions
        add_history(state, "AUTO_EXIT_SENT", {
            "ticker": ticker,
            "quantity": exit_quantity,
            "remaining_quantity": remaining_qty,
            "reason": trigger_reason,
            "source": reason_source,
            "current_price": current_price,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss,
            "take_profit_price": take_profit,
            "exit_calc": exit_calc
        })
        save_state(state)
        print(f"AUTO EXIT SENT: {ticker} reason={trigger_reason} sold={exit_quantity} price={current_price}", flush=True)

    return {
        "ok": ok,
        "decision": "auto_exit_sent" if ok else "auto_exit_failed",
        "ticker": ticker,
        "reason": trigger_reason,
        "quantity": exit_quantity,
        "current_price": current_price,
        "entry_price": entry_price,
        "stop_loss_price": stop_loss,
        "take_profit_price": take_profit,
        "exit_calc": exit_calc
    }


def start_scanner_background():
    try:
        from scanner import run_scanner
        t = threading.Thread(target=run_scanner, daemon=True)
        t.start()
        print("Scanner background thread started", flush=True)
    except Exception as e:
        print("Scanner failed to start:", e, flush=True)


def start_session_risk_background():
    if SESSION_RISK_MANAGER_ENABLED:
        t = threading.Thread(target=session_risk_worker, daemon=True)
        t.start()
    else:
        print("Session Risk Manager disabled", flush=True)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "service": "Portfolio Manager Running ✅",
        "version": "V2.7 Pending Until Fill Confirmed",
        "session": get_session_info(),
        "max_positions": MAX_POSITIONS,
        "dynamic_position_sizing": USE_DYNAMIC_POSITION_SIZING,
        "session_risk_manager": SESSION_RISK_MANAGER_ENABLED,
        "available_cash_setting": AVAILABLE_CASH,
        "effective_available_cash": get_effective_available_cash(),
        "cash_usage_percent": CASH_USAGE_PERCENT,
        "safety_buffer_percent": SAFETY_BUFFER_PERCENT
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}

    print("RAW WEBHOOK DATA:", json.dumps(data, ensure_ascii=False), flush=True)

    ticker = str(data.get("ticker", "")).upper()
    action = str(data.get("action", "")).lower()
    score = safe_float(data.get("score", data.get("alphaScore", 0)), 0)
    quantity = safe_int(data.get("quantity", 1), 1)
    order_type = str(data.get("orderType", "market")).lower()
    signal_price = data.get("signalPrice") or data.get("price") or data.get("close")

    if not data:
        return jsonify({
            "ok": True,
            "decision": "ignored_empty_webhook",
            "reason": "Empty webhook payload received"
        }), 200

    if not ticker or action not in ["buy", "sell", "exit", "price_update", "monitor"]:
        return jsonify({"ok": False, "error": "Invalid signal", "data": data}), 400

    if action == "buy" and is_test_ticker(ticker) and not ALLOW_TEST_TICKERS:
        return jsonify({
            "ok": True,
            "decision": "blocked_test_ticker",
            "ticker": ticker,
            "reason": "Test tickers are blocked in production. Use a real ticker for live tests or set ALLOW_TEST_TICKERS=true."
        })

    state = load_state()
    positions = state.setdefault("positions", {})
    pending_orders = state.setdefault("pending_orders", {})

    # PRICE UPDATE / MONITOR:
    # TradingView can send current price updates so Portfolio Manager can trigger
    # auto stop-loss or take-profit exits without a separate sell alert.
    if action in ["price_update", "monitor"]:
        current_price = get_price_update_value(data)
        if current_price <= 0:
            return jsonify({"ok": False, "error": "Missing valid current price", "data": data}), 400
        result = evaluate_auto_exit_for_ticker(ticker, current_price, reason_source=action)
        return jsonify(result)

    # SELL / EXIT:
    # إذا المركز محفوظ نخرج/نخفف حسب كمية Railway الحقيقية.
    # إذا المركز غير محفوظ بسبب restart/reset نسمح بخروج orphan باستخدام كمية TradingView
    # حتى لا تبقى مراكز معلقة في IBKR بلا إدارة.
    if action in ["sell", "exit"]:
        reason = get_exit_reason(data)
        local_position = positions.get(ticker)

        if not local_position and not ALLOW_ORPHAN_EXIT_WITH_SIGNAL_QUANTITY:
            return jsonify({
                "ok": True,
                "decision": "ignored_exit_no_local_position",
                "ticker": ticker,
                "reason": "No local position found in portfolio_state"
            })

        exit_quantity, exit_calc = calculate_exit_quantity(local_position, quantity, reason)

        if exit_quantity <= 0:
            return jsonify({
                "ok": True,
                "decision": "ignored_exit_zero_quantity",
                "ticker": ticker,
                "reason": reason,
                "exit_calc": exit_calc
            })

        payload = build_sell_payload(ticker, exit_quantity)
        ok = send_to_traderspost(payload)

        if ok:
            if local_position:
                positions, remaining_qty = update_position_after_sell(positions, ticker, exit_quantity)
            else:
                remaining_qty = "unknown_orphan_position"

            add_history(state, "EXIT_SENT", {
                "ticker": ticker,
                "quantity": exit_quantity,
                "remaining_quantity": remaining_qty,
                "reason": reason,
                "source_action": action,
                "orphan_exit": not bool(local_position),
                "exit_calc": exit_calc
            })
            state["positions"] = positions
            print(f"POSITION REMOVED/UPDATED: {ticker} sold={exit_quantity}", flush=True)
            save_state(state)

        return jsonify({
            "ok": ok,
            "decision": "exit_sent" if local_position else "orphan_exit_sent_with_signal_quantity",
            "ticker": ticker,
            "quantity": exit_quantity,
            "reason": reason,
            "exit_calc": exit_calc
        })

    # BUY final guard: يحترم سكربت TradingView.
    flags_ok, flag_reasons = validate_buy_against_tradingview_flags(data)
    if not flags_ok:
        add_history(state, "BUY_BLOCKED_BY_TV_FLAGS", {"ticker": ticker, "reasons": flag_reasons})
        save_state(state)
        return jsonify({
            "ok": True,
            "decision": "blocked_by_tradingview_safety_flags",
            "ticker": ticker,
            "score": score,
            "reasons": flag_reasons
        })

    session_ok, session_reasons, session_meta = validate_buy_against_session_rules(data)
    if not session_ok:
        add_history(state, "BUY_BLOCKED_BY_SESSION_RULES", {"ticker": ticker, "reasons": session_reasons, "meta": session_meta})
        save_state(state)
        return jsonify({
            "ok": True,
            "decision": "blocked_by_session_rules",
            "ticker": ticker,
            "score": score,
            "reasons": session_reasons,
            "session_meta": session_meta
        })

    if ticker in pending_orders:
        old_score = safe_float(pending_orders[ticker].get("score", 0), 0)
        if score <= old_score:
            return jsonify({
                "ok": True,
                "decision": "ignored_duplicate_pending_order_lower_score",
                "ticker": ticker,
                "old_score": old_score,
                "new_score": score
            })

        pending_orders[ticker]["score"] = score
        pending_orders[ticker]["entry_signal_price"] = signal_price
        pending_orders[ticker]["updated_at"] = time.time()
        pending_orders[ticker]["last_signal"] = data
        state["pending_orders"] = pending_orders
        add_history(state, "UPDATED_PENDING_ORDER_SCORE", {"ticker": ticker, "score": score})
        save_state(state)

        return jsonify({
            "ok": True,
            "decision": "updated_pending_order_score",
            "ticker": ticker,
            "score": score
        })

    if ticker in positions:
        old_score = safe_float(positions[ticker].get("score", 0), 0)

        if score <= old_score:
            return jsonify({
                "ok": True,
                "decision": "ignored_duplicate_lower_score",
                "ticker": ticker,
                "old_score": old_score,
                "new_score": score
            })

        positions[ticker]["score"] = score
        positions[ticker]["entry_signal_price"] = signal_price
        positions[ticker]["updated_at"] = time.time()
        positions[ticker]["last_signal"] = data
        state["positions"] = positions
        add_history(state, "UPDATED_EXISTING_POSITION_SCORE", {"ticker": ticker, "score": score})
        save_state(state)

        return jsonify({
            "ok": True,
            "decision": "updated_existing_position_score",
            "ticker": ticker,
            "score": score
        })

    final_quantity, sizing_info = calculate_dynamic_quantity(data, positions, state)

    if final_quantity < 1:
        print("BUY BLOCKED BY POSITION SIZING:", json.dumps(sizing_info, ensure_ascii=False), flush=True)
        add_history(state, "BUY_BLOCKED_BY_POSITION_SIZING", {"ticker": ticker, "sizing": sizing_info})
        save_state(state)
        return jsonify({
            "ok": True,
            "decision": "blocked_by_position_sizing",
            "ticker": ticker,
            "score": score,
            "sizing": sizing_info
        })

    fee_ok, fee_info = validate_expected_profit_after_fees(data, final_quantity)
    if not fee_ok:
        add_history(state, "BUY_BLOCKED_BY_IBKR_FEE_GUARD", {"ticker": ticker, "fee_info": fee_info})
        save_state(state)
        return jsonify({
            "ok": True,
            "decision": "blocked_by_ibkr_fee_guard",
            "ticker": ticker,
            "score": score,
            "fee_guard": fee_info,
            "sizing": sizing_info
        })

    occupied_slots = len(positions) + len(pending_orders)
    if occupied_slots < MAX_POSITIONS:
        payload = build_buy_payload(data, ticker, final_quantity, order_type)
        ok = send_to_traderspost(payload)

        if ok:
            order_record = {
                "score": score,
                "alpha_score": safe_float(data.get("alphaScore"), 0),
                "quantity": final_quantity,
                "entry_signal_price": signal_price,
                "entry_price": get_trade_price(data),
                "stop_loss_price": get_stop_loss_price(data),
                "take_profit_price": get_take_profit_price(data),
                "order_type": order_type,
                "limit_price": data.get("limitPrice"),
                "session": get_session_info()["session"],
                "sizing": sizing_info,
                "fee_guard": fee_info,
                "session_meta": session_meta,
                "created_at": time.time(),
                "last_signal": data
            }

            # Production safety:
            # Any submitted BUY order (market or limit) is NOT treated as a filled position.
            # IBKR may keep market orders in PreSubmitted / Submitted status, especially
            # outside regular trading hours. Keep all BUY orders in pending_orders until
            # the fill is confirmed manually via /mark_filled or a future broker callback.
            pending_orders[ticker] = order_record
            state["pending_orders"] = pending_orders

            if order_type == "limit":
                history_event = "BUY_LIMIT_ORDER_SUBMITTED_PENDING"
                decision = "buy_limit_order_submitted_pending"
                print(f"PENDING LIMIT ORDER SAVED: {ticker} qty={final_quantity}", flush=True)
            else:
                history_event = "BUY_MARKET_ORDER_SUBMITTED_PENDING"
                decision = "buy_market_order_submitted_pending"
                print(f"PENDING MARKET ORDER SAVED: {ticker} qty={final_quantity}", flush=True)

            add_history(state, history_event, {
                "ticker": ticker,
                "quantity": final_quantity,
                "score": score,
                "sizing": sizing_info,
                "fee_guard": fee_info,
                "session_meta": session_meta,
                "order_type": order_type
            })

            save_state(state)
        else:
            decision = "buy_failed"

        return jsonify({
            "ok": ok,
            "decision": decision,
            "ticker": ticker,
            "score": score,
            "signal_price": signal_price,
            "quantity": final_quantity,
            "sizing": sizing_info,
            "fee_guard": fee_info,
            "session_meta": session_meta,
            "open_positions": len(positions),
            "pending_orders": len(pending_orders)
        })

    if not positions:
        return jsonify({
            "ok": True,
            "decision": "ignored_max_positions_reached_due_to_pending_orders",
            "ticker": ticker,
            "score": score,
            "pending_orders": len(pending_orders),
            "max_positions": MAX_POSITIONS
        })

    weakest_ticker, weakest_data = min(
        positions.items(),
        key=lambda item: safe_float(item[1].get("score", 0), 0)
    )
    weakest_score = safe_float(weakest_data.get("score", 0), 0)

    if score > weakest_score:
        weakest_quantity = safe_int(weakest_data.get("quantity", 1), 1)

        sell_payload = build_sell_payload(weakest_ticker, weakest_quantity)
        sell_ok = send_to_traderspost(sell_payload)

        if not sell_ok:
            return jsonify({
                "ok": False,
                "decision": "swap_failed_sell_rejected",
                "sold": weakest_ticker,
                "sold_score": weakest_score,
                "bought": ticker,
                "bought_score": score
            })

        positions.pop(weakest_ticker, None)
        state["positions"] = positions
        save_state(state)

        final_quantity, sizing_info = calculate_dynamic_quantity(data, positions, state)
        if final_quantity < 1:
            return jsonify({
                "ok": True,
                "decision": "swap_sell_done_but_buy_blocked_by_sizing",
                "sold": weakest_ticker,
                "bought": ticker,
                "sizing": sizing_info
            })

        fee_ok, fee_info = validate_expected_profit_after_fees(data, final_quantity)
        if not fee_ok:
            return jsonify({
                "ok": True,
                "decision": "swap_sell_done_but_buy_blocked_by_ibkr_fee_guard",
                "sold": weakest_ticker,
                "bought": ticker,
                "fee_guard": fee_info,
                "sizing": sizing_info
            })

        buy_payload = build_buy_payload(data, ticker, final_quantity, order_type)
        buy_ok = send_to_traderspost(buy_payload)

        if buy_ok:
            # Production safety:
            # After a swap sell, the new BUY is still only a submitted order.
            # Keep it pending until the fill is confirmed via /mark_filled.
            pending_orders = state.setdefault("pending_orders", {})
            pending_orders[ticker] = {
                "score": score,
                "alpha_score": safe_float(data.get("alphaScore"), 0),
                "quantity": final_quantity,
                "entry_signal_price": signal_price,
                "entry_price": get_trade_price(data),
                "stop_loss_price": get_stop_loss_price(data),
                "take_profit_price": get_take_profit_price(data),
                "order_type": order_type,
                "limit_price": data.get("limitPrice"),
                "session": get_session_info()["session"],
                "sizing": sizing_info,
                "fee_guard": fee_info,
                "session_meta": session_meta,
                "created_at": time.time(),
                "last_signal": data
            }
            state["pending_orders"] = pending_orders
            add_history(state, "SWAP_SELL_DONE_BUY_SUBMITTED_PENDING", {
                "sold": weakest_ticker,
                "sold_score": weakest_score,
                "sold_quantity": weakest_quantity,
                "bought": ticker,
                "bought_score": score,
                "bought_quantity": final_quantity,
                "fee_guard": fee_info,
                "order_type": order_type
            })
            save_state(state)

        return jsonify({
            "ok": sell_ok and buy_ok,
            "decision": "swap",
            "sold": weakest_ticker,
            "sold_score": weakest_score,
            "sold_quantity": weakest_quantity,
            "bought": ticker,
            "bought_score": score,
            "bought_quantity": final_quantity,
            "signal_price": signal_price,
            "sizing": sizing_info,
            "fee_guard": fee_info
        })

    return jsonify({
        "ok": True,
        "decision": "ignored_not_better_than_top_positions",
        "ticker": ticker,
        "score": score,
        "weakest_score": weakest_score
    })


@app.route("/positions", methods=["GET"])
def get_positions():
    return jsonify(load_state())


@app.route("/reset", methods=["POST"])
def reset_positions():
    save_state({"positions": {}, "pending_orders": {}, "closed_sessions": {}, "history": []})
    return jsonify({"ok": True, "message": "positions and pending orders reset"})


@app.route("/hard_reset", methods=["POST", "GET"])
def hard_reset_positions():
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "state_file": STATE_FILE}), 500

    save_state({"positions": {}, "pending_orders": {}, "closed_sessions": {}, "history": []})
    return jsonify({"ok": True, "message": "state file deleted and recreated", "state_file": STATE_FILE})


@app.route("/force_close", methods=["POST", "GET"])
def force_close_route():
    result = force_close_positions(reason="manual_force_close")
    return jsonify(result)



@app.route("/history", methods=["GET"])
def history():
    state = load_state()
    return jsonify({
        "history": state.get("history", []),
        "positions": state.get("positions", {}),
        "pending_orders": state.get("pending_orders", {}),
        "closed_sessions": state.get("closed_sessions", {}),
        "state_file": STATE_FILE,
        "state_file_exists": os.path.exists(STATE_FILE),
    })


@app.route("/manual_position", methods=["POST"])
def manual_position():
    """
    إضافة مركز موجود فعلياً في IBKR إلى ذاكرة Railway.
    مثال PowerShell:
    Invoke-RestMethod -Uri "https://.../manual_position" -Method POST -ContentType "application/json" -Body '{"ticker":"STZ","quantity":2,"entry_price":141.89,"score":90}'
    """
    data = request.get_json(force=True, silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    quantity = safe_int(data.get("quantity"), 0)

    if not ticker or quantity <= 0:
        return jsonify({"ok": False, "error": "ticker and positive quantity are required", "data": data}), 400

    state = load_state()
    positions = state.setdefault("positions", {})

    entry_price = safe_float(data.get("entry_price") or data.get("entryPrice") or data.get("price"), 0)
    score = safe_float(data.get("score"), 0)

    positions[ticker] = {
        "score": score,
        "alpha_score": safe_float(data.get("alphaScore"), 0),
        "quantity": quantity,
        "entry_signal_price": entry_price,
        "entry_price": entry_price,
        "stop_loss_price": safe_float(data.get("stopLossPrice") or data.get("stop_loss_price"), 0),
        "take_profit_price": safe_float(data.get("takeProfitPrice") or data.get("take_profit_price"), 0),
        "order_type": "manual_sync",
        "limit_price": entry_price,
        "session": get_session_info()["session"],
        "sizing": {"mode": "manual_position_sync"},
        "fee_guard": {"mode": "manual_position_sync"},
        "session_meta": {"mode": "manual_position_sync"},
        "created_at": time.time(),
        "manual_sync": True,
        "last_signal": data
    }

    add_history(state, "MANUAL_POSITION_SYNC", {"ticker": ticker, "quantity": quantity, "entry_price": entry_price})
    save_state(state)

    return jsonify({"ok": True, "decision": "manual_position_synced", "ticker": ticker, "quantity": quantity, "positions": positions})


@app.route("/remove_position", methods=["POST"])
def remove_position():
    data = request.get_json(force=True, silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()

    if not ticker:
        return jsonify({"ok": False, "error": "ticker is required"}), 400

    state = load_state()
    existed_position = ticker in state.get("positions", {})
    existed_pending = ticker in state.get("pending_orders", {})
    state.setdefault("positions", {}).pop(ticker, None)
    state.setdefault("pending_orders", {}).pop(ticker, None)
    add_history(state, "MANUAL_POSITION_REMOVED", {
        "ticker": ticker,
        "existed_position": existed_position,
        "existed_pending_order": existed_pending
    })
    save_state(state)

    return jsonify({
        "ok": True,
        "ticker": ticker,
        "removed_position": existed_position,
        "removed_pending_order": existed_pending
    })


@app.route("/force_close/<ticker>", methods=["POST", "GET"])
def force_close_one_route(ticker):
    ticker = str(ticker).upper().strip()
    state = load_state()
    positions = state.get("positions", {})

    if ticker not in positions:
        return jsonify({
            "ok": False,
            "error": "ticker not found in local positions",
            "ticker": ticker,
            "hint": "Use /manual_position first if this position exists in IBKR but not in Railway."
        }), 404

    qty = safe_int(positions[ticker].get("quantity", 0), 0)
    if qty <= 0:
        return jsonify({"ok": False, "error": "invalid local quantity", "ticker": ticker}), 400

    payload = build_sell_payload(ticker, qty)
    ok = send_to_traderspost(payload)

    if ok:
        positions.pop(ticker, None)
        state["positions"] = positions
        add_history(state, "MANUAL_FORCE_CLOSE_ONE", {"ticker": ticker, "quantity": qty})
        save_state(state)

    return jsonify({"ok": ok, "ticker": ticker, "quantity": qty, "decision": "force_close_one_sent"})
@app.route("/mark_filled", methods=["POST"])
def mark_filled():
    """
    Moves a submitted pending order into positions after you verify it filled in IBKR/TradersPost.

    PowerShell:
    Invoke-RestMethod -Uri "https://.../mark_filled" -Method POST -ContentType "application/json" -Body '{"ticker":"F"}'
    """
    data = request.get_json(force=True, silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()

    if not ticker:
        return jsonify({"ok": False, "error": "ticker is required"}), 400

    state = load_state()
    pending_orders = state.setdefault("pending_orders", {})
    positions = state.setdefault("positions", {})

    if ticker not in pending_orders:
        return jsonify({
            "ok": False,
            "error": "ticker not found in pending_orders",
            "ticker": ticker,
            "pending_orders": list(pending_orders.keys())
        }), 404

    order = pending_orders.pop(ticker)
    if data.get("quantity") not in [None, "", "None"]:
        order["quantity"] = safe_int(data.get("quantity"), order.get("quantity", 1))
    if data.get("fill_price") not in [None, "", "None"]:
        order["entry_price"] = safe_float(data.get("fill_price"), order.get("entry_price", 0))

    order["filled_at"] = time.time()
    order["order_status"] = "filled_manual_confirm"
    positions[ticker] = order

    state["pending_orders"] = pending_orders
    state["positions"] = positions
    add_history(state, "PENDING_ORDER_MARKED_FILLED", {
        "ticker": ticker,
        "quantity": order.get("quantity"),
        "entry_price": order.get("entry_price")
    })
    save_state(state)

    return jsonify({
        "ok": True,
        "decision": "pending_order_marked_filled",
        "ticker": ticker,
        "positions": positions,
        "pending_orders": pending_orders
    })


@app.route("/update_cash", methods=["POST"])
def update_cash():
    """
    Updates the cash used by Portfolio Manager for dynamic sizing without Railway redeploy.

    PowerShell example:
    $body = @{ available_cash = 439.11; source = "traderspost_manual" } | ConvertTo-Json
    Invoke-RestMethod `
     -Uri "https://.../update_cash" `
     -Method Post `
     -ContentType "application/json" `
     -Body $body
    """
    data = request.get_json(force=True, silent=True) or {}
    new_cash = safe_float(data.get("available_cash", data.get("cash")), -1)

    if new_cash < 0:
        return jsonify({
            "ok": False,
            "error": "available_cash must be provided and must be >= 0",
            "data": data
        }), 400

    state = load_state()
    state["cash"] = {
        "available_cash": round(new_cash, 2),
        "source": str(data.get("source", "manual_update")),
        "updated_at": now_ts(),
        "updated_at_ny": get_session_info().get("now_ny"),
        "previous_env_available_cash": AVAILABLE_CASH
    }

    positions = state.get("positions", {})
    available_after_reserved, reserved_cash = get_available_cash_after_reserved(positions, state)

    add_history(state, "CASH_UPDATED", {
        "available_cash": round(new_cash, 2),
        "reserved_cash": round(reserved_cash, 2),
        "available_after_reserved": round(available_after_reserved, 2),
        "source": state["cash"]["source"]
    })
    save_state(state)

    return jsonify({
        "ok": True,
        "decision": "cash_updated",
        "available_cash": round(new_cash, 2),
        "reserved_cash": round(reserved_cash, 2),
        "available_after_reserved": round(available_after_reserved, 2),
        "cash_usage_percent": CASH_USAGE_PERCENT,
        "safety_buffer_percent": SAFETY_BUFFER_PERCENT,
        "usable_cash_after_buffer": round(available_after_reserved * CASH_USAGE_PERCENT * (1 - SAFETY_BUFFER_PERCENT), 2),
        "positions_count": len(positions),
        "cash_source": state["cash"]["source"]
    })


@app.route("/cash_status", methods=["GET"])
def cash_status():
    state = load_state()
    positions = state.get("positions", {})
    effective_cash = get_effective_available_cash(state)
    available_after_reserved, reserved_cash = get_available_cash_after_reserved(positions, state)

    return jsonify({
        "ok": True,
        "available_cash": round(effective_cash, 2),
        "env_available_cash": AVAILABLE_CASH,
        "cash_source": state.get("cash", {}).get("source", "railway_env") if isinstance(state.get("cash", {}), dict) else "railway_env",
        "cash_updated_at": state.get("cash", {}).get("updated_at") if isinstance(state.get("cash", {}), dict) else None,
        "cash_updated_at_ny": state.get("cash", {}).get("updated_at_ny") if isinstance(state.get("cash", {}), dict) else None,
        "reserved_cash": round(reserved_cash, 2),
        "available_after_reserved": round(available_after_reserved, 2),
        "cash_usage_percent": CASH_USAGE_PERCENT,
        "safety_buffer_percent": SAFETY_BUFFER_PERCENT,
        "usable_cash_after_buffer": round(available_after_reserved * CASH_USAGE_PERCENT * (1 - SAFETY_BUFFER_PERCENT), 2),
        "open_positions": len(positions),
        "pending_orders_count": len(state.get("pending_orders", {})),
        "positions": positions,
        "pending_orders": state.get("pending_orders", {})
    })


@app.route("/price_update", methods=["POST"])
def price_update():
    """
    Manual/test endpoint for Auto Stop Loss / Take Profit.

    PowerShell example:
    $body = @{ ticker = "PCG"; current_price = 18.50 } | ConvertTo-Json
    Invoke-RestMethod `
     -Uri "https://.../price_update" `
     -Method Post `
     -ContentType "application/json" `
     -Body $body
    """
    data = request.get_json(force=True, silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    current_price = get_price_update_value(data)

    if not ticker or current_price <= 0:
        return jsonify({"ok": False, "error": "ticker and valid current_price are required", "data": data}), 400

    result = evaluate_auto_exit_for_ticker(ticker, current_price, reason_source="price_update_endpoint")
    return jsonify(result)


@app.route("/settings", methods=["GET"])
def settings():
    state = load_state()
    positions = state.get("positions", {})
    available_after_reserved, reserved_cash = get_available_cash_after_reserved(positions, state)

    return jsonify({
        "VERSION": "V2.7 Pending Until Fill Confirmed",
        "MAX_POSITIONS": MAX_POSITIONS,
        "AVAILABLE_CASH": get_effective_available_cash(state),
        "ENV_AVAILABLE_CASH": AVAILABLE_CASH,
        "CASH_SOURCE": state.get("cash", {}).get("source", "railway_env") if isinstance(state.get("cash", {}), dict) else "railway_env",
        "CASH_UPDATED_AT": state.get("cash", {}).get("updated_at") if isinstance(state.get("cash", {}), dict) else None,
        "RESERVED_CASH": round(reserved_cash, 2),
        "AVAILABLE_AFTER_RESERVED": round(available_after_reserved, 2),
        "PENDING_ORDERS_COUNT": len(state.get("pending_orders", {})),
        "OPEN_POSITIONS_COUNT": len(positions),
        "CASH_USAGE_PERCENT": CASH_USAGE_PERCENT,
        "SAFETY_BUFFER_PERCENT": SAFETY_BUFFER_PERCENT,
        "MIN_TRADE_VALUE": MIN_TRADE_VALUE,
        "USE_DYNAMIC_POSITION_SIZING": USE_DYNAMIC_POSITION_SIZING,
        "RESPECT_TRADINGVIEW_SAFETY_FLAGS": RESPECT_TRADINGVIEW_SAFETY_FLAGS,
        "SESSION_RISK_MANAGER_ENABLED": SESSION_RISK_MANAGER_ENABLED,
        "BLOCK_BUY_NEAR_SESSION_END": BLOCK_BUY_NEAR_SESSION_END,
        "FORCE_CLOSE_BEFORE_SESSION_END": FORCE_CLOSE_BEFORE_SESSION_END,
        "SESSION_CLOSE_BUFFER_MINUTES": SESSION_CLOSE_BUFFER_MINUTES,
        "ALLOW_HOLD_BETWEEN_SESSIONS": ALLOW_HOLD_BETWEEN_SESSIONS,
        "ALLOW_OVERNIGHT_HOLD": ALLOW_OVERNIGHT_HOLD,
        "ALLOW_HIGH_QUALITY_NEAR_CLOSE_ENTRY": ALLOW_HIGH_QUALITY_NEAR_CLOSE_ENTRY,
        "HIGH_QUALITY_MIN_SCORE": HIGH_QUALITY_MIN_SCORE,
        "HIGH_QUALITY_MIN_ALPHA_SCORE": HIGH_QUALITY_MIN_ALPHA_SCORE,
        "HIGH_QUALITY_MIN_REL_VOLUME": HIGH_QUALITY_MIN_REL_VOLUME,
        "ENFORCE_IBKR_FEE_GUARD": ENFORCE_IBKR_FEE_GUARD,
        "IBKR_FEE_PER_SHARE": IBKR_FEE_PER_SHARE,
        "IBKR_MIN_FEE_PER_ORDER": IBKR_MIN_FEE_PER_ORDER,
        "MIN_PROFIT_FEE_MULTIPLIER": MIN_PROFIT_FEE_MULTIPLIER,
        "ALLOW_ORPHAN_EXIT_WITH_SIGNAL_QUANTITY": ALLOW_ORPHAN_EXIT_WITH_SIGNAL_QUANTITY,
        "PARTIAL_SELL_MIN_FULL_EXIT_QTY": PARTIAL_SELL_MIN_FULL_EXIT_QTY,
        "PARTIAL_TAKE_PROFIT_PERCENT": PARTIAL_TAKE_PROFIT_PERCENT,
        "SCALE_OUT_PERCENT": SCALE_OUT_PERCENT,
        "ALLOW_TEST_TICKERS": ALLOW_TEST_TICKERS,
        "TEST_TICKER_PREFIXES": list(TEST_TICKER_PREFIXES),
        "AUTO_EXIT_MANAGER_ENABLED": AUTO_EXIT_MANAGER_ENABLED,
        "AUTO_EXIT_FULL_ON_STOP_LOSS": AUTO_EXIT_FULL_ON_STOP_LOSS,
        "AUTO_EXIT_FULL_ON_TAKE_PROFIT": AUTO_EXIT_FULL_ON_TAKE_PROFIT,
        "DEFAULT_STOP_LOSS_PERCENT": DEFAULT_STOP_LOSS_PERCENT,
        "DEFAULT_TAKE_PROFIT_PERCENT": DEFAULT_TAKE_PROFIT_PERCENT,
        "STATE_FILE": STATE_FILE,
        "STATE_FILE_EXISTS": os.path.exists(STATE_FILE),
        "WORKING_DIRECTORY": os.getcwd(),
        "SESSION": get_session_info(),
    })


start_scanner_background()
start_session_risk_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))