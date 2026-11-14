
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# HISTORICAL DYNAMIC TOP-10 BUILDER
# VERSION: ATR14_WILDER_RMA
#
# STRATEGY LOGIC: UNCHANGED
# RESEARCH ONLY
# READ ONLY
# NO THRESHOLD IS OPTIMIZED
# NO FILTER IS INSTALLED
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Historical ranking uses only candles strictly BEFORE
# each selection date. No look-ahead.
# ============================================================

import csv
import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from bisect import bisect_left


ROOT = Path(r"C:\Users\Cristal\Desktop\MOEX_HISTORICAL_LAB_2Y")

REGISTRY_DIR = ROOT / "results" / "instrument_registry"
DATA_DIR = ROOT / "data" / "daily"
DOWNLOAD_DIR = ROOT / "results" / "daily_download"
OUT_DIR = ROOT / "results" / "historical_top10"

PERIOD_DAYS = 30
ATR_PERIOD = 14
TURNOVER_PERIOD = 20
MIN_CANDLES = 29
MIN_DAILY_TURNOVER_RUB = 10_000_000
TOP_N = 10

WINDOW_MODE = "B_STRICT_PREVIOUS_30D"
ATR_METHOD = "WILDER_RMA_ALPHA_1_OVER_14_ADJUST_TRUE"

FIELDS = [
    "selection_date", "rank", "ticker", "name", "uid", "figi",
    "volatility_pct", "atr14", "last_close",
    "avg_turnover_rub", "candles_in_window",
    "window_start", "window_end_exclusive"
]


def log(message):
    print(message, flush=True)


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp.replace(path)


def save_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)

    tmp.replace(path)


def latest_registry():
    files = [
        p for p in REGISTRY_DIR.glob("instrument_registry_*.json")
        if not p.name.endswith(".summary.json")
    ]

    if not files:
        raise FileNotFoundError("Instrument registry not found")

    path = max(files, key=lambda p: p.stat().st_mtime)
    payload = json.loads(path.read_text(encoding="utf-8"))

    records = payload.get("instruments", [])

    eligible = [
        x for x in records
        if x.get("is_tradeable_candidate") is True
        and x.get("uid")
    ]

    unique = {}
    for item in eligible:
        unique.setdefault(item["uid"], item)

    return path, sorted(
        unique.values(),
        key=lambda x: str(x.get("ticker") or "")
    )


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def load_daily_file(path):
    rows = []
    invalid = 0

    if not path.exists():
        return rows, 0, 0

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        required = {
            "time", "open", "high", "low", "close", "volume"
        }

        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                f"Unexpected CSV columns in {path.name}"
            )

        for raw in reader:
            try:
                dt = parse_time(raw["time"])

                o = float(raw["open"])
                h = float(raw["high"])
                low = float(raw["low"])
                c = float(raw["close"])
                v = int(raw["volume"])

                if min(o, h, low, c) <= 0:
                    raise ValueError("Non-positive OHLC")

                if h < max(o, low, c):
                    raise ValueError("Invalid high")

                if low > min(o, h, c):
                    raise ValueError("Invalid low")

                if v < 0:
                    raise ValueError("Negative volume")

                rows.append({
                    "time": dt,
                    "date": dt.date(),
                    "open": o,
                    "high": h,
                    "low": low,
                    "close": c,
                    "volume": v,
                })

            except Exception:
                invalid += 1

    # Keep first valid row for duplicate timestamps.
    unique = {}

    for row in sorted(rows, key=lambda x: x["time"]):
        unique.setdefault(row["time"], row)

    result = list(unique.values())

    return result, invalid, len(rows) - len(result)


def calculate_wilder_rma(values, period):
    """
    Wilder RMA / pandas-ta style exponential moving average.

    alpha = 1 / period
    adjust = True
    min_periods = period

    This reproduces the normalized exponentially weighted
    calculation used by pandas-ta's RMA implementation.
    """

    if len(values) < period:
        return None

    alpha = 1.0 / period
    decay = 1.0 - alpha

    weighted_sum = 0.0
    weight_sum = 0.0
    valid_count = 0
    result = None

    for value in values:
        if value is None or not math.isfinite(value):
            continue

        weighted_sum = value + decay * weighted_sum
        weight_sum = 1.0 + decay * weight_sum
        valid_count += 1

        if valid_count >= period and weight_sum > 0:
            result = weighted_sum / weight_sum

    return result


