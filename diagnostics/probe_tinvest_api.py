
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# FIRST T-INVEST API PROBE
#
# RESEARCH ONLY
# READ ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
# ============================================================

import csv
import json
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from importlib.metadata import version

ROOT = Path(
    r"C:\Users\Cristal\Desktop\MOEX_HISTORICAL_LAB_2Y"
)

OLD_ENV = Path(
    r"C:\Users\Cristal\Desktop\Tsclp_v2.1_volatile\.env"
)

OUT = ROOT / "results" / "api_probe"

TICKERS = ["SBER", "GAZP"]

# Small initial test only.
DAILY_DAYS = 30
FIVE_MIN_DAYS = 3

PAUSE = 0.5


def log(message):
    print(message, flush=True)



def read_token(path):
    if not path.exists():
        raise FileNotFoundError(
            "Old project's .env file not found."
        )

    for raw_line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines():

        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.lower().startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)

        if key.strip().upper() != "TINKOFF_READONLY_TOKEN":
            continue

        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1]:
            if value[0] in ("'", '"'):
                value = value[1:-1]

        if not value:
            raise RuntimeError(
                "TINKOFF_READONLY_TOKEN is empty."
            )

        return value, "TINKOFF_READONLY_TOKEN"

    raise RuntimeError(
        "TINKOFF_READONLY_TOKEN not found in .env."
    )



def quotation(value):
    if value is None:
        return None

    return (
        float(getattr(value, "units", 0))
        + float(getattr(value, "nano", 0)) / 1e9
    )


def candle_dict(c):
    return {
        "time": c.time.isoformat(),
        "open": quotation(c.open),
        "high": quotation(c.high),
        "low": quotation(c.low),
        "close": quotation(c.close),
        "volume": int(c.volume),
        "is_complete": bool(
            getattr(c, "is_complete", False)
        ),
    }


