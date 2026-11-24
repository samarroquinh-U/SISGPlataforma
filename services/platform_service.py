"""Operaciones de negocio para tableros, conexiones e ingestas."""

import logging
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from unicodedata import combining, normalize

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from config.groups_config import CATALOG_VIEWS, GROUPS
from database.connection import get_engine, validate_identifier
from database.queries import (
    CATALOG_OUTPUT_LABELS,
    DISPLAY_FIELDS,
    fetch_catalog_options,
    fetch_central_metrics,
    fetch_count,
    fetch_rows,
    fetch_table_columns,
    insert_inbox_row,
)
from etl.extract import ANALYTICS_SCHEMA, INBOXES
from etl.load import IDEMPOTENCY_TABLE

logger = logging.getLogger(__name__)

METRIC_CANDIDATES = {
    "atenciones": ("atenciones", "cantidad_atenciones", "total_atenciones"),
    "casos": ("casos", "cantidad_casos", "total_casos"),
    "prestaciones": ("prestaciones", "cantidad_prestaciones", "total_prestaciones"),
    "servicios": ("servicios", "cantidad_servicios", "total_servicios"),
    "nacimientos": ("nacimientos", "cantidad_nacimientos", "total_nacimientos"),
    "defunciones": ("defunciones", "cantidad_defunciones", "total_defunciones"),
}
CATALOG_FIELD_HINTS = {
    "tiempo": ("tiempo", "fecha", "periodo", "mes"),
    "geografia": ("geografia", "municipio", "departamento", "codigo_ine"),
    "establecimiento": ("establecimiento", "prestador"),
    "diagnostico": ("diagnostico", "cie10"),
    "servicio": ("servicio", "especialidad"),
    "evento": ("evento",),
    "parto": ("parto",),
    "demografia": ("demografia", "grupo_edad"),
}
CATALOG_RAW_LABELS = {
    "tiempo": ("nombre_periodo", "nombre_mes", "periodo", "fecha"),
    "geografia": ("municipio_departamento", "nombre_municipio", "municipio", "departamento"),
    "establecimiento": ("nombre_establecimiento", "establecimiento", "razon_social"),
    "diagnostico": ("descripcion_diagnostico", "nombre_diagnostico", "diagnostico"),
    "servicio": ("nombre_servicio", "servicio", "especialidad"),
    "evento": ("nombre_evento", "evento"),
    "parto": ("nombre_parto", "tipo_parto", "parto"),
    "demografia": ("nombre_demografia", "grupo_demografico", "demografia"),
}
CATALOG_INPUT_KEYS = {
    ("geografia", "codigo"): ("codigo_ine", "codigo_geografia", "codigo_municipio"),
    ("diagnostico", "codigo"): ("codigo_cie10", "cie10", "codigo_diagnostico"),
    ("tiempo", "fecha"): ("fecha", "fecha_calendario", "fecha_completa"),
}


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
        records = fetch_rows(
            connection,
            validate_identifier(group["view"]),
            group.get("catalogs", ()),
        )
        total = fetch_count(connection, validate_identifier(group["view"]))
    records, columns = _present_records(records, group.get("catalogs", ()))
    kpis = _build_kpis(records, total, group)
    charts = _build_charts(records, group)
    return {
        "group": group,
        "records": records,
        "columns": columns,
        "total": total,
        "kpis": kpis,
        "charts": charts,
    }


def _plain_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def _human_header(column: str) -> str:
    if column in DISPLAY_FIELDS:
        return DISPLAY_FIELDS[column]
    normalized = column.lower()
    if normalized in DISPLAY_FIELDS:
        return DISPLAY_FIELDS[normalized]
    named_fields = {
        "nombre_establecimiento": "Establecimiento",
        "razon_social": "Establecimiento",
        "nombre_municipio": "Municipio",
        "nombre_departamento": "Departamento",
        "descripcion_diagnostico": "Diagnóstico CIE-10",
        "nombre_diagnostico": "Diagnóstico CIE-10",
        "nombre_servicio": "Servicio",
        "nombre_evento": "Evento",
        "nombre_parto": "Tipo de parto",
        "nombre_demografia": "Demografía",
        "fecha_atencion": "Fecha de atención",
        "fecha_evento": "Fecha del evento",
        "costo_total": "Costo Total",
    }
    return named_fields.get(normalized, column.replace("_", " ").strip().title())


