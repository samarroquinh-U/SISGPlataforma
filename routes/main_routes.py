"""Rutas públicas del portal institucional."""

from flask import Blueprint, render_template

from config.groups_config import GROUPS

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def home():
    return render_template("home.html", groups=GROUPS)

