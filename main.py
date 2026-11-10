from __future__ import annotations

import hmac
import json
import math
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from dotenv import load_dotenv
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, delete, desc, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.market import ASSETS, CRYPTO_24_7, advance_market, market_payload, seed_market
from app.models import (
    AdminRequest, AppSettings, AuditLog, Base, ChatMessage, MarketState,
    ModerationReport, TradeRecord, User, utcnow,
)
from app.security import hash_password, verify_password

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
STATIC_DIR = ROOT / "app" / "static"
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "baominhadmin").strip().lower()
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "")
OWNER_DISPLAY_NAME = os.getenv("OWNER_DISPLAY_NAME", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
IS_PRODUCTION = os.getenv("RENDER", "").lower() == "true" or os.getenv("APP_ENV") == "production"

ROLE_LABELS = {
    "user": "THÀNH VIÊN", "coordinate": "TỌA ĐỘ", "admin": "ADMIN",
    "superadmin": "SPADMIN",
}
ROLE_LIMIT_DEFAULTS = {"superadmin": 5, "admin": 10, "coordinate": 20}
ROLE_LADDER = ["user", "coordinate", "admin", "superadmin"]
PENALTY_LIMITS = {"coordinate": 1_000_000_000, "admin": 10_000_000_000, "superadmin": 100_000_000_000}
PREMIUM_TIERS = {
    "bronze": {"label": "THÀNH VIÊN ĐỒNG", "short": "ĐỒNG", "prices": {"three_day": 700_000, "month": 1_000_000, "year": 5_000_000}, "daily": 100_000, "forecast": 1, "rank": 0, "color": "#c47b43"},
    "silver": {"label": "THÀNH VIÊN BẠC", "short": "BẠC", "prices": {"three_day": 2_100_000, "month": 3_000_000, "year": 15_000_000}, "daily": 350_000, "forecast": 2, "rank": 1, "color": "#c0c6d2"},
    "gold": {"label": "THÀNH VIÊN VÀNG", "short": "VÀNG", "prices": {"three_day": 7_000_000, "month": 10_000_000, "year": 50_000_000}, "daily": 1_200_000, "forecast": 3, "rank": 2, "color": "#f7c948"},
    "platinum": {"label": "THÀNH VIÊN BẠCH KIM", "short": "BẠCH KIM", "prices": {"three_day": 35_000_000, "month": 50_000_000, "year": 100_000_000}, "daily": 5_000_000, "forecast": 4, "rank": 3, "color": "#dbe5f2"},
    "diamond": {"label": "THÀNH VIÊN KIM CƯƠNG", "short": "KIM CƯƠNG", "prices": {"three_day": 700_000_000, "month": 1_000_000_000, "year": 5_000_000_000}, "daily": 100_000_000, "forecast": 5, "rank": 4, "color": "#7aa7ff"},
    "ruby": {"label": "TV RUBY", "short": "RUBY", "prices": {}, "daily": 100_000_000, "forecast": 3, "rank": 5, "color": "#f54e9a"},
    "emerald": {"label": "TV LỤC BẢO", "short": "LỤC BẢO", "prices": {}, "daily": 1_000_000_000, "forecast": 5, "rank": 6, "color": "#90eeae"},
}
PLAN_DAYS = {"three_day": 3, "month": 30, "year": 365}
PLAN_LABELS = {"three_day": "3 NGÀY", "month": "1 THÁNG", "year": "1 NĂM"}
MEMBER_CHAT_TIERS = set(PREMIUM_TIERS)


Payload = dict[str, Any]


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_username(value: str) -> str:
    return str(value or "").strip().casefold()


def clean_name(value: str) -> str:
    return " ".join(str(value or "").strip().split())[:50]


def create_account_id(db: Session) -> str:
    for _ in range(2000):
        candidate = f"{secrets.randbelow(100_000_000):08d}"
        found = db.scalar(select(User.id).where(User.account_id == candidate))
        if not found:
            return candidate
    raise RuntimeError("Không tạo được ID tài khoản duy nhất.")


def role_label(user: User) -> str:
    return "OWNER" if user.is_owner else ROLE_LABELS.get(user.role, "THÀNH VIÊN")


def active_membership(user: User, now: datetime | None = None) -> str | None:
    now = now or utcnow()
    state = dict(user.membership or {})
    tier = state.get("tier")
    if tier == "emerald" and state.get("expires_at"):
        try:
            expires = datetime.fromisoformat(str(state["expires_at"]))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now >= expires:
                previous = state.get("emerald_previous") or {}
                restored = dict(previous) if isinstance(previous, dict) else {}
                # Keep one-day reward history to prevent duplicate same-day claims.
                if state.get("last_reward_date"):
                    restored["last_reward_date"] = state["last_reward_date"]
                user.membership = restored
                tier = restored.get("tier")
        except (TypeError, ValueError):
            pass
    if tier == "ruby" and state.get("ruby_active", True):
        return "ruby"
    if tier not in PREMIUM_TIERS:
        return None
    expires_text = state.get("expires_at")
    if expires_text:
        try:
            expires = datetime.fromisoformat(str(expires_text))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now >= expires:
                return None
        except (TypeError, ValueError):
            pass
    return tier


def account_membership_label(user: User) -> str:
    tier = active_membership(user)
    if not tier:
        return "Không có thành viên"
    state = user.membership or {}
    if tier == "ruby":
        return "RUBY · VĨNH VIỄN"
    if tier == "emerald":
        return "LỤC BẢO · VĨNH VIỄN" if not state.get("expires_at") else "LỤC BẢO · 1 NGÀY"
    exp = state.get("expires_at")
    try:
        exp_label = datetime.fromisoformat(str(exp)).strftime("%d/%m/%Y") if exp else "Vĩnh viễn"
    except ValueError:
        exp_label = "Không rõ hạn"
    return f"{PREMIUM_TIERS[tier]['short']} · {PLAN_LABELS.get(state.get('plan'), state.get('plan', '—'))} · đến {exp_label}"


def add_audit(db: Session, actor: User | None, action: str, target: User | None = None, details: str = ""):
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        target_id=target.id if target else None,
        action=str(action)[:80], details=str(details)[:700],
    ))


def provision_owner_from_environment(db: Session) -> User | None:
    """Create the one Owner from Render environment variables on a fresh database.

    Existing Owner credentials are never overwritten on restart. The password is
    stored only as a salted hash; it is never returned to the browser or logged.
    """
    existing_owner = db.scalar(select(User).where(User.is_owner.is_(True)).limit(1))
    if existing_owner:
        return existing_owner

    password = str(OWNER_PASSWORD or "")
    if not password:
        if IS_PRODUCTION:
            raise RuntimeError("Chưa có OWNER trên database mới. Hãy đặt OWNER_PASSWORD trong Render Environment rồi deploy lại.")
        return None

    username = normalize_username(OWNER_USERNAME)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise RuntimeError("OWNER_USERNAME không hợp lệ; cần 3–32 ký tự chữ, số, ., _ hoặc -.")
    if (len(password) < 12 or len(password) > 256
            or not any(ch.isalpha() for ch in password)
            or not any(ch.isdigit() for ch in password)):
        raise RuntimeError("OWNER_PASSWORD cần ít nhất 12 ký tự, có cả chữ và số.")
    if db.scalar(select(User.id).where(User.username_norm == username)):
        raise RuntimeError("OWNER_USERNAME đã được tài khoản khác sử dụng trên database mới.")

    display_name = clean_name(OWNER_DISPLAY_NAME) or username
    owner = User(
        id=str(uuid.uuid4()), username=username, username_norm=username,
        account_id=create_account_id(db), password_hash=hash_password(password),
        role="admin", is_owner=True, balance=Decimal("500000.00"),
        holdings={sym: 0.0 for sym in ASSETS}, membership={},
        profile={"display_name": display_name, "owner_name": display_name},
        inbox=[], debts=[], penalties=[],
    )
    db.add(owner)
    try:
        db.flush()
        add_audit(db, owner, "OWNER_BOOTSTRAP_ENV", owner, "OWNER được khởi tạo từ biến môi trường máy chủ.")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A concurrent process may have initialized the same database first.
        existing_owner = db.scalar(select(User).where(User.is_owner.is_(True)).limit(1))
        if existing_owner:
            return existing_owner
        raise RuntimeError("Không thể khởi tạo OWNER; hãy kiểm tra OWNER_USERNAME và database.") from exc
    return owner


def add_trade(db: Session, user: User, trade_type: str, symbol: str, quantity: float = 0,
              price: float = 0, amount: float = 0, memo: str = ""):
    db.add(TradeRecord(user_id=user.id, trade_type=trade_type[:80], symbol=symbol[:24],
                       quantity=Decimal(str(quantity)), price=Decimal(str(price)),
                       amount=Decimal(str(amount)), memo=str(memo)[:600]))


def get_current_user(request: Request, db: Session, allow_banned: bool = False) -> User:
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Em cần đăng nhập trước.")
    user = db.get(User, uid)
    if not user:
        request.session.clear()
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.")
    if user.banned and not allow_banned:
        request.session.clear()
        raise HTTPException(403, user.ban_reason or "Tài khoản đã bị khóa.")
    return user


def require_owner(user: User):
    if not user.is_owner:
        raise HTTPException(403, "Chỉ OWNER được thực hiện thao tác này.")


def require_staff(user: User):
    if user.role not in ("coordinate", "admin", "superadmin") and not user.is_owner:
        raise HTTPException(403, "Chức năng chỉ dành cho QTV.")


def staff_can_use_qtv_chat(user: User) -> bool:
    return user.is_owner or user.role in ("superadmin", "admin", "coordinate")


