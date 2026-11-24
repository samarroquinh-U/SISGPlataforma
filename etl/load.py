"""Carga transaccional de hechos y auditoría de ejecuciones ETL."""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from etl.extract import ANALYTICS_SCHEMA, INBOXES

IDEMPOTENCY_TABLE = "etl_ledger_buzones"


def _columns_for(connection, table_name: str) -> list[str]:
    result = connection.execute(
        text(
            """
            SELECT COLUMN_NAME FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
            ORDER BY ORDINAL_POSITION
            """
        ),
        {"table_name": table_name},
    )
    return [row[0] for row in result]


def ensure_idempotency_table(engine) -> None:
    """Crea el ledger único de origen antes de abrir la transacción de carga."""
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS `{IDEMPOTENCY_TABLE}` (
                    `tabla_buzon` VARCHAR(64) NOT NULL,
                    `id_ingesta` BIGINT NOT NULL,
                    `grupo_origen` TINYINT NOT NULL,
                    `tabla_destino` VARCHAR(64) NOT NULL,
                    `filas_insertadas` TINYINT(1) NOT NULL DEFAULT 0,
                    `filas_limpiadas` TINYINT(1) NOT NULL DEFAULT 0,
                    `fecha_carga` DATETIME NOT NULL,
                    UNIQUE KEY `uq_buzon_registro` (`tabla_buzon`, `id_ingesta`)
                ) ENGINE=InnoDB
                """
            )
        )
    required_tables = {
        *(inbox["target"] for inbox in INBOXES),
        "stg_metadatos_ingesta",
        "fact_uso_plataforma",
        IDEMPOTENCY_TABLE,
    }
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT TABLE_NAME, ENGINE
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                """
            )
        )
        engines = {row[0]: row[1] for row in rows}
    for table_name in required_tables:
        table_engine = engines.get(table_name)
        if table_engine is None:
            raise RuntimeError(f"No existe la tabla requerida {table_name}.")
        if table_engine.upper() != "INNODB":
            raise RuntimeError(
                f"La tabla {table_name} debe usar InnoDB "
                "para garantizar la transacción ETL."
            )


