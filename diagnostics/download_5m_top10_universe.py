
# ============================================================
# MOEX_HISTORICAL_LAB_2Y
# HISTORICAL 5M DOWNLOADER FOR HISTORICAL TOP-10 UNIVERSE
#
# RESEARCH ONLY
# READ ONLY
# NO LIVE ORDERS
# NO REAL MONEY
# NO BROKER EXECUTION
#
# Downloads 5M candles only for instruments that appeared
# in historical TOP-10 at least once.
#
# Uses ONLY TINKOFF_READONLY_TOKEN.
# Resumable by instrument and weekly chunk.
# Existing candle data is preserved.
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

TOP10_DIR = ROOT / "results" / "historical_top10"
DATA_DIR = ROOT / "data" / "five_minute"
CHUNKS_DIR = DATA_DIR / "_chunks"
STATE_DIR = ROOT / "results" / "five_minute_download"

MANIFEST_PATH = STATE_DIR / "download_manifest.json"

START_DATE = datetime(2024, 11, 14, tzinfo=timezone.utc)
END_DATE = datetime(2026, 11, 14, tzinfo=timezone.utc)

CHUNK_DAYS = 7
API_PAUSE_SEC = 0.5
MAX_RETRIES = 5
RETRY_DELAYS = [1, 2, 4, 8, 16]

FIELDS = [
    "time", "open", "high", "low",
    "close", "volume", "is_complete"
]


def log(message):
    print(message, flush=True)


def read_token(path):
    if not path.exists():
        raise FileNotFoundError(".env file not found")

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()

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
            raise RuntimeError("TINKOFF_READONLY_TOKEN is empty")

        return value

    raise RuntimeError("TINKOFF_READONLY_TOKEN not found")


def safe_filename(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "UNKNOWN"))


def latest_top10():
    files = list(TOP10_DIR.glob("historical_top10_*.csv"))

    if not files:
        raise FileNotFoundError("Historical TOP-10 CSV not found")

    return max(files, key=lambda p: p.stat().st_mtime)


def load_top10_universe(path):
    instruments = {}

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        required = {"ticker", "uid", "figi"}

        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("Unexpected TOP-10 CSV columns")

        for row in reader:
            uid = (row.get("uid") or "").strip()
            ticker = (row.get("ticker") or "").strip()

            if not uid or not ticker:
                continue

            instruments.setdefault(uid, {
                "uid": uid,
                "ticker": ticker,
                "figi": row.get("figi"),
                "name": row.get("name"),
            })

    result = sorted(
        instruments.values(),
        key=lambda x: x["ticker"]
    )

    if not result:
        raise RuntimeError("TOP-10 universe is empty")

    return result


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    tmp.replace(path)


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


