from __future__ import annotations
from datetime import datetime, timezone
import math, random
from zoneinfo import ZoneInfo
from .models import MarketState

ASSETS = {
    "GOLD": {"name": "Vàng Thần Kỳ", "category": "Kim loại quý", "base": 2350.0, "vol": 0.00065, "color": "#f7c948"},
    "SILVER": {"name": "Bạc Hoàng Gia", "category": "Kim loại quý", "base": 28.5, "vol": 0.0009, "color": "#c0c6d2"},
    "COPPER": {"name": "Đồng Công Nghiệp", "category": "Kim loại quý", "base": 4.2, "vol": 0.0010, "color": "#c47b43"},
    "PLATINUM": {"name": "Bạch Kim Đỏ", "category": "Kim loại quý", "base": 980.0, "vol": 0.0008, "color": "#dbe5f2"},
    "OIL": {"name": "Dầu Thô WTI", "category": "Năng lượng", "base": 78.5, "vol": 0.0011, "color": "#ef6666"},
    "BRENT": {"name": "Dầu Brent Biển", "category": "Năng lượng", "base": 82.0, "vol": 0.0010, "color": "#ff956d"},
    "GAS": {"name": "Khí Đốt Thiên Nhiên", "category": "Năng lượng", "base": 2.65, "vol": 0.0013, "color": "#45c5e8"},
    "BTC": {"name": "Bitcoin Ảo", "category": "Crypto Real", "base": 65000.0, "vol": 0.0017, "color": "#f5a623"},
    "ETH": {"name": "Ethereum Ảo", "category": "Crypto Real", "base": 3500.0, "vol": 0.0018, "color": "#9c88ff"},
    "SOL": {"name": "Solana Siêu Tốc", "category": "Crypto Real", "base": 145.0, "vol": 0.0020, "color": "#39c995"},
}
CRYPTO_24_7 = {"BTC", "ETH", "SOL"}
FIXED_HOLIDAYS = {"01-01", "04-30", "05-01", "09-02"}
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-04-27", "2026-04-30", "2026-05-01",
    "2026-08-31", "2026-09-01", "2026-09-02",
}
MAX_HISTORY = 200
MAX_CATCHUP_STEPS = 600
TICK_SECONDS = 5


def traditional_market_open(now: datetime) -> bool:
    # Use Vietnam local calendar like the original desktop simulator.
    local = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")) if now.tzinfo else now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    if local.weekday() >= 5 or local.strftime("%m-%d") in FIXED_HOLIDAYS:
        return False
    if local.year == 2026 and local.date().isoformat() in HOLIDAYS_2026:
        return False
    return True


def seed_market(db):
    state = db.get(MarketState, 1)
    if state:
        return state
    now = datetime.now(timezone.utc)
    prices = {sym: info["base"] for sym, info in ASSETS.items()}
    history = {sym: [info["base"]] * MAX_HISTORY for sym, info in ASSETS.items()}
    state = MarketState(id=1, prices=prices, history=history, updated_at=now)
    db.add(state)
    db.flush()
    return state


def advance_market(db, now: datetime | None = None):
    """Advance a single shared paper market and persist it in PostgreSQL.

    Long gaps are approximated in bounded batches so a sleeping free web service
    can catch up without replaying an unbounded number of ticks on wake-up.
    """
    now = now or datetime.now(timezone.utc)
    state = seed_market(db)
    last = state.updated_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = max(0.0, (now - last).total_seconds())
    if elapsed < TICK_SECONDS:
        return state
    requested_steps = max(1, int(elapsed // TICK_SECONDS))
    steps = min(requested_steps, MAX_CATCHUP_STEPS)
    # Keep roughly square-root volatility over large catch-up gaps.
    scale = math.sqrt(max(1.0, requested_steps / steps))
    prices = dict(state.prices or {})
    history = {k: list(v or []) for k, v in (state.history or {}).items()}
    for sym, info in ASSETS.items():
        prices.setdefault(sym, info["base"])
        history.setdefault(sym, [prices[sym]] * MAX_HISTORY)
    rng = random.SystemRandom()
    for step in range(steps):
        sample_time = last.timestamp() + min(elapsed, (step + 1) * elapsed / steps)
        sample_dt = datetime.fromtimestamp(sample_time, tz=timezone.utc)
        tradables_open = traditional_market_open(sample_dt)
        for sym, info in ASSETS.items():
            if sym not in CRYPTO_24_7 and not tradables_open:
                continue
            old = max(0.01, float(prices[sym]))
            # Tiny positive drift mirrors the source simulator; movements remain random.
            drift = math.log(1.008) / (365.25 * 24 * 60 * 60) * TICK_SECONDS * scale
            sigma = float(info["vol"]) * math.sqrt(scale)
            log_change = rng.uniform(-0.0025, 0.0025) * (sigma / 0.001) + drift
            log_change = max(-0.08, min(0.08, log_change))
            new_price = max(0.01, old * math.exp(log_change))
            prices[sym] = round(new_price, 8 if new_price < 1 else 4)
        # Retain useful chart samples even after long offline gaps.
        if step == steps - 1 or step % max(1, steps // 80) == 0:
            for sym in ASSETS:
                history[sym].append(float(prices[sym]))
                history[sym] = history[sym][-MAX_HISTORY:]
    state.prices = prices
    state.history = history
    state.updated_at = now
    db.flush()
    return state


def moving_averages(values: list[float]) -> dict:
    out = {}
    for n in (5, 10, 20, 50, 200):
        if len(values) >= n:
            out[f"MA{n}"] = sum(values[-n:]) / n
        else:
            out[f"MA{n}"] = None
    return out


def market_payload(state):
    result = []
    now = datetime.now(timezone.utc)
    traditional_open = traditional_market_open(now)
    for sym, info in ASSETS.items():
        price = float(state.prices.get(sym, info["base"]))
        history = [float(x) for x in state.history.get(sym, [price])]
        prev = history[-2] if len(history) > 1 else price
        delta = price - prev
        change_pct = (delta / prev * 100.0) if prev else 0.0
        result.append({
            "symbol": sym, "name": info["name"], "category": info["category"],
            "price": price, "change": delta, "change_pct": change_pct,
            "history": history, "moving_averages": moving_averages(history),
            "color": info["color"], "open": (sym in CRYPTO_24_7 or traditional_open),
        })
    return {"assets": result, "updated_at": state.updated_at.isoformat(),
            "status": "Crypto 24/7 · tài sản truyền thống nghỉ cuối tuần/ngày lễ"}
