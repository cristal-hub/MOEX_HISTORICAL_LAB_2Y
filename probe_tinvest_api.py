
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# T-INVEST API: READ-ONLY COVERAGE PROBE
#
# RESEARCH ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Does not modify the old scalper project.
# Does not print or copy the API token.
# Does not download the full historical dataset.
# ============================================================

import csv
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ---------------- CONFIGURATION ----------------

OLD_PROJECT = Path(
    r"C:\Users\Cristal\Desktop\Tsclp_v2.1_volatile"
)

ENV_FILE = OLD_PROJECT / ".env"

LAB_ROOT = Path(
    r"C:\Users\Cristal\Desktop\MOEX_HISTORICAL_LAB_2Y"
)

OUTPUT_DIR = LAB_ROOT / "results" / "api_probe"

TICKERS_TO_CHECK = [
    "SBER",
    "GAZP",
    "LKOH",
    "ROSN",
    "VTBR",
    "YDEX",
    "T",
    "OZON",
    "NVTK",
    "GMKN",
]

# Six calendar days per 5M request.
# API maximum for 5M is approximately one week.
FIVE_MIN_WINDOW_DAYS = 6

# Small pause between requests.
API_CALL_DELAY_SEC = 0.3

# ------------------------------------------------


def log(message):
    print(message, flush=True)


def read_token_from_env(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Old project's .env not found: {path}"
        )

    candidates = []

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

        key = key.strip()
        value = value.strip()

        if len(value) >= 2:
            if (
                value[0] == value[-1]
                and value[0] in ("'", '"')
            ):
                value = value[1:-1]

        key_upper = key.upper()

        if (
            "TOKEN" in key_upper
            or "INVEST" in key_upper
        ):
            if value:
                candidates.append((key, value))

    if not candidates:
        raise RuntimeError(
            "No token-like variable found in .env. "
            "Token value was not displayed."
        )

    # Prefer the most likely variable names.
    preferred_names = [
        "TINKOFF_TOKEN",
        "INVEST_TOKEN",
        "TOKEN",
    ]

    for preferred in preferred_names:
        for key, value in candidates:
            if key.upper() == preferred:
                return value, key

    if len(candidates) == 1:
        return candidates[0][1], candidates[0][0]

    names = [item[0] for item in candidates]

    raise RuntimeError(
        "Several possible token variables found: "
        + ", ".join(names)
        + ". No token value was displayed. "
        "Please clarify which variable is used."
    )


def quotation_to_float(value):
    if value is None:
        return None

    units = getattr(value, "units", 0)
    nano = getattr(value, "nano", 0)

    return float(units) + float(nano) / 1_000_000_000


def candle_to_dict(candle):
    return {
        "time": candle.time.isoformat(),
        "open": quotation_to_float(candle.open),
        "high": quotation_to_float(candle.high),
        "low": quotation_to_float(candle.low),
        "close": quotation_to_float(candle.close),
        "volume": int(candle.volume),
        "is_complete": bool(
            getattr(candle, "is_complete", False)
        ),
    }


def safe_date(value):
    if value is None:
        return None

    try:
        return value.isoformat()
    except Exception:
        return str(value)


