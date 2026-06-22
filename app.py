from flask import Flask, request, jsonify
import os
import json
import time
import math
import requests
import threading

app = Flask(__name__)

TRADERSPOST_WEBHOOK = os.getenv("TRADERSPOST_WEBHOOK", "")
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
STATE_FILE = "portfolio_state.json"

# =========================================================
# DYNAMIC POSITION SIZING V1
# =========================================================
# ضع الكاش المتاح في Railway كمتغير:
# AVAILABLE_CASH=308.61
#
# احتياط أمان حتى لا نستخدم كامل الرصيد:
# CASH_USAGE_PERCENT=0.90
#
# أقل قيمة صفقة مسموحة:
# MIN_TRADE_VALUE=20
#
# السماح بتجاوز الكمية القادمة من TradingView:
# USE_DYNAMIC_POSITION_SIZING=true
# =========================================================

AVAILABLE_CASH = float(os.getenv("AVAILABLE_CASH", "0") or 0)
CASH_USAGE_PERCENT = float(os.getenv("CASH_USAGE_PERCENT", "0.90") or 0.90)
MIN_TRADE_VALUE = float(os.getenv("MIN_TRADE_VALUE", "20") or 20)
USE_DYNAMIC_POSITION_SIZING = os.getenv("USE_DYNAMIC_POSITION_SIZING", "true").lower() == "true"

# نترك جزء من السيولة احتياط للأوامر المفتوحة والعمولات
SAFETY_BUFFER_PERCENT = float(os.getenv("SAFETY_BUFFER_PERCENT", "0.05") or 0.05)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"positions": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_to_traderspost(payload):
    if not TRADERSPOST_WEBHOOK:
        print("Missing TRADERSPOST_WEBHOOK")
        return False

    r = requests.post(TRADERSPOST_WEBHOOK, json=payload, timeout=15)
    print("TradersPost:", r.status_code, r.text[:500])
    return r.status_code in [200, 201, 202]


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=1):
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def get_trade_price(data):
    """
    السعر المستخدم لحساب الكمية.
    للأوامر Limit نستخدم limitPrice لأنه السعر الذي سيُحجز عليه الكاش.
    للأوامر Market نستخدم signalPrice/current price.
    """
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


def get_available_cash_from_settings():
    """
    V1: الكاش يقرأ من متغير Railway AVAILABLE_CASH.
    لاحقاً V2 نربطه مباشرة بواجهة IBKR أو TradersPost إن توفرت API مناسبة.
    """
    return AVAILABLE_CASH


def calculate_dynamic_quantity(data, positions):
    """
    يحسب الكمية المناسبة حسب السيولة المتاحة وعدد المراكز.
    الهدف: منع رفض IBKR بسبب insufficient cash.
    """
    original_quantity = safe_int(data.get("quantity", 1), 1)
    price = get_trade_price(data)

    if not USE_DYNAMIC_POSITION_SIZING:
        return original_quantity, {
            "mode": "fixed_quantity_from_tradingview",
            "price_used": price,
            "quantity": original_quantity
        }

    available_cash = get_available_cash_from_settings()

    if available_cash <= 0:
        # حماية: إذا لم تضبط AVAILABLE_CASH لا نغامر بكمية TradingView
        return 0, {
            "mode": "dynamic_position_sizing",
            "blocked": True,
            "reason": "AVAILABLE_CASH is not configured or <= 0",
            "available_cash": available_cash,
            "price_used": price
        }

    if price <= 0:
        return 0, {
            "mode": "dynamic_position_sizing",
            "blocked": True,
            "reason": "Missing valid price for sizing",
            "available_cash": available_cash,
            "price_used": price
        }

    open_positions = len(positions)
    remaining_slots = max(MAX_POSITIONS - open_positions, 1)

    # توزيع ذكي بسيط:
    # إذا الحساب صغير، لا نقسمه على 10 بشكل مبالغ فيه.
    # نستخدم كامل الرصيد المتاح تقريباً لأفضل إشارة حالية، مع هامش أمان.
    usable_cash = available_cash * CASH_USAGE_PERCENT
    usable_cash = usable_cash * (1 - SAFETY_BUFFER_PERCENT)

    # لا تتجاوز الكمية القادمة من TradingView إلا إذا كانت أكبر من قدرة الحساب.
    max_affordable_quantity = int(math.floor(usable_cash / price))
    final_quantity = min(original_quantity, max_affordable_quantity)

    trade_value = final_quantity * price

    if final_quantity < 1:
        return 0, {
            "mode": "dynamic_position_sizing",
            "blocked": True,
            "reason": "Not enough cash to buy at least 1 share",
            "available_cash": available_cash,
            "usable_cash": round(usable_cash, 2),
            "price_used": price,
            "original_quantity": original_quantity,
            "max_affordable_quantity": max_affordable_quantity
        }

    if trade_value < MIN_TRADE_VALUE:
        return 0, {
            "mode": "dynamic_position_sizing",
            "blocked": True,
            "reason": "Trade value below MIN_TRADE_VALUE",
            "available_cash": available_cash,
            "usable_cash": round(usable_cash, 2),
            "price_used": price,
            "quantity": final_quantity,
            "trade_value": round(trade_value, 2),
            "min_trade_value": MIN_TRADE_VALUE
        }

    return final_quantity, {
        "mode": "dynamic_position_sizing",
        "available_cash": available_cash,
        "usable_cash": round(usable_cash, 2),
        "cash_usage_percent": CASH_USAGE_PERCENT,
        "safety_buffer_percent": SAFETY_BUFFER_PERCENT,
        "open_positions": open_positions,
        "remaining_slots": remaining_slots,
        "price_used": price,
        "original_quantity": original_quantity,
        "max_affordable_quantity": max_affordable_quantity,
        "final_quantity": final_quantity,
        "estimated_trade_value": round(trade_value, 2)
    }


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


