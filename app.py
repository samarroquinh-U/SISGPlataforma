"""Factory y punto de entrada de la aplicación Flask."""

import os
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unicodedata import combining, normalize

from dotenv import load_dotenv
from flask import Flask, session


SPANISH_MONTHS = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)
SPANISH_SHORT_MONTHS = (
    "Ene",
    "Feb",
    "Mar",
    "Abr",
    "May",
    "Jun",
    "Jul",
    "Ago",
    "Sep",
    "Oct",
    "Nov",
    "Dic",
)


def formato_quetzal(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return str(value)
    return f"Q {amount:,.2f}"


def formato_miles(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{Decimal(str(value).replace(',', '')):,.0f}"
    except (InvalidOperation, ValueError):
        return str(value)


def formato_fecha(value) -> str:
    if value is None or value == "":
        return "—"
    parsed = value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(parsed, datetime):
        month = SPANISH_MONTHS[parsed.month - 1]
        if parsed.day == 1 and parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
            return f"{month} {parsed.year}"
        date_label = f"{parsed.day:02d} {SPANISH_SHORT_MONTHS[parsed.month - 1]} {parsed.year}"
        if parsed.hour or parsed.minute or parsed.second:
            return f"{date_label}, {parsed:%H:%M}"
        return date_label
    if isinstance(parsed, date):
        return f"{SPANISH_MONTHS[parsed.month - 1]} {parsed.year}" if parsed.day == 1 else (
            f"{parsed.day:02d} {SPANISH_SHORT_MONTHS[parsed.month - 1]} {parsed.year}"
        )
    return str(value)


def formato_celda(value, column: str) -> str:
    normalized_column = normalize("NFKD", column.lower())
    normalized_column = "".join(
        char for char in normalized_column if not combining(char)
    )
    if any(
        token in normalized_column for token in ("monto", "costo", "quetzal")
    ):
        return formato_quetzal(value)
    if any(
        token in normalized_column
        for token in (
            "atenciones",
            "casos",
            "prestaciones",
            "servicios",
            "nacimientos",
            "defunciones",
            "filas leidas",
            "filas insertadas",
            "filas procesadas",
        )
    ):
        return formato_miles(value)
    if any(
        token in normalized_column
        for token in (
            "fecha",
            "periodo",
            "period",
            "created at",
            "updated at",
            "timestamp",
        )
    ):
        return formato_fecha(value)
    return "—" if value is None else str(value)


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower()
        == "true",
        ETL_INPUT_PATH=str(
            Path(__file__).resolve().parent
            / "data_entrada"
            / "atenciones_ejemplo.csv"
        ),
    )
    if test_config:
        app.config.update(test_config)

    app.jinja_env.filters["formato_quetzal"] = formato_quetzal
    app.jinja_env.filters["formato_miles"] = formato_miles
    app.jinja_env.filters["formato_fecha"] = formato_fecha
    app.jinja_env.filters["formato_celda"] = formato_celda

    from routes.auth_routes import auth_bp
    from routes.dashboard_routes import dashboard_bp
    from routes.main_routes import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    @app.before_request
    def ensure_csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)

    @app.context_processor
    def inject_template_state():
        return {
            "csrf_token": session.get("csrf_token"),
            "current_group_id": session.get("group_id"),
            "current_username": session.get("username"),
        }

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
