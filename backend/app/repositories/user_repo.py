"""Data access for operator accounts."""

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalars().first()

    async def count(self) -> int:
        result = await self.db.execute(select(func.count()).select_from(User))
        return result.scalar_one()

    async def create(self, username: str, password_hash: str, role: str) -> User:
        user = User(username=username, password_hash=password_hash, role=role, is_active=True)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def touch_last_login(self, user_id: int) -> None:
        """Record a successful sign-in.

        Its own statement rather than a mutation on the loaded object: the
        login path should not risk flushing unrelated pending changes, and a
        failure to record the timestamp must never fail the login itself.
        """
        await self.db.execute(
            update(User).where(User.id == user_id).values(last_login_at=datetime.now(timezone.utc))
        )
        await self.db.commit()
