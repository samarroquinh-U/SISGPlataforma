"""Factory y punto de entrada de la aplicación Flask."""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, session


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
