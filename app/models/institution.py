from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(32), default="coaching")
    board_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON array

    centers: Mapped[list["Center"]] = relationship(back_populates="institution")
    users: Mapped[list["User"]] = relationship(back_populates="institution")


class Center(Base):
    __tablename__ = "centers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    name: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(128), default="")
    student_count: Mapped[int] = mapped_column(default=0)
    batch_count: Mapped[int] = mapped_column(default=0)

    institution: Mapped["Institution"] = relationship(back_populates="centers")
