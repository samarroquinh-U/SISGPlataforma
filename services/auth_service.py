"""Autenticación real y autorización por grupo."""

import hmac

from sqlalchemy.exc import SQLAlchemyError

from config.groups_config import GROUPS
from database.connection import credentials_for_group, verify_connection


class AuthenticationError(Exception):
    """Error legible durante autenticación o validación de acceso."""


def authenticate(username: str, password: str) -> dict:
    """Valida usuario asignado y verifica el acceso a MySQL."""
    if not username or not password:
        raise AuthenticationError("Ingresa usuario y contraseña.")
    for group_id, group in GROUPS.items():
        try:
            expected_username, configured_password = credentials_for_group(group_id)
        except (RuntimeError, KeyError):
            continue
        if not hmac.compare_digest(username, expected_username):
            continue
        if not hmac.compare_digest(password, configured_password):
            raise AuthenticationError("Usuario o contraseña incorrectos.")
        try:
            if verify_connection(group_id, username, password):
                return {"group_id": group_id, "username": username}
        except (SQLAlchemyError, OSError, RuntimeError, ValueError) as error:
            raise AuthenticationError(
                f"No fue posible validar la conexión MySQL: {error}"
            ) from error
    raise AuthenticationError(
        "Cuenta no configurada. Define sus credenciales en las variables de entorno."
    )
