
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# BUILD INSTRUMENT REGISTRY
#
# RESEARCH ONLY
# READ ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Uses ONLY TINKOFF_READONLY_TOKEN.
# Preserves ALL instruments returned by T-Invest.
# Does not modify existing files.
# ============================================================

import csv
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from importlib.metadata import version

ROOT = Path(
    r"C:\Users\Cristal\Desktop\MOEX_HISTORICAL_LAB_2Y"
)

ENV_FILE = Path(
    r"C:\Users\Cristal\Desktop\Tsclp_v2.1_volatile\.env"
)

OUT = ROOT / "results" / "instrument_registry"

PAUSE = 0.5

FIELDS = [
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
    "short_enabled_flag",
    "klong",
    "kshort",
    "dlong",
    "dshort",
    "dlong_min",
    "dshort_min",
    "min_price_increment",
    "nominal",
    "country_of_risk",
    "country_of_risk_name",
    "sector",
    "brand",
]


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


def serialize(value):
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    if hasattr(value, "name"):
        return str(value.name)

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


def get_field(instrument, field):
    value = getattr(instrument, field, None)
    return serialize(value)


def to_float(value):
    if value is None:
        return None

    units = getattr(value, "units", None)
    nano = getattr(value, "nano", None)

    if units is not None:
        return float(units) + float(nano or 0) / 1e9

    try:
        return float(value)
    except (TypeError, ValueError):
        return serialize(value)


def instrument_record(instrument):
    record = {}

    for field in FIELDS:
        value = getattr(instrument, field, None)

        if field in (
            "klong",
            "kshort",
            "dlong",
            "dshort",
            "dlong_min",
            "dshort_min",
            "min_price_increment",
            "nominal",
        ):
            value = to_float(value)
        else:
            value = serialize(value)

        record[field] = value

    class_code = str(
        record.get("class_code") or ""
    ).upper()

    currency = str(
        record.get("currency") or ""
    ).lower()

    api_available = (
        record.get("api_trade_available_flag") is True
    )

    buy_available = (
        record.get("buy_available_flag") is True
    )

    sell_available = (
        record.get("sell_available_flag") is True
    )

    trading_status = str(
        record.get("trading_status") or ""
    ).upper()

    # Preliminary classification only.
    # Nothing is deleted from the full registry.

    is_tqbr = class_code == "TQBR"
    is_rub = currency == "rub"

    is_moex_candidate = is_tqbr and is_rub

    is_tradeable_candidate = (
        is_moex_candidate
        and api_available
        and buy_available
        and sell_available
    )

    record["is_tqbr"] = is_tqbr
    record["is_rub"] = is_rub
    record["is_moex_candidate"] = is_moex_candidate
    record["is_tradeable_candidate"] = (
        is_tradeable_candidate
    )

    record["candidate_reasons"] = []

    if is_tqbr:
        record["candidate_reasons"].append(
            "CLASS_CODE_TQBR"
        )

    if is_rub:
        record["candidate_reasons"].append(
            "CURRENCY_RUB"
        )

    if api_available:
        record["candidate_reasons"].append(
            "API_TRADE_AVAILABLE"
        )

    if buy_available:
        record["candidate_reasons"].append(
            "BUY_AVAILABLE"
        )

    if sell_available:
        record["candidate_reasons"].append(
            "SELL_AVAILABLE"
        )

    if trading_status:
        record["candidate_reasons"].append(
            "STATUS_" + trading_status
        )

    return record