def save_chunk(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")

    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    tmp.replace(path)


def load_chunk(path):
    if not path.exists():
        return []

    rows = []

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not set(FIELDS).issubset(set(reader.fieldnames or [])):
            raise ValueError(f"Invalid chunk CSV: {path}")

        for row in reader:
            if validate_row(row):
                rows.append({
                    "time": row["time"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "is_complete": str(
                        row["is_complete"]
                    ).lower() == "true",
                })

    return rows


def save_final_csv(path, rows):
    unique = {}

    for row in rows:
        unique.setdefault(row["time"], row)

    ordered = [
        unique[key]
        for key in sorted(unique)
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)

    tmp.replace(path)

    return len(ordered)


def build_chunks():
    chunks = []
    cursor = START_DATE
    index = 1

    while cursor < END_DATE:
        next_cursor = min(
            cursor + timedelta(days=CHUNK_DAYS),
            END_DATE
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
            "completed_chunks": {},
            "failed_chunks": {},
            "live_orders": False,
            "real_money": False,
            "broker_execution": False,
        }

    data = json.loads(
        MANIFEST_PATH.read_text(encoding="utf-8")
    )

    if data.get("schema_version") != 1:
        raise RuntimeError("Unsupported manifest version")

    return data


def request_chunk(client, uid, date_from, date_to, interval):
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

                log(f"    RETRY {attempt}/{MAX_RETRIES}, WAIT {delay}s")
                time.sleep(delay)

    return [], last_error


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 68)
    log("HISTORICAL 5M TOP-10 UNIVERSE DOWNLOADER")
    log("READ ONLY / NO TRADING")
    log("=" * 68)

    top10_path = latest_top10()
    instruments = load_top10_universe(top10_path)
    chunks = build_chunks()
    manifest = load_manifest()

    log(f"TOP-10 SOURCE: {top10_path}")
    log(f"UNIQUE INSTRUMENTS: {len(instruments)}")
    log(f"PERIOD: {START_DATE.date()} -> {END_DATE.date()}")
    log(f"CHUNK SIZE: {CHUNK_DAYS} DAYS")
    log(f"CHUNKS PER INSTRUMENT: {len(chunks)}")
    log(f"TOTAL CHUNKS: {len(instruments) * len(chunks)}")
    log("TOKEN VARIABLE: TINKOFF_READONLY_TOKEN")
    log("TOKEN VALUE: HIDDEN")

    from t_tech.invest import Client, CandleInterval

    token = read_token(ENV_FILE)
    sdk_version = version("t-tech-investments")

    manifest["top10_source"] = str(top10_path)
    manifest["period_start"] = START_DATE.isoformat()
    manifest["period_end"] = END_DATE.isoformat()
    manifest["chunk_days"] = CHUNK_DAYS
    manifest["universe_size"] = len(instruments)
    manifest["sdk_version"] = sdk_version

    total_new = 0
    total_skipped = 0
    total_failed = 0
    total_received = 0
    total_completed = 0

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sdk_version": sdk_version,
        "top10_source": str(top10_path),
        "mode": "READ_ONLY",
        "research_only": True,
        "live_orders": False,
        "real_money": False,
        "broker_execution": False,
        "token_value_saved": False,
        "period_start": START_DATE.isoformat(),
        "period_end": END_DATE.isoformat(),
        "chunk_days": CHUNK_DAYS,
        "instruments": [],
        "errors": [],
    }

    log("\nCONNECTING TO T-INVEST API...")

    with Client(token) as client:
        log("AUTHORIZATION: OK")

        for instrument_index, instrument in enumerate(
            instruments, 1
        ):
            ticker = instrument["ticker"]
            uid = instrument["uid"]

            safe_ticker = safe_filename(ticker)
            safe_uid = safe_filename(uid)

            chunk_dir = CHUNKS_DIR / f"{safe_ticker}__{safe_uid}"
            final_path = DATA_DIR / f"{safe_ticker}__{safe_uid}.csv"

            log("\n" + "-" * 60)
            log(
                f"INSTRUMENT {instrument_index}/{len(instruments)}: "
                f"{ticker}"
            )

            # If a final file exists and all chunks are complete,
            # this instrument needs no further work.
            all_keys = [
                f"{uid}:{chunk['id']}"
                for chunk in chunks
            ]

            if (
                final_path.exists()
                and all(key in manifest["completed_chunks"] for key in all_keys)
            ):
                total_skipped += len(chunks)
                total_completed += len(chunks)
                log("FINAL FILE EXISTS; ALL CHUNKS COMPLETE. SKIPPED.")
                continue

            instrument_failed = 0
            instrument_new = 0
            instrument_skipped = 0
            instrument_received = 0

            for chunk in chunks:
                key = f"{uid}:{chunk['id']}"
                chunk_path = chunk_dir / f"{chunk['id']}.csv"

                if (
                    key in manifest["completed_chunks"]
                    and chunk_path.exists()
                ):
                    instrument_skipped += 1
                    total_skipped += 1
                    total_completed += 1
                    continue

                date_from = datetime.fromisoformat(chunk["from"])
                date_to = datetime.fromisoformat(chunk["to"])

                log(
                    f"  CHUNK {chunk['id']}/{len(chunks)} "
                    f"{date_from.date()} -> {date_to.date()}"
                )

                candles, error = request_chunk(
                    client,
                    uid,
                    date_from,
                    date_to,
                    CandleInterval.CANDLE_INTERVAL_5_MIN,
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

                    log("    ERROR: " + error[:400])

                else:
                    # Keep every returned candle, including
                    # off-session and zero-volume candles.
                    candles.sort(key=lambda x: x["time"])
                    unique = {}

                    for candle in candles:
                        if validate_row(candle):
                            unique.setdefault(candle["time"], candle)

                    clean = list(unique.values())

                    save_chunk(chunk_path, clean)

                    added = len(clean)
                    instrument_new += added
                    instrument_received += len(candles)
                    total_new += added
                    total_received += len(candles)
                    total_completed += 1

                    manifest["completed_chunks"][key] = {
                        "ticker": ticker,
                        "from": chunk["from"],
                        "to": chunk["to"],
                        "candles_received": len(candles),
                        "valid_unique_candles": added,
                        "completed_utc": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }

                    manifest["failed_chunks"].pop(key, None)

                    log(
                        f"    RECEIVED: {len(candles)} "
                        f"| VALID UNIQUE: {added}"
                    )

                manifest["updated_utc"] = datetime.now(
                    timezone.utc
                ).isoformat()

                atomic_json(MANIFEST_PATH, manifest)
                time.sleep(API_PAUSE_SEC)

            # Consolidate only when every chunk for this instrument
            # has completed successfully.
            remaining = [
                key for key in all_keys
                if key not in manifest["completed_chunks"]
            ]

            if not remaining:
                merged = []

                for chunk in chunks:
                    chunk_path = chunk_dir / f"{chunk['id']}.csv"
                    merged.extend(load_chunk(chunk_path))

                final_count = save_final_csv(final_path, merged)

                # Final file is safely written; chunk files can go.
                for chunk in chunks:
                    chunk_path = chunk_dir / f"{chunk['id']}.csv"
                    if chunk_path.exists():
                        chunk_path.unlink()

                try:
                    chunk_dir.rmdir()
                except OSError:
                    pass

                log(f"FINAL CSV SAVED: {final_count} UNIQUE CANDLES")
            else:
                final_count = 0
                log(f"INSTRUMENT INCOMPLETE: {len(remaining)} CHUNKS REMAIN")

            report["instruments"].append({
                "ticker": ticker,
                "uid": uid,
                "figi": instrument.get("figi"),
                "chunks_total": len(chunks),
                "chunks_skipped": instrument_skipped,
                "chunks_failed_this_run": instrument_failed,
                "new_valid_candles_this_run": instrument_new,
                "candles_received_this_run": instrument_received,
                "final_csv": str(final_path),
                "final_candle_count": final_count,
                "complete": len(remaining) == 0,
            })

    report["summary"] = {
        "instruments_total": len(instruments),
        "chunks_per_instrument": len(chunks),
        "chunks_total": len(instruments) * len(chunks),
        "new_valid_candles_this_run": total_new,
        "candles_received_this_run": total_received,
        "chunks_skipped_this_run": total_skipped,
        "chunks_failed_this_run": total_failed,
        "completed_chunks_in_manifest": len(
            manifest["completed_chunks"]
        ),
        "failed_chunks_in_manifest": len(
            manifest["failed_chunks"]
        ),
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = STATE_DIR / f"five_minute_download_report_{timestamp}.json"

    atomic_json(report_path, report)
    atomic_json(MANIFEST_PATH, manifest)

    log("\n" + "=" * 68)
    log("5M DOWNLOAD FINISHED")
    log("=" * 68)
    log(f"INSTRUMENTS: {len(instruments)}")
    log(f"NEW VALID CANDLES: {total_new}")
    log(f"CANDLES RECEIVED: {total_received}")
    log(f"SKIPPED CHUNKS: {total_skipped}")
    log(f"FAILED CHUNKS THIS RUN: {total_failed}")
    log(f"COMPLETED CHUNKS IN MANIFEST: {len(manifest['completed_chunks'])}")
    log(f"FAILED CHUNKS IN MANIFEST: {len(manifest['failed_chunks'])}")
    log(f"DATA DIRECTORY: {DATA_DIR}")
    log(f"MANIFEST: {MANIFEST_PATH}")
    log(f"REPORT: {report_path}")
    log("TOKEN VALUE: NOT SAVED")
    log("NO TRADING OPERATIONS")
    log("=" * 68)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("\nFATAL ERROR: " + str(exc))
        log("NO TRADING OPERATIONS WERE PERFORMED.")
        raise
