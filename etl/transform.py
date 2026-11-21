"""Limpieza, normalización y cálculo de campos derivados."""

import re

import pandas as pd


def _column_name(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    columns = {str(column).lower(): column for column in frame.columns}
    return next((columns[name] for name in candidates if name in columns), None)


def _lookup_surrogate(
    source: pd.Series,
    dimension: pd.DataFrame,
    source_candidates: tuple[str, ...],
    dimension_code_candidates: tuple[str, ...],
    key_candidates: tuple[str, ...],
    code_length: int | None = None,
) -> pd.Series:
    source_name = _column_name(source.to_frame(), source_candidates)
    code_name = _column_name(dimension, dimension_code_candidates)
    key_name = _column_name(dimension, key_candidates)
    if not source_name or not code_name or not key_name:
        return pd.Series(-1, index=source.index, dtype="int64")
    mapping = dimension.set_index(code_name)[key_name].to_dict()
    normalized_mapping = {
        _normalize_code(code, code_length): key for code, key in mapping.items()
    }
    return (
        source.map(lambda value: _normalize_code(value, code_length))
        .map(normalized_mapping)
        .fillna(-1)
        .astype("int64")
    )


def _normalize_code(value, length: int | None = None):
    if pd.isna(value):
        return "-1"
    code = re.sub(r"\s+", "", str(value)).upper()
    if length:
        code = code.zfill(length)
    return code or "-1"


def transform_records(
    frame: pd.DataFrame,
    dimensions: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Elimina filas vacías/duplicadas y normaliza códigos y métricas."""
    transformed = frame.copy().dropna(how="all").drop_duplicates()
    for column in ("codigo_cie10", "cie10", "codigo_diagnostico"):
        if column in transformed:
            transformed[column] = transformed[column].map(_normalize_code)
    for column in ("codigo_ine", "codigo_geografia", "codigo_municipio"):
        if column in transformed:
            transformed[column] = transformed[column].map(
                lambda value: _normalize_code(value, length=4)
            )
    dimensions = dimensions or {}
    mapping_specs = (
        (
            "diagnostico",
            ("codigo_cie10", "cie10", "codigo_diagnostico"),
            ("codigo_cie10", "codigo_diagnostico", "cie10"),
            ("sk_diagnostico", "sk_diagnostico"),
        ),
        (
            "geografia",
            ("codigo_ine", "codigo_geografia", "codigo_municipio"),
            ("codigo_ine", "codigo_geografia", "codigo_municipio"),
            ("sk_geografia", "sk_geografia"),
        ),
        (
            "establecimiento",
            ("codigo_establecimiento", "codigo_prestador"),
            ("codigo_establecimiento", "codigo_prestador"),
            ("sk_establecimiento", "sk_establecimiento"),
        ),
    )
    for name, source_columns, dimension_columns, key_columns in mapping_specs:
        dimension = dimensions.get(name)
        source_column = _column_name(transformed, source_columns)
        if dimension is not None and source_column:
            target_key = key_columns[0]
            transformed[target_key] = _lookup_surrogate(
                transformed[source_column],
                dimension,
                source_columns,
                dimension_columns,
                key_columns,
                code_length=4 if name == "geografia" else None,
            )
    time_dimension = dimensions.get("tiempo")
    time_source = _column_name(transformed, ("fecha", "fecha_atencion", "fecha_evento"))
    if time_dimension is not None and time_source:
        transformed["sk_tiempo"] = _lookup_surrogate(
            transformed[time_source],
            time_dimension,
            (time_source,),
            ("fecha", "fecha_completa", "fecha_calendario"),
            ("sk_tiempo",),
        )
    for key_column in (
        "sk_tiempo",
        "sk_geografia",
        "sk_establecimiento",
        "sk_diagnostico",
    ):
        if key_column in transformed:
            transformed[key_column] = pd.to_numeric(
                transformed[key_column], errors="coerce"
            ).fillna(-1).astype("int64")
    if "peso_gramos" in transformed:
        weight = pd.to_numeric(transformed["peso_gramos"], errors="coerce")
        transformed["bajo_peso"] = (weight < 2500).fillna(False).astype("int8")
    if {"costo_total", "cantidad"}.issubset(transformed.columns):
        cost = pd.to_numeric(transformed["costo_total"], errors="coerce")
        quantity = pd.to_numeric(transformed["cantidad"], errors="coerce")
        transformed["costo_promedio"] = cost.div(quantity.where(quantity != 0))
    for key_column in (
        "sk_tiempo",
        "sk_geografia",
        "sk_establecimiento",
        "sk_diagnostico",
    ):
        if key_column not in transformed:
            transformed[key_column] = -1
    return transformed.reset_index(drop=True)

