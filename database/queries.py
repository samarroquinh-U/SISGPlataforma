"""Consultas parametrizadas separadas por dominios de la plataforma."""

from sqlalchemy import text


def fetch_rows(connection, view_name: str, limit: int = 100):
    query = text(f"SELECT * FROM `{view_name}` LIMIT :row_limit")
    return connection.execute(query, {"row_limit": limit}).mappings().all()


def fetch_count(connection, object_name: str) -> int:
    return int(
        connection.execute(
            text(f"SELECT COUNT(*) FROM `{object_name}`")
        ).scalar_one()
    )


def fetch_central_metrics(connection) -> dict:
    """Consulta indicadores de ingesta y uso del esquema central."""
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
    table_counts = {}
    for table_name in (
        "stg_metadatos_ingesta",
        "fact_uso_plataforma",
        "fact_atencion",
        "fact_morbilidad",
        "fact_hecho_vital",
    ):
        table_counts[table_name] = (
            fetch_count(connection, table_name)
            if table_name in existing_tables
            else None
        )
    ingestas = []
    if "stg_metadatos_ingesta" in existing_tables:
        ingestas = connection.execute(
            text("SELECT * FROM `stg_metadatos_ingesta` ORDER BY 1 DESC LIMIT 10")
        ).mappings().all()
    return {"counts": table_counts, "ingestas": ingestas}


def fetch_catalog_options(connection, view_name: str, limit: int = 200):
    """Extrae una columna de catálogo con consultas de solo lectura."""
    rows = connection.execute(
        text(f"SELECT * FROM `{view_name}` LIMIT :row_limit"),
        {"row_limit": limit},
    ).mappings().all()
    if not rows:
        return []
    first_column = next(iter(rows[0].keys()))
    return [row[first_column] for row in rows]


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
    names = ", ".join(f"`{column}`" for column in columns)
    bind_names = ", ".join(f":{column}" for column in columns)
    return connection.execute(
        text(f"INSERT INTO `{table_name}` ({names}) VALUES ({bind_names})"),
        {column: values[column] for column in columns},
    )

