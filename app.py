from flask import Flask, request, jsonify
import os
import json
import time
import requests
import threading

app = Flask(__name__)

TRADERSPOST_WEBHOOK = os.getenv("TRADERSPOST_WEBHOOK", "")
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
STATE_FILE = "portfolio_state.json"


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
    return "Portfolio Manager Running ✅"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}

    print("RAW WEBHOOK DATA:", json.dumps(data, ensure_ascii=False))

    ticker = str(data.get("ticker", "")).upper()
    action = str(data.get("action", "")).lower()
    score = float(data.get("score", data.get("alphaScore", 0)) or 0)
    quantity = data.get("quantity", 1)
    order_type = data.get("orderType", "market")
    signal_price = data.get("signalPrice") or data.get("price") or data.get("close")

    if not ticker or action not in ["buy", "sell", "exit"]:
        return jsonify({"ok": False, "error": "Invalid signal", "data": data}), 400

    state = load_state()
    positions = state.get("positions", {})

    if action in ["sell", "exit"]:
        payload = {
            "ticker": ticker,
            "action": "sell",
            "quantity": quantity,
            "orderType": "market"
        }

        ok = send_to_traderspost(payload)
        positions.pop(ticker, None)
        state["positions"] = positions
        save_state(state)

        return jsonify({"ok": ok, "decision": "exit_sent", "ticker": ticker})

    if ticker in positions:
        old_score = float(positions[ticker].get("score", 0))

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

    if len(positions) < MAX_POSITIONS:
        payload = {
            "ticker": ticker,
            "action": "buy",
            "quantity": quantity,
            "orderType": order_type
        }

        if order_type == "limit" and data.get("limitPrice"):
            payload["limitPrice"] = data.get("limitPrice")

        ok = send_to_traderspost(payload)

        if ok:
            positions[ticker] = {
                "score": score,
                "entry_signal_price": signal_price,
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
            "open_positions": len(positions)
        })

    weakest_ticker, weakest_data = min(
        positions.items(),
        key=lambda item: float(item[1].get("score", 0))
    )
    weakest_score = float(weakest_data.get("score", 0))

    if score > weakest_score:
        sell_payload = {
            "ticker": weakest_ticker,
            "action": "sell",
            "quantity": 1,
            "orderType": "market"
        }

        buy_payload = {
            "ticker": ticker,
            "action": "buy",
            "quantity": quantity,
            "orderType": order_type
        }

        if order_type == "limit" and data.get("limitPrice"):
            buy_payload["limitPrice"] = data.get("limitPrice")

        sell_ok = send_to_traderspost(sell_payload)
        buy_ok = send_to_traderspost(buy_payload)

        if sell_ok and buy_ok:
            positions.pop(weakest_ticker, None)
            positions[ticker] = {
                "score": score,
                "entry_signal_price": signal_price,
                "created_at": time.time()
            }
            state["positions"] = positions
            save_state(state)

        return jsonify({
            "ok": sell_ok and buy_ok,
            "decision": "swap",
            "sold": weakest_ticker,
            "sold_score": weakest_score,
            "bought": ticker,
            "bought_score": score,
            "signal_price": signal_price
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


start_scanner_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))