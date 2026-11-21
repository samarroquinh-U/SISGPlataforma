import unittest

import pandas as pd

from app import create_app
from etl.transform import transform_records


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-key"})
        self.client = self.app.test_client()

    def test_public_home_and_login_render(self):
        home = self.client.get("/")
        login = self.client.get("/login")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Hub-and-Spoke", home.data)
        self.assertEqual(login.status_code, 200)
        self.assertIn(b"Usuario MySQL", login.data)

    def test_group_cannot_open_another_groups_dashboard(self):
        with self.client.session_transaction() as browser_session:
            browser_session["group_id"] = 1
            browser_session["username"] = "usr_grupo1"
            browser_session["csrf_token"] = "test-csrf"
        response = self.client.get("/dashboard/grupo/2")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/dashboard/grupo/1"))

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


if __name__ == "__main__":
    unittest.main()
