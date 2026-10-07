"""Lead persistence."""

from .base import LeadRepository
from .sqlalchemy_repo import SqlAlchemyLeadRepository

__all__ = ["LeadRepository", "SqlAlchemyLeadRepository"]
