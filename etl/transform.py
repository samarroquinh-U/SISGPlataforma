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


def _dimension_key_column(dimension: pd.DataFrame, name: str) -> str | None:
    return _column_name(
        dimension,
        (f"sk_{name}", f"id_{name}", "sk"),
    )


def _validated_surrogate_keys(
    transformed: pd.DataFrame,
    dimensions: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    for name in (
        "tiempo",
        "establecimiento",
        "geografia",
        "diagnostico",
        "servicio",
        "evento",
        "demografia",
    ):
        key_column = f"sk_{name}"
        dimension = dimensions.get(name)
        dimension_key = (
            _dimension_key_column(dimension, name)
            if dimension is not None
            else None
        )
        if dimension is None or dimension_key is None:
            transformed[key_column] = -1
            continue
        valid_keys = pd.to_numeric(
            dimension[dimension_key], errors="coerce"
        ).dropna()
        valid_keys = set(valid_keys.astype("int64"))
        if key_column not in transformed:
            transformed[key_column] = -1
        values = pd.to_numeric(transformed[key_column], errors="coerce")
        transformed[key_column] = (
            values.where(values.isin(valid_keys), -1).fillna(-1).astype("int64")
        )
    return transformed


def _lookup_time_surrogate(
    source: pd.Series,
    dimension: pd.DataFrame,
) -> pd.Series:
    date_column = _column_name(
        dimension, ("fecha", "fecha_completa", "fecha_calendario")
    )
    key_column = _dimension_key_column(dimension, "tiempo")
    if not date_column or not key_column:
        return pd.Series(-1, index=source.index, dtype="int64")
    dimension_dates = pd.to_datetime(
        dimension[date_column], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    mapping = dict(zip(dimension_dates, dimension[key_column]))
    source_dates = pd.to_datetime(source, errors="coerce").dt.strftime("%Y-%m-%d")
    return (
        source_dates.map(mapping)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(-1)
        .astype("int64")
    )


def transform_records(
    frame: pd.DataFrame,
    dimensions: dict[str, pd.DataFrame] | None = None,
    group_id: int | None = None,
) -> pd.DataFrame:
    """Limpia, homologa y valida un lote de registros de ingesta."""
    transformed = frame.copy()
    metadata_columns = [
        column
        for column in transformed.columns
        if str(column).lower() in {"id_ingesta", "procesado_etl"}
    ]
    content_columns = [
        column
        for column in transformed.columns
        if column not in metadata_columns
        and str(column).lower() != "fecha_envio"
    ]
    duplicate_columns = [
        column for column in transformed.columns if column not in metadata_columns
    ]
    if content_columns:
        transformed = transformed.dropna(subset=content_columns, how="all")
    else:
        transformed = transformed.iloc[0:0]
    for column in ("codigo_cie10", "cie10", "codigo_diagnostico"):
        if column in transformed:
            transformed[column] = transformed[column].map(_normalize_code)
    for column in ("codigo_ine", "codigo_geografia", "codigo_municipio"):
        if column in transformed:
            transformed[column] = transformed[column].map(
                lambda value: _normalize_code(value, length=4)
            )
    metric_names = {
        "atenciones",
        "casos",
        "prestaciones",
        "servicios",
        "dias_estancia",
        "monto_cobrado",
    }
    for column in transformed.columns:
        if str(column).lower() in metric_names:
            values = pd.to_numeric(transformed[column], errors="coerce").round(2)
            transformed[column] = values.mask(values < 0, 0)
    if duplicate_columns:
        transformed = transformed.drop_duplicates(subset=duplicate_columns)

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
        (
            "servicio",
            ("codigo_servicio", "servicio", "id_servicio"),
            ("codigo_servicio", "servicio", "id_servicio"),
            ("sk_servicio",),
        ),
        (
            "evento",
            ("codigo_evento", "evento", "id_evento"),
            ("codigo_evento", "evento", "id_evento"),
            ("sk_evento",),
        ),
        (
            "demografia",
            ("codigo_demografia", "demografia", "grupo_edad"),
            ("codigo_demografia", "demografia", "grupo_edad"),
            ("sk_demografia",),
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
    time_source = _column_name(
        transformed,
        (
            "fecha",
            "fecha_atencion",
            "fecha_evento",
            "fecha_prestacion",
            "fecha_servicio",
        ),
    )
    if time_dimension is not None and time_source:
        transformed["sk_tiempo"] = _lookup_time_surrogate(
            transformed[time_source], time_dimension
        )
    for key_column in (
        "sk_tiempo",
        "sk_geografia",
        "sk_establecimiento",
        "sk_diagnostico",
        "sk_servicio",
        "sk_evento",
        "sk_demografia",
    ):
        if key_column not in transformed:
            transformed[key_column] = -1
    transformed = _validated_surrogate_keys(transformed, dimensions)

    if group_id == 1:
        transformed["sk_actor"] = 2
    elif group_id == 4:
        establishment = dimensions.get("establecimiento")
        establishment_key = (
            _dimension_key_column(establishment, "establecimiento")
            if establishment is not None
            else None
        )
        actor_by_establishment = {}
        if (
            establishment is not None
            and establishment_key
            and "tipo" in establishment.columns
        ):
            actor_by_establishment = {
                int(key): (
                    7 if "ONG" in str(kind).upper() else 6
                )
                for key, kind in zip(
                    pd.to_numeric(
                        establishment[establishment_key], errors="coerce"
                    ),
                    establishment["tipo"],
                )
                if pd.notna(key)
            }
        transformed["sk_actor"] = transformed["sk_establecimiento"].map(
            actor_by_establishment
        ).fillna(6).astype("int64")

    if "peso_gramos" in transformed:
        weight = pd.to_numeric(transformed["peso_gramos"], errors="coerce")
        transformed["bajo_peso"] = (weight < 2500).fillna(False).astype("int8")
    if group_id in {1, 4} and "monto_cobrado" in transformed:
        metric_column = "atenciones" if group_id == 1 else "servicios"
        if metric_column not in transformed:
            metric_column = "cantidad"
        cost = pd.to_numeric(transformed["monto_cobrado"], errors="coerce")
        quantity = (
            pd.to_numeric(transformed[metric_column], errors="coerce")
            if metric_column in transformed
            else pd.Series(1, index=transformed.index, dtype="int64")
        )
        transformed["costo_promedio"] = (
            cost.div(quantity.fillna(0).clip(lower=1)).round(2)
        )
    elif {"costo_total", "cantidad"}.issubset(transformed.columns):
        cost = pd.to_numeric(transformed["costo_total"], errors="coerce")
        quantity = pd.to_numeric(transformed["cantidad"], errors="coerce")
        transformed["costo_promedio"] = cost.div(quantity.where(quantity != 0))
    return transformed.reset_index(drop=True)