def get_settings(db: Session) -> AppSettings:
    settings = db.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1, role_limits=dict(ROLE_LIMIT_DEFAULTS))
        db.add(settings)
        db.flush()
    return settings


def role_count(db: Session, role: str) -> int:
    query = select(func.count(User.id)).where(User.role == role, User.is_owner.is_(False))
    return int(db.scalar(query) or 0)


def enforce_role_capacity(db: Session, role: str):
    settings = get_settings(db)
    limits = dict(ROLE_LIMIT_DEFAULTS)
    limits.update(settings.role_limits or {})
    cap = int(limits.get(role, 0))
    used = role_count(db, role)
    if used >= cap:
        raise HTTPException(409, f"Đã đủ {cap} suất {ROLE_LABELS.get(role, role)}. OWNER có thể tăng hạn mức.")


def issue_inbox(user: User, title: str, body: str):
    inbox = list(user.inbox or [])
    inbox.insert(0, {"id": secrets.token_hex(6), "title": title[:120], "body": body[:1000],
                     "created_at": utc_iso(utcnow()), "read": False})
    user.inbox = inbox[:100]


def reward_daily_membership(db: Session, user: User) -> float:
    tier = active_membership(user)
    if not tier:
        return 0.0
    now = utcnow()
    state = dict(user.membership or {})
    today = now.date().isoformat()
    started = state.get("started_at")
    last_reward = state.get("last_reward_date")
    try:
        last = datetime.fromisoformat(str(last_reward)).date() if last_reward else None
    except ValueError:
        last = None
    try:
        started_date = datetime.fromisoformat(str(started)).date() if started else now.date()
    except ValueError:
        started_date = now.date()
    lower = started_date - timedelta(days=1)
    if last is None:
        days_due = max(1, (now.date() - lower).days)
    else:
        days_due = max(0, (now.date() - max(last, lower)).days)
    if days_due <= 0:
        return 0.0
    amount = float(PREMIUM_TIERS[tier]["daily"]) * min(days_due, 365)
    user.balance = Decimal(str(user.balance)) + Decimal(str(amount))
    state["last_reward_date"] = today
    user.membership = state
    add_trade(db, user, f"THƯỞNG {PREMIUM_TIERS[tier]['short']} ({days_due} NGÀY)", "USD", amount, 1, amount)
    issue_inbox(user, "🎁 Thưởng thành viên đã được cộng", f"+{amount:,.0f} USD · {days_due} ngày tích lũy.")
    return amount


def reward_coordinate_daily(db: Session, user: User) -> float:
    if user.role != "coordinate":
        return 0.0
    now = utcnow()
    state = dict(user.profile or {})
    today = now.date().isoformat()
    last_txt = state.get("last_coordinate_bonus_date")
    try:
        last = datetime.fromisoformat(last_txt).date() if last_txt else now.date() - timedelta(days=1)
    except (TypeError, ValueError):
        last = now.date() - timedelta(days=1)
    try:
        promoted = datetime.fromisoformat(state.get("coordinate_promoted_at", "")).date()
        last = max(last, promoted - timedelta(days=1))
    except (TypeError, ValueError):
        pass
    days = min(max(0, (now.date() - last).days), 365)
    if days <= 0:
        return 0.0
    usd = 100_000 * days
    btc = 100.0 * days
    user.balance = Decimal(str(user.balance)) + Decimal(str(usd))
    holdings = dict(user.holdings or {})
    holdings["BTC"] = round(float(holdings.get("BTC", 0)) + btc, 6)
    user.holdings = holdings
    state["last_coordinate_bonus_date"] = today
    user.profile = state
    add_trade(db, user, f"THƯỞNG TỌA ĐỘ ({days} NGÀY)", "USD + BTC", btc, 1, usd,
              f"+{usd:,.0f} USD và +{btc:,.0f} BTC ảo")
    issue_inbox(user, "🎖️ Thưởng TỌA ĐỘ", f"+{usd:,.0f} USD và +{btc:,.0f} BTC ảo ({days} ngày).")
    return float(usd)


def user_payload(user: User, db: Session | None = None) -> dict:
    tier = active_membership(user)
    state = user.membership or {}
    assets_value = 0.0
    prices = {}
    if db:
        market = db.get(MarketState, 1)
        if market:
            prices = market.prices or {}
    holdings = dict(user.holdings or {})
    for sym, qty in holdings.items():
        assets_value += safe_float(qty) * safe_float(prices.get(sym))
    balance = safe_float(user.balance)
    return {
        "id": user.id, "username": user.username, "account_id": user.account_id,
        "display_name": (user.profile or {}).get("display_name") or user.username,
        "role": "owner" if user.is_owner else user.role,
        "role_label": role_label(user), "membership": tier,
        "membership_label": account_membership_label(user),
        "membership_expires_at": state.get("expires_at"),
        "balance": balance, "holdings": holdings,
        "portfolio_value": assets_value, "net_worth": balance + assets_value,
        "banned": user.banned, "ban_reason": user.ban_reason,
        "role_request_status": user.role_request_status,
        "forecast_seconds": PREMIUM_TIERS[tier]["forecast"] if tier else 0,
        "ruby_immunity_left": int(state.get("ruby_immunity_left", 0) or 0),
        "inbox_unread": sum(1 for x in (user.inbox or []) if not x.get("read")),
        "created_at": utc_iso(user.created_at),
    }


