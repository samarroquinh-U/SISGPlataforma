"""Orquestador del flujo ETL SISG."""

import argparse
from pathlib import Path

from etl.extract import extract_dimensions, extract_file, extract_operational_table
from etl.load import load_records
from etl.transform import transform_records

DEFAULT_INPUT = (
    Path(__file__).resolve().parents[1]
    / "data_entrada"
    / "atenciones_ejemplo.csv"
)


def ejecutar_etl_completo(
    source_path: str | Path | None = None,
    source_table: str | None = None,
) -> dict:
    """Ejecuta extracción, transformación, carga transaccional y auditoría."""
    if source_table:
        extracted = extract_operational_table(source_table)
        source_name = source_table
    else:
        path = Path(source_path) if source_path else DEFAULT_INPUT
        extracted = extract_file(path)
        source_name = "archivo"
    dimensions = extract_dimensions()
    transformed = transform_records(extracted, dimensions)
    loaded_result = load_records(
        transformed,
        source_name=source_name,
        extracted_count=len(extracted),
    )
    return {
        "source": source_name,
        "extracted": len(extracted),
        "transformed": len(transformed),
        "loaded": loaded_result["loaded"],
        "target": loaded_result["target"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline ETL del SISG")
    parser.add_argument("--file", dest="source_path", help="Archivo CSV o Excel")
    parser.add_argument(
        "--table",
        dest="source_table",
        choices=(
            "op_atencion_medica",
            "op_morbilidad_epidemiologica",
            "op_hecho_vital",
        ),
        help="Tabla operativa de sisg_dw",
    )
    arguments = parser.parse_args()
    print(ejecutar_etl_completo(arguments.source_path, arguments.source_table))


if __name__ == "__main__":
    main()

