"""Carga transaccional de hechos y auditoría de ejecuciones ETL."""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from database.connection import get_engine

FACT_TABLES = {
    "op_atencion_medica": "fact_atencion",
    "op_morbilidad_epidemiologica": "fact_morbilidad",
    "op_hecho_vital": "fact_hecho_vital",
    "archivo": "fact_atencion",
}


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


def _insert_frame(connection, table_name: str, frame: pd.DataFrame) -> int:
    columns = _columns_for(connection, table_name)
    if not columns:
        raise RuntimeError(f"No existe la tabla de destino {table_name}.")
    selected = [column for column in frame.columns if column in columns]
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


def load_records(
    frame: pd.DataFrame,
    source_name: str,
    extracted_count: int,
) -> dict:
    """Carga hechos y registra las métricas de ejecución en una transacción."""
    target = FACT_TABLES.get(source_name)
    if not target:
        raise ValueError(f"No hay tabla de hechos asignada para {source_name}.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    engine = get_engine(8)
    with engine.begin() as connection:
        loaded_count = _insert_frame(connection, target, frame)
        metadata = {
            "fuente": source_name,
            "tabla_destino": target,
            "filas_extraidas": extracted_count,
            "filas_transformadas": len(frame),
            "filas_cargadas": loaded_count,
            "estado": "completado",
            "fecha_inicio": now,
            "fecha_fin": now,
            "created_at": now,
            "updated_at": now,
        }
        _write_audit(connection, "stg_metadatos_ingesta", metadata)
        _write_audit(
            connection,
            "fact_uso_plataforma",
            {
                "usuario": "etl",
                "accion": "ejecucion_etl",
                "modulo": target,
                "filas_procesadas": loaded_count,
                "fecha_uso": now,
                "created_at": now,
            },
        )
    return {"loaded": loaded_count, "target": target}

