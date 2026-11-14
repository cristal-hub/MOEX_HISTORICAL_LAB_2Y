# research/instruments/build_session_top10.py
# MOEX_HISTORICAL_LAB_2Y — построение исторического динамического TOP-10
# на основе 5M-свечей, агрегированных в торговые сессии.
#
# READ ONLY. RESEARCH ONLY. NO LIVE ORDERS. NO BROKER EXECUTION.
#
# Правила (утверждены):
#   - UTC+3 (Europe/Moscow).
#   - Пн–Пт, торговое время 07:00 <= t < 00:00 следующего дня.
#   - Время свечи — время начала. Свеча 23:55 включается.
#   - Сессия: Open=first, High=max, Low=min, Close=last, Volume=sum.
#   - TOP-10 по предыдущим 30 фактическим торговым сессиям (без текущей).
#   - ATR14 — Wilder RMA.
#   - Ликвидность — mean(close*volume) за последние 20 сессий, >= 10_000_000 RUB.
#   - Сортировка по ATR% desc, при равенстве — ticker asc.
#   - Никакого lookahead. Только инструменты из актуального реестра.

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_DIR = PROJECT_ROOT / "results" / "instrument_registry"
DEFAULT_FIVE_MIN_DIR = PROJECT_ROOT / "data" / "five_minute_session"
DEFAULT_OUT_DIR = PROJECT_ROOT / "results" / "historical_top10"

MSK = ZoneInfo("Europe/Moscow")
HISTORY_START = date(2024, 11, 14)
HISTORY_END = date(2026, 11, 14)

TOP_N = 10
LOOKBACK_SESSIONS = 30
ATR_PERIOD = 14
TURNOVER_WINDOW = 20
MIN_AVG_TURNOVER_RUB = 10_000_000.0

# Торговое окно: 07:00 включительно, 00:00 следующего дня исключительно.
SESSION_START_HOUR = 7
SESSION_END_HOUR = 0  # 00:00 следующего дня (исключительно)


@dataclass
class BuildReport:
    registry_file: str = ""
    five_min_dir: str = ""
    out_dir: str = ""
    instruments_total: int = 0
    instruments_used: int = 0
    sessions_total: int = 0
    session_rows: int = 0
    top10_rows: int = 0
    metrics_rows: int = 0
    dates_with_top10: int = 0
    dates_empty: int = 0
    dates_skipped_warmup: int = 0
    duplicates_5m: int = 0
    unsorted_5m: int = 0
    invalid_ohlc: int = 0
    negative_volume: int = 0
    missing_sessions: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------
# Утилиты
# ------------------------------------------------------------

def _list_five_min_files(five_min_dir: Path) -> list[Path]:
    if not five_min_dir.is_dir():
        return []
    return sorted(five_min_dir.glob("*.csv"))


