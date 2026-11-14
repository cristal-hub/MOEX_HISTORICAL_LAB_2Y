
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# DAILY UNIVERSE HISTORICAL DOWNLOADER
#
# RESEARCH ONLY
# READ ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Uses ONLY TINKOFF_READONLY_TOKEN.
# Downloads 1D history for the current tradeable TQBR/RUB universe.
# Resumable by instrument and time chunk.
# Existing candle values are preserved.
# Atomic CSV and manifest writes.
# ============================================================

import csv
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from importlib.metadata import version

ROOT = Path(r"C:\Users\Cristal\Desktop\MOEX_HISTORICAL_LAB_2Y")
ENV_FILE = Path(r"C:\Users\Cristal\Desktop\Tsclp_v2.1_volatile\.env")

REGISTRY_DIR = ROOT / "results" / "instrument_registry"
DATA_DIR = ROOT / "data" / "daily"
STATE_DIR = ROOT / "results" / "daily_download"
MANIFEST_PATH = STATE_DIR / "download_manifest.json"

START_DATE = datetime(2024, 11, 14, tzinfo=timezone.utc)

# Smaller chunks make recovery safer.
CHUNK_DAYS = 180

API_PAUSE_SEC = 0.35
MAX_RETRIES = 5
RETRY_DELAYS = [1, 2, 4, 8, 16]

FIELDS = [
    "time", "open", "high", "low", "close",
    "volume", "is_complete"
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


def load_registry():
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

    payload = json.loads(
        latest.read_text(encoding="utf-8")
    )

    records = payload.get("instruments", [])

    # Current preliminary universe:
    # TQBR + RUB + API + BUY + SELL.
    eligible = [
        x for x in records
        if x.get("is_tradeable_candidate") is True
        and x.get("uid")
    ]

    # Defensive UID deduplication.
    unique = {}
    for item in eligible:
        unique.setdefault(item["uid"], item)

    result = sorted(
        unique.values(),
        key=lambda x: str(x.get("ticker") or "")
    )

    if not result:
        raise RuntimeError(
            "No eligible instruments in registry"
        )

    return latest, result


def atomic_write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def save_json(path, payload):
    atomic_write_text(
        path,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        )
    )


def safe_filename(value):
    value = str(value or "UNKNOWN")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def candle_price(value):
    if value is None:
        return None

    return (
        float(getattr(value, "units", 0))
        + float(getattr(value, "nano", 0)) / 1e9
    )


def candle_to_row(candle):
    return {
        "time": candle.time.isoformat(),
        "open": candle_price(candle.open),
        "high": candle_price(candle.high),
        "low": candle_price(candle.low),
        "close": candle_price(candle.close),
        "volume": int(candle.volume),
        "is_complete": bool(
            getattr(candle, "is_complete", False)
        ),
    }


def validate_row(row):
    try:
        o = float(row["open"])
        h = float(row["high"])
        low = float(row["low"])
        c = float(row["close"])
        v = int(row["volume"])

        if min(o, h, low, c) <= 0:
            return False

        if h < max(o, low, c):
            return False

        if low > min(o, h, c):
            return False

        if v < 0:
            return False

        datetime.fromisoformat(row["time"])
        return True

    except Exception:
        return False


def load_existing_csv(path):
    if not path.exists():
        return {}

    rows = {}

    try:
        with path.open(
            "r",
            newline="",
            encoding="utf-8-sig"
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:
                if not all(field in row for field in FIELDS):
                    raise ValueError("Unexpected CSV columns")

                if not validate_row(row):
                    continue

                timestamp = row["time"]

                rows[timestamp] = {
                    "time": timestamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "is_complete": str(
                        row["is_complete"]
                    ).lower() == "true",
                }

        return rows

    except Exception as exc:
        backup = path.with_name(
            path.name + ".corrupt_" +
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        )

        path.replace(backup)

        log(
            "WARNING: existing CSV moved to backup: "
            + str(backup)
        )
        log("CSV READ ERROR: " + str(exc))

        return {}


def save_candles_csv(path, rows):
    ordered = [
        rows[key]
        for key in sorted(rows)
    ]

    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")

    with tmp.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=FIELDS
        )

        writer.writeheader()
        writer.writerows(ordered)

    tmp.replace(path)


