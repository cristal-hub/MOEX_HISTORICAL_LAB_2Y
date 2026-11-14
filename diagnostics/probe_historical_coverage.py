
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# HISTORICAL COVERAGE PROBE
#
# RESEARCH ONLY
# READ ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Uses ONLY TINKOFF_READONLY_TOKEN.
# Tests historical coverage on a representative sample.
# Never overwrites previous reports.
# ============================================================

import csv
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from importlib.metadata import version

ROOT = Path(
    r"C:\Users\Cristal\Desktop\MOEX_HISTORICAL_LAB_2Y"
)

ENV_FILE = Path(
    r"C:\Users\Cristal\Desktop\Tsclp_v2.1_volatile\.env"
)

REGISTRY_DIR = ROOT / "results" / "instrument_registry"
OUT = ROOT / "results" / "historical_coverage"

START_DATE = datetime(
    2024, 11, 14, 0, 0, tzinfo=timezone.utc
)

PREFERRED_TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "VTBR",
    "YDEX", "T", "OZON", "NVTK", "GMKN"
]

# Each 5M request is limited to one calendar week.
FIVE_MIN_WINDOWS = [
    {
        "name": "EARLY_2024",
        "from": datetime(
            2024, 11, 11, 0, 0, tzinfo=timezone.utc
        ),
        "to": datetime(
            2024, 11, 18, 0, 0, tzinfo=timezone.utc
        ),
    },
    {
        "name": "MIDDLE_2025",
        "from": datetime(
            2025, 11, 10, 0, 0, tzinfo=timezone.utc
        ),
        "to": datetime(
            2025, 11, 17, 0, 0, tzinfo=timezone.utc
        ),
    },
    {
        "name": "RECENT_2026",
        "from": datetime(
            2026, 11, 9, 0, 0, tzinfo=timezone.utc
        ),
        "to": datetime(
            2026, 11, 14, 23, 59, tzinfo=timezone.utc
        ),
    },
]

PAUSE = 0.35
MAX_RETRIES = 3


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


def load_registry():
    if not REGISTRY_DIR.exists():
        raise FileNotFoundError(
            "Instrument registry directory not found"
        )

    files = [
        p for p in REGISTRY_DIR.glob(
            "instrument_registry_*.json"
        )
        if not p.name.endswith(".summary.json")
    ]

    if not files:
        raise FileNotFoundError(
            "No instrument registry JSON found"
        )

    latest = max(
        files,
        key=lambda p: p.stat().st_mtime
    )

    data = json.loads(
        latest.read_text(encoding="utf-8")
    )

    records = data.get("instruments", [])

    if not records:
        raise RuntimeError(
            "Registry contains no instruments"
        )

    return latest, records


def select_sample(records):
    eligible = [
        x for x in records
        if x.get("is_moex_candidate") is True
        and x.get("is_tradeable_candidate") is True
        and x.get("uid")
    ]

    by_ticker = {}

    for item in eligible:
        ticker = str(
            item.get("ticker") or ""
        ).upper()

        if ticker:
            by_ticker.setdefault(ticker, []).append(item)

    selected = []
    used_uids = set()

    # First select the known 10 instruments.
    for ticker in PREFERRED_TICKERS:
        candidates = by_ticker.get(ticker, [])

        if not candidates:
            continue

        candidate = candidates[0]

        if candidate["uid"] not in used_uids:
            selected.append(candidate)
            used_uids.add(candidate["uid"])

    # If any preferred ticker is absent, fill the sample.
    for item in sorted(
        eligible,
        key=lambda x: str(x.get("ticker") or "")
    ):
        if len(selected) >= len(PREFERRED_TICKERS):
            break

        if item["uid"] not in used_uids:
            selected.append(item)
            used_uids.add(item["uid"])

    return selected, len(eligible)


def quotation(value):
    if value is None:
        return None

    return (
        float(getattr(value, "units", 0))
        + float(getattr(value, "nano", 0)) / 1e9
    )


def candle_to_dict(c):
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