def main():

    OUT.mkdir(parents=True, exist_ok=True)

    log("=" * 60)
    log("T-INVEST API FIRST PROBE")
    log("READ ONLY / NO TRADING")
    log("=" * 60)

    # Imports
    from t_tech.invest import Client, CandleInterval

    sdk_version = version("t-tech-investments")
    log("SDK VERSION: " + sdk_version)

    # Local token
    token, token_key = read_token(OLD_ENV)

    log("TOKEN FILE: FOUND")
    log("TOKEN VARIABLE: " + token_key)
    log("TOKEN VALUE: HIDDEN")

    now = datetime.now(timezone.utc)

    report = {
        "created_utc": now.isoformat(),
        "sdk_version": sdk_version,
        "mode": "READ_ONLY",
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "token_value_saved": False,
        "token_variable": token_key,
        "authorization": "NOT_TESTED",
        "instrument_list": "NOT_TESTED",
        "instruments": [],
        "errors": [],
    }

    csv_rows = []

    log("\nCONNECTING TO API...")

    with Client(token) as client:

        # Authorization check.
        try:
            client.users.get_info()
            report["authorization"] = "OK"
            log("AUTHORIZATION: OK")

        except Exception as exc:
            report["authorization"] = "ERROR"
            report["errors"].append({
                "stage": "authorization",
                "error": str(exc),
            })
            raise RuntimeError(
                "Authorization failed: " + str(exc)
            )

        time.sleep(PAUSE)

        # Get available shares.
        log("\nREQUESTING SHARE INSTRUMENT LIST...")

        try:
            response = client.instruments.shares()
            shares = response.instruments

            report["instrument_list"] = "OK"
            report["shares_received"] = len(shares)

            log(
                "SHARES RECEIVED: "
                + str(len(shares))
            )

        except Exception as exc:
            report["instrument_list"] = "ERROR"
            report["errors"].append({
                "stage": "instrument_list",
                "error": str(exc),
            })
            raise RuntimeError(
                "Cannot retrieve shares: " + str(exc)
            )

        # Find requested tickers.
        selected = {}

        for instrument in shares:

            ticker = getattr(
                instrument,
                "ticker",
                ""
            )

            exchange = str(
                getattr(instrument, "exchange", "")
            )

            currency = str(
                getattr(instrument, "currency", "")
            )

            api_flag = bool(
                getattr(
                    instrument,
                    "api_trade_available_flag",
                    False
                )
            )

            buy_flag = bool(
                getattr(
                    instrument,
                    "buy_available_flag",
                    False
                )
            )

            sell_flag = bool(
                getattr(
                    instrument,
                    "sell_available_flag",
                    False
                )
            )

            if ticker not in TICKERS:
                continue

            if exchange.upper() != "MOEX":
                continue

            if currency.lower() != "rub":
                continue

            if not api_flag or not buy_flag or not sell_flag:
                continue

            selected[ticker] = instrument

        # Historical data tests.
        for ticker in TICKERS:

            log("\n" + "-" * 45)
            log("INSTRUMENT: " + ticker)

            instrument = selected.get(ticker)

            if instrument is None:
                log(
                    "NOT FOUND AS CURRENT TRADEABLE MOEX SHARE"
                )

                report["instruments"].append({
                    "ticker": ticker,
                    "selection": "NOT_FOUND_OR_NOT_AVAILABLE",
                })
                continue

            uid = getattr(instrument, "uid", None)

            item = {
                "ticker": ticker,
                "name": getattr(instrument, "name", ""),
                "figi": getattr(instrument, "figi", ""),
                "uid": uid,
                "exchange": str(
                    getattr(instrument, "exchange", "")
                ),
                "currency": str(
                    getattr(instrument, "currency", "")
                ),
                "lot": getattr(instrument, "lot", None),
                "first_1day_candle_date": str(
                    getattr(
                        instrument,
                        "first_1day_candle_date",
                        None
                    )
                ),
                "tests": {},
            }

            # 1D request.
            daily_from = now - timedelta(
                days=DAILY_DAYS
            )

            try:
                response = client.market_data.get_candles(
                    instrument_id=uid,
                    from_=daily_from,
                    to=now,
                    interval=(
                        CandleInterval.CANDLE_INTERVAL_DAY
                    ),
                )

                candles = [
                    candle_dict(c)
                    for c in response.candles
                ]

                item["tests"]["1D"] = {
                    "status": "OK",
                    "count": len(candles),
                    "first": (
                        candles[0]["time"]
                        if candles else None
                    ),
                    "last": (
                        candles[-1]["time"]
                        if candles else None
                    ),
                }

                for c in candles:
                    csv_rows.append({
                        "ticker": ticker,
                        "interval": "1D",
                        **c,
                    })

                log(
                    "1D: OK, candles = "
                    + str(len(candles))
                )

            except Exception as exc:
                item["tests"]["1D"] = {
                    "status": "ERROR",
                    "error": str(exc),
                }

                report["errors"].append({
                    "ticker": ticker,
                    "test": "1D",
                    "error": str(exc),
                })

                log("1D ERROR: " + str(exc))

            time.sleep(PAUSE)

            # 5M request.
            five_from = now - timedelta(
                days=FIVE_MIN_DAYS
            )

            try:
                response = client.market_data.get_candles(
                    instrument_id=uid,
                    from_=five_from,
                    to=now,
                    interval=(
                        CandleInterval.CANDLE_INTERVAL_5_MIN
                    ),
                )

                candles = [
                    candle_dict(c)
                    for c in response.candles
                ]

                item["tests"]["5M"] = {
                    "status": "OK",
                    "count": len(candles),
                    "first": (
                        candles[0]["time"]
                        if candles else None
                    ),
                    "last": (
                        candles[-1]["time"]
                        if candles else None
                    ),
                }

                for c in candles:
                    csv_rows.append({
                        "ticker": ticker,
                        "interval": "5M",
                        **c,
                    })

                log(
                    "5M: OK, candles = "
                    + str(len(candles))
                )

            except Exception as exc:
                item["tests"]["5M"] = {
                    "status": "ERROR",
                    "error": str(exc),
                }

                report["errors"].append({
                    "ticker": ticker,
                    "test": "5M",
                    "error": str(exc),
                })

                log("5M ERROR: " + str(exc))

            report["instruments"].append(item)

            time.sleep(PAUSE)

    # Save JSON report.
    json_path = OUT / "api_probe_report.json"

    json_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    # Save candles CSV.
    csv_path = OUT / "api_probe_candles.csv"

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        fields = [
            "ticker",
            "interval",
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_complete",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()
        writer.writerows(csv_rows)

    log("\n" + "=" * 60)
    log("PROBE FINISHED")
    log("AUTHORIZATION: " + report["authorization"])
    log("INSTRUMENT LIST: " + report["instrument_list"])
    log("ERRORS: " + str(len(report["errors"])))
    log("JSON: " + str(json_path))
    log("CSV: " + str(csv_path))
    log("TOKEN VALUE: NOT SAVED")
    log("NO TRADING OPERATIONS")
    log("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("\nFATAL ERROR: " + str(exc))
        log("NO TRADING OPERATIONS WERE PERFORMED.")
        raise