def build_chunks(start, end):
    chunks = []
    cursor = start
    index = 1

    while cursor < end:
        next_cursor = min(
            cursor + timedelta(days=CHUNK_DAYS),
            end
        )

        chunks.append({
            "id": f"{index:03d}",
            "from": cursor.isoformat(),
            "to": next_cursor.isoformat(),
        })

        cursor = next_cursor
        index += 1

    return chunks


def load_manifest():
    if not MANIFEST_PATH.exists():
        return {
            "schema_version": 1,
            "created_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "mode": "READ_ONLY",
            "live_orders": False,
            "real_money": False,
            "broker_execution": False,
            "completed_chunks": {},
            "failed_chunks": {},
        }

    data = json.loads(
        MANIFEST_PATH.read_text(encoding="utf-8")
    )

    if data.get("schema_version") != 1:
        raise RuntimeError(
            "Unsupported manifest schema version"
        )

    return data


def request_chunk(
    client,
    uid,
    date_from,
    date_to,
    interval
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
                candle_to_row(c)
                for c in response.candles
            ], None

        except Exception as exc:
            last_error = str(exc)

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[
                    min(attempt - 1, len(RETRY_DELAYS) - 1)
                ]

                log(
                    f"    RETRY {attempt}/{MAX_RETRIES}, "
                    f"WAIT {delay}s"
                )

                time.sleep(delay)

    return [], last_error


