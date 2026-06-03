from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[int | None] = mapped_column()
    connector_name: Mapped[str | None] = mapped_column(String(128))
    connector_image: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64))
    status_detail: Mapped[dict | None] = mapped_column(JSON)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw: Mapped[dict | None] = mapped_column(JSON)

    accounts: Mapped[list["Account"]] = relationship(back_populates="item", cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    type: Mapped[str | None] = mapped_column(String(32))
    subtype: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(256))
    marketing_name: Mapped[str | None] = mapped_column(String(256))
    number: Mapped[str | None] = mapped_column(String(64))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str | None] = mapped_column(String(8))
    owner: Mapped[str | None] = mapped_column(String(256))
    taxnumber: Mapped[str | None] = mapped_column(String(32))
    credit_data: Mapped[dict | None] = mapped_column(JSON)
    bank_data: Mapped[dict | None] = mapped_column(JSON)
    raw: Mapped[dict | None] = mapped_column(JSON)

    item: Mapped[Item] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str | None] = mapped_column(Text)
    description_raw: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    category_id: Mapped[str | None] = mapped_column(String(64))
    payment_data: Mapped[dict | None] = mapped_column(JSON)
    credit_card_metadata: Mapped[dict | None] = mapped_column(JSON)
    merchant: Mapped[dict | None] = mapped_column(JSON)
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw: Mapped[dict | None] = mapped_column(JSON)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    raw_line: Mapped[str | None] = mapped_column(Text)
    mega: Mapped[str | None] = mapped_column(String(64), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("category_rules.id", ondelete="SET NULL"), index=True)

    account: Mapped["Account"] = relationship(back_populates="transactions")


class Investment(Base):
    __tablename__ = "investments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    type: Mapped[str | None] = mapped_column(String(64))
    subtype: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(256))
    code: Mapped[str | None] = mapped_column(String(64))
    issuer: Mapped[str | None] = mapped_column(String(256))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount_original: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    amount_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str | None] = mapped_column(String(8))
    date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict | None] = mapped_column(JSON)


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event: Mapped[str | None] = mapped_column(String(64))
    item_id: Mapped[str | None] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict | None] = mapped_column(JSON)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_path: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    issuer: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str] = mapped_column(String(64))
    period_year: Mapped[int | None] = mapped_column(index=True)
    period_month: Mapped[int | None] = mapped_column(index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    summary: Mapped[dict | None] = mapped_column(JSON)
    file_size: Mapped[int | None] = mapped_column()
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_text: Mapped[str | None] = mapped_column(Text)


class ParseWarning(Base):
    __tablename__ = "parse_warnings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    raw_line: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pattern_type: Mapped[str] = mapped_column(String(16))
    pattern: Mapped[str] = mapped_column(String(256), index=True)
    account_type: Mapped[str | None] = mapped_column(String(16))
    sign: Mapped[str | None] = mapped_column(String(8))
    mega: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(128), index=True)
    is_internal: Mapped[bool] = mapped_column(default=False)
    priority: Mapped[int] = mapped_column(default=100)
    enabled: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TransactionOverride(Base):
    __tablename__ = "transaction_overrides"

    tx_id: Mapped[str] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True)
    mega: Mapped[str | None] = mapped_column(String(64), index=True)
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    is_internal: Mapped[bool | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[str | None] = mapped_column(String(64))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mega: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    period: Mapped[str] = mapped_column(String(16), default="monthly")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str] = mapped_column(String(8), default="BRL")
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    color: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TransactionTag(Base):
    __tablename__ = "transaction_tags"

    tx_id: Mapped[str] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str] = mapped_column(String(8), default="BRL")
    target_date: Mapped[date | None] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(32), default="net_worth")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SavedView(Base):
    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
