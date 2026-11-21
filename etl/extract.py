"""Extracción desde archivos locales o tablas operativas MySQL."""

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from database.connection import get_engine

ALLOWED_OPERATIONAL_TABLES = {
    "op_atencion_medica",
    "op_morbilidad_epidemiologica",
    "op_hecho_vital",
}


def extract_file(path: str | Path) -> pd.DataFrame:
    """Lee CSV o Excel desde una ruta local."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"No existe el archivo de entrada: {source}")
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(source)
    raise ValueError("Formato no soportado. Usa CSV o Excel.")


def extract_operational_table(table_name: str) -> pd.DataFrame:
    """Extrae una tabla operativa permitida del esquema central."""
    if table_name not in ALLOWED_OPERATIONAL_TABLES:
        raise ValueError(f"Tabla operativa no permitida: {table_name}")
    engine = get_engine(8)
    with engine.connect() as connection:
        return pd.read_sql_query(text(f"SELECT * FROM `{table_name}`"), connection)


def extract_dimensions() -> dict[str, pd.DataFrame]:
    """Carga las dimensiones necesarias para resolver llaves sustitutas."""
    engine = get_engine(8)
    dimensions = {
        "diagnostico": "dim_diagnostico",
        "geografia": "dim_geografia",
        "tiempo": "dim_tiempo",
        "establecimiento": "dim_establecimiento",
    }
    with engine.connect() as connection:
        return {
            name: pd.read_sql_query(text(f"SELECT * FROM `{table_name}`"), connection)
            for name, table_name in dimensions.items()
        }

