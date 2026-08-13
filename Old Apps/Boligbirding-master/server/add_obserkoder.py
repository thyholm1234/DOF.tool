import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from server import Base, User, Obserkode, DATABASE_URL

# Liste af obserkoder
obserkoder = [
    "8220CVH",
    "8900LTP",
    "8240MDH",
    "6600JB",
    "8230ESB",
    "8240EKM",
    "8000KECL",
    "8960JODK",
    "9520APS",
    "6600JF"
]

async def main():
    engine = create_async_engine(DATABASE_URL, echo=False, future=True)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        for kode in obserkoder:
            # Tilføj til User hvis ikke findes
            res = await session.execute(
                User.__table__.select().where(User.obserkode == kode)
            )
            if not res.first():
                session.add(User(obserkode=kode, navn=kode))
            # Tilføj til Obserkode hvis ikke findes
            res2 = await session.execute(
                Obserkode.__table__.select().where(Obserkode.kode == kode)
            )
            if not res2.first():
                session.add(Obserkode(kode=kode))
        await session.commit()
    await engine.dispose()
    print("Obserkoder tilføjet!")

if __name__ == "__main__":
    asyncio.run(main())