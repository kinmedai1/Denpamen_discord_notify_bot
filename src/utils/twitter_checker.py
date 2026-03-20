"""
Twitter（X）アカウントの新着ツイートチェックモジュール。
Twitter Syndication API を利用してツイート情報を取得する（公式APIキー不要）。
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Syndication API のエンドポイント
SYNDICATION_API_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"


class TwitterChecker:
    """Syndication API 経由で Twitter アカウントのツイートを取得するクラス"""

    def __init__(self, username: str, rsshub_base: Optional[str] = None):
        """
        Args:
            username: Twitterユーザー名（@なし）
            rsshub_base: 互換性のために残していますが、使用されません。
        """
        self.username = username
        self.feed_url = SYNDICATION_API_URL.format(username=username)

    def fetch_tweets(self) -> list[dict]:
        """
        Syndication API 経由でツイート一覧を取得する。

        Returns:
            ツイート情報のリスト。各ツイートは以下のキーを持つ:
            - tweet_id: ツイートの一意識別子（ID）
            - text: ツイート本文
            - url: ツイートのURL
            - published: 公開日時
            - published_formatted: 表示用の日時文字列（YYYY/MM/DD HH:MM）
            - images: 画像URLのリスト
            - author: アカウント表示名
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            response = requests.get(self.feed_url, headers=headers, timeout=15)
            response.raise_for_status()
            html_content = response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Syndication APIからの取得に失敗しました: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"ステータスコード: {e.response.status_code}")
                logger.error(f"レスポンス内容 (先頭500文字): {e.response.text[:500]}")
            return []

        # __NEXT_DATA__ スクリプトタグ内の JSON を探す
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html_content)
        if not match:
            logger.error("HTML内に __NEXT_DATA__ が見つかりませんでした。APIの仕様が変更された可能性があります。")
            return []

        json_str = match.group(1)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSONのパースに失敗しました: {e}")
            return []

        # ツイートのエントリを抽出
        try:
            entries = data["props"]["pageProps"]["timeline"]["entries"]
        except (KeyError, TypeError) as e:
            logger.error(f"JSONデータから timeline.entries を抽出できませんでした: {e}")
            return []

        tweets = []
        for entry in entries:
            if entry.get("type") != "tweet":
                continue

            try:
                tweet_data = entry["content"]["tweet"]
                tweet_id = str(tweet_data["id_str"])
                
                # 本文
                full_text = tweet_data.get("full_text", tweet_data.get("text", ""))

                # 画像の抽出
                images = []
                media_list = []
                if "extended_entities" in tweet_data and "media" in tweet_data["extended_entities"]:
                    media_list = tweet_data["extended_entities"]["media"]
                elif "entities" in tweet_data and "media" in tweet_data["entities"]:
                    media_list = tweet_data["entities"]["media"]
                    
                for media in media_list:
                    if media.get("type") == "photo" and "media_url_https" in media:
                        images.append(media["media_url_https"])

                # 日時
                created_at = tweet_data.get("created_at", "")
                published_formatted = self._format_published_date(created_at)

                # 著者
                user_data = tweet_data.get("user", {})
                author_name = user_data.get("name", f"@{self.username}")
                author_screen_name = user_data.get("screen_name", self.username)
                
                tweet_url = f"https://x.com/{author_screen_name}/status/{tweet_id}"

                tweets.append({
                    "tweet_id": tweet_id,
                    "text": full_text,
                    "url": tweet_url,
                    "published": created_at,
                    "published_formatted": published_formatted,
                    "images": images,
                    "author": author_name,
                })
            except KeyError as e:
                logger.warning(f"ツイートデータのパース中に必須キーが見つかりませんでした: {e}")
                continue

        logger.info(f"Syndication APIから {len(tweets)} 件のツイートを取得しました")
        return tweets

    def _format_published_date(self, published: str) -> str:
        """
        公開日時 (Mon Mar 10 02:32:55 +0000 2025 など) を表示用にフォーマットする。
        """
        if not published:
            return ""
        
        try:
            # 形式: "Day Mon DD HH:MM:SS +0000 YYYY" (例: Mon Mar 10 02:32:55 +0000 2025)
            dt = datetime.strptime(published, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime("%Y/%m/%d %H:%M")
        except ValueError:
            return published

    def detect_new_tweets(
        self, current_tweets: list[dict], known_tweet_ids: list[str]
    ) -> list[dict]:
        """
        既知のツイートIDリストと比較して新着ツイートを検出する。
        """
        new_tweets = [
            tweet for tweet in current_tweets
            if str(tweet["tweet_id"]) not in known_tweet_ids
        ]

        if new_tweets:
            logger.info(f"新着ツイートを {len(new_tweets)} 件検出しました")
        else:
            logger.info("新着ツイートはありません")

        return new_tweets