def calculate_metrics(rows, dates, selection_date):
    """
    Calculate metrics from candles in:

        [selection_date - 30 days, selection_date)

    Selection-date candle is strictly excluded.

    Previous close immediately before the window is used
    only as the reference for the first True Range.
    """

    window_start = selection_date - timedelta(days=PERIOD_DAYS)

    start_index = bisect_left(dates, window_start)
    end_index = bisect_left(dates, selection_date)

    window = rows[start_index:end_index]

    if len(window) < MIN_CANDLES:
        return None

    previous_close = (
        rows[start_index - 1]["close"]
        if start_index > 0
        else None
    )

    true_ranges = []

    for row in window:
        if previous_close is None:
            tr = row["high"] - row["low"]
        else:
            tr = max(
                row["high"] - row["low"],
                abs(row["high"] - previous_close),
                abs(row["low"] - previous_close)
            )

        true_ranges.append(tr)
        previous_close = row["close"]

    atr = calculate_wilder_rma(true_ranges, ATR_PERIOD)

    if atr is None:
        return None

    last_close = window[-1]["close"]

    if last_close <= 0 or atr <= 0:
        return None

    turnover_rows = window[-TURNOVER_PERIOD:]

    avg_volume = sum(
        r["volume"] for r in turnover_rows
    ) / len(turnover_rows)

    avg_price = sum(
        r["close"] for r in turnover_rows
    ) / len(turnover_rows)

    avg_turnover = avg_volume * avg_price
    volatility = atr / last_close * 100.0

    if not all(math.isfinite(x) for x in (
        atr, last_close, avg_turnover, volatility
    )):
        return None

    return {
        "selection_date": selection_date.isoformat(),
        "volatility_pct": volatility,
        "atr14": atr,
        "last_close": last_close,
        "avg_turnover_rub": avg_turnover,
        "candles_in_window": len(window),
        "window_start": window_start.isoformat(),
        "window_end_exclusive": selection_date.isoformat(),
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("HISTORICAL DYNAMIC TOP-10 BUILDER")
    log("ATR: WILDER RMA / PANDAS-TA STYLE")
    log("READ ONLY / NO TRADING")
    log("=" * 70)

    registry_path, instruments = latest_registry()

    log(f"REGISTRY: {registry_path}")
    log(f"ELIGIBLE INSTRUMENTS: {len(instruments)}")

    data = {}
    all_dates = set()
    missing_files = []

    invalid_total = 0
    duplicate_total = 0

    for index, instrument in enumerate(instruments, 1):
        ticker = str(instrument.get("ticker") or "UNKNOWN")
        uid = instrument["uid"]

        filename = f"{ticker}__{uid}.csv"
        path = DATA_DIR / filename

        if not path.exists():
            missing_files.append(ticker)
            continue

        rows, invalid, duplicates = load_daily_file(path)

        invalid_total += invalid
        duplicate_total += duplicates

        if rows:
            dates = [r["date"] for r in rows]

            data[uid] = {
                "instrument": instrument,
                "rows": rows,
                "dates": dates,
            }

            all_dates.update(dates)

        if index % 25 == 0 or index == len(instruments):
            log(
                f"LOADED: {index}/{len(instruments)} "
                f"| WITH DATA: {len(data)}"
            )

    if not all_dates:
        raise RuntimeError("No daily candles found")

    selection_dates = sorted(all_dates)

    log(f"SELECTION DATES: {len(selection_dates)}")
    log(f"INSTRUMENTS WITH DATA: {len(data)}")
    log(f"MISSING CSV FILES: {len(missing_files)}")
    log(f"INVALID ROWS SKIPPED: {invalid_total}")
    log(f"DUPLICATE ROWS REMOVED: {duplicate_total}")
    log(f"METHOD: {WINDOW_MODE}")
    log(f"ATR METHOD: {ATR_METHOD}")
    log(f"TURNOVER FLOOR: {MIN_DAILY_TURNOVER_RUB:,.0f} RUB")

    top_rows = []
    metrics_rows = []
    daily_summary = []

    for date_index, selection_date in enumerate(
        selection_dates, 1
    ):
        eligible_metrics = []
        metrics_before_turnover = 0

        for uid, item in data.items():
            instrument = item["instrument"]

            metric = calculate_metrics(
                item["rows"],
                item["dates"],
                selection_date
            )

            if metric is None:
                continue

            metrics_before_turnover += 1

            record = {
                **metric,
                "ticker": instrument.get("ticker"),
                "name": instrument.get("name"),
                "uid": uid,
                "figi": instrument.get("figi"),
            }

            record["passes_turnover_floor"] = (
                metric["avg_turnover_rub"]
                >= MIN_DAILY_TURNOVER_RUB
            )

            metrics_rows.append(record)

            if record["passes_turnover_floor"]:
                eligible_metrics.append(record)

        eligible_metrics.sort(
            key=lambda x: (
                -x["volatility_pct"],
                -x["avg_turnover_rub"],
                str(x["ticker"] or "")
            )
        )

        selected = eligible_metrics[:TOP_N]

        for rank, record in enumerate(selected, 1):
            top_rows.append({
                "selection_date": record["selection_date"],
                "rank": rank,
                "ticker": record["ticker"],
                "name": record["name"],
                "uid": record["uid"],
                "figi": record["figi"],
                "volatility_pct": round(
                    record["volatility_pct"], 8
                ),
                "atr14": round(record["atr14"], 8),
                "last_close": round(record["last_close"], 8),
                "avg_turnover_rub": round(
                    record["avg_turnover_rub"], 2
                ),
                "candles_in_window": record["candles_in_window"],
                "window_start": record["window_start"],
                "window_end_exclusive": (
                    record["window_end_exclusive"]
                ),
            })

        daily_summary.append({
            "selection_date": selection_date.isoformat(),
            "metrics_available": metrics_before_turnover,
            "passed_turnover_floor": len(eligible_metrics),
            "top_count": len(selected),
            "top_tickers": [x["ticker"] for x in selected],
        })

        if date_index % 50 == 0 or date_index == len(selection_dates):
            log(
                f"PROCESSED DATES: {date_index}/"
                f"{len(selection_dates)}"
            )

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )

    top_path = OUT_DIR / f"historical_top10_{timestamp}.csv"
    metrics_path = OUT_DIR / f"historical_metrics_{timestamp}.csv"
    daily_path = OUT_DIR / f"daily_summary_{timestamp}.json"
    report_path = OUT_DIR / f"historical_top10_report_{timestamp}.json"

    save_csv(top_path, top_rows, FIELDS)

    metrics_fields = [
        "selection_date", "ticker", "name", "uid", "figi",
        "volatility_pct", "atr14", "last_close",
        "avg_turnover_rub", "candles_in_window",
        "window_start", "window_end_exclusive",
        "passes_turnover_floor"
    ]

    save_csv(metrics_path, metrics_rows, metrics_fields)
    atomic_json(daily_path, daily_summary)

    manifest_path = DOWNLOAD_DIR / "download_manifest.json"
    failed_chunks = 0

    if manifest_path.exists():
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        failed_chunks = len(manifest.get("failed_chunks", {}))

    selected_uids = {r["uid"] for r in top_rows}

    selected_tickers = sorted({
        r["ticker"] for r in top_rows if r["ticker"]
    })

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "READ_ONLY",
        "research_only": True,
        "strategy_logic": "UNCHANGED",
        "threshold_optimized": False,
        "filter_installed": False,
        "lookahead": False,
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "registry_file": str(registry_path),
        "daily_data_directory": str(DATA_DIR),
        "method": WINDOW_MODE,
        "atr_method": ATR_METHOD,
        "period_days": PERIOD_DAYS,
        "atr_period": ATR_PERIOD,
        "turnover_period": TURNOVER_PERIOD,
        "minimum_candles": MIN_CANDLES,
        "minimum_daily_turnover_rub": MIN_DAILY_TURNOVER_RUB,
        "top_n": TOP_N,
        "selection_date_rule": (
            "Only candles with candle_date < selection_date "
            "are used."
        ),
        "summary": {
            "registry_instruments": len(instruments),
            "instruments_with_data": len(data),
            "missing_csv_files": len(missing_files),
            "selection_dates": len(selection_dates),
            "metrics_rows": len(metrics_rows),
            "top10_rows": len(top_rows),
            "unique_selected_instruments": len(selected_uids),
            "unique_selected_tickers": selected_tickers,
            "invalid_rows_skipped": invalid_total,
            "duplicate_rows_removed": duplicate_total,
            "failed_daily_download_chunks": failed_chunks,
        },
        "missing_files": missing_files,
        "output_files": {
            "top10_csv": str(top_path),
            "all_metrics_csv": str(metrics_path),
            "daily_summary_json": str(daily_path),
        },
    }

    atomic_json(report_path, report)

    log("\n" + "=" * 70)
    log("HISTORICAL TOP-10 BUILD FINISHED")
    log("=" * 70)

    log(f"REGISTRY INSTRUMENTS: {len(instruments)}")
    log(f"INSTRUMENTS WITH DATA: {len(data)}")
    log(f"SELECTION DATES: {len(selection_dates)}")
    log(f"METRICS ROWS: {len(metrics_rows)}")
    log(f"TOP-10 ROWS: {len(top_rows)}")
    log(f"UNIQUE SELECTED INSTRUMENTS: {len(selected_uids)}")
    log(f"MISSING CSV FILES: {len(missing_files)}")
    log(f"FAILED DAILY CHUNKS IN MANIFEST: {failed_chunks}")
    log(f"INVALID ROWS: {invalid_total}")
    log(f"DUPLICATE ROWS: {duplicate_total}")
    log(f"ATR METHOD: {ATR_METHOD}")

    log("\nOUTPUT:")
    log(str(top_path))
    log(str(metrics_path))
    log(str(daily_path))
    log(str(report_path))

    log("\nSTRATEGY LOGIC: UNCHANGED")
    log("RESEARCH ONLY")
    log("NO THRESHOLD IS OPTIMIZED")
    log("NO FILTER IS INSTALLED")
    log("NO LIVE ORDERS")
    log("NO REAL MONEY")
    log("NO BROKER EXECUTION")
    log("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"\nFATAL ERROR: {exc}")
        log("NO TRADING OPERATIONS WERE PERFORMED.")
        raise