def set_login_session(request: Request, user: User):
    request.session.clear()
    request.session["uid"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(32)


def parse_money_text(value: Any, *, allow_zero: bool = False) -> Decimal:
    raw = str(value or "").strip().replace(" ", "")
    # Vietnamese format: comma is decimal separator; dot may group thousands.
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise HTTPException(400, "Số tiền không hợp lệ.")
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise HTTPException(400, "Số tiền phải là số hữu hạn và lớn hơn 0.")
    if amount > Decimal("999999999999999999999999"):
        raise HTTPException(400, "Số tiền vượt giới hạn lưu trữ.")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or os.getenv("DATABASE_URL") or "sqlite:///./sanchungkhoan_local.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    engine_kwargs = {"pool_pre_ping": True, "pool_recycle": 300}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **engine_kwargs)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            seed_market(db)
            get_settings(db)
            db.commit()
            provision_owner_from_environment(db)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="SÀN CHỨNG KHOÁN", version="2.1.0", docs_url="/api/docs" if not IS_PRODUCTION else None,
                  redoc_url=None, lifespan=lifespan)
    if not SESSION_SECRET and IS_PRODUCTION:
        # The app can start, but endpoints that create sessions will be blocked by the guard below.
        secret = ""
    else:
        secret = SESSION_SECRET or "local-dev-only-change-before-deploy-" + secrets.token_hex(12)
    app.add_middleware(SessionMiddleware, secret_key=secret or "missing-secret-not-for-production",
                       session_cookie="sck_session", same_site="lax", https_only=IS_PRODUCTION)
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal
    app.state.database_url_is_configured = bool(os.getenv("DATABASE_URL"))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def check_csrf(request: Request):
        expected = request.session.get("csrf")
        supplied = request.headers.get("x-csrf-token", "")
        if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
            raise HTTPException(403, "Phiên bảo vệ biểu mẫu đã hết hạn. Hãy tải lại trang.")

    def fresh_session_guard():
        if IS_PRODUCTION and not SESSION_SECRET:
            raise HTTPException(503, "Thiếu SESSION_SECRET trên cấu hình máy chủ.")

    @app.get("/healthz")
    def healthz(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
            owner_exists = db.scalar(select(func.count(User.id)).where(User.is_owner.is_(True))) or 0
            return {"ok": True, "database": "connected", "owner_configured": bool(owner_exists)}
        except SQLAlchemyError:
            raise HTTPException(503, "Database connection failed")

    @app.get("/")
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/auth/status")
    def auth_status(request: Request, db: Session = Depends(get_db)):
        if not request.session.get("csrf"):
            request.session["csrf"] = secrets.token_urlsafe(32)
        owner_exists = bool(db.scalar(select(User.id).where(User.is_owner.is_(True)).limit(1)))
        current = None
        uid = request.session.get("uid")
        if uid:
            user = db.get(User, uid)
            if user and not user.banned:
                current = user_payload(user, db)
            else:
                request.session.clear()
                request.session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf": request.session["csrf"], "owner_exists": owner_exists, "user": current}

    @app.post("/api/auth/register")
    def register(request: Request, payload: Payload, db: Session = Depends(get_db)):
        fresh_session_guard(); check_csrf(request)
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        display_name = clean_name(payload.get("display_name", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            raise HTTPException(400, "Tên đăng nhập cần 3–32 ký tự: chữ không dấu, số, ., _ hoặc -.")
        if len(password) < 8 or len(password) > 256:
            raise HTTPException(400, "Mật khẩu cần từ 8 đến 256 ký tự.")
        username_norm = normalize_username(username)
        if username_norm == OWNER_USERNAME and not db.scalar(select(User.id).where(User.is_owner.is_(True))):
            raise HTTPException(409, "Tên đăng nhập này được dành riêng cho tài khoản OWNER.")
        user = User(id=str(uuid.uuid4()), username=username, username_norm=username_norm,
                    account_id=create_account_id(db), password_hash=hash_password(password),
                    role="user", is_owner=False, balance=Decimal("500000.00"),
                    holdings={sym: 0.0 for sym in ASSETS}, membership={},
                    profile={"display_name": display_name or username}, inbox=[], debts=[], penalties=[])
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Tên tài khoản đã tồn tại.")
        set_login_session(request, user)
        add_audit(db, user, "REGISTER", user, "Tạo tài khoản thường và đăng nhập ngay.")
        db.commit()
        return {"ok": True, "message": "Đăng ký thành công. Em đã được đăng nhập luôn!",
                "csrf": request.session["csrf"], "user": user_payload(user, db)}

    @app.post("/api/auth/login")
    def login(request: Request, payload: Payload, db: Session = Depends(get_db)):
        fresh_session_guard(); check_csrf(request)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = app.state.__dict__.setdefault("login_attempts", {})
        item = attempts.get(ip, {"count": 0, "blocked_until": 0})
        if item.get("blocked_until", 0) > now:
            remain = int(item["blocked_until"] - now)
            raise HTTPException(429, f"Thử đăng nhập quá nhiều. Hãy thử lại sau {remain} giây.")
        username_norm = normalize_username(payload.get("username", ""))
        user = db.scalar(select(User).where(User.username_norm == username_norm))
        password = str(payload.get("password", ""))
        if not user or not verify_password(password, user.password_hash):
            item["count"] = item.get("count", 0) + 1
            if item["count"] >= 5:
                item["count"] = 0
                item["blocked_until"] = now + 60
            attempts[ip] = item
            raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
        if user.banned:
            raise HTTPException(403, user.ban_reason or "Tài khoản đã bị khóa.")
        attempts.pop(ip, None)
        set_login_session(request, user)
        add_audit(db, user, "LOGIN", user, "Đăng nhập thành công.")
        db.commit()
        return {"ok": True, "message": "Đăng nhập thành công.",
                "csrf": request.session["csrf"], "user": user_payload(user, db)}

    @app.post("/api/auth/logout")
    def logout(request: Request, db: Session = Depends(get_db)):
        check_csrf(request)
        uid = request.session.get("uid")
        user = db.get(User, uid) if uid else None
        if user:
            add_audit(db, user, "LOGOUT", user, "Đăng xuất.")
            db.commit()
        request.session.clear()
        request.session["csrf"] = secrets.token_urlsafe(32)
        return {"ok": True, "message": "Đã đăng xuất."}

    @app.get("/api/me")
    def me(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        # Resolve expiry and persist any restored one-day Emerald membership.
        active_membership(user)
        db.commit()
        return {"user": user_payload(user, db), "profile": user.profile or {},
                "inbox": user.inbox or [], "debts": user.debts or [], "penalties": user.penalties or []}

    @app.patch("/api/profile")
    def update_profile(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        name = clean_name(payload.get("display_name", user.username))
        bio = " ".join(str(payload.get("bio", "")).split())[:160]
        if not name:
            raise HTTPException(400, "Tên hiển thị không được để trống.")
        profile = dict(user.profile or {})
        profile.update({"display_name": name, "bio": bio})
        user.profile = profile
        add_audit(db, user, "PROFILE_UPDATE", user, "Cập nhật hồ sơ hiển thị.")
        db.commit()
        return {"ok": True, "user": user_payload(user, db)}

    @app.get("/api/market")
    def get_market(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        state = advance_market(db)
        db.commit()
        return market_payload(state)

    @app.post("/api/trade")
    def trade(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        symbol = str(payload.get("symbol", "")).upper()
        mode = str(payload.get("mode", "")).lower()
        if symbol not in ASSETS or mode not in ("buy", "sell"):
            raise HTTPException(400, "Mã tài sản hoặc loại giao dịch không hợp lệ.")
        state = advance_market(db)
        price = Decimal(str(state.prices.get(symbol, ASSETS[symbol]["base"])))
        asset_status = next((a for a in market_payload(state)["assets"] if a["symbol"] == symbol), None)
        tradable = bool(asset_status and asset_status["open"])
        if not tradable:
            raise HTTPException(409, f"{symbol}: sàn truyền thống đang nghỉ, chưa thể giao dịch.")
        amount = parse_money_text(payload.get("amount_usd", ""))
        balance = Decimal(str(user.balance))
        holdings = dict(user.holdings or {})
        held_qty = Decimal(str(safe_float(holdings.get(symbol))))
        if mode == "buy":
            if amount > balance:
                raise HTTPException(409, "Số dư USD không đủ để mua.")
            qty = (amount / price).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            cost = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if qty <= 0 or cost <= 0 or cost > balance:
                raise HTTPException(400, "Số tiền quá nhỏ để tạo lệnh mua hợp lệ.")
            user.balance = balance - cost
            holdings[symbol] = float(held_qty + qty)
            user.holdings = holdings
            if active_membership(user) == "emerald":
                state_m = dict(user.membership or {})
                lots = list(state_m.get("protected_lots", []))
                lots.append({"symbol": symbol, "qty": float(qty), "buy_price": float(price), "time": utc_iso(utcnow())})
                state_m["protected_lots"] = lots[-5000:]
                user.membership = state_m
            add_trade(db, user, "MUA", symbol, float(qty), float(price), float(cost))
            result_message = f"Đã mua {float(qty):,.6f} {symbol} bằng {float(cost):,.2f} USD."
        else:
            qty = (amount / price).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            if qty > held_qty:
                raise HTTPException(409, f"Không đủ {symbol} để bán theo số tiền đã nhập.")
            revenue = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            refund = Decimal("0.00")
            state_m = dict(user.membership or {})
            lots = list(state_m.get("protected_lots", []))
            remaining = float(qty)
            allow_refund = active_membership(user) == "emerald"
            updated_lots = []
            for lot in lots:
                if lot.get("symbol") != symbol or remaining <= 1e-10:
                    updated_lots.append(lot); continue
                lot_qty = max(0.0, safe_float(lot.get("qty")))
                used_qty = min(lot_qty, remaining)
                if allow_refund and used_qty > 0:
                    loss = max(0.0, (safe_float(lot.get("buy_price")) - float(price)) * used_qty)
                    refund += Decimal(str(loss * 0.80))
                leftover = lot_qty - used_qty
                remaining -= used_qty
                if leftover > 1e-9:
                    new_lot = dict(lot); new_lot["qty"] = leftover; updated_lots.append(new_lot)
            state_m["protected_lots"] = updated_lots
            user.membership = state_m
            holdings[symbol] = max(0.0, float(held_qty - qty))
            user.holdings = holdings
            user.balance = balance + revenue + refund
            add_trade(db, user, "BÁN" if refund <= 0 else "BÁN + BỒI HOÀN LỤC BẢO", symbol,
                      float(qty), float(price), float(revenue + refund),
                      f"Bồi hoàn 80% khoản lỗ đủ điều kiện: {float(refund):,.2f} USD." if refund > 0 else "")
            result_message = f"Đã bán {float(qty):,.6f} {symbol}, thu về {float(revenue):,.2f} USD."
            if refund > 0:
                result_message += f" Lục Bảo bồi hoàn thêm {float(refund):,.2f} USD."
        db.commit()
        return {"ok": True, "message": result_message, "user": user_payload(user, db)}

    @app.get("/api/trades")
    def trades(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        rows = db.scalars(select(TradeRecord).where(TradeRecord.user_id == user.id).order_by(desc(TradeRecord.created_at)).limit(100)).all()
        return {"trades": [{"type": r.trade_type, "symbol": r.symbol, "quantity": float(r.quantity),
                            "price": float(r.price), "amount": float(r.amount), "memo": r.memo,
                            "created_at": utc_iso(r.created_at)} for r in rows]}

    @app.post("/api/admin/cash")
    def adjust_cash(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db)
        if not (actor.is_owner or actor.role in ("admin", "superadmin")):
            raise HTTPException(403, "Chỉ OWNER, SPADMIN hoặc ADMIN được điều chỉnh USD.")
        target = actor
        target_username = str(payload.get("target_username", "")).strip()
        if target_username and target_username != actor.username:
            if not actor.is_owner:
                raise HTTPException(403, "ADMIN/SPADMIN chỉ được điều chỉnh ví của chính mình; OWNER mới được chọn tài khoản khác.")
            target = db.scalar(select(User).where(User.username_norm == normalize_username(target_username)))
            if not target:
                raise HTTPException(404, "Không tìm thấy tài khoản mục tiêu.")
        amount = parse_money_text(payload.get("amount", ""))
        action = str(payload.get("action", "add"))
        if action not in ("add", "subtract"):
            raise HTTPException(400, "Hành động cộng/trừ không hợp lệ.")
        current = Decimal(str(target.balance))
        applied = amount if action == "add" else min(amount, max(Decimal("0"), current))
        new_balance = current + applied if action == "add" else current - applied
        if not new_balance.is_finite():
            raise HTTPException(400, "Số dư sau điều chỉnh không hợp lệ.")
        target.balance = new_balance
        add_trade(db, target, "ADMIN +USD" if action == "add" else "ADMIN -USD", "USD", float(applied), 1, float(applied),
                  f"Điều chỉnh bởi {role_label(actor)} ({actor.username}).")
        add_audit(db, actor, "ADMIN_CASH_ADJUST", target, f"{action} {applied} USD.")
        issue_inbox(target, "Điều chỉnh số dư USD", f"{role_label(actor)} đã {'cộng' if action == 'add' else 'trừ'} {applied:,.2f} USD.")
        db.commit()
        return {"ok": True, "message": f"Đã {'cộng' if action == 'add' else 'trừ'} {float(applied):,.2f} USD cho {target.username}.",
                "user": user_payload(target, db)}

    @app.get("/api/membership/shop")
    def membership_shop(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        tier = active_membership(user)
        items = []
        for key in ("bronze", "silver", "gold", "platinum", "diamond"):
            info = PREMIUM_TIERS[key]
            is_free = user.role == "admin" or user.is_owner or (user.role == "coordinate" and key == "bronze")
            items.append({"key": key, "label": info["label"], "daily": info["daily"], "forecast": info["forecast"],
                          "prices": info["prices"], "free_trial": is_free, "color": info["color"]})
        specials = []
        if user.is_owner or user.role == "superadmin":
            specials.append({"key": "ruby", "label": "TV RUBY", "daily": PREMIUM_TIERS["ruby"]["daily"],
                             "forecast": 3, "gift_only": True, "owner_approval": user.role == "superadmin" and not user.is_owner})
        if user.is_owner:
            specials.append({"key": "emerald", "label": "TV LỤC BẢO", "daily": PREMIUM_TIERS["emerald"]["daily"],
                             "forecast": 5, "gift_only": True, "owner_only": True})
        return {"current_tier": tier, "current_label": account_membership_label(user), "items": items, "specials": specials,
                "plans": [{"key": k, "label": PLAN_LABELS[k], "days": v} for k, v in PLAN_DAYS.items()]}

    @app.post("/api/membership/purchase")
    def membership_purchase(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        tier = str(payload.get("tier", "")); plan = str(payload.get("plan", "month"))
        if tier not in ("bronze", "silver", "gold", "platinum", "diamond") or plan not in PLAN_DAYS:
            raise HTTPException(400, "Gói hoặc thời hạn không hợp lệ.")
        now = utcnow(); current = active_membership(user, now)
        if current == "ruby":
            raise HTTPException(409, "RUBY là gói vĩnh viễn; chỉ OWNER có thể thu hồi.")
        if current and PREMIUM_TIERS[current]["rank"] > PREMIUM_TIERS[tier]["rank"]:
            raise HTTPException(409, "Không thể mua cấp thấp hơn cấp đang có.")
        admin_trial = user.role == "admin" or user.is_owner
        coordinate_free = user.role == "coordinate" and tier == "bronze"
        cost = Decimal("0") if admin_trial or coordinate_free else Decimal(str(PREMIUM_TIERS[tier]["prices"][plan]))
        if Decimal(str(user.balance)) < cost:
            raise HTTPException(409, f"Chưa đủ USD. Cần {cost:,.0f} USD.")
        state = dict(user.membership or {})
        old_exp = None
        if current == tier and state.get("plan") == plan and state.get("expires_at"):
            try:
                old_exp = datetime.fromisoformat(state["expires_at"])
                if old_exp.tzinfo is None: old_exp = old_exp.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        starts = old_exp if old_exp and old_exp > now else now
        expires = starts + timedelta(days=PLAN_DAYS[plan])
        if current:
            reward_daily_membership(db, user)
        user.balance = Decimal(str(user.balance)) - cost
        new_state = dict(user.membership or {})
        new_state.update({"tier": tier, "plan": plan,
                          "started_at": (new_state.get("started_at") if current == tier and new_state.get("started_at") else utc_iso(now)),
                          "expires_at": utc_iso(expires)})
        new_state.pop("emerald_previous", None)
        new_state.pop("protected_lots", None)
        user.membership = new_state
        add_trade(db, user, f"MUA PREMIUM {PREMIUM_TIERS[tier]['short']} - {PLAN_LABELS[plan]}", "USD", float(cost), 1, float(cost))
        add_audit(db, user, "PREMIUM_PURCHASE", user, f"{tier}/{plan} · {cost} USD.")
        if not current:
            reward_daily_membership(db, user)
        db.commit()
        return {"ok": True, "message": f"Đã kích hoạt {PREMIUM_TIERS[tier]['label']} · {PLAN_LABELS[plan]}. Hạn dùng đến {expires.strftime('%d/%m/%Y')}.",
                "user": user_payload(user, db)}

    @app.post("/api/membership/claim")
    def membership_claim(request: Request, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        amount = reward_daily_membership(db, user)
        role_amount = reward_coordinate_daily(db, user)
        if amount <= 0 and role_amount <= 0:
            raise HTTPException(409, "Hôm nay em đã nhận thưởng hoặc chưa có gói thành viên/TỌA ĐỘ đủ điều kiện.")
        db.commit()
        return {"ok": True, "message": f"Đã nhận {amount + role_amount:,.0f} USD" + (" và thưởng 100 BTC/ngày của TỌA ĐỘ." if role_amount else "."),
                "user": user_payload(user, db)}

    @app.post("/api/membership/cancel")
    def membership_cancel(request: Request, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        tier = active_membership(user)
        if tier == "ruby":
            raise HTTPException(409, "TV RUBY vĩnh viễn; chỉ OWNER mới được thu hồi.")
        if tier == "emerald":
            raise HTTPException(409, "TV LỤC BẢO là gói đặc biệt; không thể tự hủy.")
        if not tier:
            raise HTTPException(409, "Tài khoản hiện không có gói đang hoạt động.")
        previous = dict(user.membership or {})
        user.membership = {}
        add_audit(db, user, "PREMIUM_CANCEL", user, f"Hủy {tier}; không hoàn phí.")
        issue_inbox(user, "Đã hủy gói thành viên", "Phí đã trả không được hoàn lại. Số dư và tài khoản vẫn được giữ.")
        db.commit()
        return {"ok": True, "message": "Đã hủy gói. Khoản phí đã trả không được hoàn lại; tài khoản và số dư được giữ.",
                "user": user_payload(user, db)}

    @app.post("/api/membership/gift")
    def membership_gift(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); sender = get_current_user(request, db)
        target_id = str(payload.get("recipient_id", ""))
        target_name = clean_name(payload.get("recipient_name", ""))
        target = db.scalar(select(User).where(User.account_id == target_id)) if re.fullmatch(r"\d{8}", target_id) else None
        if not target or target.id == sender.id or target.banned:
            raise HTTPException(404, "Không tìm thấy tài khoản nhận hợp lệ.")
        expected_name = clean_name((target.profile or {}).get("display_name") or target.username)
        if normalize_username(expected_name) != normalize_username(target_name):
            raise HTTPException(400, "Tên hiển thị không khớp ID người nhận.")
        tier = str(payload.get("tier", "")); plan = str(payload.get("plan", "month"))
        if tier == "emerald":
            if not sender.is_owner:
                raise HTTPException(403, "Chỉ OWNER được tặng LỤC BẢO.")
            return grant_emerald(db, sender, target, permanent=False)
        if tier == "ruby":
            if sender.is_owner:
                return grant_ruby(db, sender, target, "OWNER")
            if sender.role != "superadmin":
                raise HTTPException(403, "Chỉ OWNER/SPADMIN mới có mục tặng RUBY.")
            owner = db.scalar(select(User).where(User.is_owner.is_(True)))
            if not owner:
                raise HTTPException(409, "Chưa có OWNER để xét duyệt.")
            report = ModerationReport(id=secrets.token_hex(8), reporter_id=sender.id, target_id=target.id,
                                      channel="owner", report_type="ruby_gift_request", status="pending_owner",
                                      reason="SPADMIN xin OWNER duyệt tặng TV RUBY vĩnh viễn.",
                                      messages=[{"from": sender.username, "text": f"Xin duyệt tặng TV RUBY cho {expected_name} (ID {target_id}).", "at": utc_iso(utcnow())}])
            db.add(report)
            issue_inbox(owner, "Yêu cầu tặng TV RUBY", f"SPADMIN {sender.username} xin duyệt tặng RUBY cho {expected_name}.")
            add_audit(db, sender, "RUBY_GIFT_REQUEST", target, "SPADMIN gửi yêu cầu Owner duyệt.")
            db.commit()
            return {"ok": True, "message": "Đã gửi yêu cầu tặng RUBY cho OWNER xét duyệt.", "pending": True}
        if tier not in ("bronze", "silver", "gold", "platinum", "diamond") or plan not in PLAN_DAYS:
            raise HTTPException(400, "Gói tặng không hợp lệ.")
        current = active_membership(target)
        if current and PREMIUM_TIERS[current]["rank"] > PREMIUM_TIERS[tier]["rank"]:
            raise HTTPException(409, "Người nhận đang có cấp cao hơn; không thể tặng gói thấp hơn.")
        base = Decimal(str(PREMIUM_TIERS[tier]["prices"][plan]))
        total = (base * Decimal("1.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if Decimal(str(sender.balance)) < total:
            raise HTTPException(409, f"Cần {total:,.2f} USD (đã gồm phí tặng 15%).")
        sender.balance = Decimal(str(sender.balance)) - total
        tstate = dict(target.membership or {})
        exp = None
        if current == tier and tstate.get("expires_at"):
            try:
                exp = datetime.fromisoformat(tstate["expires_at"])
                if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
            except ValueError: pass
        start = exp if exp and exp > utcnow() else utcnow()
        expires = start + timedelta(days=PLAN_DAYS[plan])
        tstate.update({"tier": tier, "plan": plan, "started_at": utc_iso(utcnow()), "expires_at": utc_iso(expires)})
        target.membership = tstate
        add_trade(db, sender, f"TẶNG PREMIUM {PREMIUM_TIERS[tier]['short']}", "USD", 0, 1, float(total), f"Người nhận: {target.username}; phí tặng 15%.")
        add_trade(db, target, f"ĐƯỢC TẶNG PREMIUM {PREMIUM_TIERS[tier]['short']}", "PREMIUM", 0, 0, float(base), f"Người tặng: {sender.username}.")
        issue_inbox(target, "🎁 Bạn được tặng gói thành viên", f"{sender.username} tặng {PREMIUM_TIERS[tier]['label']} · {PLAN_LABELS[plan]}. Hạn đến {expires.strftime('%d/%m/%Y')}.")
        add_audit(db, sender, "PREMIUM_GIFT", target, f"{tier}/{plan} · tổng phí {total} USD.")
        db.commit()
        return {"ok": True, "message": f"Đã tặng {PREMIUM_TIERS[tier]['label']} cho {target.username}; tổng phí gồm phụ phí 15% là {total:,.0f} USD.",
                "user": user_payload(sender, db)}

    def grant_ruby(db: Session, actor: User, target: User, source: str):
        if not actor.is_owner and not (source == "staff_self_test" and actor.role == "superadmin"):
            raise HTTPException(403, "Chỉ OWNER cấp trực tiếp/duyệt RUBY; SPADMIN chỉ tự thử hoặc xin duyệt.")
        if active_membership(target) == "ruby":
            raise HTTPException(409, "Tài khoản này đã có TV RUBY.")
        state = {"tier": "ruby", "plan": "permanent", "started_at": utc_iso(utcnow()),
                 "ruby_active": True, "ruby_immunity_left": int((target.membership or {}).get("ruby_immunity_left", 3))}
        target.membership = state
        issue_inbox(target, "💗 Bạn được cấp TV RUBY vĩnh viễn", f"Người cấp: {actor.username}. Thưởng 100.000.000 USD/ngày; miễn phạt còn {state['ruby_immunity_left']}/3 lượt.")
        add_audit(db, actor, "GRANT_RUBY", target, "Cấp TV RUBY vĩnh viễn.")
        db.commit()
        return {"ok": True, "message": f"Đã cấp TV RUBY vĩnh viễn cho {target.username}.", "user": user_payload(target, db)}

    def grant_emerald(db: Session, actor: User, target: User, permanent: bool):
        if not actor.is_owner:
            raise HTTPException(403, "Chỉ OWNER được cấp LỤC BẢO.")
        if target.is_owner and not permanent:
            raise HTTPException(400, "OWNER chỉ được kích hoạt LỤC BẢO vĩnh viễn trên chính tài khoản OWNER.")
        old_state = dict(target.membership or {})
        now = utcnow()
        state = {"tier": "emerald", "plan": "permanent" if permanent else "one_day",
                 "started_at": utc_iso(now), "last_reward_date": now.date().isoformat(),
                 "protected_lots": [], "emerald_previous": old_state if not permanent else {}}
        if not permanent:
            state["expires_at"] = utc_iso(now + timedelta(days=1))
        target.membership = state
        target.balance = Decimal(str(target.balance)) + Decimal("1000000000")
        add_trade(db, target, "THƯỞNG LỤC BẢO NGÀY ĐẦU", "USD", 0, 1, 1_000_000_000)
        issue_inbox(target, "🍃 Bạn được cấp TV LỤC BẢO", "Thưởng ngày đầu +1.000.000.000 USD. Lệnh mua trong thời gian hiệu lực được bảo vệ 80% khoản lỗ đủ điều kiện khi bán.")
        add_audit(db, actor, "GRANT_EMERALD", target, "Cấp LỤC BẢO vĩnh viễn." if permanent else "Tặng LỤC BẢO một ngày.")
        db.commit()
        return {"ok": True, "message": "Đã kích hoạt LỤC BẢO vĩnh viễn." if permanent else f"Đã tặng LỤC BẢO một ngày cho {target.username}.",
                "user": user_payload(target, db)}

    @app.post("/api/membership/ruby-self-test")
    def ruby_self_test(request: Request, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db)
        if not (actor.is_owner or actor.role == "superadmin"):
            raise HTTPException(403, "Chỉ OWNER/SPADMIN được tự kích hoạt RUBY thử nghiệm.")
        result = grant_ruby(db, actor, actor, "staff_self_test")
        # Self-test gets the initial daily reward immediately.
        reward_daily_membership(db, actor)
        db.commit()
        return result

    @app.post("/api/membership/emerald-owner")
    def emerald_owner(request: Request, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        return grant_emerald(db, actor, actor, permanent=True)

    @app.post("/api/membership/revoke")
    def revoke_membership(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        username = normalize_username(payload.get("username", ""))
        target = db.scalar(select(User).where(User.username_norm == username))
        if not target or target.is_owner:
            raise HTTPException(404, "Không tìm thấy tài khoản có thể thu hồi.")
        tier = active_membership(target)
        if tier not in ("ruby", "emerald"):
            raise HTTPException(409, "Chỉ thu hồi trực tiếp RUBY hoặc LỤC BẢO.")
        target.membership = {}
        issue_inbox(target, f"Đã thu hồi TV {tier.upper()}", "OWNER đã thu hồi gói đặc biệt; tài khoản trở về thành viên thường.")
        add_audit(db, actor, f"REVOKE_{tier.upper()}", target, "Thu hồi toàn bộ trạng thái thành viên.")
        db.commit()
        return {"ok": True, "message": f"Đã thu hồi TV {tier.upper()} của {target.username}."}

    @app.post("/api/transfers")
    def transfer(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); sender = get_current_user(request, db)
        target_id = str(payload.get("recipient_id", "")); target_name = clean_name(payload.get("recipient_name", ""))
        target = db.scalar(select(User).where(User.account_id == target_id)) if re.fullmatch(r"\d{8}", target_id) else None
        if not target or target.id == sender.id or target.banned:
            raise HTTPException(404, "Không tìm thấy người nhận hợp lệ.")
        expected_name = clean_name((target.profile or {}).get("display_name") or target.username)
        if normalize_username(target_name) != normalize_username(expected_name):
            raise HTTPException(400, "Tên hiển thị không khớp ID.")
        amount = parse_money_text(payload.get("amount", ""))
        if amount > Decimal(str(sender.balance)):
            raise HTTPException(409, "Số dư không đủ.")
        mode = str(payload.get("mode", "transfer"))
        if mode not in ("transfer", "loan"):
            raise HTTPException(400, "Loại chuyển tiền không hợp lệ.")
        received = amount if mode == "loan" else (amount * Decimal("0.85")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fee = amount - received
        sender.balance = Decimal(str(sender.balance)) - amount
        target.balance = Decimal(str(target.balance)) + received
        add_trade(db, sender, "CHO NỢ" if mode == "loan" else "CHUYỂN TIỀN", "USD", float(amount), 1, float(amount), f"Người nhận: {target.username}.")
        add_trade(db, target, "NHẬN KHOẢN NỢ" if mode == "loan" else "NHẬN TIỀN", "USD", float(received), 1, float(received), f"Người gửi: {sender.username}; phí bị xóa: {fee}.")
        if mode == "loan":
            debts = list(target.debts or [])
            debts.append({"lender": sender.username, "lender_id": sender.account_id, "amount": float(amount), "created_at": utc_iso(utcnow()), "status": "open"})
            target.debts = debts[-500:]
        issue_inbox(target, "Bạn nhận được khoản tiền ảo", f"Người gửi: {sender.username}. Nhận: {received:,.2f} USD; phí bị xóa: {fee:,.2f}.")
        issue_inbox(sender, "Đã chuyển tiền ảo", f"Người nhận: {target.username}. Đã gửi: {amount:,.2f}; người nhận: {received:,.2f}.")
        add_audit(db, sender, "TRANSFER" if mode == "transfer" else "LOAN_RECORDED", target, f"Gửi {amount} USD; nhận {received}; phí {fee}.")
        db.commit()
        return {"ok": True, "message": f"Đã gửi {amount:,.2f} USD; người nhận nhận {received:,.2f}. Phí bị xóa khỏi hệ thống: {fee:,.2f}.", "user": user_payload(sender, db)}

    @app.post("/api/role/request-admin")
    def request_admin(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        if user.is_owner or user.role == "admin":
            raise HTTPException(409, "Tài khoản đã có quyền ADMIN/OWNER.")
        existing = db.scalar(select(AdminRequest).where(AdminRequest.requester_id == user.id, AdminRequest.status == "pending"))
        if existing:
            raise HTTPException(409, "Yêu cầu ADMIN đang chờ OWNER xét duyệt.")
        note = " ".join(str(payload.get("note", "")).split())[:300]
        request_row = AdminRequest(requester_id=user.id, status="pending", applicant_note=note)
        db.add(request_row); user.role_request_status = "pending"
        owner = db.scalar(select(User).where(User.is_owner.is_(True)))
        if owner:
            issue_inbox(owner, "Yêu cầu xét quyền ADMIN", f"{user.username} (ID {user.account_id}) vừa gửi yêu cầu ADMIN.")
        add_audit(db, user, "ADMIN_ACCESS_REQUEST", user, note)
        db.commit()
        return {"ok": True, "message": "Đã gửi yêu cầu ADMIN. Em có thể tiếp tục dùng tài khoản thường khi chờ duyệt."}

    @app.get("/api/admin/users")
    def admin_users(request: Request, db: Session = Depends(get_db)):
        actor = get_current_user(request, db); require_staff(actor)
        users = db.scalars(select(User).order_by(User.created_at.desc()).limit(500)).all()
        return {"users": [{"username": u.username, "display_name": (u.profile or {}).get("display_name") or u.username,
                            "account_id": u.account_id, "role": "owner" if u.is_owner else u.role,
                            "role_label": role_label(u), "membership": account_membership_label(u),
                            "balance": float(u.balance), "banned": u.banned, "created_at": utc_iso(u.created_at)} for u in users]}

    @app.get("/api/admin/requests")
    def admin_requests(request: Request, db: Session = Depends(get_db)):
        actor = get_current_user(request, db); require_owner(actor)
        rows = db.scalars(select(AdminRequest).order_by(desc(AdminRequest.created_at)).limit(300)).all()
        result = []
        for row in rows:
            user = db.get(User, row.requester_id)
            if user:
                result.append({"id": row.id, "username": user.username, "account_id": user.account_id,
                               "status": row.status, "note": row.applicant_note, "created_at": utc_iso(row.created_at)})
        return {"requests": result}

    @app.post("/api/admin/requests/{request_id}/decision")
    def decide_admin_request(request: Request, request_id: int, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        item = db.get(AdminRequest, request_id)
        if not item or item.status != "pending":
            raise HTTPException(404, "Yêu cầu không còn ở trạng thái chờ duyệt.")
        applicant = db.get(User, item.requester_id)
        approve = bool(payload.get("approve"))
        if not applicant:
            item.status = "rejected"; db.commit()
            raise HTTPException(404, "Tài khoản người gửi không còn tồn tại.")
        if approve:
            enforce_role_capacity(db, "admin")
            applicant.role = "admin"; applicant.is_owner = False; applicant.role_request_status = "approved"
            item.status = "approved"
            issue_inbox(applicant, "Yêu cầu ADMIN đã được duyệt", "OWNER đã trao quyền ADMIN cho tài khoản của em.")
        else:
            item.status = "rejected"; applicant.role_request_status = "rejected"
            issue_inbox(applicant, "Yêu cầu ADMIN chưa được duyệt", "OWNER đã từ chối yêu cầu lần này.")
        item.reviewed_at = utcnow(); item.review_note = str(payload.get("note", ""))[:300]
        add_audit(db, actor, "ADMIN_REQUEST_APPROVED" if approve else "ADMIN_REQUEST_REJECTED", applicant, item.review_note)
        db.commit()
        return {"ok": True, "message": "Đã cập nhật yêu cầu."}

    @app.post("/api/admin/roles/change")
    def change_role(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        username = normalize_username(payload.get("username", "")); direction = str(payload.get("direction", ""))
        target = db.scalar(select(User).where(User.username_norm == username))
        if not target or target.is_owner or target.id == actor.id:
            raise HTTPException(404, "Không thể thay đổi vai trò tài khoản này.")
        old = target.role
        if direction == "grant_spadmin":
            next_role = "superadmin"
        elif direction in ("promote", "demote"):
            idx = ROLE_LADDER.index(target.role) if target.role in ROLE_LADDER else 0
            next_idx = idx + (1 if direction == "promote" else -1)
            if not 0 <= next_idx < len(ROLE_LADDER):
                raise HTTPException(409, "Không thể thay đổi thêm một cấp theo hướng này.")
            next_role = ROLE_LADDER[next_idx]
        else:
            raise HTTPException(400, "Thao tác vai trò không hợp lệ.")
        if next_role in ROLE_LIMIT_DEFAULTS:
            enforce_role_capacity(db, next_role)
        target.role = next_role
        if next_role == "coordinate":
            profile = dict(target.profile or {}); profile["coordinate_promoted_at"] = utc_iso(utcnow()); target.profile = profile
        elif old == "coordinate":
            profile = dict(target.profile or {}); profile.pop("coordinate_promoted_at", None); target.profile = profile
        add_audit(db, actor, "ROLE_CHANGE", target, f"{old} → {next_role}")
        db.commit()
        return {"ok": True, "message": f"Đã đổi vai trò {target.username}: {ROLE_LABELS.get(old, old)} → {ROLE_LABELS.get(next_role, next_role)}."}

    @app.get("/api/admin/role-limits")
    def get_role_limits(request: Request, db: Session = Depends(get_db)):
        actor = get_current_user(request, db); require_owner(actor)
        settings = get_settings(db)
        limits = dict(ROLE_LIMIT_DEFAULTS); limits.update(settings.role_limits or {})
        return {"role_limits": limits, "used": {role: role_count(db, role) for role in ROLE_LIMIT_DEFAULTS}}

    @app.post("/api/admin/role-limits")
    def change_role_limits(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        parsed = {}
        for role, default in ROLE_LIMIT_DEFAULTS.items():
            try: value = int(payload.get(role, default))
            except (TypeError, ValueError): raise HTTPException(400, f"Hạn mức {ROLE_LABELS[role]} phải là số nguyên.")
            if not 1 <= value <= 1000 or value < role_count(db, role):
                raise HTTPException(400, f"Hạn mức {ROLE_LABELS[role]} không hợp lệ hoặc thấp hơn số đang giữ chức.")
            parsed[role] = value
        settings = get_settings(db); settings.role_limits = parsed; settings.updated_at = utcnow()
        add_audit(db, actor, "ROLE_LIMITS_UPDATE", None, json.dumps(parsed))
        db.commit()
        return {"ok": True, "role_limits": parsed}

    @app.post("/api/admin/users/ban")
    def ban_user(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_staff(actor)
        target = db.scalar(select(User).where(User.username_norm == normalize_username(payload.get("username", ""))))
        if not target or target.is_owner or target.id == actor.id:
            raise HTTPException(404, "Không tìm thấy tài khoản có thể xử lý.")
        ban = bool(payload.get("ban", True)); reason = " ".join(str(payload.get("reason", "")).split())[:240]
        target_role = target.role
        actor_role = "owner" if actor.is_owner else actor.role
        permitted = False
        tier = active_membership(target)
        tier_rank = PREMIUM_TIERS[tier]["rank"] if tier else -1
        if actor.is_owner:
            permitted = True
        elif actor_role == "superadmin":
            permitted = target_role in ("user", "coordinate", "superadmin")
        elif actor_role == "admin":
            permitted = target_role == "user"
        elif actor_role == "coordinate":
            permitted = target_role == "user" and tier_rank <= 3
        if not permitted:
            raise HTTPException(403, "Vai trò này không được ban trực tiếp tài khoản mục tiêu.")
        if not reason and ban:
            raise HTTPException(400, "Hãy nhập lý do ban cụ thể.")
        if actor_role == "admin" and target_role == "user" and ban:
            m = dict(target.membership or {})
            try:
                started = datetime.fromisoformat(m.get("started_at", ""))
                if started.tzinfo is None: started = started.replace(tzinfo=timezone.utc)
                long_membership = (utcnow() - started).days >= 30
            except (TypeError, ValueError): long_membership = False
            if long_membership:
                approved = None
                pending = db.scalars(select(ModerationReport).where(
                    ModerationReport.reporter_id == actor.id, ModerationReport.target_id == target.id,
                    ModerationReport.report_type == "ban_request",
                    ModerationReport.status == "instruction_granted"
                ).order_by(desc(ModerationReport.updated_at))).first()
                if pending:
                    grants = [m for m in (pending.messages or []) if m.get("expires_at")]
                    if grants:
                        try:
                            expiry = datetime.fromisoformat(grants[-1]["expires_at"])
                            if expiry.tzinfo is None: expiry = expiry.replace(tzinfo=timezone.utc)
                            if utcnow() <= expiry: approved = pending
                        except (TypeError, ValueError):
                            approved = None
                if approved is None:
                    owner = db.scalar(select(User).where(User.is_owner.is_(True)))
                    report = ModerationReport(id=secrets.token_hex(8), reporter_id=actor.id, target_id=target.id,
                                              channel="owner", report_type="ban_request", status="pending_owner",
                                              reason=reason or "Xin chỉ thị OWNER để ban tài khoản có gói dài hạn.", messages=[])
                    db.add(report)
                    if owner: issue_inbox(owner, "Yêu cầu chỉ thị ban", f"ADMIN {actor.username} xin chỉ thị ban {target.username}.")
                    db.commit()
                    raise HTTPException(409, "Tài khoản có gói từ 1 tháng: đã gửi yêu cầu OWNER, cần chỉ thị trước khi ban.")
                approved.status = "used"
                approved.updated_at = utcnow()
        target.banned = ban; target.ban_reason = reason if ban else ""
        issue_inbox(target, "Tài khoản đã bị khóa" if ban else "Tài khoản đã được mở khóa", reason or "OWNER/ QTV đã cập nhật trạng thái tài khoản.")
        add_audit(db, actor, "BAN_ACCOUNT" if ban else "UNBAN_ACCOUNT", target, reason)
        db.commit()
        return {"ok": True, "message": f"Đã {'ban' if ban else 'gỡ ban'} {target.username}."}

    @app.post("/api/admin/users/penalty")
    def penalize_user(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_staff(actor)
        target = db.scalar(select(User).where(User.username_norm == normalize_username(payload.get("username", ""))))
        if not target or target.is_owner or target.id == actor.id:
            raise HTTPException(404, "Không tìm thấy mục tiêu hợp lệ để phạt.")
        target_tier = active_membership(target)
        if not actor.is_owner and actor.role in ("admin", "coordinate") and target.role != "user":
            raise HTTPException(403, "ADMIN/TỌA ĐỘ chỉ được phạt thành viên thường.")
        spec = str(payload.get("amount", "")).strip()
        if not actor.is_owner and actor.role in ("admin", "coordinate") and target_tier == "ruby":
            # Ruby-target penalties are routed to SPADMIN for review, never applied directly by ADMIN/Coordinate.
            staff_rows = db.scalars(select(User).where(User.role == "superadmin", User.banned.is_(False))).all()
            report = ModerationReport(
                id=secrets.token_hex(8), reporter_id=actor.id, target_id=target.id,
                channel="spadmin", report_type="ruby_penalty_request", status="pending_spadmin",
                reason=str(payload.get("reason", "Xin xem xét xử lý tài khoản RUBY."))[:500],
                messages=[{"from": actor.username, "text": "Đề nghị SPADMIN xem xét mức phạt.",
                           "requested_amount": spec, "at": utc_iso(utcnow())}],
            )
            db.add(report)
            for staff_member in staff_rows:
                issue_inbox(staff_member, "Yêu cầu xem xét RUBY", f"{actor.username} gửi yêu cầu xử lý tài khoản RUBY {target.username}.")
            add_audit(db, actor, "RUBY_PENALTY_REQUEST", target, f"Yêu cầu SPADMIN xem xét: {spec}")
            db.commit()
            return {"ok": True, "pending": True, "message": "Đã chuyển yêu cầu xử lý RUBY lên SPADMIN duyệt; tài khoản chưa bị trừ tiền."}
        balance = Decimal(str(target.balance))
        if spec.endswith("%"):
            try: pct = Decimal(spec[:-1])
            except InvalidOperation: raise HTTPException(400, "Phần trăm không hợp lệ.")
            if not pct.is_finite() or pct <= 0 or pct > 1000 or balance <= 0:
                raise HTTPException(400, "Phần trăm cần >0%, tối đa 1000% và mục tiêu phải có số dư dương.")
            amount = (balance * pct / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        else:
            try: amount = Decimal(re.sub(r"[^0-9]", "", spec))
            except InvalidOperation: raise HTTPException(400, "Số tiền phạt không hợp lệ.")
            limit = None if actor.is_owner else Decimal(str(PENALTY_LIMITS.get(actor.role, 0)))
            if amount <= 0 or (limit is not None and amount > limit):
                raise HTTPException(400, "Số tiền vượt hạn mức của vai trò hoặc không hợp lệ.")
        if not amount.is_finite() or amount <= 0:
            raise HTTPException(400, "Số tiền phạt không hợp lệ.")
        state = dict(target.membership or {})
        if target_tier == "ruby" and int(state.get("ruby_immunity_left", 0) or 0) > 0:
            state["ruby_immunity_left"] = int(state.get("ruby_immunity_left", 0)) - 1
            target.membership = state
            issue_inbox(target, "💗 TV RUBY đã kích hoạt miễn phạt", f"Lần này không trừ USD. Còn {state['ruby_immunity_left']}/3 lượt.")
            add_audit(db, actor, "PENALTY_IMMUNITY_USED", target, f"Còn {state['ruby_immunity_left']}/3 lượt.")
            db.commit()
            return {"ok": True, "message": f"RUBY miễn phạt thành công. Còn {state['ruby_immunity_left']}/3 lượt."}
        old = Decimal(str(target.balance)); target.balance = old - amount
        debt = max(Decimal("0"), -target.balance)
        penalties = list(target.penalties or [])
        penalties.insert(0, {"amount": float(amount), "old_balance": float(old), "new_balance": float(target.balance),
                             "actor": actor.username, "actor_role": role_label(actor), "reason": str(payload.get("reason", ""))[:500], "at": utc_iso(utcnow())})
        target.penalties = penalties[:100]
        add_trade(db, target, "PHẠT VI PHẠM", "USD", float(amount), 1, float(amount), f"Nợ sàn hiện tại: {debt:,.2f} USD.")
        issue_inbox(target, "⚠️ Tài khoản bị phạt", f"Người phạt: {actor.username} · {role_label(actor)}\nPhạt: {amount:,.0f} USD\nSố dư mới: {target.balance:,.2f}\nNợ sàn: {debt:,.2f}\nLý do: {str(payload.get('reason', ''))[:240]}")
        add_audit(db, actor, "PENALTY_APPLIED", target, f"{amount} USD; debt={debt}")
        db.commit()
        return {"ok": True, "message": f"Đã áp dụng phạt {amount:,.0f} USD; nợ sàn hiện tại {debt:,.2f}."}

    @app.post("/api/admin/users/delete")
    def delete_user(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        username = normalize_username(payload.get("username", ""))
        target = db.scalar(select(User).where(User.username_norm == username))
        if not target or target.is_owner or target.id == actor.id:
            raise HTTPException(404, "Không tìm thấy tài khoản có thể xóa; OWNER không thể tự xóa.")
        target_name = target.username
        add_audit(db, actor, "DELETE_ACCOUNT", target, "OWNER xóa tài khoản theo yêu cầu quản trị.")
        db.execute(delete(ChatMessage).where(ChatMessage.sender_id == target.id))
        db.execute(delete(AdminRequest).where(AdminRequest.requester_id == target.id))
        db.execute(delete(ModerationReport).where(
            (ModerationReport.reporter_id == target.id) | (ModerationReport.target_id == target.id)
        ))
        db.delete(target)
        db.commit()
        return {"ok": True, "message": f"Đã xóa tài khoản {target_name}. Thao tác này không thể hoàn tác."}

    @app.post("/api/reports")
    def create_report(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); reporter = get_current_user(request, db)
        target = db.scalar(select(User).where(User.account_id == str(payload.get("target_id", ""))))
        if not target or target.id == reporter.id:
            raise HTTPException(404, "Không tìm thấy tài khoản mục tiêu.")
        entered_name = normalize_username(clean_name(payload.get("target_name", "")))
        actual_name = normalize_username(clean_name((target.profile or {}).get("display_name") or target.username))
        if entered_name != actual_name:
            raise HTTPException(400, "Tên hiển thị không khớp ID đã nhập.")
        reason = " ".join(str(payload.get("reason", "")).split())[:500]
        if len(reason) < 8:
            raise HTTPException(400, "Lý do cần ít nhất 8 ký tự.")
        if reporter.is_owner or reporter.role in ("superadmin", "admin"):
            channel = "owner"
            owner = db.scalar(select(User).where(User.is_owner.is_(True)))
            recipient = owner
        else:
            channel = "admin"
            recipient = db.scalar(select(User).where(User.role == "admin", User.is_owner.is_(False), User.banned.is_(False)).order_by(User.created_at).limit(1))
        report = ModerationReport(id=secrets.token_hex(8), reporter_id=reporter.id, target_id=target.id,
                                  channel=channel, report_type="complaint", status="pending_owner" if channel == "owner" else "pending_admin",
                                  reason=reason, messages=[{"from": reporter.username, "text": reason, "at": utc_iso(utcnow())}])
        db.add(report)
        if recipient: issue_inbox(recipient, "Có tố cáo/trao đổi mới", f"{reporter.username} gửi hồ sơ liên quan đến {target.username}: {reason[:220]}")
        add_audit(db, reporter, "MODERATION_REPORT", target, f"Channel={channel}; {reason}")
        db.commit()
        return {"ok": True, "message": "Đã gửi hồ sơ đến bộ phận quản trị phù hợp."}

    @app.get("/api/reports")
    def list_reports(request: Request, db: Session = Depends(get_db)):
        actor = get_current_user(request, db); require_staff(actor)
        if actor.is_owner:
            rows = db.scalars(select(ModerationReport).order_by(desc(ModerationReport.created_at)).limit(300)).all()
        elif actor.role == "admin":
            rows = db.scalars(select(ModerationReport).where(ModerationReport.channel == "admin").order_by(desc(ModerationReport.created_at)).limit(300)).all()
        elif actor.role == "superadmin":
            rows = db.scalars(select(ModerationReport).where(
                ((ModerationReport.channel == "spadmin") & (ModerationReport.status == "pending_spadmin"))
                | ((ModerationReport.channel == "owner") & (ModerationReport.reporter_id == actor.id))
            ).order_by(desc(ModerationReport.created_at)).limit(150)).all()
        else:
            rows = db.scalars(select(ModerationReport).where(ModerationReport.channel == "owner", ModerationReport.reporter_id == actor.id).order_by(desc(ModerationReport.created_at)).limit(100)).all()
        result = []
        for r in rows:
            reporter = db.get(User, r.reporter_id); target = db.get(User, r.target_id)
            result.append({"id": r.id, "reporter": reporter.username if reporter else "—", "target": target.username if target else "—",
                           "reason": r.reason, "status": r.status, "type": r.report_type, "channel": r.channel,
                           "created_at": utc_iso(r.created_at), "messages": r.messages or []})
        return {"reports": result}

    @app.post("/api/reports/{report_id}/decision")
    def decide_report(request: Request, report_id: str, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db); require_owner(actor)
        report = db.get(ModerationReport, report_id)
        if not report or report.status not in ("pending_owner", "pending_admin"):
            raise HTTPException(404, "Không tìm thấy hồ sơ đang chờ xử lý.")
        target = db.get(User, report.target_id)
        if not target:
            raise HTTPException(404, "Tài khoản mục tiêu không còn tồn tại.")
        decision = str(payload.get("decision", ""))
        if decision == "approve_ruby_gift" and report.report_type == "ruby_gift_request":
            reporter = db.get(User, report.reporter_id)
            if not reporter or reporter.role != "superadmin":
                raise HTTPException(409, "Người gửi không còn là SPADMIN.")
            grant_ruby(db, actor, target, "OWNER")
            report.status = "approved_ruby_gift"
        elif decision == "authorize_ban" and report.report_type == "ban_request":
            report.status = "instruction_granted"
            report.messages = list(report.messages or []) + [{"from": actor.username, "text": "OWNER cho phép xử lý trong 24 giờ.", "at": utc_iso(utcnow()), "expires_at": utc_iso(utcnow()+timedelta(hours=24))}]
        elif decision in ("reject", "close"):
            report.status = "rejected" if decision == "reject" else "closed"
        else:
            raise HTTPException(400, "Quyết định hồ sơ không hợp lệ.")
        reply = str(payload.get("reply", "")).strip()[:500]
        if reply:
            report.messages = list(report.messages or []) + [{"from": actor.username, "text": reply, "at": utc_iso(utcnow())}]
        report.updated_at = utcnow()
        add_audit(db, actor, "MODERATION_DECISION", target, decision)
        db.commit()
        return {"ok": True, "message": "Đã cập nhật quyết định hồ sơ."}

    @app.post("/api/reports/{report_id}/spadmin-decision")
    def decide_spadmin_report(request: Request, report_id: str, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); actor = get_current_user(request, db)
        if actor.role != "superadmin":
            raise HTTPException(403, "Chỉ SPADMIN mới được xét yêu cầu phạt RUBY chuyển cấp.")
        report = db.get(ModerationReport, report_id)
        if not report or report.channel != "spadmin" or report.report_type != "ruby_penalty_request" or report.status != "pending_spadmin":
            raise HTTPException(404, "Không tìm thấy yêu cầu RUBY đang chờ SPADMIN.")
        target = db.get(User, report.target_id)
        if not target or active_membership(target) != "ruby":
            report.status = "closed"; report.updated_at = utcnow(); db.commit()
            raise HTTPException(409, "Tài khoản mục tiêu không còn là RUBY; hồ sơ đã đóng.")
        decision = str(payload.get("decision", "reject"))
        if decision == "approve":
            request_message = next((m for m in (report.messages or []) if m.get("requested_amount") is not None), {})
            spec = str(request_message.get("requested_amount", "")).strip()
            balance = Decimal(str(target.balance))
            if spec.endswith("%"):
                try: pct = Decimal(spec[:-1])
                except InvalidOperation: raise HTTPException(400, "Phần trăm trong yêu cầu không hợp lệ.")
                if not pct.is_finite() or pct <= 0 or pct > 1000 or balance <= 0:
                    raise HTTPException(400, "Phần trăm cần >0%, tối đa 1000% và số dư mục tiêu phải dương.")
                amount = (balance * pct / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            else:
                try: amount = Decimal(re.sub(r"[^0-9]", "", spec))
                except InvalidOperation: raise HTTPException(400, "Số tiền trong yêu cầu không hợp lệ.")
                if amount <= 0 or amount > Decimal(str(PENALTY_LIMITS["superadmin"])):
                    raise HTTPException(400, "Mức phạt cố định của SPADMIN tối đa 100 tỷ USD.")
            state = dict(target.membership or {})
            if int(state.get("ruby_immunity_left", 0) or 0) > 0:
                state["ruby_immunity_left"] = int(state.get("ruby_immunity_left", 0)) - 1
                target.membership = state
                issue_inbox(target, "💗 TV RUBY đã kích hoạt miễn phạt", f"Yêu cầu đã được SPADMIN duyệt nhưng RUBY miễn phạt lần này. Còn {state['ruby_immunity_left']}/3 lượt.")
                detail = f"RUBY miễn phạt; còn {state['ruby_immunity_left']}/3 lượt."
            else:
                old_balance = Decimal(str(target.balance)); target.balance = old_balance - amount
                debt = max(Decimal("0"), -target.balance)
                penalties = list(target.penalties or [])
                penalties.insert(0, {"amount": float(amount), "old_balance": float(old_balance), "new_balance": float(target.balance),
                                     "actor": actor.username, "actor_role": role_label(actor), "reason": report.reason, "at": utc_iso(utcnow())})
                target.penalties = penalties[:100]
                add_trade(db, target, "PHẠT RUBY QUA SPADMIN", "USD", float(amount), 1, float(amount), f"Nợ sàn: {debt:,.2f} USD.")
                issue_inbox(target, "⚠️ Yêu cầu xử lý RUBY đã được duyệt", f"SPADMIN {actor.username} áp dụng mức phạt {amount:,.0f} USD. Nợ sàn: {debt:,.2f} USD.")
                detail = f"SPADMIN áp dụng phạt {amount} USD; nợ={debt}."
            report.status = "approved_penalty"
            decision_text = detail
        elif decision == "reject":
            report.status = "rejected"
            decision_text = "SPADMIN từ chối yêu cầu phạt RUBY."
            issue_inbox(db.get(User, report.reporter_id), "Yêu cầu xử lý RUBY bị từ chối", decision_text)
        else:
            raise HTTPException(400, "Quyết định SPADMIN không hợp lệ.")
        report.messages = list(report.messages or []) + [{"from": actor.username, "text": decision_text, "at": utc_iso(utcnow())}]
        report.updated_at = utcnow()
        add_audit(db, actor, "SPADMIN_RUBY_PENALTY_DECISION", target, decision_text)
        db.commit()
        return {"ok": True, "message": decision_text}

    @app.get("/api/chat/{room}")
    def get_chat(room: str, request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        if room not in ("total", "members", "qtv"):
            raise HTTPException(404, "Phòng chat không tồn tại.")
        if room == "members" and not (user.is_owner or user.role in ("coordinate", "admin", "superadmin") or active_membership(user)):
            raise HTTPException(403, "CHAT THÀNH VIÊN chỉ dành cho thành viên có gói hoặc QTV.")
        if room == "qtv" and not staff_can_use_qtv_chat(user):
            raise HTTPException(403, "CHAT QTV chỉ dành cho OWNER/SPADMIN/ADMIN/TỌA ĐỘ.")
        rows = db.scalars(select(ChatMessage).where(ChatMessage.room == room).order_by(desc(ChatMessage.created_at)).limit(100)).all()
        rows.reverse()
        return {"messages": [{"name": r.sender_name, "role": r.sender_role, "text": r.message, "at": utc_iso(r.created_at)} for r in rows]}

    @app.post("/api/chat/{room}")
    def send_chat(room: str, request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        if room not in ("total", "members", "qtv"):
            raise HTTPException(404, "Phòng chat không tồn tại.")
        if room == "members" and not (user.is_owner or user.role in ("coordinate", "admin", "superadmin") or active_membership(user)):
            raise HTTPException(403, "CHAT THÀNH VIÊN chỉ dành cho thành viên có gói hoặc QTV.")
        if room == "qtv" and not staff_can_use_qtv_chat(user):
            raise HTTPException(403, "CHAT QTV chỉ dành cho QTV.")
        message = " ".join(str(payload.get("message", "")).split())[:500]
        if not message:
            raise HTTPException(400, "Nhập tin nhắn trước khi gửi.")
        sender_role = role_label(user)
        tier = active_membership(user)
        if room != "qtv" and tier and tier != "emerald":
            sender_role += " · " + PREMIUM_TIERS[tier]["short"]
        db.add(ChatMessage(room=room, sender_id=user.id, sender_name=(user.profile or {}).get("display_name") or user.username,
                           sender_role=sender_role, message=message))
        db.commit()
        return {"ok": True, "message": "Đã gửi tin nhắn."}

    @app.get("/api/inbox")
    def inbox(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        return {"items": user.inbox or []}

    @app.post("/api/inbox/read")
    def inbox_read(request: Request, payload: Payload, db: Session = Depends(get_db)):
        check_csrf(request); user = get_current_user(request, db)
        ids = set(payload.get("ids", []))
        inbox_items = list(user.inbox or [])
        for item in inbox_items:
            if not ids or item.get("id") in ids:
                item["read"] = True
        user.inbox = inbox_items; db.commit()
        return {"ok": True}

    @app.get("/api/admin/audit")
    def audit(request: Request, db: Session = Depends(get_db)):
        actor = get_current_user(request, db); require_owner(actor)
        rows = db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(250)).all()
        return {"events": [{"action": r.action, "details": r.details, "at": utc_iso(r.created_at),
                            "actor": db.get(User, r.actor_id).username if r.actor_id and db.get(User, r.actor_id) else "SYSTEM",
                            "target": db.get(User, r.target_id).username if r.target_id and db.get(User, r.target_id) else "—"} for r in rows]}

    @app.get("/api/admin/backup")
    def backup(request: Request, db: Session = Depends(get_db)):
        actor = get_current_user(request, db); require_owner(actor)
        users = db.scalars(select(User)).all()
        payload = {"format": "sanchungkhoan-web-backup-v2", "created_at": utc_iso(utcnow()),
                   "created_by": actor.username,
                   "users": [{"username": u.username, "account_id": u.account_id, "role": u.role,
                              "is_owner": u.is_owner, "balance": str(u.balance), "holdings": u.holdings,
                              "membership": u.membership, "profile": u.profile,
                              "banned": u.banned, "ban_reason": u.ban_reason} for u in users]}
        return JSONResponse(payload, headers={"Content-Disposition": "attachment; filename=sanchungkhoan-backup.json"})

    @app.get("/api/forecast")
    def forecast(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        if not (user.is_owner or user.role in ("admin", "superadmin") or active_membership(user) == "emerald"):
            raise HTTPException(403, "Bảng dự báo đặc quyền chỉ dành cho QTV hoặc TV LỤC BẢO.")
        state = advance_market(db); db.commit()
        results = []
        for sym, info in ASSETS.items():
            values = [float(x) for x in state.history.get(sym, [])]
            if len(values) < 20: continue
            ma5 = sum(values[-5:]) / 5; ma20 = sum(values[-20:]) / 20
            trend = "TĂNG" if ma5 > ma20 else "GIẢM" if ma5 < ma20 else "ĐI NGANG"
            results.append({"symbol": sym, "price": float(state.prices[sym]), "trend": trend,
                            "confidence": "THẤP — chỉ số tham khảo mô phỏng", "horizon_seconds": 5 if user.is_owner or user.role in ("admin", "superadmin") else 5})
        return {"items": results, "note": "Dữ liệu chỉ là mô phỏng theo MA; không bảo đảm hướng giá và không phải lời khuyên đầu tư."}

    return app


app = create_app()