def validate_candles(candles):
    times = [
        x["time"] for x in candles
    ]

    sorted_ok = times == sorted(times)
    unique_ok = len(times) == len(set(times))

    invalid_ohlc = 0
    negative_volume = 0
    zero_volume = 0

    for c in candles:
        o = c["open"]
        h = c["high"]
        low = c["low"]
        close = c["close"]
        volume = c["volume"]

        if (
            o is None or h is None
            or low is None or close is None
        ):
            invalid_ohlc += 1
            continue

        if (
            h < max(o, close, low)
            or low > min(o, close, h)
        ):
            invalid_ohlc += 1

        if volume < 0:
            negative_volume += 1

        if volume == 0:
            zero_volume += 1

    return {
        "sorted": sorted_ok,
        "unique_timestamps": unique_ok,
        "duplicate_timestamps": (
            len(times) - len(set(times))
        ),
        "invalid_ohlc": invalid_ohlc,
        "negative_volume": negative_volume,
        "zero_volume": zero_volume,
    }


def request_candles(
    client,
    uid,
    interval,
    date_from,
    date_to,
):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.market_data.get_candles(
                instrument_id=uid,
                from_=date_from,
                to=date_to,
                interval=interval,
            )

            return [
                candle_to_dict(c)
                for c in response.candles
            ], None

        except Exception as exc:
            last_error = str(exc)

            if attempt < MAX_RETRIES:
                delay = 2 ** (attempt - 1)

                log(
                    f"  RETRY {attempt}/{MAX_RETRIES} "
                    f"AFTER {delay}s"
                )

                time.sleep(delay)

    return [], last_error


