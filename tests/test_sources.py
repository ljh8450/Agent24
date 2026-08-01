import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.errors import DomainError
from app.sources import _store_download, fetch_kosis_statistics, source_excerpt


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


class SourceExcerptTests(unittest.TestCase):
    def test_kosis_openapi_snapshot_is_compacted_per_cell(self):
        rows = [
            {
                "TBL_NM": "1인 가구 비율",
                "PRD_DE": "2025",
                "C1_NM": "서울",
                "C2_NM": "청년",
                "ITM_NM": "비율",
                "DT": "34.5",
                "UNIT_NM": "%",
                "C1_NM_ENG": "Seoul",
                "ORG_ID": "101",
            }
        ]
        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / "data" / "source-cache" / "table.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps(rows), encoding="utf-8")
            excerpt = source_excerpt(
                Path(root),
                {"snapshot_path": "data/source-cache/table.json", "source_kind": "kosis_openapi"},
            )
        self.assertIn("2025 | 서울 > 청년 | 비율 = 34.5 %", excerpt)
        self.assertNotIn("C1_NM_ENG", excerpt)

    def test_non_kosis_snapshot_stays_raw(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / "data" / "source-cache" / "page.txt"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text("일반 웹 원문", encoding="utf-8")
            excerpt = source_excerpt(
                Path(root), {"snapshot_path": "data/source-cache/page.txt", "source_kind": "web_page"}
            )
        self.assertEqual(excerpt, "일반 웹 원문")


if __name__ == "__main__":
    unittest.main()
