"""Consultas parametrizadas separadas por dominios de la plataforma."""

from sqlalchemy import text

from config.groups_config import CATALOG_VIEWS
from database.connection import validate_identifier

DISPLAY_FIELDS = {
    "sk_tiempo": "Periodo",
    "sk_geografia": "Municipio / Depto.",
    "sk_municipio": "Municipio / Depto.",
    "sk_establecimiento": "Establecimiento",
    "sk_diagnostico": "Diagnóstico CIE-10",
    "sk_servicio": "Servicio",
    "sk_evento": "Evento",
    "sk_parto": "Tipo de parto",
    "sk_demografia": "Demografía",
    "nombre_mes": "Mes",
    "anio": "Año",
    "ano": "Año",
    "atenciones": "Atenciones",
    "cantidad_atenciones": "Atenciones",
    "casos": "Casos",
    "prestaciones": "Prestaciones",
    "servicios": "Servicios",
    "nacimientos": "Nacimientos",
    "defunciones": "Defunciones",
    "dias_estancia": "Días Estancia",
    "dias_hospitalizacion": "Días Estancia",
    "monto_cobrado": "Monto Cobrado",
    "costo_promedio": "Costo Promedio",
    "codigo_cie10": "Código CIE-10",
    "cie10": "Código CIE-10",
    "codigo_ine": "Código INE",
    "filas_leidas": "Filas Leídas",
    "filas_insertadas": "Filas Insertadas",
}

CATALOG_OUTPUT_LABELS = {
    "tiempo": "Periodo",
    "geografia": "Municipio / Depto.",
    "establecimiento": "Establecimiento",
    "diagnostico": "Diagnóstico CIE-10",
    "servicio": "Servicio",
    "evento": "Evento",
    "parto": "Tipo de parto",
    "demografia": "Demografía",
}


def _column_metadata(connection, table_name: str) -> list[str]:
    result = connection.execute(
        text(
            """
            SELECT COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
            ORDER BY ORDINAL_POSITION
            """
        ),
        {"table_name": table_name},
    )
    return [row[0] for row in result]


def _matching_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    by_lower_name = {column.lower(): column for column in columns}
    return next(
        (
            by_lower_name[candidate.lower()]
            for candidate in candidates
            if candidate.lower() in by_lower_name
        ),
        None,
    )


def fetch_rows(connection, view_name: str, catalog_names: tuple[str, ...] = ()):
    """Consulta una vista y agrega nombres descriptivos mediante LEFT JOIN."""
    safe_view = validate_identifier(view_name)
    view_columns = _column_metadata(connection, safe_view)
    if not view_columns:
        raise RuntimeError(f"No se encontró la vista asignada {safe_view}.")

    join_clauses = []
    label_selections = []
    for catalog_name in catalog_names:
        catalog = CATALOG_VIEWS[catalog_name]
        catalog_view = validate_identifier(catalog["view"])
        catalog_columns = _column_metadata(connection, catalog_view)
        if not catalog_columns:
            continue
        fact_key = _matching_column(view_columns, catalog["fact_keys"])
        catalog_key = _matching_column(catalog_columns, catalog["catalog_keys"])
        label_column = _matching_column(catalog_columns, catalog["labels"])
        if not fact_key or not catalog_key or not label_column:
            continue
        fact_key = validate_identifier(fact_key)
        catalog_key = validate_identifier(catalog_key)
        label_column = validate_identifier(label_column)

        alias = f"cat_{catalog_name}"
        join_clauses.append(
            f"LEFT JOIN `{catalog_view}` AS `{alias}` "
            f"ON v.`{fact_key}` = `{alias}`.`{catalog_key}`"
        )
        output_name = f"__label_{catalog_name}"
        label_expression = f"CAST(`{alias}`.`{label_column}` AS CHAR)"
        if catalog_name == "tiempo":
            year_column = _matching_column(
                catalog_columns, ("anio", "ano", "year")
            )
            if (
                year_column
                and label_column.lower()
                not in ("nombre_periodo", "periodo", "fecha")
            ):
                year_column = validate_identifier(year_column)
                label_expression = (
                    f"CONCAT({label_expression}, ' ', "
                    f"CAST(`{alias}`.`{year_column}` AS CHAR))"
                )
        elif catalog_name == "geografia":
            municipality_column = _matching_column(
                catalog_columns, ("nombre_municipio", "municipio")
            )
            department_column = _matching_column(
                catalog_columns, ("nombre_departamento", "departamento")
            )
            if (
                municipality_column
                and department_column
                and label_column.lower()
                not in ("municipio_departamento",)
            ):
                municipality_column = validate_identifier(municipality_column)
                department_column = validate_identifier(department_column)
                label_expression = (
                    f"CONCAT_WS(' / ', NULLIF(CAST(`{alias}`."
                    f"`{municipality_column}` AS CHAR), ''), "
                    f"NULLIF(CAST(`{alias}`.`{department_column}` AS CHAR), ''))"
                )
        label_selections.append(
            "COALESCE(NULLIF(CAST("
            f"{label_expression} AS CHAR), ''), 'No especificado') "
            f"AS `{output_name}`"
        )

    selected_columns = ["v.*", *label_selections]
    query = (
        f"SELECT {', '.join(selected_columns)} FROM `{safe_view}` AS v "
        f"{' '.join(join_clauses)}"
    )
    return connection.execute(text(query)).mappings().all()


