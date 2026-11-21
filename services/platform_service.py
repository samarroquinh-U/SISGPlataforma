"""Operaciones de negocio para tableros, conexiones e ingestas."""

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from config.groups_config import CATALOG_VIEWS, GROUPS
from database.connection import get_engine, validate_identifier
from database.queries import (
    fetch_catalog_options,
    fetch_central_metrics,
    fetch_count,
    fetch_rows,
    fetch_table_columns,
    insert_inbox_row,
)

logger = logging.getLogger(__name__)


def connection_status(group_id: int) -> dict:
    group = GROUPS[group_id]
    engine = get_engine(group_id)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"database": group["database"], "group_name": group["name"]}


def group_dashboard(group_id: int) -> dict:
    group = GROUPS[group_id]
    engine = get_engine(group_id)
    with engine.connect() as connection:
        records = fetch_rows(connection, validate_identifier(group["view"]))
        total = fetch_count(connection, validate_identifier(group["view"]))
    columns = list(records[0].keys()) if records else []
    return {
        "group": group,
        "records": records,
        "columns": columns,
        "total": total,
    }


def central_dashboard() -> dict:
    engine = get_engine(8)
    with engine.connect() as connection:
        metrics = fetch_central_metrics(connection)
    return metrics


def ingestion_form(group_id: int) -> dict:
    group = GROUPS[group_id]
    if group.get("read_only") or not group.get("inbox"):
        return {"fields": [], "catalogs": {}}
    engine = get_engine(group_id)
    with engine.connect() as connection:
        fields = fetch_table_columns(connection, validate_identifier(group["inbox"]))
        catalogs = {}
        for catalog_view in CATALOG_VIEWS:
            try:
                catalogs[catalog_view] = fetch_catalog_options(connection, catalog_view)
            except SQLAlchemyError as error:
                logger.warning(
                    "No se pudo consultar el catálogo %s: %s",
                    catalog_view,
                    error,
                )
                catalogs[catalog_view] = []
    editable_fields = [
        field
        for field in fields
        if field["extra"] != "auto_increment"
        and field["default_value"] is None
        and field["nullable"] == "NO"
    ]
    field_catalogs = {}
    catalog_keywords = {
        "vw_cat_tiempo": ("tiempo", "fecha", "anio", "mes"),
        "vw_cat_geografia": ("geografia", "municipio", "departamento", "codigo_ine"),
        "vw_cat_establecimiento": ("establecimiento", "prestador"),
        "vw_cat_diagnostico": ("diagnostico", "cie10"),
        "vw_cat_servicio": ("servicio", "especialidad"),
    }
    for field in editable_fields:
        normalized_name = field["name"].lower()
        for view_name, keywords in catalog_keywords.items():
            if any(keyword in normalized_name for keyword in keywords):
                options = catalogs.get(view_name, [])
                if options:
                    field_catalogs[field["name"]] = options
                    break
    return {
        "fields": editable_fields,
        "catalogs": catalogs,
        "field_catalogs": field_catalogs,
    }


def submit_ingestion(group_id: int, submitted: dict) -> None:
    group = GROUPS[group_id]
    if group.get("read_only") or not group.get("inbox"):
        raise PermissionError("Este grupo tiene acceso de solo lectura.")
    engine = get_engine(group_id)
    with engine.begin() as connection:
        metadata = fetch_table_columns(connection, validate_identifier(group["inbox"]))
        required = {
            field["name"]
            for field in metadata
            if field["extra"] != "auto_increment"
            and field["default_value"] is None
            and field["nullable"] == "NO"
        }
        allowed_columns = {field["name"] for field in metadata}
        values = {
            key: value for key, value in submitted.items() if key in allowed_columns
        }
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError("Completa los campos obligatorios.")
        if not values:
            raise ValueError("No se recibieron campos válidos para la ingesta.")
        insert_inbox_row(
            connection,
            validate_identifier(group["inbox"]),
            list(values),
            values,
        )
