
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# T-INVEST API DIAGNOSTICS — V2
#
# RESEARCH ONLY
# READ ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Uses ONLY TINKOFF_READONLY_TOKEN.
# Does not modify the old scalper project.
# Does not save or print token values.
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

ENV_FILE = Path(
    r"C:\Users\Cristal\Desktop\Tsclp_v2.1_volatile\.env"
)

OUT = ROOT / "results" / "api_probe"

TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "VTBR",
    "YDEX", "T", "OZON", "NVTK", "GMKN"
]

DAILY_DAYS = 30
FIVE_MIN_DAYS = 3
PAUSE = 0.5


def log(message):
    print(message, flush=True)


def read_token(path):
    if not path.exists():
        raise FileNotFoundError(".env file not found")

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
                "TINKOFF_READONLY_TOKEN is empty"
            )

        return value

    raise RuntimeError(
        "TINKOFF_READONLY_TOKEN not found"
    )


def safe_value(obj, field):
    value = getattr(obj, field, None)

    if value is None:
        return None

    if hasattr(value, "name"):
        return str(value.name)

    return str(value)


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


def instrument_dict(instrument):
    fields = [
        "ticker",
        "name",
        "figi",
        "uid",
        "class_code",
        "exchange",
        "currency",
        "lot",
        "api_trade_available_flag",
        "buy_available_flag",
        "sell_available_flag",
        "trading_status",
        "instrument_type",
        "first_1day_candle_date",
        "first_1min_candle_date",
        "otc_flag",
        "for_qual_investor_flag",
    ]

    result = {}

    for field in fields:
        value = getattr(instrument, field, None)

        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif value is not None:
            value = safe_value(instrument, field)

        result[field] = value

    return result


def test_candles(client, instrument, ticker, interval_name,
                 interval, date_from, date_to, csv_rows):

    uid = getattr(instrument, "uid", None)

    if not uid:
        return {
            "status": "SKIPPED",
            "reason": "Instrument UID is empty"
        }

    try:
        response = client.market_data.get_candles(
            instrument_id=uid,
            from_=date_from,
            to=date_to,
            interval=interval,
        )

        candles = [
            candle_dict(c)
            for c in response.candles
        ]

        for candle in candles:
            csv_rows.append({
                "ticker": ticker,
                "uid": uid,
                "interval": interval_name,
                **candle,
            })

        return {
            "status": "OK",
            "count": len(candles),
            "first": (
                candles[0]["time"] if candles else None
            ),
            "last": (
                candles[-1]["time"] if candles else None
            ),
        }

    except Exception as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
        }