def fetch_count(connection, object_name: str) -> int:
    safe_name = validate_identifier(object_name)
    return int(
        connection.execute(
            text(f"SELECT COUNT(*) FROM `{safe_name}`")
        ).scalar_one()
    )


def fetch_central_metrics(connection) -> dict:
    """Consulta contadores y actividad de auditoría del esquema central."""
    existing_tables = {
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
    count_tables = (
        "stg_metadatos_ingesta",
        "fact_uso_plataforma",
        "fact_atencion_privada",
        "fact_morbilidad",
        "fact_nacimiento",
        "fact_defuncion",
    )
    table_counts = {
        table_name: (
            fetch_count(connection, table_name)
            if table_name in existing_tables
            else None
        )
        for table_name in count_tables
    }
    ingestas = _fetch_audit_rows(connection, "stg_metadatos_ingesta", existing_tables)
    usos = _fetch_audit_rows(connection, "fact_uso_plataforma", existing_tables)
    return {
        "counts": table_counts,
        "ingestas": ingestas,
        "usos": usos,
    }


def _fetch_audit_rows(connection, table_name: str, existing_tables: set):
    if table_name not in existing_tables:
        return []
    safe_name = validate_identifier(table_name)
    return connection.execute(
        text(f"SELECT * FROM `{safe_name}` ORDER BY 1 DESC LIMIT :row_limit"),
        {"row_limit": 500},
    ).mappings().all()


def fetch_catalog_options(
    connection,
    view_name: str,
    limit: int = 200,
    value_candidates: tuple[str, ...] | None = None,
):
    """Devuelve pares de llave/etiqueta para poblar selects de catálogo."""
    safe_view = validate_identifier(view_name)
    columns = _column_metadata(connection, safe_view)
    if not columns:
        return []
    catalog = next(
        (metadata for metadata in CATALOG_VIEWS.values() if metadata["view"] == safe_view),
        None,
    )
    if not catalog:
        raise ValueError(f"Catálogo no configurado: {safe_view}")
    key_column = (
        _matching_column(columns, value_candidates)
        if value_candidates
        else None
    ) or _matching_column(columns, catalog["catalog_keys"])
    label_column = _matching_column(columns, catalog["labels"])
    if not key_column or not label_column:
        return []
    key_column = validate_identifier(key_column)
    label_column = validate_identifier(label_column)
    result = connection.execute(
        text(
            f"SELECT `{key_column}` AS value, `{label_column}` AS label "
            f"FROM `{safe_view}` ORDER BY `{label_column}` LIMIT :row_limit"
        ),
        {"row_limit": limit},
    )
    return [dict(row) for row in result.mappings()]


def fetch_table_columns(connection, table_name: str) -> list[dict]:
    """Devuelve metadatos de columnas de una tabla buzón permitida."""
    result = connection.execute(
        text(
            """
            SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type,
                   IS_NULLABLE AS nullable, COLUMN_DEFAULT AS default_value,
                   EXTRA AS extra
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
            ORDER BY ORDINAL_POSITION
            """
        ),
        {"table_name": table_name},
    )
    return [dict(row) for row in result.mappings()]


def insert_inbox_row(connection, table_name: str, columns: list[str], values: dict):
    """Inserta una fila con parámetros; identificadores proceden del esquema."""
    safe_table = validate_identifier(table_name)
    safe_columns = [validate_identifier(column) for column in columns]
    names = ", ".join(f"`{column}`" for column in safe_columns)
    bind_names = ", ".join(f":{column}" for column in safe_columns)
    return connection.execute(
        text(f"INSERT INTO `{safe_table}` ({names}) VALUES ({bind_names})"),
        {column: values[column] for column in safe_columns},
    )