def _present_records(raw_records, catalog_names: tuple[str, ...]):
    records = []
    source_columns = [
        column
        for column in (list(raw_records[0].keys()) if raw_records else [])
        if not column.startswith("__label_")
    ]
    label_columns = {
        name: f"__label_{name}"
        for name in catalog_names
        if raw_records and f"__label_{name}" in raw_records[0]
    }
    key_to_catalog = {}
    for catalog_name in catalog_names:
        metadata = CATALOG_VIEWS[catalog_name]
        for key in metadata["fact_keys"]:
            key_to_catalog[key.lower()] = catalog_name

    display_columns = []
    for column in source_columns:
        catalog_name = key_to_catalog.get(column.lower())
        if catalog_name:
            header = CATALOG_OUTPUT_LABELS[catalog_name]
        else:
            header = _human_header(column)
        if header not in display_columns:
            display_columns.append(header)

    for raw_row in raw_records:
        row = dict(raw_row)
        presented = {}
        for column in source_columns:
            catalog_name = key_to_catalog.get(column.lower())
            if catalog_name:
                label_column = label_columns.get(catalog_name)
                value = row.get(label_column) if label_column else None
                if value in (None, ""):
                    value = next(
                        (
                            row[source_column]
                            for source_column in source_columns
                            if source_column.lower()
                            in CATALOG_RAW_LABELS[catalog_name]
                            and row.get(source_column) not in (None, "")
                        ),
                        None,
                    )
                presented[CATALOG_OUTPUT_LABELS[catalog_name]] = (
                    value if value not in (None, "") else "No especificado"
                )
            else:
                presented[_human_header(column)] = _plain_value(row.get(column))
        records.append(presented)
    return records, display_columns