def chunk_key(uid, chunk_id):
    return f"{uid}:{chunk_id}"


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 68)
    log("DAILY UNIVERSE HISTORICAL DOWNLOADER")
    log("READ ONLY / NO TRADING")
    log("=" * 68)

    registry_path, instruments = load_registry()

    end_date = datetime.now(timezone.utc)
    chunks = build_chunks(START_DATE, end_date)

    manifest = load_manifest()

    manifest["registry_file"] = str(registry_path)
    manifest["period_start"] = START_DATE.isoformat()
    manifest["period_end"] = end_date.isoformat()
    manifest["chunk_days"] = CHUNK_DAYS
    manifest["universe_size"] = len(instruments)
    manifest["updated_utc"] = datetime.now(
        timezone.utc
    ).isoformat()

    log("REGISTRY: " + str(registry_path))
    log("INSTRUMENTS: " + str(len(instruments)))
    log("PERIOD START: " + START_DATE.isoformat())
    log("PERIOD END: " + end_date.isoformat())
    log("CHUNK DAYS: " + str(CHUNK_DAYS))
    log("CHUNKS PER INSTRUMENT: " + str(len(chunks)))
    log("TOKEN VARIABLE: TINKOFF_READONLY_TOKEN")
    log("TOKEN VALUE: HIDDEN")

    from t_tech.invest import Client, CandleInterval

    token = read_token(ENV_FILE)
    sdk_version = version("t-tech-investments")

    manifest["sdk_version"] = sdk_version

    total_new = 0
    total_existing = 0
    total_failed = 0
    total_skipped = 0

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
        "period_start": START_DATE.isoformat(),
        "period_end": end_date.isoformat(),
        "instruments": [],
        "errors": [],
    }

    log("\nCONNECTING TO T-INVEST API...")

    with Client(token) as client:

        log("AUTHORIZATION: OK")

        for instrument_index, instrument in enumerate(
            instruments,
            start=1
        ):

            ticker = str(
                instrument.get("ticker") or "UNKNOWN"
            ).upper()

            uid = instrument["uid"]

            filename = (
                safe_filename(ticker)
                + "__"
                + safe_filename(uid)
                + ".csv"
            )

            csv_path = DATA_DIR / filename

            log("\n" + "-" * 60)
            log(
                f"INSTRUMENT {instrument_index}/"
                f"{len(instruments)}: {ticker}"
            )

            rows = load_existing_csv(csv_path)

            existing_before = len(rows)
            instrument_new = 0
            instrument_failed = 0
            instrument_skipped = 0

            instrument_report = {
                "ticker": ticker,
                "name": instrument.get("name"),
                "uid": uid,
                "figi": instrument.get("figi"),
                "csv_file": str(csv_path),
                "existing_candles_before": existing_before,
                "chunks_total": len(chunks),
                "chunks_downloaded": 0,
                "chunks_skipped": 0,
                "chunks_failed": 0,
            }

            for chunk in chunks:

                key = chunk_key(uid, chunk["id"])

                if key in manifest["completed_chunks"]:
                    instrument_skipped += 1
                    total_skipped += 1
                    continue

                date_from = datetime.fromisoformat(
                    chunk["from"]
                )

                date_to = datetime.fromisoformat(
                    chunk["to"]
                )

                log(
                    f"  CHUNK {chunk['id']}/{len(chunks)} "
                    f"{date_from.date()} -> {date_to.date()}"
                )

                candles, error = request_chunk(
                    client,
                    uid,
                    date_from,
                    date_to,
                    CandleInterval.CANDLE_INTERVAL_DAY,
                )

                if error:
                    instrument_failed += 1
                    total_failed += 1

                    manifest["failed_chunks"][key] = {
                        "ticker": ticker,
                        "from": chunk["from"],
                        "to": chunk["to"],
                        "error": error,
                        "failed_utc": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }

                    report["errors"].append({
                        "ticker": ticker,
                        "uid": uid,
                        "chunk": chunk["id"],
                        "error": error,
                    })

                    log("    ERROR: " + error[:500])

                else:
                    added = 0

                    # Existing timestamp values are preserved.
                    for candle in candles:
                        timestamp = candle["time"]

                        if timestamp not in rows:
                            rows[timestamp] = candle
                            added += 1

                    instrument_new += added
                    total_new += added

                    # Save data before marking the chunk complete.
                    save_candles_csv(csv_path, rows)

                    manifest["completed_chunks"][key] = {
                        "ticker": ticker,
                        "from": chunk["from"],
                        "to": chunk["to"],
                        "candles_received": len(candles),
                        "new_candles_added": added,
                        "completed_utc": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }

                    manifest["failed_chunks"].pop(
                        key,
                        None
                    )

                    instrument_report["chunks_downloaded"] += 1

                    log(
                        f"    RECEIVED: {len(candles)}"
                        f" | NEW: {added}"
                        f" | TOTAL STORED: {len(rows)}"
                    )

                manifest["updated_utc"] = datetime.now(
                    timezone.utc
                ).isoformat()

                save_json(MANIFEST_PATH, manifest)

                time.sleep(API_PAUSE_SEC)

            instrument_report["chunks_skipped"] = (
                instrument_skipped
            )

            instrument_report["chunks_failed"] = (
                instrument_failed
            )

            instrument_report["new_candles_added"] = (
                instrument_new
            )

            instrument_report["total_candles_stored"] = (
                len(rows)
            )

            instrument_report["first_candle"] = (
                min(rows) if rows else None
            )

            instrument_report["last_candle"] = (
                max(rows) if rows else None
            )

            report["instruments"].append(
                instrument_report
            )

            total_existing += existing_before

            log(
                f"INSTRUMENT FINISHED: {ticker}"
                f" | STORED: {len(rows)}"
                f" | NEW: {instrument_new}"
                f" | FAILED CHUNKS: {instrument_failed}"
            )

    report["summary"] = {
        "instruments_total": len(instruments),
        "chunks_per_instrument": len(chunks),
        "chunks_total": len(instruments) * len(chunks),
        "new_candles_added": total_new,
        "existing_candles_before_run": total_existing,
        "chunks_skipped_from_manifest": total_skipped,
        "failed_chunks_this_run": total_failed,
        "completed_chunks_total": len(
            manifest["completed_chunks"]
        ),
        "failed_chunks_in_manifest": len(
            manifest["failed_chunks"]
        ),
    }

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    report_path = STATE_DIR / (
        f"daily_download_report_{timestamp}.json"
    )

    save_json(report_path, report)
    save_json(MANIFEST_PATH, manifest)

    log("\n" + "=" * 68)
    log("DAILY DOWNLOAD FINISHED")
    log("=" * 68)

    log("INSTRUMENTS: " + str(len(instruments)))
    log("NEW CANDLES: " + str(total_new))
    log("SKIPPED CHUNKS: " + str(total_skipped))
    log("FAILED CHUNKS: " + str(total_failed))
    log(
        "COMPLETED CHUNKS TOTAL: "
        + str(len(manifest["completed_chunks"]))
    )
    log(
        "FAILED CHUNKS IN MANIFEST: "
        + str(len(manifest["failed_chunks"]))
    )

    log("\nDATA DIRECTORY:")
    log(str(DATA_DIR))

    log("\nMANIFEST:")
    log(str(MANIFEST_PATH))

    log("\nREPORT:")
    log(str(report_path))

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
