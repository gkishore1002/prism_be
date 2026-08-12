from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.registry import institution_fk_target


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(32))

    grades: Mapped[list["Grade"]] = relationship(back_populates="board", cascade="all, delete-orphan")


class Grade(Base):
    __tablename__ = "grades"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"))
    name: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=8)

    board: Mapped["Board"] = relationship(back_populates="grades")
    subjects: Mapped[list["Subject"]] = relationship(back_populates="grade", cascade="all, delete-orphan")


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    grade_id: Mapped[str] = mapped_column(ForeignKey("grades.id"))
    name: Mapped[str] = mapped_column(String(128))
    icon: Mapped[str] = mapped_column(String(64), default="book")
    color: Mapped[str] = mapped_column(String(16), default="#6366f1")

    grade: Mapped["Grade"] = relationship(back_populates="subjects")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="subject", cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id"))
    name: Mapped[str] = mapped_column(String(128))
    order: Mapped[int] = mapped_column(Integer, default=1)

    subject: Mapped["Subject"] = relationship(back_populates="chapters")
    topics: Mapped[list["Topic"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"))
    name: Mapped[str] = mapped_column(String(128))
    weight: Mapped[float] = mapped_column(default=0.25)

    chapter: Mapped["Chapter"] = relationship(back_populates="topics")
    questions: Mapped[list["Question"]] = relationship(back_populates="topic_rel", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"))
    institution_id: Mapped[str] = mapped_column(ForeignKey(institution_fk_target()))
    board: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(128))
    chapter: Mapped[str] = mapped_column(String(128))
    topic_name: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    marks: Mapped[int] = mapped_column(Integer, default=1)
    question_type: Mapped[str] = mapped_column(String(16), default="mcq")
    status: Mapped[str] = mapped_column(String(16), default="active")
    option_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_c: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_d: Mapped[str | None] = mapped_column(Text, nullable=True)
    correct_answer: Mapped[str | None] = mapped_column(String(8), nullable=True)

    topic_rel: Mapped["Topic"] = relationship(back_populates="questions")
