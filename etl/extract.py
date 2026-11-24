"""Extracción de filas pendientes de los buzones de ingesta MySQL."""

import pandas as pd
from sqlalchemy import text

ANALYTICS_SCHEMA = "sisg_analitica"
INBOXES = (
    {
        "group_id": 1,
        "table": "ingesta_g1_atencion",
        "target": "fact_atencion_privada",
    },
    {
        "group_id": 2,
        "table": "ingesta_g2_morbilidad",
        "target": "fact_morbilidad",
    },
    {
        "group_id": 3,
        "table": "ingesta_g3_prestacion",
        "target": "fact_prestacion_igss",
    },
    {
        "group_id": 4,
        "table": "ingesta_g4_servicio",
        "target": "fact_servicio",
    },
)
WAREHOUSE_DIMENSIONS = {
    "diagnostico": "dim_diagnostico",
    "geografia": "dim_geografia",
    "tiempo": "dim_tiempo",
    "establecimiento": "dim_establecimiento",
    "servicio": "dim_servicio",
    "evento": "dim_evento",
    "demografia": "dim_demografia",
}
def asegurar_columna_estado_buzones(engine) -> None:
    """Agrega el marcador de proceso a cada buzón, sin alterar sus filas."""
    with engine.begin() as connection:
        for inbox in INBOXES:
            table_engine = connection.execute(
                text(
                    """
                    SELECT ENGINE
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = :schema_name AND TABLE_NAME = :table_name
                    """
                ),
                {
                    "schema_name": ANALYTICS_SCHEMA,
                    "table_name": inbox["table"],
                },
            ).scalar_one_or_none()
            if table_engine is None:
                raise RuntimeError(
                    f"No existe el buzón "
                    f"{ANALYTICS_SCHEMA}.{inbox['table']}."
                )
            if table_engine.upper() != "INNODB":
                raise RuntimeError(
                    f"El buzón {inbox['table']} debe usar InnoDB "
                    "para garantizar la transacción ETL."
                )
            has_id = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = :schema_name AND TABLE_NAME = :table_name
                      AND COLUMN_NAME = 'id_ingesta'
                    """
                ),
                {
                    "schema_name": ANALYTICS_SCHEMA,
                    "table_name": inbox["table"],
                },
            ).scalar_one()
            if not has_id:
                raise RuntimeError(
                    f"El buzón {inbox['table']} debe tener una columna id_ingesta."
                )
            has_status = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = :schema_name AND TABLE_NAME = :table_name
                      AND COLUMN_NAME = 'procesado_etl'
                    """
                ),
                {
                    "schema_name": ANALYTICS_SCHEMA,
                    "table_name": inbox["table"],
                },
            ).scalar_one()
            if not has_status:
                connection.execute(
                    text(
                        f"ALTER TABLE `{ANALYTICS_SCHEMA}`.`{inbox['table']}` "
                        "ADD COLUMN `procesado_etl` "
                        "TINYINT(1) NOT NULL DEFAULT 0"
                    )
                )


def extract_pending_inboxes(connection) -> list[dict]:
    """Extrae candidatos pendientes; el ledger serializa las cargas concurrentes."""
    batches = []
    for inbox in INBOXES:
        frame = pd.read_sql_query(
            text(
                f"SELECT * FROM `{ANALYTICS_SCHEMA}`.`{inbox['table']}` "
                "WHERE `procesado_etl` = 0 ORDER BY `id_ingesta`"
            ),
            connection,
        )
        previously_processed = connection.execute(
            text(
                f"SELECT COUNT(*) FROM `{ANALYTICS_SCHEMA}`.`{inbox['table']}` "
                "WHERE `procesado_etl` = 1"
            )
        ).scalar_one()
        if not frame.empty and "id_ingesta" not in frame.columns:
            raise RuntimeError(
                f"El buzón {inbox['table']} debe tener una columna id_ingesta."
            )
        batches.append(
            {
                **inbox,
                "records": frame,
                "previously_processed": int(previously_processed),
            }
        )
    return batches


def extract_dimensions_from_connection(connection) -> dict[str, pd.DataFrame]:
    """Carga las dimensiones usando la conexión transaccional del ETL."""
    return {
        name: pd.read_sql_query(text(f"SELECT * FROM `{table_name}`"), connection)
        for name, table_name in WAREHOUSE_DIMENSIONS.items()
    }

