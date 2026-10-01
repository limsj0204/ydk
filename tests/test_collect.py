"""가짜 YouTube API로 수집 주기·스냅샷 기록·파생 지표를 검증한다.

실행: python -m unittest discover tests
"""

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import collect  # noqa: E402

CFG = {
    "channel_handle": "@test",
    "early_days": 7,
    "early_interval_hours": 1,
    "late_interval_hours": 12,
    "timezone_offset_hours": 9,
    "full_scan_interval_hours": 24,
    "shorts_max_seconds": 180,
}
T0 = datetime(2026, 10, 1, 0, 17, tzinfo=timezone.utc)  # 한국 시각 09:17


class FakeAPI(collect.YouTubeAPI):
    def __init__(self):
        super().__init__("fake")
        self.views = {"new1": 100, "old1": 5000, "short1": 30}
        self.published = {
            "new1": T0 - timedelta(days=2),
            "old1": T0 - timedelta(days=30),
            "short1": T0 - timedelta(hours=3),
        }
        self.removed: set[str] = set()
        self.requested: list[list[str]] = []

    def get(self, endpoint, **params):
        self.units += 1
        if endpoint == "channels":
            return {
                "items": [
                    {
                        "id": "UCabc",
                        "snippet": {"title": "테스트", "customUrl": "@test", "thumbnails": {}},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
                        "statistics": {"subscriberCount": "10", "viewCount": "999", "videoCount": "3"},
                    }
                ]
            }
        raise AssertionError(endpoint)

    def playlist_video_ids(self, playlist_id, max_pages):
        self.units += 1
        live = [v for v in self.views if v not in self.removed]
        if playlist_id == "UUSHabc":
            return [v for v in live if v.startswith("short")]
        return live

    def videos(self, ids):
        self.units += 1
        self.requested.append(sorted(ids))
        return {
            vid: {
                "id": vid,
                "snippet": {
                    "title": f"영상 {vid}",
                    "publishedAt": collect.fmt_time(self.published[vid]),
                    "thumbnails": {},
                    "liveBroadcastContent": "none",
                },
                "statistics": {"viewCount": str(self.views[vid]), "likeCount": "1", "commentCount": "2"},
                "contentDetails": {"duration": "PT10M"},
            }
            for vid in ids
            if vid not in self.removed
        }


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        collect.DATA_DIR = d
        collect.SNAPSHOT_DIR = d / "snapshots"
        collect.CHANNEL_PATH = d / "channel.json"
        collect.VIDEOS_PATH = d / "videos.json"
        collect.CHANNEL_SNAPSHOTS_PATH = d / "channel_snapshots.csv"
        self.api = FakeAPI()

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, vid):
        with (collect.SNAPSHOT_DIR / f"{vid}.csv").open() as f:
            return list(csv.DictReader(f))

    def videos(self):
        return {v["id"]: v for v in json.loads(collect.VIDEOS_PATH.read_text())}

    def test_tiered_schedule(self):
        collect.run(self.api, CFG, T0)
        self.assertEqual(self.api.requested[-1], ["new1", "old1", "short1"])

        # 다음 정각(KST 10:00): 7일 이내 영상만
        collect.run(self.api, CFG, T0 + timedelta(minutes=43))
        self.assertEqual(self.api.requested[-1], ["new1", "short1"])

        # KST 12:00: 오래된 영상도 새 12시간 구간에 들어가 수집
        collect.run(self.api, CFG, datetime(2026, 10, 1, 3, 0, 20, tzinfo=timezone.utc))
        self.assertEqual(self.api.requested[-1], ["new1", "old1", "short1"])

        # KST 13:00 ~ 23:00: 오래된 영상은 건너뜀
        collect.run(self.api, CFG, datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(self.api.requested[-1], ["new1", "short1"])

        # KST 다음날 00:00: 다시 수집
        collect.run(self.api, CFG, datetime(2026, 10, 1, 15, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(self.api.requested[-1], ["new1", "old1", "short1"])

        self.assertEqual(len(self.rows("new1")), 5)
        self.assertEqual(len(self.rows("old1")), 3)

    def test_backup_run_in_same_hour_is_skipped(self):
        on_hour = datetime(2026, 10, 1, 1, 0, 30, tzinfo=timezone.utc)
        collect.run(self.api, CFG, on_hour)
        calls = len(self.api.requested)
        summary = collect.run(self.api, CFG, on_hour + timedelta(minutes=30))
        self.assertIn("skipped", summary)
        self.assertEqual(len(self.api.requested), calls)

    def test_missed_on_hour_run_recovers_next_hour(self):
        # 10:00 실행이 빠져 10:30 백업이 수집했어도, 11:00에는 다시 정각 수집
        collect.run(self.api, CFG, datetime(2026, 10, 1, 1, 30, tzinfo=timezone.utc))
        collect.run(self.api, CFG, datetime(2026, 10, 1, 2, 0, 10, tzinfo=timezone.utc))
        self.assertIn("new1", self.api.requested[-1])

    def test_switch_to_late_interval_after_early_days(self):
        self.api.published["new1"] = T0 - timedelta(days=6, hours=23)
        collect.run(self.api, CFG, T0)
        collect.run(self.api, CFG, T0 + timedelta(hours=2))  # 이제 7일 경과, 같은 12시간 구간
        self.assertNotIn("new1", self.api.requested[-1])

    def test_metrics_and_shorts(self):
        self.api.published["short1"] = T0 - timedelta(hours=1)
        for h in range(0, 30):
            self.api.views["short1"] = 1000 * h
            collect.run(self.api, CFG, T0 + timedelta(hours=h))
        v = self.videos()["short1"]
        self.assertTrue(v["is_short"])
        self.assertFalse(self.videos()["new1"]["is_short"])
        # 업로드 24시간 후 = T0+23h 스냅샷
        self.assertEqual(v["views_24h_after"], 23000)
        self.assertEqual(v["gain_24h"], 24000)
        self.assertIsNone(v["views_7d_after"])
        self.assertEqual(v["latest"]["views"], 29000)

    def test_removed_video_marked_unavailable(self):
        collect.run(self.api, CFG, T0)
        self.api.removed.add("new1")
        collect.run(self.api, CFG, T0 + timedelta(hours=1))
        self.assertEqual(self.videos()["new1"]["status"], "unavailable")
        collect.run(self.api, CFG, T0 + timedelta(hours=2))
        self.assertNotIn("new1", self.api.requested[-1])

    def test_new_upload_picked_up(self):
        collect.run(self.api, CFG, T0)
        self.api.views["new2"] = 5
        self.api.published["new2"] = T0 + timedelta(minutes=30)
        collect.run(self.api, CFG, T0 + timedelta(hours=1))
        self.assertIn("new2", self.api.requested[-1])
        self.assertEqual(self.rows("new2")[0]["views"], "5")


class HelpersTest(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(collect.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(collect.parse_duration("PT45S"), 45)
        self.assertEqual(collect.parse_duration("P1DT1S"), 86401)

    def test_interpolate(self):
        s = [(T0, 0), (T0 + timedelta(hours=2), 200)]
        self.assertEqual(collect.interpolate(s, T0 + timedelta(hours=1)), 100)
        self.assertIsNone(collect.interpolate(s, T0 - timedelta(hours=1)))
        self.assertIsNone(collect.interpolate(s, T0 + timedelta(hours=3)))


if __name__ == "__main__":
    unittest.main()