def main():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    log("=" * 65)
    log("MOEX HISTORICAL LAB — API PROBE")
    log("RESEARCH ONLY / READ-ONLY")
    log("NO LIVE ORDERS / NO REAL MONEY")
    log("=" * 65)

    # 1. Check environment and token.
    log("\n[1/5] Checking local token file...")

    token, token_variable = read_token_from_env(
        ENV_FILE
    )

    log("ENV FILE: FOUND")
    log("TOKEN VARIABLE: " + token_variable)
    log("TOKEN VALUE: NOT DISPLAYED")

    # 2. Import SDK.
    log("\n[2/5] Checking T-Invest Python SDK...")

    try:
        from tinkoff.invest import (
            Client,
            CandleInterval,
        )
    except Exception as exc:
        raise RuntimeError(
            "Cannot import tinkoff.invest SDK. "
            "The script has not installed or changed "
            "anything. Python error: "
            + str(exc)
        )

    log("SDK IMPORT: OK")

    # 3. Prepare date ranges.
    now = datetime.now(timezone.utc)

    daily_from = now - timedelta(days=730)

    recent_5m_from = now - timedelta(
        days=FIVE_MIN_WINDOW_DAYS
    )

    old_5m_from = now - timedelta(
        days=730 + FIVE_MIN_WINDOW_DAYS
    )

    old_5m_to = now - timedelta(days=730)

    report = {
        "created_at_utc": now.isoformat(),
        "project": "MOEX_HISTORICAL_LAB_2Y",
        "mode": "READ_ONLY",
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "token_file": str(ENV_FILE),
        "token_variable_name": token_variable,
        "token_value_saved": False,
        "daily_period": {
            "from": daily_from.isoformat(),
            "to": now.isoformat(),
        },
        "five_minute_periods": {
            "recent": {
                "from": recent_5m_from.isoformat(),
                "to": now.isoformat(),
            },
            "historical": {
                "from": old_5m_from.isoformat(),
                "to": old_5m_to.isoformat(),
            },
        },
        "instruments": [],
        "errors": [],
    }

    csv_rows = []

    # 4. Connect and discover current instruments.
    log("\n[3/5] Connecting to T-Invest API...")

    with Client(token) as client:

        log("API CONNECTION: OK")

        log("\n[4/5] Loading current MOEX shares...")

        shares_response = client.instruments.shares()

        all_shares = shares_response.instruments

        log(
            "SHARES RECEIVED FROM API: "
            + str(len(all_shares))
        )

        selected = {}

        for instrument in all_shares:

            ticker = getattr(
                instrument,
                "ticker",
                ""
            )

            exchange = getattr(
                instrument,
                "exchange",
                ""
            )

            currency = getattr(
                instrument,
                "currency",
                ""
            )

            api_available = bool(
                getattr(
                    instrument,
                    "api_trade_available_flag",
                    False
                )
            )

            buy_available = bool(
                getattr(
                    instrument,
                    "buy_available_flag",
                    False
                )
            )

            sell_available = bool(
                getattr(
                    instrument,
                    "sell_available_flag",
                    False
                )
            )

            if ticker not in TICKERS_TO_CHECK:
                continue

            if exchange.upper() != "MOEX":
                continue

            if currency.lower() != "rub":
                continue

            if not api_available:
                continue

            if not buy_available or not sell_available:
                continue

            selected[ticker] = instrument

        log(
            "SELECTED CURRENT MOEX INSTRUMENTS: "
            + str(len(selected))
        )

        for ticker in TICKERS_TO_CHECK:

            if ticker not in selected:
                log(
                    "NOT SELECTED / NOT AVAILABLE: "
                    + ticker
                )

        # 5. Test history for each selected instrument.
        log("\n[5/5] Testing historical candles...")

        for number, ticker in enumerate(
            TICKERS_TO_CHECK,
            start=1
        ):

            if ticker not in selected:
                continue

            instrument = selected[ticker]

            figi = getattr(
                instrument,
                "figi",
                ""
            )

            uid = getattr(
                instrument,
                "uid",
                ""
            )

            item = {
                "ticker": ticker,
                "name": getattr(
                    instrument,
                    "name",
                    ""
                ),
                "figi": figi,
                "instrument_uid": uid,
                "class_code": getattr(
                    instrument,
                    "class_code",
                    ""
                ),
                "exchange": getattr(
                    instrument,
                    "exchange",
                    ""
                ),
                "currency": getattr(
                    instrument,
                    "currency",
                    ""
                ),
                "lot": getattr(
                    instrument,
                    "lot",
                    None
                ),
                "api_trade_available": True,
                "buy_available": True,
                "sell_available": True,
                "first_1day_candle_date": safe_date(
                    getattr(
                        instrument,
                        "first_1day_candle_date",
                        None
                    )
                ),
                "first_1min_candle_date": safe_date(
                    getattr(
                        instrument,
                        "first_1min_candle_date",
                        None
                    )
                ),
                "tests": {},
            }

            log(
                f"\n--- {number}/{len(TICKERS_TO_CHECK)} "
                f"{ticker} ---"
            )

            # Daily candles: approximately two years.
            try:
                response = client.market_data.get_candles(
                    instrument_id=uid,
                    from_=daily_from,
                    to=now,
                    interval=(
                        CandleInterval.CANDLE_INTERVAL_DAY
                    ),
                )

                candles = response.candles

                converted = [
                    candle_to_dict(c)
                    for c in candles
                ]

                item["tests"]["1D_2Y"] = {
                    "status": "OK",
                    "count": len(converted),
                    "first_candle": (
                        converted[0]["time"]
                        if converted else None
                    ),
                    "last_candle": (
                        converted[-1]["time"]
                        if converted else None
                    ),
                }

                for c in converted:
                    csv_rows.append({
                        "ticker": ticker,
                        "interval": "1D",
                        **c,
                    })

                log(
                    "1D / 2Y: "
                    + str(len(converted))
                    + " candles"
                )

            except Exception as exc:

                item["tests"]["1D_2Y"] = {
                    "status": "ERROR",
                    "error": str(exc),
                }

                report["errors"].append({
                    "ticker": ticker,
                    "test": "1D_2Y",
                    "error": str(exc),
                })

                log("1D / 2Y: ERROR - " + str(exc))

            time.sleep(API_CALL_DELAY_SEC)

            # Recent 5-minute candles.
            try:
                response = client.market_data.get_candles(
                    instrument_id=uid,
                    from_=recent_5m_from,
                    to=now,
                    interval=(
                        CandleInterval.CANDLE_INTERVAL_5_MIN
                    ),
                )

                candles = response.candles

                converted = [
                    candle_to_dict(c)
                    for c in candles
                ]

                item["tests"]["5M_RECENT"] = {
                    "status": "OK",
                    "count": len(converted),
                    "first_candle": (
                        converted[0]["time"]
                        if converted else None
                    ),
                    "last_candle": (
                        converted[-1]["time"]
                        if converted else None
                    ),
                }

                for c in converted:
                    csv_rows.append({
                        "ticker": ticker,
                        "interval": "5M_RECENT",
                        **c,
                    })

                log(
                    "5M RECENT: "
                    + str(len(converted))
                    + " candles"
                )

            except Exception as exc:

                item["tests"]["5M_RECENT"] = {
                    "status": "ERROR",
                    "error": str(exc),
                }

                report["errors"].append({
                    "ticker": ticker,
                    "test": "5M_RECENT",
                    "error": str(exc),
                })

                log("5M RECENT: ERROR - " + str(exc))

            time.sleep(API_CALL_DELAY_SEC)

            # Historical 5-minute candles, around two years ago.
            try:
                response = client.market_data.get_candles(
                    instrument_id=uid,
                    from_=old_5m_from,
                    to=old_5m_to,
                    interval=(
                        CandleInterval.CANDLE_INTERVAL_5_MIN
                    ),
                )

                candles = response.candles

                converted = [
                    candle_to_dict(c)
                    for c in candles
                ]

                item["tests"]["5M_OLD"] = {
                    "status": "OK",
                    "count": len(converted),
                    "first_candle": (
                        converted[0]["time"]
                        if converted else None
                    ),
                    "last_candle": (
                        converted[-1]["time"]
                        if converted else None
                    ),
                }

                for c in converted:
                    csv_rows.append({
                        "ticker": ticker,
                        "interval": "5M_OLD",
                        **c,
                    })

                log(
                    "5M HISTORICAL: "
                    + str(len(converted))
                    + " candles"
                )

            except Exception as exc:

                item["tests"]["5M_OLD"] = {
                    "status": "ERROR",
                    "error": str(exc),
                }

                report["errors"].append({
                    "ticker": ticker,
                    "test": "5M_OLD",
                    "error": str(exc),
                })

                log(
                    "5M HISTORICAL: ERROR - "
                    + str(exc)
                )

            report["instruments"].append(item)

            time.sleep(API_CALL_DELAY_SEC)

    # Save reports.
    json_path = OUTPUT_DIR / "api_probe_report.json"
    csv_path = OUTPUT_DIR / "api_probe_candles.csv"

    json_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        fieldnames = [
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
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(csv_rows)

    # Final summary.
    log("\n" + "=" * 65)
    log("PROBE FINISHED")
    log("=" * 65)

    log(
        "SELECTED INSTRUMENTS: "
        + str(len(report["instruments"]))
    )

    log(
        "API ERRORS: "
        + str(len(report["errors"]))
    )

    log("JSON REPORT: " + str(json_path))
    log("CANDLES CSV: " + str(csv_path))

    log("\nTOKEN VALUE WAS NOT SAVED.")
    log("OLD PROJECT FILES WERE NOT MODIFIED.")
    log("NO TRADING OPERATIONS WERE PERFORMED.")

    log("\nEND.")


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        log("\nFATAL ERROR:")
        log(str(exc))

        log(
            "\nNo trading operations were performed."
        )

        sys.exit(1)
