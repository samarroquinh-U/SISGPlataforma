"""Rutas protegidas de tableros, conexiones, ingestas y ETL."""

from functools import wraps

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import SQLAlchemyError

from config.groups_config import GROUPS
from etl.pipeline import ejecutar_etl_completo
from services.auth_service import AuthenticationError, authenticate
from services.platform_service import (
    central_dashboard,
    connection_status,
    group_dashboard,
    ingestion_form,
    submit_ingestion,
)

dashboard_bp = Blueprint("dashboard", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "group_id" not in session:
            flash("Ingresa al portal para continuar.", "error")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def csrf_valid() -> bool:
    return request.form.get("csrf_token") == session.get("csrf_token")


def dashboard_redirect(group_id: int):
    if group_id == 8:
        return redirect(url_for("dashboard.central"))
    return redirect(url_for("dashboard.group", group_id=group_id))


@dashboard_bp.get("/dashboard/central")
@login_required
def central():
    if session["group_id"] != 8:
        flash("Acceso denegado: esta sección es exclusiva del Grupo 8.", "error")
        return dashboard_redirect(session["group_id"])
    data = {}
    try:
        data = central_dashboard()
    except (SQLAlchemyError, RuntimeError, ValueError) as error:
        flash(f"No se pudo consultar el esquema central: {error}", "error")
    return render_template(
        "dashboard_central.html",
        data=data,
        groups=GROUPS,
        active_group=session["group_id"],
    )


@dashboard_bp.get("/dashboard/grupo/<int:group_id>")
@login_required
def group(group_id: int):
    if group_id not in GROUPS or group_id == 8 or session["group_id"] != group_id:
        flash("Acceso denegado: solo puedes consultar tu pestaña asignada.", "error")
        return dashboard_redirect(session["group_id"])
    data = {}
    form = {"fields": [], "catalogs": {}}
    try:
        data = group_dashboard(group_id)
        form = ingestion_form(group_id)
    except (SQLAlchemyError, RuntimeError, ValueError) as error:
        flash(f"No se pudo cargar la vista del grupo: {error}", "error")
    return render_template(
        "dashboard_grupo.html",
        data=data,
        form=form,
        active_group=group_id,
        groups=GROUPS,
    )


@dashboard_bp.post("/ingesta")
@login_required
def ingestion():
    group_id = session["group_id"]
    if not csrf_valid():
        flash("La sesión del formulario expiró.", "error")
    else:
        try:
            submit_ingestion(group_id, request.form.to_dict())
            flash("Registro enviado al buzón de ingesta.", "success")
        except (SQLAlchemyError, RuntimeError, ValueError, PermissionError) as error:
            flash(f"No se pudo completar la ingesta: {error}", "error")
    return dashboard_redirect(group_id)


@dashboard_bp.post("/cambiar-conexion")
@login_required
def change_connection():
    if not csrf_valid():
        flash("La sesión del formulario expiró.", "error")
        return dashboard_redirect(session["group_id"])
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    try:
        identity = authenticate(username, password)
        status = connection_status(identity["group_id"])
    except (AuthenticationError, SQLAlchemyError, RuntimeError, ValueError) as error:
        flash(f"No se pudo cambiar la conexión: {error}", "error")
    else:
        session["group_id"] = identity["group_id"]
        session["username"] = identity["username"]
        flash(
            f"Conexión activa: {status['group_name']} · {status['database']}.",
            "success",
        )
    return dashboard_redirect(session["group_id"])


@dashboard_bp.post("/ejecutar-etl")
@login_required
def run_etl():
    if session["group_id"] != 8:
        flash("Acceso denegado: solo el Grupo 8 puede ejecutar el ETL.", "error")
        return dashboard_redirect(session["group_id"])
    if not csrf_valid():
        flash("La sesión del formulario expiró.", "error")
        return dashboard_redirect(8)
    try:
        result = ejecutar_etl_completo()
    except (OSError, ValueError, RuntimeError, SQLAlchemyError) as error:
        flash(f"El ETL no pudo completarse: {error}", "error")
    else:
        flash(
            "ETL completado: "
            f"{result['extracted']} recibidas, {result['transformed']} limpias/"
            f"homologadas, {result['loaded']} enviadas al DW y "
            f"{result['already_processed']} omitidas por ya estar procesadas.",
            "success",
        )
    return dashboard_redirect(8)

