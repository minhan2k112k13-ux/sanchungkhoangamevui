from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_norm: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(8), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="user", nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    balance: Mapped[object] = mapped_column(Numeric(32, 2), default=500000, nullable=False)
    holdings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    membership: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    profile: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    inbox: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    debts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    penalties: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ban_reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    role_request_status: Mapped[str] = mapped_column(String(24), default="none", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    trades: Mapped[list["TradeRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class TradeRecord(Base):
    __tablename__ = "trade_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    trade_type: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(24), default="USD", nullable=False)
    quantity: Mapped[object] = mapped_column(Numeric(32, 8), default=0, nullable=False)
    price: Mapped[object] = mapped_column(Numeric(32, 8), default=0, nullable=False)
    amount: Mapped[object] = mapped_column(Numeric(32, 2), default=0, nullable=False)
    memo: Mapped[str] = mapped_column(String(600), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    user: Mapped[User] = relationship(back_populates="trades")


class MarketState(Base):
    __tablename__ = "market_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    prices: Mapped[dict] = mapped_column(JSON, nullable=False)
    history: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AppSettings(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    role_limits: Mapped[dict] = mapped_column(JSON, default=lambda: {"superadmin": 5, "admin": 10, "coordinate": 20}, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_name: Mapped[str] = mapped_column(String(60), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AdminRequest(Base):
    __tablename__ = "admin_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    applicant_note: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    review_note: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModerationReport(Base):
    __tablename__ = "moderation_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reporter_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(32), default="complaint", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    messages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[str] = mapped_column(String(700), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


Index("ix_reports_channel_status", ModerationReport.channel, ModerationReport.status)
Index("ix_chat_room_created", ChatMessage.room, ChatMessage.created_at)