def _require_innodb(connection, table_name: str) -> None:
    table_engine = connection.execute(
        text(
            """
            SELECT ENGINE FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalar_one_or_none()
    if table_engine is None:
        raise RuntimeError(f"No existe la tabla requerida {table_name}.")
    if table_engine.upper() != "INNODB":
        raise RuntimeError(
            f"La tabla {table_name} debe usar InnoDB para ser transaccional."
        )


def _insert_frame(connection, table_name: str, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    columns = _columns_for(connection, table_name)
    if not columns:
        raise RuntimeError(f"No existe la tabla de destino {table_name}.")
    selected = [
        column
        for column in frame.columns
        if column in columns and column not in {"id_ingesta", "procesado_etl"}
    ]
    if not selected:
        raise RuntimeError(
            f"El dataframe no contiene columnas compatibles con {table_name}."
        )
    statement = text(
        f"INSERT INTO `{table_name}` "
        f"({', '.join(f'`{column}`' for column in selected)}) "
        f"VALUES ({', '.join(f':{column}' for column in selected)})"
    )
    records = frame[selected].astype(object).where(pd.notna(frame[selected]), None)
    connection.execute(statement, records.to_dict(orient="records"))
    return len(records)


def _update_inbox_status(connection, source_table: str, ids: list) -> None:
    if not ids:
        return
    binds = {f"id_{index}": value for index, value in enumerate(ids)}
    placeholders = ", ".join(f":id_{index}" for index in range(len(ids)))
    connection.execute(
        text(
            f"UPDATE `{ANALYTICS_SCHEMA}`.`{source_table}` "
            f"SET `procesado_etl` = 1 WHERE `id_ingesta` IN ({placeholders})"
        ),
        binds,
    )


def load_inbox_batch(
    connection,
    frame: pd.DataFrame,
    source_table: str,
    source_ids: list,
    group_id: int,
    target: str,
    cost_frame: pd.DataFrame | None = None,
) -> dict:
    """Inserta un lote, reclama sus IDs y marca el buzón dentro de una transacción."""
    configured_inbox = next(
        (
            inbox
            for inbox in INBOXES
            if inbox["table"] == source_table
            and inbox["group_id"] == group_id
            and inbox["target"] == target
        ),
        None,
    )
    if configured_inbox is None:
        raise ValueError(f"Origen ETL no permitido: {source_table}.")
    if any(value is None or pd.isna(value) for value in source_ids):
        raise ValueError(f"El buzón {source_table} contiene un id_ingesta vacío.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    claimed_ids = set()
    for source_id in source_ids:
        result = connection.execute(
            text(
                f"""
                INSERT IGNORE INTO `{IDEMPOTENCY_TABLE}`
                    (`tabla_buzon`, `id_ingesta`, `grupo_origen`,
                     `tabla_destino`, `filas_insertadas`, `filas_limpiadas`,
                     `fecha_carga`)
                VALUES (:source_table, :source_id, :group_id,
                        :target, 0, 0, :loaded_at)
                """
            ),
            {
                "source_table": source_table,
                "source_id": int(source_id),
                "group_id": group_id,
                "target": target,
                "loaded_at": now,
            },
        )
        if result.rowcount == 1:
            claimed_ids.add(str(source_id))

    claimed_frame = (
        frame.loc[frame["id_ingesta"].astype(str).isin(claimed_ids)].copy()
        if "id_ingesta" in frame
        else frame.iloc[0:0].copy()
    )
    cleaned_ids = claimed_frame["id_ingesta"].tolist()
    if cleaned_ids:
        binds = {f"id_{index}": value for index, value in enumerate(cleaned_ids)}
        placeholders = ", ".join(f":id_{index}" for index in range(len(cleaned_ids)))
        connection.execute(
            text(
                f"UPDATE `{IDEMPOTENCY_TABLE}` SET `filas_limpiadas` = 1 "
                f"WHERE `tabla_buzon` = :source_table "
                f"AND `id_ingesta` IN ({placeholders})"
            ),
            {"source_table": source_table, **binds},
        )
    if not claimed_frame.empty:
        _require_innodb(connection, target)
    loaded_count = _insert_frame(connection, target, claimed_frame)
    loaded_ids = claimed_frame["id_ingesta"].astype(str).tolist()
    if loaded_ids:
        binds = {f"id_{index}": value for index, value in enumerate(loaded_ids)}
        placeholders = ", ".join(f":id_{index}" for index in range(len(loaded_ids)))
        connection.execute(
            text(
                f"UPDATE `{IDEMPOTENCY_TABLE}` SET `filas_insertadas` = 1 "
                f"WHERE `tabla_buzon` = :source_table "
                f"AND `id_ingesta` IN ({placeholders})"
            ),
            {"source_table": source_table, **binds},
        )

    cost_loaded = 0
    if cost_frame is not None and not cost_frame.empty:
        cost_batch = (
            cost_frame.loc[
                cost_frame["id_ingesta"].astype(str).isin(set(loaded_ids))
            ]
            if "id_ingesta" in cost_frame
            else cost_frame.iloc[0:0]
        )
        if not cost_batch.empty:
            _require_innodb(connection, "fact_costo_atencion")
        cost_loaded = _insert_frame(
            connection, "fact_costo_atencion", cost_batch
        )

    _update_inbox_status(connection, source_table, source_ids)
    return {
        "group_id": group_id,
        "source": source_table,
        "target": target,
        "extracted": len(source_ids),
        "cleaned": len(claimed_frame),
        "loaded": loaded_count,
        "cost_loaded": cost_loaded,
        "already_processed": len(source_ids) - len(claimed_ids),
    }


def _write_audit(connection, table_name: str, payload: dict) -> None:
    columns = _columns_for(connection, table_name)
    if not columns:
        raise RuntimeError(f"No existe la tabla de auditoría requerida {table_name}.")
    record = {key: value for key, value in payload.items() if key in columns}
    if not record:
        raise RuntimeError(
            f"No se encontraron columnas de auditoría compatibles en {table_name}."
        )
    names = ", ".join(f"`{column}`" for column in record)
    binds = ", ".join(f":{column}" for column in record)
    connection.execute(
        text(f"INSERT INTO `{table_name}` ({names}) VALUES ({binds})"), record
    )


def _write_etl_audit(connection, results: list[dict], sk_tiempo: int) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _require_innodb(connection, "stg_metadatos_ingesta")
    _require_innodb(connection, "fact_uso_plataforma")
    for result in results:
        _write_audit(
            connection,
            "stg_metadatos_ingesta",
            {
                "grupo_origen": result["group_id"],
                "fuente_datos": result["source"],
                "tabla_destino": result["target"],
                "filas_leidas": result["extracted"],
                "filas_limpiadas": result["cleaned"],
                "filas_insertadas": result["loaded"],
                "estado_carga": "COMPLETADO",
                "fecha_ingesta": now,
                "fuente": result["source"],
                "filas_extraidas": result["extracted"],
                "filas_transformadas": result["cleaned"],
                "filas_cargadas": result["loaded"],
                "estado": "completado",
                "fecha_inicio": now,
                "fecha_fin": now,
                "created_at": now,
                "updated_at": now,
            },
        )
    _write_audit(
        connection,
        "fact_uso_plataforma",
        {
            "sk_tiempo": sk_tiempo,
            "sk_actor": 8,
            "sk_operacion": 6,
            "operaciones": 1,
            "filas_procesadas": sum(result["loaded"] for result in results),
            "usuario": "etl",
            "accion": "ejecucion_etl",
            "modulo": "buzones_g1_g4",
            "fecha_uso": now,
            "created_at": now,
        },
    )


def record_etl_audit(connection, results: list[dict], sk_tiempo: int) -> None:
    """Registra los resultados del lote global dentro de la transacción activa."""
    _write_etl_audit(connection, results, sk_tiempo)

