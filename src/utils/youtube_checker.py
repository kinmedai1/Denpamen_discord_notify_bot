"""
YouTube チャンネルの新着動画チェックモジュール。
YouTube RSSフィードを利用して動画情報を取得する（APIキー不要）。
"""

import logging
from datetime import datetime
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)

# YouTube RSSフィードのURL テンプレート
RSS_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class YouTubeChecker:
    """YouTube チャンネルの動画をRSSフィードから取得するクラス"""

    def __init__(self, channel_id: str):
        """
        Args:
            channel_id: YouTubeチャンネルID（UCで始まる文字列）
        """
        self.channel_id = channel_id
        self.feed_url = RSS_FEED_URL.format(channel_id=channel_id)

    def fetch_videos(self) -> list[dict]:
        """
        RSSフィードから動画一覧を取得する。

        Returns:
            動画情報のリスト。各動画は以下のキーを持つ:
            - video_id: YouTube動画ID
            - title: 動画タイトル
            - url: 動画のURL
            - published: 公開日時（ISO 8601形式の文字列）
            - published_formatted: 表示用の日時文字列（YYYY/MM/DD HH:MM）
            - thumbnail: サムネイルURL
            - author: チャンネル名
        """
        try:
            feed = feedparser.parse(self.feed_url)
        except Exception as e:
            logger.error(f"RSSフィードの取得に失敗しました: {e}")
            return []

        if feed.bozo and not feed.entries:
            logger.error(f"RSSフィードのパースに失敗しました: {feed.bozo_exception}")
            return []

        videos = []
        for entry in feed.entries:
            video_id = entry.get("yt_videoid", "")
            if not video_id:
                continue

            # 公開日時をパース
            published = entry.get("published", "")
            published_formatted = self._format_published_date(published)

            # サムネイルURLを構築
            thumbnail = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

            # チャンネル名を取得
            author = entry.get("author", "")

            videos.append({
                "video_id": video_id,
                "title": entry.get("title", "無題"),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published": published,
                "published_formatted": published_formatted,
                "thumbnail": thumbnail,
                "author": author,
            })

        logger.info(f"RSSフィードから {len(videos)} 件の動画を取得しました")
        return videos

    def _format_published_date(self, published: str) -> str:
        """
        公開日時を表示用にフォーマットする。

        Args:
            published: ISO 8601形式の日時文字列

        Returns:
            YYYY/MM/DD HH:MM 形式の文字列
        """
        if not published:
            return ""

        try:
            # feedparserが返すフォーマットに対応
            # 例: "2025-03-10T12:00:00+00:00"
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            return dt.strftime("%Y/%m/%d %H:%M")
        except (ValueError, AttributeError):
            return published

    def detect_new_videos(
        self, current_videos: list[dict], known_video_ids: list[str]
    ) -> list[dict]:
        """
        既知の動画IDリストと比較して新着動画を検出する。

        Args:
            current_videos: 現在の全動画リスト
            known_video_ids: 既知の動画IDリスト

        Returns:
            新着動画のリスト
        """
        new_videos = [
            video for video in current_videos
            if video["video_id"] not in known_video_ids
        ]

        if new_videos:
            logger.info(f"新着動画を {len(new_videos)} 件検出しました")
        else:
            logger.info("新着動画はありません")

        return new_videos
