import unittest
from unittest.mock import patch

import pandas as pd

from app import (
    create_app,
    formato_celda,
    formato_fecha,
    formato_miles,
    formato_quetzal,
)
from database.queries import fetch_rows
from etl.load import load_inbox_batch
from etl.pipeline import _build_cost_frame
from etl.transform import transform_records
from services.platform_service import _build_charts, _period_label, _present_records


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def mappings(self):
        return self

    def all(self):
        return self.rows


class CatalogQueryConnection:
    columns = {
        "vw_group": ["sk_tiempo", "sk_geografia"],
        "vw_cat_tiempo": ["sk_tiempo", "nombre_mes", "anio"],
        "vw_cat_geografia": [
            "sk_geografia",
            "nombre_municipio",
            "nombre_departamento",
        ],
    }

    def __init__(self):
        self.dashboard_query = ""

    def execute(self, statement, params=None):
        query = str(statement)
        if "information_schema.COLUMNS" in query:
            return FakeResult(
                [(column,) for column in self.columns[params["table_name"]]]
            )
        self.dashboard_query = query
        return FakeResult([])


class IdempotencyConnection:
    def __init__(self):
        self.claimed = set()
        self.fact_rows = []
        self.statements = []

    def execute(self, statement, params=None):
        query = str(statement)
        self.statements.append(query)
        if "INSERT IGNORE" in query:
            key = (params["source_table"], params["source_id"])
            if key in self.claimed:
                return type("Result", (), {"rowcount": 0})()
            self.claimed.add(key)
            return type("Result", (), {"rowcount": 1})()
        if "INSERT INTO `fact_atencion_privada`" in query:
            self.fact_rows.extend(params)
            return type("Result", (), {"rowcount": len(params)})()
        return type("Result", (), {"rowcount": 1})()


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-key"})
        self.client = self.app.test_client()

    def test_public_home_and_login_render(self):
        home = self.client.get("/")
        login = self.client.get("/login")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Hub-and-Spoke", home.data)
        self.assertEqual(home.data.count(b"Ingresar al portal"), 1)
        self.assertEqual(login.status_code, 200)
        self.assertIn(b"Usuario MySQL", login.data)
        self.assertNotIn(b"Ingresar al portal", login.data)

    def test_human_format_filters(self):
        self.assertEqual(formato_quetzal(29250), "Q 29,250.00")
        self.assertEqual(formato_miles(14360), "14,360")
        self.assertEqual(formato_fecha("2024-01-01"), "Enero 2024")
        self.assertEqual(
            formato_fecha("2026-11-21 16:11:16"),
            "21 Nov 2026, 16:11",
        )
        self.assertEqual(
            formato_celda("2026-11-21 16:11:16", "fecha_ingesta"),
            "21 Nov 2026, 16:11",
        )

    def test_group_cannot_open_another_groups_dashboard(self):
        with self.client.session_transaction() as browser_session:
            browser_session["group_id"] = 1
            browser_session["username"] = "usr_grupo1"
            browser_session["csrf_token"] = "test-csrf"
        response = self.client.get("/dashboard/grupo/2")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/dashboard/grupo/1"))

    def test_etl_monitor_is_only_available_to_group_8(self):
        with self.client.session_transaction() as browser_session:
            browser_session["group_id"] = 1
            browser_session["username"] = "usr_grupo1"
        denied = self.client.get("/dashboard/central")
        self.assertEqual(denied.status_code, 302)
        self.assertNotIn(b"Monitor ETL", denied.data)

        with self.client.session_transaction() as browser_session:
            browser_session["group_id"] = 8
            browser_session["username"] = "usr_grupo8"
        with patch(
            "routes.dashboard_routes.central_dashboard",
            return_value={
                "counts": {},
                "ingestas": [],
                "usos": [],
                "activity_chart": {"labels": [], "values": []},
                "etl_monitor": {
                    "received_total": 3,
                    "pending_total": 2,
                    "processed_total": 1,
                    "cleaned_total": 1,
                    "loaded_total": 1,
                    "groups": [],
                    "chart": {"labels": [], "received": [], "sent": []},
                },
            },
        ):
            allowed = self.client.get("/dashboard/central")
        self.assertEqual(allowed.status_code, 200)
        self.assertIn(b"Monitor ETL", allowed.data)
        self.assertIn(b"Ejecutar ETL Ahora", allowed.data)

    def test_transform_maps_dimension_keys_and_marks_unmatched(self):
        source = pd.DataFrame(
            {
                "codigo_cie10": [" a09 ", "Z99"],
                "codigo_ine": ["0101", "9999"],
                "peso_gramos": [2400, None],
                "costo_total": [100, 25],
                "cantidad": [2, 0],
            }
        )
        dimensions = {
            "diagnostico": pd.DataFrame(
                {"codigo_cie10": ["A09"], "sk_diagnostico": [12]}
            ),
            "geografia": pd.DataFrame(
                {"codigo_ine": ["101"], "sk_geografia": [44]}
            ),
        }
        result = transform_records(source, dimensions)
        self.assertEqual(result["sk_diagnostico"].tolist(), [12, -1])
        self.assertEqual(result["sk_geografia"].tolist(), [44, -1])
        self.assertEqual(result["bajo_peso"].tolist(), [1, 0])
        self.assertEqual(result.loc[0, "costo_promedio"], 50)
        self.assertTrue(pd.isna(result.loc[1, "costo_promedio"]))

    def test_transform_sanitizes_metrics_and_homologates_actors_and_keys(self):
        source = pd.DataFrame(
            {
                "id_ingesta": [101, 102],
                "codigo_establecimiento": ["ONG-1", "PRIV-1"],
                "sk_servicio": [5, 999],
                "servicios": [-2, 2],
                "monto_cobrado": [100, -4],
            }
        )
        dimensions = {
            "establecimiento": pd.DataFrame(
                {
                    "codigo_establecimiento": ["ONG-1", "PRIV-1"],
                    "sk_establecimiento": [11, 12],
                    "tipo": ["ONG comunitaria", "Privado"],
                }
            ),
            "servicio": pd.DataFrame({"sk_servicio": [5]}),
        }
        result = transform_records(source, dimensions, group_id=4)
        self.assertEqual(result["sk_actor"].tolist(), [7, 6])
        self.assertEqual(result["servicios"].tolist(), [0, 2])
        self.assertEqual(result["sk_servicio"].tolist(), [5, -1])
        self.assertEqual(result["sk_geografia"].tolist(), [-1, -1])
        self.assertEqual(result["costo_promedio"].tolist(), [100, 0])
        empty = transform_records(
            pd.DataFrame(
                {
                    "id_ingesta": [103],
                    "procesado_etl": [0],
                    "fecha_envio": ["2026-11-23 09:00:00"],
                    "servicios": [None],
                }
            ),
            group_id=4,
        )
        self.assertTrue(empty.empty)

    def test_inbox_load_preserves_rows_and_is_idempotent(self):
        connection = IdempotencyConnection()
        batch = pd.DataFrame(
            {"id_ingesta": [501], "atenciones": [12], "sk_tiempo": [20261123]}
        )
        with (
            patch("etl.load._columns_for", return_value=["atenciones"]),
            patch("etl.load._require_innodb"),
        ):
            first = load_inbox_batch(
                connection,
                batch,
                source_table="ingesta_g1_atencion",
                source_ids=[501],
                group_id=1,
                target="fact_atencion_privada",
            )
            second = load_inbox_batch(
                connection,
                batch,
                source_table="ingesta_g1_atencion",
                source_ids=[501],
                group_id=1,
                target="fact_atencion_privada",
            )
        self.assertEqual(first["loaded"], 1)
        self.assertEqual(second["loaded"], 0)
        self.assertEqual(second["already_processed"], 1)
        self.assertEqual(len(connection.fact_rows), 1)
        self.assertTrue(
            any(
                "UPDATE `sisg_analitica`.`ingesta_g1_atencion`" in query
                and "SET `procesado_etl` = 1" in query
                for query in connection.statements
            )
        )
        self.assertFalse(
            any("DELETE" in query.upper() or "TRUNCATE" in query.upper()
                for query in connection.statements)
        )

    def test_cost_fact_rows_use_group_specific_dimensions(self):
        source = pd.DataFrame(
            {
                "id_ingesta": [1],
                "sk_actor": [7],
                "sk_diagnostico": [12],
                "sk_servicio": [9],
                "atenciones": [4],
                "servicios": [3],
                "monto_cobrado": [120.0],
                "costo_promedio": [30.0],
            }
        )
        private_cost = _build_cost_frame(source, 1)
        service_cost = _build_cost_frame(source, 4)
        self.assertEqual(private_cost.iloc[0]["sk_actor"], 2)
        self.assertEqual(private_cost.iloc[0]["sk_servicio"], 1)
        self.assertEqual(service_cost.iloc[0]["sk_diagnostico"], -1)
        self.assertEqual(service_cost.iloc[0]["atenciones"], 3)

    def test_catalog_labels_replace_surrogate_keys(self):
        records, columns = _present_records(
            [
                {
                    "sk_tiempo": 20240101,
                    "sk_establecimiento": 1,
                    "atenciones": 12,
                    "__label_tiempo": "Enero 2024",
                    "__label_establecimiento": "Hospital Centro Médico",
                }
            ],
            ("tiempo", "establecimiento"),
        )
        self.assertEqual(records[0]["Periodo"], "Enero 2024")
        self.assertEqual(records[0]["Establecimiento"], "Hospital Centro Médico")
        self.assertNotIn("sk_tiempo", columns)
        self.assertNotIn("sk_establecimiento", columns)

    def test_monthly_chart_is_sorted_by_calendar_period(self):
        charts = _build_charts(
            [
                {"Periodo": "Febrero 2024", "Atenciones": 3, "Establecimiento": "A"},
                {"Periodo": "Enero 2024", "Atenciones": 2, "Establecimiento": "A"},
            ],
            {
                "metric": "atenciones",
                "metric_label": "Atenciones",
                "category": "Establecimiento",
            },
        )
        self.assertEqual(
            charts["monthly"]["labels"],
            ["Enero 2024", "Febrero 2024"],
        )
        self.assertEqual(_period_label({"Periodo": "2024-01-01"}), "Enero 2024")

    def test_dashboard_query_joins_catalogs_for_descriptive_names(self):
        connection = CatalogQueryConnection()
        fetch_rows(connection, "vw_group", ("tiempo", "geografia"))
        self.assertIn("LEFT JOIN `vw_cat_tiempo`", connection.dashboard_query)
        self.assertIn("LEFT JOIN `vw_cat_geografia`", connection.dashboard_query)
        self.assertIn("CONCAT(", connection.dashboard_query)
        self.assertIn("CONCAT_WS(' / '", connection.dashboard_query)
        self.assertIn("`__label_geografia`", connection.dashboard_query)

    def test_group_dashboard_renders_analytics_and_detail_tabs(self):
        group_data = {
            "group": {
                "name": "Atención privada",
                "description": "Servicios privados.",
                "view": "vw_g1_fact_atencion_privada",
                "read_only": False,
                "inbox": "ingesta_g1_atencion",
                "metric": "atenciones",
                "metric_label": "Atenciones",
                "category": "Establecimiento",
                "money_candidates": (),
                "days_candidates": (),
            },
            "total": 14360,
            "records": [
                {
                    "Periodo": "Enero 2024",
                    "Atenciones": 14360,
                    "Monto Cobrado": 29250,
                }
            ],
            "columns": ["Periodo", "Atenciones", "Monto Cobrado"],
            "kpis": [
                {"label": "Atenciones", "value": 14360, "format": "miles"},
                {"label": "Monto", "value": 29250, "format": "quetzal"},
                {"label": "Registros", "value": 1, "format": "miles"},
                {"label": "Categorías", "value": 1, "format": "miles"},
            ],
            "charts": {
                "monthly": {"labels": ["Enero 2024"], "values": [14360]},
                "categories": {"labels": ["Hospital Centro Médico"], "values": [14360]},
                "metric_label": "Atenciones",
                "category_label": "Establecimiento",
            },
        }
        with self.client.session_transaction() as browser_session:
            browser_session["group_id"] = 1
            browser_session["username"] = "usr_grupo1"
            browser_session["csrf_token"] = "test-csrf"
        with (
            patch("routes.dashboard_routes.group_dashboard", return_value=group_data),
            patch(
                "routes.dashboard_routes.ingestion_form",
                return_value={
                    "fields": [
                        {
                            "name": "sk_establecimiento",
                            "data_type": "int",
                        }
                    ],
                    "catalogs": {},
                    "field_catalogs": {
                        "sk_establecimiento": [
                            {"value": 1, "label": "Hospital Centro Médico"}
                        ]
                    },
                },
            ),
        ):
            response = self.client.get("/dashboard/grupo/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tablero Anal", response.data)
        self.assertIn(b"Datos Detallados", response.data)
        self.assertIn(b"Registrar / Ingresar Datos", response.data)
        self.assertIn(b"14,360", response.data)
        self.assertIn(b"Q 29,250.00", response.data)
        self.assertIn(b'value="1">Hospital Centro M\xc3\xa9dico', response.data)

    def test_central_dashboard_shows_corrected_fact_counts_and_activity_chart(self):
        with self.client.session_transaction() as browser_session:
            browser_session["group_id"] = 8
            browser_session["username"] = "root"
            browser_session["csrf_token"] = "test-csrf"
        central_data = {
            "counts": {
                "stg_metadatos_ingesta": 3,
                "fact_uso_plataforma": 20,
                "fact_atencion_privada": 14360,
                "fact_morbilidad": 12,
                "fact_nacimiento": 5,
                "fact_defuncion": 4,
            },
            "ingestas": [],
            "usos": [],
            "activity_chart": {
                "labels": ["Grupo 1"],
                "values": [14360],
            },
        }
        with patch(
            "routes.dashboard_routes.central_dashboard",
            return_value=central_data,
        ):
            response = self.client.get("/dashboard/central")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Fact Atencion Privada", response.data)
        self.assertIn(b"14,360", response.data)
        self.assertIn(b"Filas procesadas por grupo / actor", response.data)
        self.assertIn(b'"Grupo 1"', response.data)


if __name__ == "__main__":
    unittest.main()