def _read_registry(registry_file: Path) -> list[dict]:
    with open(registry_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("instruments", "items", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"Реестр {registry_file} не содержит списка инструментов")
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        figi = row.get("figi") or row.get("instrument_uid") or ""
        ticker = row.get("ticker") or ""
        if not figi or not ticker:
            continue
        out.append({
            "figi": str(figi),
            "ticker": str(ticker),
            "name": str(row.get("name", "")),
            "lot": int(row.get("lot", 1) or 1),
            "real_exchange": int(row.get("real_exchange", 0) or 0),
            "currency": str(row.get("currency", "")).lower(),
            "buy_available_flag": bool(row.get("buy_available_flag", False)),
            "sell_available_flag": bool(row.get("sell_available_flag", False)),
            "api_trade_available_flag": bool(row.get("api_trade_available_flag", False)),
        })
    return out


def _find_latest_registry(registry_dir: Path) -> Optional[Path]:
    if not registry_dir.is_dir():
        return None
    files = sorted(registry_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _parse_quotation(val) -> float:
    """T-Invest Quotation может быть dict/obj. Возвращает float или NaN."""
    if val is None:
        return float("nan")
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        units = val.get("units", 0) or 0
        nano = val.get("nano", 0) or 0
        return float(units) + float(nano) / 1e9
    # objects with .units / .nano
    units = getattr(val, "units", None)
    nano = getattr(val, "nano", None)
    if units is None and nano is None:
        return float("nan")
    return float(units or 0) + float(nano or 0) / 1e9


def _read_five_min_csv(path: Path) -> pd.DataFrame:
    """Читает 5M CSV. Колонки: time, open, high, low, close, volume."""
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError(f"{path}: нет колонки 'time'")
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time"])
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"{path}: нет колонки '{col}'")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.sort_values("time").reset_index(drop=True)
    return df


# ------------------------------------------------------------
# Агрегация 5M -> сессия
# ------------------------------------------------------------

def _session_date_for_ts(ts_utc: pd.Timestamp) -> Optional[date]:
    """
    Возвращает торговую дату сессии (MSK) для 5M-свечи.
    Торговое окно: [D 07:00 MSK, D+1 00:00 MSK).
    Свеча 23:55 MSK относится к D.
    """
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.tz_localize("UTC")
    ts_msk = ts_utc.tz_convert(MSK)
    d = ts_msk.date()
    # 07:00 <= time < 00:00+1 => сессия D
    # time >= 07:00 => сессия D
    # time < 07:00 => сессия D-1 (но 00:00-07:00 — вне окна, исключаем)
    if ts_msk.hour >= SESSION_START_HOUR:
        return d
    # 00:00 <= time < 07:00 — вне торгового окна (исключаем)
    return None


def _aggregate_to_sessions(df_5m: pd.DataFrame, figi: str) -> pd.DataFrame:
    """
    Агрегирует 5M в торговые сессии.
    Возвращает DataFrame с колонками: session_date, open, high, low, close, volume, n_5m.
    """
    if df_5m.empty:
        return pd.DataFrame(columns=["session_date", "open", "high", "low", "close", "volume", "n_5m"])

    df = df_5m.copy()
    df["time_msk"] = df["time"].dt.tz_convert(MSK)
    df["session_date"] = df["time_msk"].apply(
        lambda ts: _session_date_for_ts(pd.Timestamp(ts))
    )
    df = df[df["session_date"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=["session_date", "open", "high", "low", "close", "volume", "n_5m"])

    df["time_msk"] = pd.to_datetime(df["time_msk"])
    df = df.sort_values("time_msk")

    grouped = df.groupby("session_date", sort=True)
    rows = []
    for sess_date, g in grouped:
        g = g.sort_values("time_msk")
        rows.append({
            "session_date": sess_date,
            "open": float(g["open"].iloc[0]),
            "high": float(g["high"].max()),
            "low": float(g["low"].min()),
            "close": float(g["close"].iloc[-1]),
            "volume": float(g["volume"].sum()),
            "n_5m": int(len(g)),
        })
    out = pd.DataFrame(rows)
    out = out.sort_values("session_date").reset_index(drop=True)
    return out


# ------------------------------------------------------------
# ATR14 Wilder RMA
# ------------------------------------------------------------

def _atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    ATR по Уайлдеру (RMA).
    TR_t = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR_t = (ATR_{t-1} * (period-1) + TR_t) / period
    Первое значение ATR = mean(TR_1..TR_period) (начальное).
    Возвращает Series с индексом исходного ряда (NaN до period-1).
    """
    n = len(close)
    atr = pd.Series(index=close.index, dtype=float)
    if n == 0:
        return atr
    h = high.astype(float).to_numpy()
    l = low.astype(float).to_numpy()
    c = close.astype(float).to_numpy()
    tr = np.empty(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(
            h[i] - l[i],
            abs(h[i] - c[i - 1]),
            abs(l[i] - c[i - 1]),
        )
    if n < period:
        return atr
    first_atr = float(np.mean(tr[:period]))
    atr.iloc[period - 1] = first_atr
    prev = first_atr
    for i in range(period, n):
        cur = (prev * (period - 1) + tr[i]) / period
        atr.iloc[i] = cur
        prev = cur
    return atr


# ------------------------------------------------------------
# TOP-10
# ------------------------------------------------------------

@dataclass
class InstrumentSessionData:
    figi: str
    ticker: str
    sessions: pd.DataFrame  # session_date, open, high, low, close, volume, atr, turnover


def _build_top10_for_session(
    session_date: date,
    instruments: list[dict],
    data_by_figi: dict[str, InstrumentSessionData],
) -> tuple[list[dict], list[dict]]:
    """
    Строит TOP-10 на дату session_date по предыдущим 30 торговым сессиям
    (без текущей). Возвращает (top10_rows, metrics_rows).
    """
    # Для каждой FIGI — берём сессии < session_date, последние 30
    candidates = []
    for inst in instruments:
        figi = inst["figi"]
        sd = data_by_figi.get(figi)
        if sd is None:
            continue
        sess = sd.sessions
        prior = sess[sess["session_date"] < session_date].sort_values("session_date")
        if len(prior) < max(ATR_PERIOD + 1, TURNOVER_WINDOW):
            continue
        prior = prior.tail(LOOKBACK_SESSIONS)
        if len(prior) < LOOKBACK_SESSIONS:
            continue
        # ATR14 по prior (Wilder RMA)
        atr_series = _atr_wilder(
            prior["high"].reset_index(drop=True),
            prior["low"].reset_index(drop=True),
            prior["close"].reset_index(drop=True),
            ATR_PERIOD,
        )
        atr_last = atr_series.iloc[-1]
        if pd.isna(atr_last) or atr_last <= 0:
            continue
        close_last = float(prior["close"].iloc[-1])
        if close_last <= 0:
            continue
        atr_pct = (float(atr_last) / close_last) * 100.0
        # Turnover: mean(close*volume) за последние 20 сессий
        tail20 = prior.tail(TURNOVER_WINDOW)
        turnover_avg = float((tail20["close"] * tail20["volume"]).mean())
        if not math.isfinite(turnover_avg) or turnover_avg < MIN_AVG_TURNOVER_RUB:
            continue
        candidates.append({
            "session_date": session_date,
            "figi": figi,
            "ticker": sd.ticker,
            "atr_pct": atr_pct,
            "atr_abs": float(atr_last),
            "close": close_last,
            "avg_turnover_rub": turnover_avg,
            "n_sessions_used": int(len(prior)),
        })

    if not candidates:
        return [], []

    # Сортировка: ATR% desc, при равенстве — ticker asc
    candidates.sort(key=lambda r: (-r["atr_pct"], r["ticker"]))
    top10 = candidates[:TOP_N]
    return top10, candidates


# ------------------------------------------------------------
# Валидация
# ------------------------------------------------------------

def _validate_5m(df: pd.DataFrame) -> dict:
    issues = {
        "duplicates": 0,
        "unsorted": 0,
        "invalid_ohlc": 0,
        "negative_volume": 0,
    }
    if df.empty:
        return issues
    # duplicates
    issues["duplicates"] = int(df.duplicated(subset=["time"]).sum())
    # unsorted
    if not df["time"].is_monotonic_increasing:
        issues["unsorted"] = 1
    # OHLC
    bad_hl = (df["high"] < df["low"])
    bad_h = (df["high"] < df[["open", "close"]].max(axis=1))
    bad_l = (df["low"] > df[["open", "close"]].min(axis=1))
    issues["invalid_ohlc"] = int((bad_hl | bad_h | bad_l).sum())
    # negative volume
    issues["negative_volume"] = int((df["volume"] < 0).sum())
    return issues


# ------------------------------------------------------------
# Запись результатов
# ------------------------------------------------------------

def _write_csv(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, columns=columns, encoding="utf-8")


def _write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ------------------------------------------------------------
# main
# ------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MOEX_HISTORICAL_LAB_2Y — build_session_top10 (READ ONLY)"
    )
    parser.add_argument("--registry", type=str, default=None,
                        help="Путь к реестру (JSON). По умолчанию — последний в results/instrument_registry/")
    parser.add_argument("--five-min-dir", type=str, default=str(DEFAULT_FIVE_MIN_DIR))
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--start", type=str, default=HISTORY_START.isoformat())
    parser.add_argument("--end", type=str, default=HISTORY_END.isoformat())
    args = parser.parse_args(argv)

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    five_min_dir = Path(args.five_min_dir)
    out_dir = Path(args.out_dir)

    report = BuildReport()
    report.five_min_dir = str(five_min_dir)
    report.out_dir = str(out_dir)

    # 1. Реестр
    registry_file: Optional[Path] = None
    if args.registry:
        registry_file = Path(args.registry)
    else:
        registry_file = _find_latest_registry(DEFAULT_REGISTRY_DIR)
    if registry_file is None or not registry_file.is_file():
        print("[ERROR] Реестр не найден. Укажите --registry или положите JSON в results/instrument_registry/",
              file=sys.stderr)
        return 1
    report.registry_file = str(registry_file)
    try:
        instruments = _read_registry(registry_file)
    except Exception as e:
        print(f"[ERROR] Не удалось прочитать реестр: {e}", file=sys.stderr)
        return 1
    report.instruments_total = len(instruments)
    instruments_by_figi = {i["figi"]: i for i in instruments}
    valid_tickers = {i["ticker"] for i in instruments}

    # 2. 5M-файлы
    files = _list_five_min_files(five_min_dir)
    if not files:
        print(f"[ERROR] Нет 5M CSV в {five_min_dir}", file=sys.stderr)
        return 1

    # 3. Читаем 5M, агрегируем в сессии, валидируем
    session_rows = []
    data_by_figi: dict[str, InstrumentSessionData] = {}
    used_figis = set()

    for path in files:
        figi = path.stem
        inst = instruments_by_figi.get(figi)
        if inst is None:
            # FIGI нет в реестре — пропускаем (правило 14)
            continue
        if not inst.get("buy_available_flag") or not inst.get("sell_available_flag"):
            continue
        try:
            df = _read_five_min_csv(path)
        except Exception as e:
            report.errors.append(f"{path.name}: {e}")
            continue
        if df.empty:
            continue

        issues = _validate_5m(df)
        report.duplicates_5m += issues["duplicates"]
        report.unsorted_5m += issues["unsorted"]
        report.invalid_ohlc += issues["invalid_ohlc"]
        report.negative_volume += issues["negative_volume"]

        # Фильтр по датам (по MSK-дате сессии)
        df = df[(df["time"].dt.tz_convert(MSK).dt.date >= start_date - timedelta(days=1))
                & (df["time"].dt.tz_convert(MSK).dt.date <= end_date + timedelta(days=1))]
        if df.empty:
            continue

        sessions = _aggregate_to_sessions(df, figi)
        if sessions.empty:
            continue

        sessions = sessions[
            (sessions["session_date"] >= start_date - timedelta(days=LOOKBACK_SESSIONS * 2))
            & (sessions["session_date"] <= end_date)
        ].reset_index(drop=True)
        if sessions.empty:
            continue

        # Для каждой сессии — строка в session_rows
        for _, r in sessions.iterrows():
            session_rows.append({
                "figi": figi,
                "ticker": inst["ticker"],
                "session_date": r["session_date"].isoformat(),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
                "n_5m": r["n_5m"],
            })

        # Сохраняем в data_by_figi (с ATR/turnover не нужны — считаем на лету)
        data_by_figi[figi] = InstrumentSessionData(
            figi=figi,
            ticker=inst["ticker"],
            sessions=sessions,
        )
        used_figis.add(figi)

    report.instruments_used = len(used_figis)
    report.session_rows = len(session_rows)

    if not session_rows:
        print("[ERROR] Не удалось построить ни одной сессии", file=sys.stderr)
        return 1

    # 4. Все торговые даты (из сессий)
    all_dates = sorted({row["session_date"] for row in session_rows})
    report.sessions_total = len(all_dates)

    # 5. TOP-10 на каждую дату
    top10_rows = []
    metrics_rows = []
    for d_str in all_dates:
        d = date.fromisoformat(d_str)
        top10, all_candidates = _build_top10_for_session(d, instruments, data_by_figi)
        if not top10:
            report.dates_empty += 1
            continue
        report.dates_with_top10 += 1
        # Метрики — все кандидаты
        for rank, r in enumerate(top10, start=1):
            top10_rows.append({
                "session_date": d_str,
                "rank": rank,
                "figi": r["figi"],
                "ticker": r["ticker"],
                "atr_pct": r["atr_pct"],
                "atr_abs": r["atr_abs"],
                "close": r["close"],
                "avg_turnover_rub": r["avg_turnover_rub"],
                "n_sessions_used": r["n_sessions_used"],
            })
        for r in all_candidates:
            metrics_rows.append({
                "session_date": d_str,
                "figi": r["figi"],
                "ticker": r["ticker"],
                "atr_pct": r["atr_pct"],
                "atr_abs": r["atr_abs"],
                "close": r["close"],
                "avg_turnover_rub": r["avg_turnover_rub"],
                "n_sessions_used": r["n_sessions_used"],
            })

    report.top10_rows = len(top10_rows)
    report.metrics_rows = len(metrics_rows)

    # 6. Запись
    out_dir.mkdir(parents=True, exist_ok=True)

    session_df = pd.DataFrame(session_rows, columns=[
        "figi", "ticker", "session_date", "open", "high", "low", "close", "volume", "n_5m",
    ])
    session_df = session_df.sort_values(["session_date", "figi"]).reset_index(drop=True)
    _write_csv(session_df, out_dir / "session_bars.csv", list(session_df.columns))

    top10_df = pd.DataFrame(top10_rows, columns=[
        "session_date", "rank", "figi", "ticker", "atr_pct", "atr_abs", "close",
        "avg_turnover_rub", "n_sessions_used",
    ])
    if not top10_df.empty:
        top10_df = top10_df.sort_values(["session_date", "rank"]).reset_index(drop=True)
    _write_csv(top10_df, out_dir / "historical_top10.csv", list(top10_df.columns))

    metrics_df = pd.DataFrame(metrics_rows, columns=[
        "session_date", "figi", "ticker", "atr_pct", "atr_abs", "close",
        "avg_turnover_rub", "n_sessions_used",
    ])
    if not metrics_df.empty:
        metrics_df = metrics_df.sort_values(["session_date", "figi"]).reset_index(drop=True)
    _write_csv(metrics_df, out_dir / "historical_metrics.csv", list(metrics_df.columns))

    _write_json(report.__dict__, out_dir / "build_report.json")

    # 7. Сводка
    print("=" * 70)
    print("build_session_top10 — ГОТОВО")
    print("=" * 70)
    print(f"Реестр: {report.registry_file}")
    print(f"5M директория: {report.five_min_dir}")
    print(f"Выход: {report.out_dir}")
    print()
    print(f"Инструментов в реестре: {report.instruments_total}")
    print(f"Использовано (в 5M + реестр): {report.instruments_used}")
    print(f"Сессий: {report.sessions_total}")
    print(f"Строк session_bars: {report.session_rows}")
    print(f"Строк historical_top10: {report.top10_rows}")
    print(f"Строк historical_metrics: {report.metrics_rows}")
    print(f"Дат с TOP-10: {report.dates_with_top10}")
    print(f"Дат с пустым TOP-10: {report.dates_empty}")
    print()
    print(f"Дубликаты 5M: {report.duplicates_5m}")
    print(f"Несортированные: {report.unsorted_5m}")
    print(f"Некорректные OHLC: {report.invalid_ohlc}")
    print(f"Отрицательный volume: {report.negative_volume}")
    print()
    if report.errors:
        print(f"ОШИБКИ: {len(report.errors)}")
        for e in report.errors[:20]:
            print(f"  !! {e}")
    else:
        print("Ошибок нет.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())