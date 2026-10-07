"""YouTube 채널 영상별 조회수 스냅샷 수집기.

GitHub Actions에서 1시간마다 실행된다. config.json의 채널마다
  1. 채널 업로드 목록에서 새 영상을 찾고
  2. 수집 주기가 돌아온 영상(업로드 후 early_days 이내면 early_interval_hours,
     그 이후면 late_interval_hours)의 조회수/좋아요/댓글 수를 가져와
  3. docs/data/<채널 key>/snapshots/<video_id>.csv 에 한 줄씩 추가하고
  4. 대시보드가 읽는 docs/data/<채널 key>/videos.json, channel.json 을 갱신한다.
마지막으로 채널 목록 docs/data/channels.json 을 갱신한다.

외부 패키지 없이 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "docs" / "data"
# 채널별 폴더 안의 파일 이름
LEGACY_FILES = ("channel.json", "videos.json", "channel_snapshots.csv", "snapshots")

API_BASE = "https://www.googleapis.com/youtube/v3/"
SNAPSHOT_FIELDS = ["t", "views", "likes", "comments"]


# ---------------------------------------------------------------- 시간 유틸

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration(iso: str | None) -> int | None:
    """ISO 8601 기간(PT1H2M3S)을 초로 변환."""
    if not iso:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


# ---------------------------------------------------------------- YouTube API

class YouTubeAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.units = 0  # 이번 실행에서 사용한 쿼터 (list 호출은 1유닛)

    def get(self, endpoint: str, **params) -> dict:
        params["key"] = self.api_key
        url = API_BASE + endpoint + "?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            try:
                self.units += 1
                with urllib.request.urlopen(url, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                # 404(재생목록 없음)나 403(키/쿼터 문제)은 재시도해도 소용없다.
                if e.code < 500 or attempt == 3:
                    raise APIError(e.code, body) from None
            except urllib.error.URLError:
                if attempt == 3:
                    raise
            time.sleep(2 ** (attempt + 1))
        raise AssertionError("unreachable")

    def playlist_video_ids(self, playlist_id: str, max_pages: int | None) -> list[str]:
        ids: list[str] = []
        token = None
        pages = 0
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self.get("playlistItems", **params)
            ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
            token = data.get("nextPageToken")
            pages += 1
            if not token or (max_pages is not None and pages >= max_pages):
                return ids

    def videos(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            data = self.get(
                "videos",
                part="snippet,statistics,contentDetails,liveStreamingDetails",
                id=",".join(ids[i : i + 50]),
                maxResults=50,
            )
            for it in data.get("items", []):
                out[it["id"]] = it
        return out


class APIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"YouTube API HTTP {status}: {body[:500]}")
        self.status = status


# ---------------------------------------------------------------- 파일 입출력

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def append_csv(path: Path, fields: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def read_snapshots(d: Path, video_id: str) -> list[tuple[datetime, int]]:
    path = d / "snapshots" / f"{video_id}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [(parse_time(r["t"]), int(r["views"])) for r in csv.DictReader(f) if r["views"]]


# ---------------------------------------------------------------- 수집 로직

def interval_for(video: dict, now: datetime, cfg: dict) -> int:
    """이 영상의 수집 간격(시간)."""
    age = now - parse_time(video["published_at"])
    if age < timedelta(days=cfg["early_days"]):
        return cfg["early_interval_hours"]
    return cfg["late_interval_hours"]


def slot(dt: datetime, hours: int, cfg: dict) -> int:
    """현지 시각 기준으로 정각부터 hours시간씩 나눈 구간 번호.

    예) 12시간 간격이면 00:00~11:59가 한 구간, 12:00~23:59가 다음 구간.
    수집은 '이 구간에 아직 안 했으면 한다'로 판단하므로, 실행이 몇 분 늦거나
    한 번 빠져도 다음 정각부터 다시 제자리를 찾는다.
    """
    local = dt + timedelta(hours=cfg.get("timezone_offset_hours", 0))
    return int(local.timestamp()) // (hours * 3600)


def is_due(video: dict, now: datetime, cfg: dict) -> bool:
    if video.get("status") == "unavailable":
        return False
    if video.get("status") == "upcoming":
        return True  # 예정된 라이브/프리미어: 시작됐는지 매번 확인
    last = parse_time(video.get("last_collected_at"))
    if last is None:
        return True
    hours = interval_for(video, now, cfg)
    return slot(now, hours, cfg) > slot(last, hours, cfg)


def interpolate(series: list[tuple[datetime, int]], at: datetime) -> int | None:
    """스냅샷 사이를 선형 보간한 특정 시각의 조회수. 범위 밖이면 None."""
    if not series or at < series[0][0] or at > series[-1][0]:
        return None
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        if t0 <= at <= t1:
            if t1 == t0:
                return v1
            frac = (at - t0) / (t1 - t0)
            return round(v0 + (v1 - v0) * frac)
    return series[-1][1]


def derived_metrics(d: Path, video: dict) -> dict:
    series = read_snapshots(d, video["id"])
    if not series:
        return {"views_24h_after": None, "views_7d_after": None, "gain_24h": None, "snapshots": 0}
    pub = parse_time(video["published_at"])
    last_t, last_v = series[-1]
    before = interpolate(series, last_t - timedelta(hours=24))
    return {
        "views_24h_after": interpolate(series, pub + timedelta(hours=24)),
        "views_7d_after": interpolate(series, pub + timedelta(days=7)),
        "gain_24h": None if before is None else last_v - before,
        "snapshots": len(series),
    }


def resolve_channel(api: YouTubeAPI, cfg: dict, channel: dict) -> dict:
    handle = cfg["channel_handle"]
    if cfg.get("channel_id"):
        data = api.get("channels", part="snippet,contentDetails,statistics", id=cfg["channel_id"])
    else:
        data = api.get("channels", part="snippet,contentDetails,statistics", forHandle=handle)
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"채널을 찾을 수 없습니다: {cfg.get('channel_id') or handle}")
    it = items[0]
    channel.update(
        {
            "handle": it["snippet"].get("customUrl") or handle,
            "channel_id": it["id"],
            "title": it["snippet"]["title"],
            "thumbnail": it["snippet"]["thumbnails"].get("default", {}).get("url"),
            "uploads_playlist": it["contentDetails"]["relatedPlaylists"]["uploads"],
            "subscribers": _int(it["statistics"].get("subscriberCount")),
            "total_views": _int(it["statistics"].get("viewCount")),
            "video_count": _int(it["statistics"].get("videoCount")),
        }
    )
    return channel


def _int(v) -> int | None:
    return None if v is None else int(v)


def update_video_meta(video: dict, item: dict, is_short: bool | None, cfg: dict) -> None:
    """is_short가 None이면 영상 길이로 쇼츠 여부를 추정한다."""
    sn = item["snippet"]
    live = item.get("liveStreamingDetails") or {}
    duration = parse_duration(item.get("contentDetails", {}).get("duration"))
    # 라이브/프리미어는 실제 시작 시각을 기준으로 경과 시간을 잰다.
    published = live.get("actualStartTime") or sn["publishedAt"]
    thumbs = sn.get("thumbnails", {})
    if is_short is None:
        is_short = duration is not None and 0 < duration <= cfg["shorts_max_seconds"] and not live
    video.update(
        {
            "title": sn["title"],
            "published_at": fmt_time(parse_time(published)),
            "thumbnail": (thumbs.get("medium") or thumbs.get("default") or {}).get("url"),
            "duration_s": duration,
            "is_short": is_short,
            "is_live": bool(live),
        }
    )
    video["status"] = "upcoming" if sn.get("liveBroadcastContent") == "upcoming" else "active"


def run(api: YouTubeAPI, cfg: dict, now: datetime) -> dict:
    """채널 하나를 수집한다. cfg는 공통 설정에 채널별 설정(key, channel_handle 등)을 합친 것."""
    d = DATA_DIR / cfg["key"]
    api.units = 0
    channel = load_json(d / "channel.json", {})
    videos: dict[str, dict] = {v["id"]: v for v in load_json(d / "videos.json", [])}

    if channel.get("source_handle") not in (None, cfg["channel_handle"]):
        raise RuntimeError(f"{cfg['key']}: 설정의 채널 핸들이 바뀌었습니다. 새 key를 쓰거나 기존 데이터를 지우세요.")
    channel["source_handle"] = cfg["channel_handle"]

    # 정각 실행이 성공했다면 같은 시간대의 백업 실행은 아무것도 하지 않는다.
    last_run = parse_time(channel.get("last_run_at"))
    if last_run is not None and slot(now, 1, cfg) == slot(last_run, 1, cfg):
        return {"skipped": "이번 시간대에 이미 수집했습니다"}

    resolve_channel(api, cfg, channel)
    append_csv(
        d / "channel_snapshots.csv",
        ["t", "subscribers", "total_views", "video_count"],
        {"t": fmt_time(now), **{k: channel[k] for k in ("subscribers", "total_views", "video_count")}},
    )

    # 1) 영상 목록: 평소엔 최신 50개만, 하루 한 번(또는 처음)은 전체 목록을 훑는다.
    last_full = parse_time(channel.get("last_full_scan_at"))
    full_h = cfg["full_scan_interval_hours"]
    full = last_full is None or slot(now, full_h, cfg) > slot(last_full, full_h, cfg)
    max_pages = None if full else 1
    listed = api.playlist_video_ids(channel["uploads_playlist"], max_pages)

    # 쇼츠 판별: 채널의 쇼츠 전용 재생목록(UUSH...)을 사용하고, 없으면 영상 길이로 추정한다.
    shorts_ids: set[str] | None
    try:
        shorts_ids = set(api.playlist_video_ids("UUSH" + channel["channel_id"][2:], max_pages))
        channel["shorts_detection"] = "playlist"
    except APIError:
        shorts_ids = None
        channel["shorts_detection"] = "duration"
    if full:
        channel["last_full_scan_at"] = fmt_time(now)

    new_ids = [vid for vid in listed if vid not in videos]
    for vid in listed:
        v = videos.setdefault(vid, {"id": vid, "first_seen_at": fmt_time(now)})
        if v.get("status") == "unavailable":
            v["status"] = "active"  # 비공개였다가 다시 공개된 경우

    # 2) 수집 대상 선정
    targets = [vid for vid, v in videos.items() if vid in new_ids or "published_at" not in v or is_due(v, now, cfg)]
    items = api.videos(targets)

    # 3) 스냅샷 기록
    collected = 0
    for vid in targets:
        v = videos[vid]
        item = items.get(vid)
        if item is None:
            v["status"] = "unavailable"  # 삭제·비공개 전환
            continue
        if shorts_ids is None:
            is_short = None
        elif full:
            is_short = vid in shorts_ids
        else:
            # 증분 실행에서는 쇼츠 목록도 최신 50개뿐이므로, 거기 없으면 기존 판별을 유지한다.
            is_short = vid in shorts_ids or v.get("is_short", False)
        update_video_meta(v, item, is_short, cfg)
        if v["status"] == "upcoming":
            continue
        st = item.get("statistics", {})
        if "viewCount" not in st:
            continue
        row = {
            "t": fmt_time(now),
            "views": int(st["viewCount"]),
            "likes": st.get("likeCount", ""),
            "comments": st.get("commentCount", ""),
        }
        append_csv(d / "snapshots" / f"{vid}.csv", SNAPSHOT_FIELDS, row)
        v["last_collected_at"] = row["t"]
        v["latest"] = {
            "views": row["views"],
            "likes": _int(row["likes"] or None),
            "comments": _int(row["comments"] or None),
        }
        collected += 1

    # 4) 대시보드용 파생 지표
    for v in videos.values():
        if "published_at" in v:
            v.update(derived_metrics(d, v))

    ordered = sorted(
        (v for v in videos.values() if "published_at" in v),
        key=lambda v: v["published_at"],
        reverse=True,
    )
    channel["last_run_at"] = fmt_time(now)
    channel["settings"] = {
        k: cfg.get(k) for k in ("early_days", "early_interval_hours", "late_interval_hours", "timezone_offset_hours")
    }
    save_json(d / "channel.json", channel)
    save_json(d / "videos.json", ordered)

    summary = {
        "full_scan": full,
        "listed": len(listed),
        "new": len(new_ids),
        "targets": len(targets),
        "collected": collected,
        "quota_units": api.units,
    }
    return summary


def channel_configs(cfg: dict) -> list[dict]:
    """공통 설정 위에 채널별 설정을 덮어쓴 목록. 채널마다 수집 주기를 다르게 줄 수도 있다."""
    common = {k: v for k, v in cfg.items() if k != "channels"}
    return [{**common, **ch} for ch in cfg["channels"]]


def migrate_legacy_layout(channels: list[dict]) -> None:
    """채널이 하나뿐이던 시절의 docs/data/*.json 을 docs/data/<key>/ 로 옮긴다."""
    legacy = DATA_DIR / "channel.json"
    if not legacy.exists():
        return
    handle = load_json(legacy, {}).get("source_handle")
    target = next((c for c in channels if c["channel_handle"] == handle), None)
    if target is None:
        raise RuntimeError(f"기존 데이터의 채널({handle})이 config.json에 없습니다.")
    dest = DATA_DIR / target["key"]
    dest.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_FILES:
        src = DATA_DIR / name
        if src.exists():
            src.rename(dest / name)
    print(f"기존 데이터를 data/{target['key']}/ 로 옮겼습니다.")


def write_channel_index(channels: list[dict]) -> None:
    index = []
    for c in channels:
        info = load_json(DATA_DIR / c["key"] / "channel.json", None)
        if info is None:
            continue
        entry = {"key": c["key"], **{k: info.get(k) for k in ("title", "handle", "thumbnail", "last_run_at")}}
        # 설정값은 config.json에서 바로 가져온다 (수집을 건너뛴 실행에서도 대시보드에 반영되도록).
        entry["split_minutes"] = c.get("split_minutes")
        index.append(entry)
    save_json(DATA_DIR / "channels.json", index)


def main() -> None:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise SystemExit("환경 변수 YOUTUBE_API_KEY가 필요합니다.")
    channels = channel_configs(load_json(CONFIG_PATH, None))
    migrate_legacy_layout(channels)
    api = YouTubeAPI(api_key)
    now = utcnow()
    failed = []
    for cfg in channels:
        # 한 채널이 실패해도 나머지 채널은 계속 수집한다.
        try:
            summary = run(api, cfg, now)
        except Exception as e:  # noqa: BLE001
            failed.append(cfg["key"])
            summary = {"error": str(e)}
        print(cfg["key"], json.dumps(summary, ensure_ascii=False))
    write_channel_index(channels)
    if failed:
        raise SystemExit(f"수집 실패: {', '.join(failed)}")


if __name__ == "__main__":
    main()
