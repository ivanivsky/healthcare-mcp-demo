"""
Shared PostgreSQL connection pool for backend and MCP server.
Uses asyncpg for async PostgreSQL access.

Connection string is read from DB_CONNECTION_STRING environment variable.
Production uses Cloud SQL Unix socket, local dev uses TCP via Cloud SQL Auth Proxy.
"""
import os
import logging
import asyncpg

logger = logging.getLogger(__name__)

# Module-level connection pool
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get the connection pool, initializing if necessary."""
    global _pool
    if _pool is None:
        await init_pool()
    return _pool


async def init_pool() -> None:
    """Initialize the asyncpg connection pool."""
    global _pool

    connection_string = os.environ.get("DB_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError(
            "DB_CONNECTION_STRING environment variable not set. "
            "Set it to your PostgreSQL connection string.\n"
            "Local dev: postgresql://healthcare_user:PASSWORD@localhost:5432/healthcare\n"
            "Production: Set via Secret Manager"
        )

    logger.info("DB_POOL initializing...")
    try:
        _pool = await asyncpg.create_pool(
            dsn=connection_string,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("DB_POOL ready")
    except Exception as e:
        logger.error(f"DB_POOL initialization failed: {e}")
        raise


async def close_pool() -> None:
    """Close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("DB_POOL closed")


async def execute(query: str, *args) -> str:
    """Execute a query that returns no rows (INSERT, UPDATE, DELETE, DDL)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def executemany(query: str, args_list: list) -> None:
    """Execute a query multiple times with different arguments."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(query, args_list)


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    """Execute a query and return all rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args) -> asyncpg.Record | None:
    """Execute a query and return one row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args):
    """Execute a query and return a single value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)
