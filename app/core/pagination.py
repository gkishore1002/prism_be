"""Shared pagination helpers for list endpoints."""

from math import ceil
from typing import Generic, TypeVar

from pydantic import Field

from app.schemas.base import CamelModel

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None) -> int:
    if limit is None or limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


class PaginatedOut(CamelModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    limit: int = DEFAULT_LIMIT
    pages: int = 1


def paginate_query(query, page: int = 1, limit: int = DEFAULT_LIMIT):
    page = max(1, page)
    limit = clamp_limit(limit)
    total = query.count()
    items = query.offset((page - 1) * limit).limit(limit).all()
    pages = max(1, ceil(total / limit)) if limit else 1
    return items, total, page, limit, pages


def paginate_list(items: list[T], page: int = 1, limit: int = DEFAULT_LIMIT) -> PaginatedOut[T]:
    page = max(1, page)
    limit = clamp_limit(limit)
    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    pages = max(1, ceil(total / limit)) if limit else 1
    return PaginatedOut(items=items[start:end], total=total, page=page, limit=limit, pages=pages)