def main():

    OUT.mkdir(parents=True, exist_ok=True)

    log("=" * 65)
    log("T-INVEST API DIAGNOSTICS V2")
    log("READ ONLY / NO TRADING")
    log("=" * 65)

    from t_tech.invest import Client, CandleInterval

    sdk_version = version("t-tech-investments")
    token = read_token(ENV_FILE)

    log("SDK VERSION: " + sdk_version)
    log("TOKEN VARIABLE: TINKOFF_READONLY_TOKEN")
    log("TOKEN VALUE: HIDDEN")

    now = datetime.now(timezone.utc)
    daily_from = now - timedelta(days=DAILY_DAYS)
    five_from = now - timedelta(days=FIVE_MIN_DAYS)

    report = {
        "created_utc": now.isoformat(),
        "sdk_version": sdk_version,
        "mode": "READ_ONLY",
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "token_value_saved": False,
        "authorization": "NOT_TESTED",
        "instrument_list": "NOT_TESTED",
        "shares_received": 0,
        "requested_tickers": TICKERS,
        "instruments": [],
        "errors": [],
    }

    csv_rows = []

    log("\nCONNECTING TO API...")

    with Client(token) as client:

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

        log("\nREQUESTING SHARE INSTRUMENT LIST...")

        try:
            response = client.instruments.shares()
            shares = response.instruments

            report["instrument_list"] = "OK"
            report["shares_received"] = len(shares)

            log("SHARES RECEIVED: " + str(len(shares)))

        except Exception as exc:
            report["instrument_list"] = "ERROR"
            report["errors"].append({
                "stage": "instrument_list",
                "error": str(exc),
            })
            raise

        # IMPORTANT:
        # Find by ticker ONLY.
        # Do NOT filter by exchange, currency or trade flags.

        matches = {}

        for instrument in shares:
            ticker = str(
                getattr(instrument, "ticker", "")
            ).strip().upper()

            if ticker in TICKERS:
                matches.setdefault(ticker, []).append(
                    instrument
                )

        log("\nTICKER SEARCH RESULTS:")

        for ticker in TICKERS:
            count = len(matches.get(ticker, []))
            log(f"{ticker}: {count} match(es)")

        # Test every matching instrument separately.
        for ticker in TICKERS:

            candidates = matches.get(ticker, [])

            log("\n" + "-" * 60)
            log("TICKER: " + ticker)

            if not candidates:
                log("NOT FOUND IN SHARES LIST")

                report["instruments"].append({
                    "ticker": ticker,
                    "search_status": "NOT_FOUND_IN_SHARES",
                    "candidates": [],
                })
                continue

            ticker_item = {
                "ticker": ticker,
                "search_status": "FOUND",
                "candidate_count": len(candidates),
                "candidates": [],
            }

            for index, instrument in enumerate(
                candidates, start=1
            ):

                metadata = instrument_dict(instrument)

                log(
                    f"\nCANDIDATE {index}/{len(candidates)}"
                )

                for field in [
                    "name",
                    "uid",
                    "figi",
                    "class_code",
                    "exchange",
                    "currency",
                    "lot",
                    "api_trade_available_flag",
                    "buy_available_flag",
                    "sell_available_flag",
                    "trading_status",
                    "first_1day_candle_date",
                ]:
                    log(
                        f"{field}: {metadata.get(field)}"
                    )

                candidate = {
                    "metadata": metadata,
                    "tests": {},
                }

                uid = metadata.get("uid")

                if not uid:
                    candidate["tests"]["status"] = (
                        "NO_UID"
                    )
                    ticker_item["candidates"].append(
                        candidate
                    )
                    continue

                log("\nTESTING DAILY CANDLES...")

                candidate["tests"]["1D_30D"] = test_candles(
                    client,
                    instrument,
                    ticker,
                    "1D",
                    CandleInterval.CANDLE_INTERVAL_DAY,
                    daily_from,
                    now,
                    csv_rows,
                )

                log(
                    "1D RESULT: "
                    + json.dumps(
                        candidate["tests"]["1D_30D"],
                        ensure_ascii=False
                    )
                )

                time.sleep(PAUSE)

                log("TESTING 5M CANDLES...")

                candidate["tests"]["5M_3D"] = test_candles(
                    client,
                    instrument,
                    ticker,
                    "5M",
                    CandleInterval.CANDLE_INTERVAL_5_MIN,
                    five_from,
                    now,
                    csv_rows,
                )

                log(
                    "5M RESULT: "
                    + json.dumps(
                        candidate["tests"]["5M_3D"],
                        ensure_ascii=False
                    )
                )

                ticker_item["candidates"].append(
                    candidate
                )

                time.sleep(PAUSE)

            report["instruments"].append(ticker_item)

    json_path = OUT / "api_probe_report_v2.json"
    csv_path = OUT / "api_probe_candles_v2.csv"

    json_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    fields = [
        "ticker",
        "uid",
        "interval",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "is_complete",
    ]

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    log("\n" + "=" * 65)
    log("PROBE V2 FINISHED")
    log("AUTHORIZATION: " + report["authorization"])
    log("INSTRUMENT LIST: " + report["instrument_list"])
    log("SHARES: " + str(report["shares_received"]))
    log("MATCHED TICKERS: " + str(
        sum(
            1 for x in report["instruments"]
            if x["search_status"] == "FOUND"
        )
    ))
    log("ERRORS: " + str(len(report["errors"])))
    log("JSON: " + str(json_path))
    log("CSV: " + str(csv_path))
    log("TOKEN VALUE: NOT SAVED")
    log("NO TRADING OPERATIONS")
    log("=" * 65)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("\nFATAL ERROR: " + str(exc))
        log("NO TRADING OPERATIONS WERE PERFORMED.")
        raise
