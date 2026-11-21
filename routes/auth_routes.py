"""Inicio y cierre de sesión."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from services.auth_service import AuthenticationError, authenticate

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("csrf_token") != session.get("csrf_token"):
            flash("La sesión del formulario expiró. Intenta de nuevo.", "error")
            return redirect(url_for("auth.login"))
        try:
            identity = authenticate(
                request.form.get("username", "").strip(),
                request.form.get("password", ""),
            )
        except AuthenticationError as error:
            flash(str(error), "error")
        else:
            session.clear()
            session["group_id"] = identity["group_id"]
            session["username"] = identity["username"]
            if identity["group_id"] == 8:
                return redirect(url_for("dashboard.central"))
            return redirect(
                url_for("dashboard.group", group_id=identity["group_id"])
            )
    return render_template("login.html")


@auth_bp.post("/logout")
def logout():
    if request.form.get("csrf_token") != session.get("csrf_token"):
        flash("La sesión del formulario expiró.", "error")
        return redirect(url_for("main.home"))
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("main.home"))