def _as_number(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _find_column(records: list[dict], candidates: tuple[str, ...]) -> str | None:
    if not records:
        return None

    def normalized(value: str) -> str:
        decomposed = normalize("NFKD", value.replace("_", " ").lower())
        return "".join(char for char in decomposed if not combining(char))

    available = {
        normalized(column): column for column in records[0]
    }
    return next(
        (
            available[normalized(candidate)]
            for candidate in candidates
            if normalized(candidate) in available
        ),
        None,
    )


def _build_kpis(records: list[dict], total: int, group: dict) -> list[dict]:
    metric_candidates = (
        *METRIC_CANDIDATES[group["metric"]],
        group["metric_label"],
    )
    metric_column = _find_column(records, metric_candidates)
    metric_total = (
        sum(_as_number(row.get(metric_column)) or 0 for row in records)
        if metric_column
        else total
    )
    money_column = _find_column(records, group.get("money_candidates", ()))
    money_is_average = bool(
        money_column and "promedio" in money_column.lower()
    )
    days_column = _find_column(records, group.get("days_candidates", ()))
    category_column = _find_column(
        records,
        (
            group.get("category", ""),
            "Establecimiento",
            "Municipio / Depto.",
            "Diagnóstico CIE-10",
            "Servicio",
        ),
    )
    kpis = [
        {"label": group["metric_label"], "value": metric_total, "format": "miles"},
        {
            "label": (
                "Costo Promedio"
                if money_is_average
                else "Monto Total Cobrado" if money_column else "Registros"
            ),
            "value": (
                _average(records, money_column)
                if money_is_average
                else sum(_as_number(row.get(money_column)) or 0 for row in records)
                if money_column
                else total
            ),
            "format": "quetzal" if money_column else "miles",
        },
        {
            "label": "Promedio Días Estancia" if days_column else "Registros Activos",
            "value": (
                _average(records, days_column) if days_column else total
            ),
            "format": "decimal" if days_column else "miles",
        },
        {
            "label": "Categorías",
            "value": (
                len({row.get(category_column) for row in records if row.get(category_column)})
                if category_column
                else total
            ),
            "format": "miles",
        },
    ]
    return kpis


def _average(records: list[dict], column: str) -> float | None:
    values = [_as_number(row.get(column)) for row in records]
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else 0


def _period_label(row: dict) -> str:
    if row.get("Periodo"):
        label = str(row["Periodo"])
        try:
            parsed = date.fromisoformat(label[:10])
        except ValueError:
            return label
        month_names = (
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
        return f"{month_names[parsed.month - 1]} {parsed.year}"
    month = row.get("Mes") or row.get("Nombre Mes") or row.get("Mes Nombre")
    year = row.get("Año") or row.get("Anio") or row.get("Ano")
    if month and year:
        return f"{month} {year}"
    for column in ("Fecha de atención", "Fecha del evento", "Fecha"):
        if row.get(column):
            return str(row[column])[:10]
    return "Periodo no especificado"


def _period_sort_key(label: str) -> tuple[int, int, str]:
    month_names = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }
    year_match = re.search(r"\b(19|20)\d{2}\b", label)
    if not year_match:
        return (9999, 13, label)
    year = int(year_match.group())
    normalized_label = normalize("NFKD", label.lower())
    normalized_label = "".join(
        char for char in normalized_label if not combining(char)
    )
    month = next(
        (number for name, number in month_names.items() if name in normalized_label),
        None,
    )
    if month is None:
        numeric_month = re.search(r"\b(0?[1-9]|1[0-2])\b", label)
        month = int(numeric_month.group()) if numeric_month else 13
    return (year, month, label)


def _build_charts(records: list[dict], group: dict) -> dict:
    metric_column = _find_column(
        records, (*METRIC_CANDIDATES[group["metric"]], group["metric_label"])
    )
    category_column = _find_column(
        records,
        (group.get("category", ""), "Establecimiento", "Municipio / Depto.",
         "Diagnóstico CIE-10", "Servicio", "Evento"),
    )
    monthly_values = defaultdict(float)
    category_values = defaultdict(float)
    for row in records:
        amount = _as_number(row.get(metric_column)) if metric_column else None
        amount = amount if amount is not None else 1
        monthly_values[_period_label(row)] += amount
        category = row.get(category_column) if category_column else None
        category_values[str(category or "No especificado")] += amount
    monthly_items = sorted(monthly_values.items(), key=lambda item: _period_sort_key(item[0]))
    return {
        "monthly": {
            "labels": [label for label, _ in monthly_items],
            "values": [value for _, value in monthly_items],
        },
        "categories": {
            "labels": list(category_values)[:10],
            "values": list(category_values.values())[:10],
        },
        "metric_label": group["metric_label"],
        "category_label": group.get("category", "Categoría"),
    }


def central_dashboard() -> dict:
    engine = get_engine(8)
    with engine.connect() as connection:
        metrics = fetch_central_metrics(connection)
        metrics["etl_monitor"] = _etl_monitor_data(connection)
    metrics["activity_chart"] = _central_activity_chart(
        metrics.get("usos", []), metrics.get("ingestas", [])
    )
    return metrics


def _etl_monitor_data(connection) -> dict:
    """Resume recepción, pendientes, limpieza y filas cargadas por buzón."""
    status_columns = {
        row[0]
        for row in connection.execute(
            text(
                """
                SELECT TABLE_NAME FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = :schema_name
                  AND COLUMN_NAME = 'procesado_etl'
                """
            ),
            {"schema_name": ANALYTICS_SCHEMA},
        )
    }
    warehouse_tables = {
        row[0]
        for row in connection.execute(
            text(
                """
                SELECT TABLE_NAME FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                """
            )
        )
    }
    ledger_exists = IDEMPOTENCY_TABLE in warehouse_tables
    details = []
    for inbox in INBOXES:
        table_name = inbox["table"]
        if table_name in status_columns:
            row = connection.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS received,
                           COALESCE(SUM(`procesado_etl` = 0), 0) AS pending,
                           COALESCE(SUM(`procesado_etl` = 1), 0) AS processed
                    FROM `{ANALYTICS_SCHEMA}`.`{table_name}`
                    """
                )
            ).mappings().one()
            received = int(row["received"])
            pending = int(row["pending"])
            processed = int(row["processed"])
        else:
            received = int(
                connection.execute(
                    text(
                        f"SELECT COUNT(*) FROM "
                        f"`{ANALYTICS_SCHEMA}`.`{table_name}`"
                    )
                ).scalar_one()
            )
            pending = received
            processed = 0

        sent = 0
        if ledger_exists:
            sent = int(
                connection.execute(
                    text(
                        f"""
                        SELECT COALESCE(SUM(`filas_insertadas`), 0)
                        FROM `{IDEMPOTENCY_TABLE}`
                        WHERE `tabla_buzon` = :source_table
                        """
                    ),
                    {"source_table": table_name},
                ).scalar_one()
            )
        details.append(
            {
                "group_id": inbox["group_id"],
                "group_name": GROUPS[inbox["group_id"]]["name"],
                "inbox": table_name,
                "target": inbox["target"],
                "received": received,
                "pending": pending,
                "processed": processed,
                "sent": sent,
                "cleaned": _ledger_count(
                    connection, inbox["table"], "filas_limpiadas", ledger_exists
                ),
            }
        )

    return {
        "received_total": sum(item["received"] for item in details),
        "pending_total": sum(item["pending"] for item in details),
        "processed_total": sum(item["processed"] for item in details),
        "cleaned_total": sum(item["cleaned"] for item in details),
        "loaded_total": sum(item["sent"] for item in details),
        "groups": details,
        "chart": {
            "labels": [f"Grupo {item['group_id']}" for item in details],
            "received": [item["received"] for item in details],
            "sent": [item["sent"] for item in details],
        },
    }


def _ledger_count(
    connection,
    source_table: str,
    column: str,
    ledger_exists: bool,
) -> int:
    if not ledger_exists or column not in {"filas_limpiadas", "filas_insertadas"}:
        return 0
    result = connection.execute(
        text(
            f"SELECT COALESCE(SUM(`{column}`), 0) "
            f"FROM `{IDEMPOTENCY_TABLE}` WHERE `tabla_buzon` = :source_table"
        ),
        {"source_table": source_table},
    ).scalar_one()
    return int(result)


def _central_activity_chart(usos: list, ingestas: list) -> dict:
    rows_by_actor = defaultdict(float)
    for row in [*usos, *ingestas]:
        record = {str(key).lower(): value for key, value in dict(row).items()}
        actor = next(
            (
                record[key]
                for key in (
                    "grupo_id",
                    "id_grupo",
                    "grupo",
                    "nombre_grupo",
                    "usuario",
                    "actor",
                    "fuente",
                    "tabla_destino",
                )
                if record.get(key)
            ),
            "Actividad registrada",
        )
        processed = next(
            (
                record[key]
                for key in (
                    "filas_procesadas",
                    "filas_transformadas",
                    "filas_insertadas",
                    "filas_leidas",
                    "filas_cargadas",
                )
                if record.get(key) is not None
            ),
            1,
        )
        rows_by_actor[str(actor)] += _as_number(processed) or 0
    return {
        "labels": list(rows_by_actor),
        "values": list(rows_by_actor.values()),
    }


def ingestion_form(group_id: int) -> dict:
    group = GROUPS[group_id]
    if group.get("read_only") or not group.get("inbox"):
        return {"fields": [], "catalogs": {}}
    engine = get_engine(group_id)
    with engine.connect() as connection:
        fields = fetch_table_columns(connection, validate_identifier(group["inbox"]))
        editable_fields = [
            field
            for field in fields
            if field["extra"] != "auto_increment"
            and field["default_value"] is None
            and field["nullable"] == "NO"
        ]
        catalogs = {}
        for catalog_name in group.get("catalogs", ()):
            catalog_view = CATALOG_VIEWS[catalog_name]["view"]
            try:
                catalogs[catalog_name] = fetch_catalog_options(connection, catalog_view)
            except SQLAlchemyError as error:
                logger.warning(
                    "No se pudo consultar el catálogo %s: %s",
                    catalog_view,
                    error,
                )
                catalogs[catalog_name] = []
        field_catalogs = {}
        for field in editable_fields:
            normalized_name = field["name"].lower()
            for catalog_name, keywords in CATALOG_FIELD_HINTS.items():
                if catalog_name not in catalogs or not any(
                    keyword in normalized_name for keyword in keywords
                ):
                    continue
                value_candidates = _catalog_value_candidates(
                    catalog_name, normalized_name
                )
                options = catalogs[catalog_name]
                if value_candidates:
                    catalog_view = CATALOG_VIEWS[catalog_name]["view"]
                    try:
                        options = fetch_catalog_options(
                            connection,
                            catalog_view,
                            value_candidates=value_candidates,
                        )
                    except SQLAlchemyError as error:
                        logger.warning(
                            "No se pudieron consultar opciones de %s: %s",
                            catalog_view,
                            error,
                        )
                        options = []
                if options:
                    field_catalogs[field["name"]] = options
                    break
    return {
        "fields": editable_fields,
        "catalogs": catalogs,
        "field_catalogs": field_catalogs,
    }


def _catalog_value_candidates(
    catalog_name: str,
    field_name: str,
) -> tuple[str, ...] | None:
    if "codigo" in field_name:
        return CATALOG_INPUT_KEYS.get((catalog_name, "codigo"))
    if "fecha" in field_name:
        return CATALOG_INPUT_KEYS.get((catalog_name, "fecha"))
    return None


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
