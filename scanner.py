import os
import time
import requests

PORTFOLIO_WEBHOOK = os.getenv("PORTFOLIO_WEBHOOK", "http://127.0.0.1:8080/webhook")
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

SYMBOLS_FILE = "symbols.txt"


def load_symbols():
    with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
        return [x.strip().upper() for x in f.readlines() if x.strip()]


def mock_check_signal(symbol):
    """
    مؤقتاً: هذا مكان منطق الفحص.
    لاحقاً نربطه بمصدر بيانات حقيقي للسعر والفوليوم.
    """
    return None


def send_signal(payload):
    r = requests.post(PORTFOLIO_WEBHOOK, json=payload, timeout=15)
    print("Portfolio:", r.status_code, r.text[:300])


def run_scanner():
    print("Scanner started")
    symbols = load_symbols()

    while True:
        for symbol in symbols:
            signal = mock_check_signal(symbol)

            if signal:
                send_signal(signal)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_scanner()