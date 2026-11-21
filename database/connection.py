"""Creación y administración de conexiones MySQL por grupo."""

import os
import re
from functools import lru_cache

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

from config.groups_config import GROUPS

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def validate_identifier(value: str) -> str:
    """Valida identificadores de esquema/tabla provenientes de configuración."""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Identificador SQL no válido: {value!r}")
    return value


def credentials_for_group(group_id: int) -> tuple[str, str]:
    """Obtiene credenciales del entorno sin incluir contraseñas en el código."""
    group = GROUPS[group_id]
    username = os.getenv(group["username_env"], "").strip()
    password = os.getenv(group["password_env"], "")
    if not username or not password:
        raise RuntimeError(
            f"Faltan {group['username_env']} o {group['password_env']} en el entorno."
        )
    return username, password


@lru_cache(maxsize=32)
def _engine_for_connection(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> Engine:
    url = URL.create(
        "mysql+pymysql",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    )
    connect_args = {"connect_timeout": 8}
    if os.getenv("DB_SSL_DISABLED", "false").lower() != "true":
        connect_args["ssl"] = {"check_hostname": True}
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )


def get_engine(group_id: int) -> Engine:
    """Devuelve el motor SQLAlchemy asociado al esquema del grupo."""
    group = GROUPS[group_id]
    username, password = credentials_for_group(group_id)
    host = os.getenv("DB_HOST", "").strip()
    port = int(os.getenv("DB_PORT", "3306"))
    if not host:
        raise RuntimeError("DB_HOST no está configurado.")
    return _engine_for_connection(
        host,
        port,
        validate_identifier(group["database"]),
        username,
        password,
    )


def verify_connection(group_id: int, username: str, password: str) -> bool:
    """Comprueba credenciales reales ejecutando SELECT 1 en MySQL."""
    group = GROUPS[group_id]
    host = os.getenv("DB_HOST", "").strip()
    if not host:
        raise RuntimeError("DB_HOST no está configurado.")
    url = URL.create(
        "mysql+pymysql",
        username=username,
        password=password,
        host=host,
        port=int(os.getenv("DB_PORT", "3306")),
        database=validate_identifier(group["database"]),
        query={"charset": "utf8mb4"},
    )
    connect_args = {"connect_timeout": 8}
    if os.getenv("DB_SSL_DISABLED", "false").lower() != "true":
        connect_args["ssl"] = {"check_hostname": True}
    engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
