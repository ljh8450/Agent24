import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.errors import DomainError
from app.sources import _store_download, fetch_kosis_statistics


class SourceConnectorTests(unittest.TestCase):
    def test_machine_readable_api_error_is_not_persisted_as_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            with patch(
                "app.sources._download", return_value=(b'{"err":"21","errMsg":"bad request"}', "application/json")
            ):
                with self.assertRaises(DomainError) as raised:
                    _store_download(Path(root), "https://kosis.kr/api", "https://kosis.kr/api", {}, "kosis_openapi")
            self.assertEqual(raised.exception.code, "UPSTREAM_API_ERROR")
            self.assertFalse((Path(root) / "data" / "source-cache").exists())

    def test_direct_kosis_selection_uses_parameter_data_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict("os.environ", {"KOSIS_API_KEY": "configured"}, clear=True):
                with patch("app.sources._store_download", return_value="fixture") as stored:
                    result = fetch_kosis_statistics(
                        Path(root),
                        {
                            "org_id": "101",
                            "table_id": "DT_1B040A3",
                            "item_id": "T20",
                            "period_type": "Y",
                            "classification_1": "00",
                        },
                    )
            self.assertEqual(result, "fixture")
            self.assertIn("/openapi/Param/statisticsParameterData.do", stored.call_args.args[1])
            self.assertIn("apiKey=%5Bconfigured%5D", stored.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