def run_test(
    client,
    instrument,
    ticker,
    test_name,
    interval,
    date_from,
    date_to,
    all_rows,
):
    uid = instrument["uid"]

    candles, error = request_candles(
        client,
        uid,
        interval,
        date_from,
        date_to,
    )

    result = {
        "test": test_name,
        "ticker": ticker,
        "uid": uid,
        "interval": interval,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "status": "ERROR" if error else "OK",
        "count": len(candles),
        "first": candles[0]["time"] if candles else None,
        "last": candles[-1]["time"] if candles else None,
        "validation": validate_candles(candles),
        "error": error,
    }

    for candle in candles:
        all_rows.append({
            "ticker": ticker,
            "uid": uid,
            "test": test_name,
            "interval": interval,
            **candle,
        })

    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    log("=" * 68)
    log("HISTORICAL COVERAGE PROBE")
    log("READ ONLY / NO TRADING")
    log("=" * 68)

    log("\nLOADING INSTRUMENT REGISTRY...")

    registry_path, records = load_registry()

    selected, eligible_count = select_sample(records)

    if not selected:
        raise RuntimeError(
            "No eligible instruments found in registry"
        )

    log("REGISTRY: " + str(registry_path))
    log("ELIGIBLE TQBR/RUB: " + str(eligible_count))
    log("SELECTED SAMPLE: " + str(len(selected)))

    for item in selected:
        log(
            f"  {item.get('ticker')} | "
            f"{item.get('name')} | "
            f"{item.get('uid')}"
        )

    from t_tech.invest import (
        Client,
        CandleInterval,
    )

    token = read_token(ENV_FILE)

    sdk_version = version("t-tech-investments")

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "sdk_version": sdk_version,
        "registry_file": str(registry_path),
        "mode": "READ_ONLY",
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "token_value_saved": False,
        "requested_period": {
            "start": START_DATE.isoformat(),
            "end": datetime.now(
                timezone.utc
            ).isoformat(),
        },
        "sample_size": len(selected),
        "eligible_universe_size": eligible_count,
        "instruments": [],
        "errors": [],
    }

    all_rows = []

    log("\nCONNECTING TO API...")
    log("SDK VERSION: " + sdk_version)
    log("TOKEN VARIABLE: TINKOFF_READONLY_TOKEN")
    log("TOKEN VALUE: HIDDEN")

    with Client(token) as client:

        log("AUTHORIZATION: OK")

        now = datetime.now(timezone.utc)

        for index, instrument in enumerate(
            selected, start=1
        ):

            ticker = str(
                instrument.get("ticker")
            ).upper()

            uid = instrument["uid"]

            log("\n" + "-" * 60)
            log(
                f"INSTRUMENT {index}/{len(selected)}: "
                f"{ticker}"
            )

            item_report = {
                "ticker": ticker,
                "name": instrument.get("name"),
                "uid": uid,
                "figi": instrument.get("figi"),
                "class_code": instrument.get("class_code"),
                "currency": instrument.get("currency"),
                "tests": [],
            }

            # Full two-year daily range.
            log("TESTING 1D: FULL TWO-YEAR RANGE...")

            daily_result = run_test(
                client,
                instrument,
                ticker,
                "DAILY_FULL_2Y",
                CandleInterval.CANDLE_INTERVAL_DAY,
                START_DATE,
                now,
                all_rows,
            )

            item_report["tests"].append(
                daily_result
            )

            log(
                "1D RESULT: "
                + json.dumps(
                    daily_result,
                    ensure_ascii=False,
                )
            )

            time.sleep(PAUSE)

            # Three separate 5M windows.
            for window in FIVE_MIN_WINDOWS:

                log(
                    "TESTING 5M: "
                    + window["name"]
                )

                result = run_test(
                    client,
                    instrument,
                    ticker,
                    window["name"],
                    CandleInterval.CANDLE_INTERVAL_5_MIN,
                    window["from"],
                    window["to"],
                    all_rows,
                )

                item_report["tests"].append(
                    result
                )

                log(
                    "5M RESULT: "
                    + json.dumps(
                        result,
                        ensure_ascii=False,
                    )
                )

                time.sleep(PAUSE)

            report["instruments"].append(
                item_report
            )

    # --------------------------------------------------------
    # Overall summary
    # --------------------------------------------------------

    all_tests = [
        test
        for instrument in report["instruments"]
        for test in instrument["tests"]
    ]

    successful = [
        x for x in all_tests
        if x["status"] == "OK"
    ]

    failed = [
        x for x in all_tests
        if x["status"] != "OK"
    ]

    empty = [
        x for x in successful
        if x["count"] == 0
    ]

    report["summary"] = {
        "total_tests": len(all_tests),
        "successful_tests": len(successful),
        "failed_tests": len(failed),
        "empty_tests": len(empty),
        "total_candles_received": len(all_rows),
        "daily_tests": sum(
            1 for x in successful
            if x["interval"] == "CANDLE_INTERVAL_DAY"
        ),
        "five_minute_tests": sum(
            1 for x in successful
            if x["interval"] == "CANDLE_INTERVAL_5_MIN"
        ),
    }

    for failure in failed:
        report["errors"].append({
            "ticker": failure["ticker"],
            "test": failure["test"],
            "error": failure["error"],
        })

    json_path = OUT / (
        f"historical_coverage_{timestamp}.json"
    )

    csv_path = OUT / (
        f"historical_coverage_candles_{timestamp}.csv"
    )

    summary_path = OUT / (
        f"historical_coverage_summary_{timestamp}.json"
    )

    json_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    fields = [
        "ticker",
        "uid",
        "test",
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
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(all_rows)

    summary_path.write_text(
        json.dumps(
            report["summary"],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log("\n" + "=" * 68)
    log("HISTORICAL COVERAGE PROBE FINISHED")
    log("=" * 68)

    log(
        "TOTAL TESTS: "
        + str(report["summary"]["total_tests"])
    )

    log(
        "SUCCESSFUL: "
        + str(report["summary"]["successful_tests"])
    )

    log(
        "FAILED: "
        + str(report["summary"]["failed_tests"])
    )

    log(
        "EMPTY: "
        + str(report["summary"]["empty_tests"])
    )

    log(
        "TOTAL CANDLES: "
        + str(report["summary"]["total_candles_received"])
    )

    log("\nREPORT:")
    log(str(json_path))

    log("\nCANDLES:")
    log(str(csv_path))

    log("\nSUMMARY:")
    log(str(summary_path))

    log("\nTOKEN VALUE: NOT SAVED")
    log("NO TRADING OPERATIONS")
    log("=" * 68)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("\nFATAL ERROR: " + str(exc))
        log("NO TRADING OPERATIONS WERE PERFORMED.")
        raise