def start_scanner_background():
    try:
        from scanner import run_scanner
        t = threading.Thread(target=run_scanner, daemon=True)
        t.start()
        print("Scanner background thread started")
    except Exception as e:
        print("Scanner failed to start:", e)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "service": "Portfolio Manager Running ✅",
        "max_positions": MAX_POSITIONS,
        "dynamic_position_sizing": USE_DYNAMIC_POSITION_SIZING,
        "available_cash_setting": AVAILABLE_CASH,
        "cash_usage_percent": CASH_USAGE_PERCENT,
        "safety_buffer_percent": SAFETY_BUFFER_PERCENT
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}

    print("RAW WEBHOOK DATA:", json.dumps(data, ensure_ascii=False))

    ticker = str(data.get("ticker", "")).upper()
    action = str(data.get("action", "")).lower()
    score = safe_float(data.get("score", data.get("alphaScore", 0)), 0)
    quantity = safe_int(data.get("quantity", 1), 1)
    order_type = str(data.get("orderType", "market")).lower()
    signal_price = data.get("signalPrice") or data.get("price") or data.get("close")

    if not ticker or action not in ["buy", "sell", "exit"]:
        return jsonify({"ok": False, "error": "Invalid signal", "data": data}), 400

    state = load_state()
    positions = state.get("positions", {})

    if action in ["sell", "exit"]:
        # لا نرسل Sell إذا لا يوجد مركز محفوظ لدينا، حتى لا تظهر Rejected كثيرة في TradersPost
        if ticker not in positions:
            return jsonify({
                "ok": True,
                "decision": "ignored_exit_no_local_position",
                "ticker": ticker,
                "reason": "No local position found in portfolio_state"
            })

        exit_quantity = safe_int(positions.get(ticker, {}).get("quantity", quantity), quantity)

        payload = {
            "ticker": ticker,
            "action": "sell",
            "quantity": exit_quantity,
            "orderType": "market"
        }

        ok = send_to_traderspost(payload)

        if ok:
            positions.pop(ticker, None)
            state["positions"] = positions
            save_state(state)

        return jsonify({
            "ok": ok,
            "decision": "exit_sent",
            "ticker": ticker,
            "quantity": exit_quantity
        })

    # BUY LOGIC
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
        state["positions"] = positions
        save_state(state)

        return jsonify({
            "ok": True,
            "decision": "updated_existing_position_score",
            "ticker": ticker,
            "score": score
        })

    final_quantity, sizing_info = calculate_dynamic_quantity(data, positions)

    if final_quantity < 1:
        print("BUY BLOCKED BY POSITION SIZING:", json.dumps(sizing_info, ensure_ascii=False))
        return jsonify({
            "ok": True,
            "decision": "blocked_by_position_sizing",
            "ticker": ticker,
            "score": score,
            "sizing": sizing_info
        })

    if len(positions) < MAX_POSITIONS:
        payload = build_buy_payload(data, ticker, final_quantity, order_type)

        ok = send_to_traderspost(payload)

        if ok:
            positions[ticker] = {
                "score": score,
                "quantity": final_quantity,
                "entry_signal_price": signal_price,
                "order_type": order_type,
                "limit_price": data.get("limitPrice"),
                "sizing": sizing_info,
                "created_at": time.time()
            }
            state["positions"] = positions
            save_state(state)

        return jsonify({
            "ok": ok,
            "decision": "buy_sent",
            "ticker": ticker,
            "score": score,
            "signal_price": signal_price,
            "quantity": final_quantity,
            "sizing": sizing_info,
            "open_positions": len(positions)
        })

    weakest_ticker, weakest_data = min(
        positions.items(),
        key=lambda item: safe_float(item[1].get("score", 0), 0)
    )
    weakest_score = safe_float(weakest_data.get("score", 0), 0)

    if score > weakest_score:
        weakest_quantity = safe_int(weakest_data.get("quantity", 1), 1)

        sell_payload = {
            "ticker": weakest_ticker,
            "action": "sell",
            "quantity": weakest_quantity,
            "orderType": "market"
        }

        buy_payload = build_buy_payload(data, ticker, final_quantity, order_type)

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

        buy_ok = send_to_traderspost(buy_payload)

        if sell_ok and buy_ok:
            positions.pop(weakest_ticker, None)
            positions[ticker] = {
                "score": score,
                "quantity": final_quantity,
                "entry_signal_price": signal_price,
                "order_type": order_type,
                "limit_price": data.get("limitPrice"),
                "sizing": sizing_info,
                "created_at": time.time()
            }
            state["positions"] = positions
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
            "sizing": sizing_info
        })

    return jsonify({
        "ok": True,
        "decision": "ignored_not_better_than_top10",
        "ticker": ticker,
        "score": score,
        "weakest_score": weakest_score
    })


@app.route("/positions", methods=["GET"])
def get_positions():
    return jsonify(load_state())


@app.route("/reset", methods=["POST"])
def reset_positions():
    save_state({"positions": {}})
    return jsonify({"ok": True, "message": "positions reset"})


@app.route("/settings", methods=["GET"])
def settings():
    return jsonify({
        "MAX_POSITIONS": MAX_POSITIONS,
        "AVAILABLE_CASH": AVAILABLE_CASH,
        "CASH_USAGE_PERCENT": CASH_USAGE_PERCENT,
        "SAFETY_BUFFER_PERCENT": SAFETY_BUFFER_PERCENT,
        "MIN_TRADE_VALUE": MIN_TRADE_VALUE,
        "USE_DYNAMIC_POSITION_SIZING": USE_DYNAMIC_POSITION_SIZING
    })


start_scanner_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
