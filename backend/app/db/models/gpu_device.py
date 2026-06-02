"""GpuDevice ORM model — local GPU inventory (host + device_index)."""
from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GpuDevice(Base):
    __tablename__ = "gpu_devices"
    __table_args__ = (
        UniqueConstraint("host", "device_index", name="uq_gpu_devices_host_idx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    device_index: Mapped[int] = mapped_column(Integer, nullable=False)  # /dev/nvidiaN
    uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cuda_capability: Mapped[str | None] = mapped_column(String(8), nullable=True)
    memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="free")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GpuDevice {self.host}:{self.device_index} {self.status}>"
