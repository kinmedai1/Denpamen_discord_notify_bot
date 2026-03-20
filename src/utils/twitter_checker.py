"""
Twitter（X）アカウントの新着ツイートチェックモジュール。
外部の RSS フィードまたは Twitter Syndication API を利用してツイート情報を取得する。
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

import requests
import feedparser

logger = logging.getLogger(__name__)

# Syndication API のエンドポイント (フォールバック用)
SYNDICATION_API_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"


class TwitterChecker:
    """RSS フィードまたは Syndication API 経由で Twitter アカウントのツイートを取得するクラス"""

    def __init__(self, username: str, rss_url: Optional[str] = None):
        """
        Args:
            username: Twitterユーザー名（@なし）
            rss_url: 外部サービスで生成された RSS フィードの URL (推奨)
        """
        self.username = username
        self.rss_url = rss_url
        self.syndication_url = SYNDICATION_API_URL.format(username=username)

    def fetch_tweets(self) -> list[dict]:
        """
        ツイート一覧を取得する。RSS URL が指定されている場合は RSS を、
        指定されていない場合は Syndication API を試行する。

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
        if self.rss_url:
            logger.info(f"外部 RSS フィードを利用して取得中: {self.rss_url}")
            return self._fetch_from_rss(self.rss_url)
        else:
            logger.info("RSS URL が設定されていないため、Syndication API を利用します（制限される可能性があります）")
            return self._fetch_from_syndication()

    def _fetch_from_rss(self, url: str) -> list[dict]:
        """指定された RSS URL からツイートを取得する"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            feed_content = response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"RSS フィードの取得に失敗しました: {e}")
            return []

        try:
            feed = feedparser.parse(feed_content)
        except Exception as e:
            logger.error(f"RSS フィードのパースに失敗しました: {e}")
            return []

        tweets = []
        for entry in feed.entries:
            tweet_url = entry.get("link", "")
            # ID は guid または link から取得
            tweet_id = entry.get("id", tweet_url)
            # 多くの RSS 生成サービスではリンクの末尾が ID になっている
            if not tweet_id and tweet_url:
                tweet_id = tweet_url.split("/")[-1]

            if not tweet_id:
                continue

            # 本文 (summary または description)
            raw_text = entry.get("summary", "") or entry.get("description", "")
            clean_text = self._strip_html(raw_text)

            # 画像URLの抽出
            images = self._extract_images_from_html(raw_text)

            # 日時
            published = entry.get("published", "")
            published_formatted = self._format_rss_date(published)

            # アカウント名
            author = entry.get("author", f"@{self.username}")

            tweets.append({
                "tweet_id": str(tweet_id),
                "text": clean_text,
                "url": tweet_url,
                "published": published,
                "published_formatted": published_formatted,
                "images": images,
                "author": author,
            })

        logger.info(f"RSS から {len(tweets)} 件のツイートを取得しました")
        return tweets

    def _fetch_from_syndication(self) -> list[dict]:
        """Syndication API からツイートを取得する (フォールバック)"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://twitter.com/"
        }

        try:
            response = requests.get(self.syndication_url, headers=headers, timeout=15)
            response.raise_for_status()
            html_content = response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Syndication API からの取得に失敗しました: {e}")
            return []

        # __NEXT_DATA__ スクリプトタグ内の JSON を探す
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html_content)
        if not match:
            logger.error("HTML 内に __NEXT_DATA__ が見つかりませんでした。")
            return []

        try:
            data = json.loads(match.group(1))
            entries = data["props"]["pageProps"]["timeline"]["entries"]
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            logger.error(f"JSON データの解析に失敗しました: {e}")
            return []

        tweets = []
        for entry in entries:
            if entry.get("type") != "tweet":
                continue

            try:
                tweet_data = entry["content"]["tweet"]
                tweet_id = str(tweet_data["id_str"])
                text = tweet_data.get("full_text", tweet_data.get("text", ""))
                
                images = []
                # メディア抽出
                entities = tweet_data.get("extended_entities", tweet_data.get("entities", {}))
                for media in entities.get("media", []):
                    if media.get("type") == "photo" and "media_url_https" in media:
                        images.append(media["media_url_https"])

                created_at = tweet_data.get("created_at", "")
                published_formatted = self._format_syndication_date(created_at)

                user_data = tweet_data.get("user", {})
                author = user_data.get("name", f"@{self.username}")
                screen_name = user_data.get("screen_name", self.username)

                tweets.append({
                    "tweet_id": tweet_id,
                    "text": text,
                    "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                    "published": created_at,
                    "published_formatted": published_formatted,
                    "images": images,
                    "author": author,
                })
            except Exception:
                continue

        logger.info(f"Syndication API から {len(tweets)} 件のツイートを取得しました")
        return tweets

    def _strip_html(self, html_text: str) -> str:
        """HTML タグを除去してプレーンテキストにする"""
        if not html_text: return ""
        text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
            text = text.replace(entity, char)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _extract_images_from_html(self, html_text: str) -> list[str]:
        """HTML 内の <img> タグから画像 URL を抽出する"""
        if not html_text: return []
        return re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE)

    def _format_rss_date(self, published: str) -> str:
        """RSS の日付文字列をフォーマットする"""
        if not published: return ""
        try:
            # feedparser のパースを利用
            import email.utils
            dt = email.utils.parsedate_to_datetime(published)
            return dt.strftime("%Y/%m/%d %H:%M")
        except Exception:
            return published

    def _format_syndication_date(self, published: str) -> str:
        """Syndication API の日付文字列をフォーマットする"""
        if not published: return ""
        try:
            dt = datetime.strptime(published, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime("%Y/%m/%d %H:%M")
        except Exception:
            return published

    def detect_new_tweets(self, current_tweets: list[dict], known_tweet_ids: list[str]) -> list[dict]:
        """新着ツイートを検出する"""
        new_tweets = [t for t in current_tweets if str(t["tweet_id"]) not in known_tweet_ids]
        if new_tweets:
            logger.info(f"新着ツイートを {len(new_tweets)} 件検出しました")
        else:
            logger.info("新着ツイートはありません")
        return new_tweets
