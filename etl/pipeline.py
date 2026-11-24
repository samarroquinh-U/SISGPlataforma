"""Orquestador del flujo ETL SISG."""

from datetime import datetime, timezone

import pandas as pd

from database.connection import get_engine
from etl.extract import (
    extract_dimensions_from_connection,
    extract_pending_inboxes,
    asegurar_columna_estado_buzones,
)
from etl.load import (
    ensure_idempotency_table,
    load_inbox_batch,
    record_etl_audit,
)
from etl.transform import transform_records


def ejecutar_etl_completo() -> dict:
    """Procesa pendientes de los cuatro buzones en una transacción global."""
    engine = get_engine(8)
    asegurar_columna_estado_buzones(engine)
    ensure_idempotency_table(engine)
    with engine.begin() as connection:
        batches = extract_pending_inboxes(connection)
        has_pending = any(not batch["records"].empty for batch in batches)
        dimensions = (
            extract_dimensions_from_connection(connection) if has_pending else {}
        )
        results = []
        for batch in batches:
            records = batch["records"]
            source_ids = records["id_ingesta"].tolist() if not records.empty else []
            transformed = transform_records(
                records,
                dimensions,
                group_id=batch["group_id"],
            )
            cost_frame = _build_cost_frame(transformed, batch["group_id"])
            result = load_inbox_batch(
                connection,
                transformed,
                source_table=batch["table"],
                source_ids=source_ids,
                group_id=batch["group_id"],
                target=batch["target"],
                cost_frame=cost_frame,
            )
            result["already_processed"] += batch["previously_processed"]
            results.append(result)
        record_etl_audit(connection, results, _today_surrogate(dimensions))

    return {
        "source": "buzones_g1_g4",
        "extracted": sum(result["extracted"] for result in results),
        "transformed": sum(result["cleaned"] for result in results),
        "loaded": sum(result["loaded"] for result in results),
        "already_processed": sum(
            result["already_processed"] for result in results
        ),
        "cost_loaded": sum(result["cost_loaded"] for result in results),
        "targets": [batch["target"] for batch in batches],
    }


def _build_cost_frame(
    transformed: pd.DataFrame,
    group_id: int,
) -> pd.DataFrame | None:
    if group_id not in {1, 4} or "monto_cobrado" not in transformed:
        return None
    amounts = pd.to_numeric(transformed["monto_cobrado"], errors="coerce")
    costs = transformed.loc[amounts > 0].copy()
    if group_id == 1:
        costs["sk_actor"] = 2
        costs["sk_servicio"] = 1
    else:
        costs["sk_diagnostico"] = -1
        costs["atenciones"] = costs["servicios"]
    return costs


def _today_surrogate(dimensions: dict[str, pd.DataFrame]) -> int:
    dimension = dimensions.get("tiempo")
    if dimension is None:
        return -1
    date_column = next(
        (
            column
            for column in ("fecha", "fecha_completa", "fecha_calendario")
            if column in dimension.columns
        ),
        None,
    )
    key_column = "sk_tiempo" if "sk_tiempo" in dimension.columns else None
    if not key_column:
        return -1
    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    keys = pd.to_numeric(dimension[key_column], errors="coerce")
    current_month_key = int(today.strftime("%Y%m01"))
    valid_matches = keys.loc[keys == current_month_key].dropna()
    if valid_matches.empty and date_column:
        dates = pd.to_datetime(dimension[date_column], errors="coerce").dt.date
        matches = dimension.loc[dates == month_start, key_column]
        if matches.empty:
            matches = dimension.loc[dates == today, key_column]
        valid_matches = pd.to_numeric(matches, errors="coerce").dropna()
    return int(valid_matches.iloc[0]) if not valid_matches.empty else -1


def main() -> None:
    print(ejecutar_etl_completo())


if __name__ == "__main__":
    main()

