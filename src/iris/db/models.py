"""IRIS metadata schema.

firmadyne-compatible core tables (brand / image / object / object_to_image /
product) plus IRIS-native tables for run tracking, failure profiling and
repair-action ledger.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Brand(Base):
    __tablename__ = "brand"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class Image(Base):
    __tablename__ = "image"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String)
    brand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("brand.id", ondelete="CASCADE"), default=1, nullable=False
    )
    hash: Mapped[Optional[str]] = mapped_column(String, unique=True)
    rootfs_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    kernel_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    arch: Mapped[Optional[str]] = mapped_column(String)
    kernel_version: Mapped[Optional[str]] = mapped_column(String)
    target_type: Mapped[Optional[str]] = mapped_column(String)  # router / camera / ...



class FileObject(Base):
    __tablename__ = "object"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)


class ObjectToImage(Base):
    __tablename__ = "object_to_image"
    __table_args__ = (UniqueConstraint("oid", "iid", "filename", name="oid_iid_filename_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    oid: Mapped[int] = mapped_column(
        Integer, ForeignKey("object.id", ondelete="CASCADE"), nullable=False, index=True
    )
    iid: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    regular_file: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[Optional[int]] = mapped_column(Integer)
    uid: Mapped[Optional[int]] = mapped_column(Integer)
    gid: Mapped[Optional[int]] = mapped_column(Integer)


class Product(Base):
    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("iid", "product", "version", "build", name="product_version_build_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    iid: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String, nullable=False)
    mib_hash: Mapped[Optional[str]] = mapped_column(String)
    mib_url: Mapped[Optional[str]] = mapped_column(String)
    sdk_hash: Mapped[Optional[str]] = mapped_column(String)
    sdk_url: Mapped[Optional[str]] = mapped_column(String)
    product: Mapped[Optional[str]] = mapped_column(String)
    version: Mapped[Optional[str]] = mapped_column(String)
    build: Mapped[Optional[str]] = mapped_column(String)
    date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    mib_filename: Mapped[Optional[str]] = mapped_column(String)
    sdk_filename: Mapped[Optional[str]] = mapped_column(String)


class EmulationRun(Base):
    __tablename__ = "emulation_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    iid: Mapped[int] = mapped_column(
        Integer, ForeignKey("image.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String, default="check")  # check / run / analyze
    network_type: Mapped[Optional[str]] = mapped_column(String)  # normal / reload / bridge / ...
    web_reachable: Mapped[Optional[bool]] = mapped_column(Boolean)
    ping_reachable: Mapped[Optional[bool]] = mapped_column(Boolean)
    ip: Mapped[Optional[str]] = mapped_column(String)
    time_web: Mapped[Optional[int]] = mapped_column(Integer)
    time_ping: Mapped[Optional[int]] = mapped_column(Integer)
    result: Mapped[Optional[bool]] = mapped_column(Boolean)
    result_kind: Mapped[Optional[str]] = mapped_column(String)  # structured failure category
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class FailureProfile(Base):
    __tablename__ = "failure_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("emulation_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String, nullable=False)  # extraction / arch / boot / nvram / network / service
    signal: Mapped[Optional[str]] = mapped_column(String)
    log_fingerprint: Mapped[Optional[str]] = mapped_column(String)
    detail: Mapped[Optional[dict]] = mapped_column(JSON)


class RepairAction(Base):
    __tablename__ = "repair_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("emulation_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String, nullable=False)  # rule / llm / manual
    rule_id: Mapped[Optional[str]] = mapped_column(String)
    evidence: Mapped[Optional[str]] = mapped_column(String)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)  # promoted to deterministic rule