def unique_output_path(timestamp, suffix):
    return OUT / (
        f"instrument_registry_{timestamp}.{suffix}"
    )


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    log("=" * 65)
    log("MOEX INSTRUMENT REGISTRY BUILDER")
    log("READ ONLY / NO TRADING")
    log("=" * 65)

    sdk_version = version("t-tech-investments")
    token = read_token(ENV_FILE)

    log("SDK VERSION: " + sdk_version)
    log("TOKEN VARIABLE: TINKOFF_READONLY_TOKEN")
    log("TOKEN VALUE: HIDDEN")

    from t_tech.invest import Client

    created = datetime.now(timezone.utc)
    timestamp = created.strftime("%Y%m%d_%H%M%S")

    log("\nCONNECTING TO T-INVEST API...")

    with Client(token) as client:

        log("REQUESTING COMPLETE SHARE CATALOG...")

        response = client.instruments.shares()
        shares = response.instruments

        log(
            "SHARES RECEIVED: "
            + str(len(shares))
        )

        time.sleep(PAUSE)

        records = []

        for index, instrument in enumerate(
            shares, start=1
        ):

            record = instrument_record(instrument)
            records.append(record)

            if index % 250 == 0:
                log(
                    f"PROCESSED: {index}/{len(shares)}"
                )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    class_counts = Counter(
        str(x.get("class_code") or "EMPTY")
        for x in records
    )

    currency_counts = Counter(
        str(x.get("currency") or "EMPTY").lower()
        for x in records
    )

    status_counts = Counter(
        str(x.get("trading_status") or "EMPTY")
        for x in records
    )

    candidate_records = [
        x for x in records
        if x["is_moex_candidate"]
    ]

    tradeable_records = [
        x for x in records
        if x["is_tradeable_candidate"]
    ]

    ticker_counts = Counter(
        str(x.get("ticker") or "").upper()
        for x in records
        if x.get("ticker")
    )

    uid_counts = Counter(
        str(x.get("uid") or "")
        for x in records
        if x.get("uid")
    )

    duplicate_tickers = {
        ticker: count
        for ticker, count in ticker_counts.items()
        if count > 1
    }

    duplicate_uids = {
        uid: count
        for uid, count in uid_counts.items()
        if count > 1
    }

    summary = {
        "created_utc": created.isoformat(),
        "sdk_version": sdk_version,
        "mode": "READ_ONLY",
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "token_value_saved": False,

        "total_shares": len(records),

        "preliminary_moex_candidates": len(
            candidate_records
        ),

        "preliminary_tradeable_candidates": len(
            tradeable_records
        ),

        "unique_tickers": len(ticker_counts),
        "unique_uids": len(uid_counts),

        "duplicate_tickers": duplicate_tickers,
        "duplicate_uids": duplicate_uids,

        "class_code_distribution": dict(
            class_counts.most_common()
        ),

        "currency_distribution": dict(
            currency_counts.most_common()
        ),

        "trading_status_distribution": dict(
            status_counts.most_common()
        ),

        "classification_rule": (
            "PRELIMINARY: class_code == TQBR "
            "AND currency == RUB"
        ),

        "tradeable_rule": (
            "PRELIMINARY MOEX candidate AND "
            "api_trade_available_flag AND "
            "buy_available_flag AND "
            "sell_available_flag"
        ),

        "note": (
            "All API share records are preserved. "
            "Classification is preliminary and "
            "does not remove any instrument."
        ),
    }

    # --------------------------------------------------------
    # Save files without overwriting previous runs
    # --------------------------------------------------------

    json_path = unique_output_path(
        timestamp, "json"
    )

    csv_path = unique_output_path(
        timestamp, "csv"
    )

    summary_path = unique_output_path(
        timestamp, "summary.json"
    )

    payload = {
        "summary": summary,
        "instruments": records,
    }

    json_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_fields = (
        FIELDS
        + [
            "is_tqbr",
            "is_rub",
            "is_moex_candidate",
            "is_tradeable_candidate",
            "candidate_reasons",
        ]
    )

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=csv_fields,
            extrasaction="ignore",
        )

        writer.writeheader()

        for record in records:
            row = dict(record)

            row["candidate_reasons"] = ";".join(
                row.get("candidate_reasons") or []
            )

            writer.writerow(row)

    summary_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # Console report
    # --------------------------------------------------------

    log("\n" + "=" * 65)
    log("REGISTRY BUILD FINISHED")
    log("=" * 65)

    log("TOTAL SHARES: " + str(len(records)))

    log(
        "PRELIMINARY TQBR/RUB CANDIDATES: "
        + str(len(candidate_records))
    )

    log(
        "PRELIMINARY TRADEABLE CANDIDATES: "
        + str(len(tradeable_records))
    )

    log(
        "UNIQUE TICKERS: "
        + str(len(ticker_counts))
    )

    log(
        "UNIQUE UIDS: "
        + str(len(uid_counts))
    )

    log(
        "DUPLICATE TICKERS: "
        + str(len(duplicate_tickers))
    )

    log(
        "DUPLICATE UIDS: "
        + str(len(duplicate_uids))
    )

    log("\nTOP CLASS CODES:")

    for key, value in class_counts.most_common(15):
        log(f"  {key}: {value}")

    log("\nCURRENCIES:")

    for key, value in currency_counts.most_common():
        log(f"  {key}: {value}")

    log("\nTRADING STATUSES:")

    for key, value in status_counts.most_common(15):
        log(f"  {key}: {value}")

    log("\nFILES CREATED:")
    log(str(json_path))
    log(str(csv_path))
    log(str(summary_path))

    log("\nTOKEN VALUE: NOT SAVED")
    log("ALL INSTRUMENTS PRESERVED")
    log("NO TRADING OPERATIONS")
    log("=" * 65)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("\nFATAL ERROR: " + str(exc))
        log("NO TRADING OPERATIONS WERE PERFORMED.")
        raise
