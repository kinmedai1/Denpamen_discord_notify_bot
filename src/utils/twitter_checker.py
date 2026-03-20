"""
Twitter（X）アカウントの新着ツイートチェックモジュール。
RSSHub を利用してツイート情報を取得する（公式APIキー不要）。
"""

import logging
import re
from datetime import datetime
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)

# RSSHub のエンドポイント（公開インスタンス）
# セルフホストの場合はこのURLを変更する
DEFAULT_RSSHUB_BASE = "https://rsshub.app"
TWITTER_FEED_PATH = "/twitter/user/{username}"


class TwitterChecker:
    """RSSHub 経由で Twitter アカウントのツイートを取得するクラス"""

    def __init__(self, username: str, rsshub_base: Optional[str] = None):
        """
        Args:
            username: Twitterユーザー名（@なし）
            rsshub_base: RSSHub のベースURL（省略時は公開インスタンスを使用）
        """
        self.username = username
        self.rsshub_base = rsshub_base or DEFAULT_RSSHUB_BASE
        self.feed_url = (
            self.rsshub_base.rstrip("/")
            + TWITTER_FEED_PATH.format(username=username)
        )

    def fetch_tweets(self) -> list[dict]:
        """
        RSSHub 経由でツイート一覧を取得する。

        Returns:
            ツイート情報のリスト。各ツイートは以下のキーを持つ:
            - tweet_id: ツイートの一意識別子（URL or ID）
            - text: ツイート本文（HTML タグ除去済み）
            - url: ツイートのURL
            - published: 公開日時（ISO 8601形式の文字列）
            - published_formatted: 表示用の日時文字列（YYYY/MM/DD HH:MM）
            - images: 画像URLのリスト
            - author: アカウント表示名
        """
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            response = requests.get(self.feed_url, headers=headers, timeout=15)
            response.raise_for_status()
            feed_content = response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"RSSフィードの取得に失敗しました: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"ステータスコード: {e.response.status_code}")
                logger.error(f"レスポンス内容 (先頭500文字): {e.response.text[:500]}")
            return []

        try:
            feed = feedparser.parse(feed_content)
        except Exception as e:
            logger.error(f"RSSフィードのパースに失敗しました: {e}")
            logger.error(f"コンテンツ内容 (先頭500文字): {feed_content[:500]}")
            return []

        if feed.bozo and not feed.entries:
            logger.error(f"RSSフィードが不正な形式です: {feed.bozo_exception}")
            logger.error(f"コンテンツ内容 (先頭500文字): {feed_content[:500]}")
            return []

        tweets = []
        for entry in feed.entries:
            # ツイートIDとしてURLを使用（RSSHubではguidやlinkが利用可能）
            tweet_url = entry.get("link", "")
            tweet_id = entry.get("id", tweet_url)

            if not tweet_id:
                continue

            # 本文を取得（HTML形式の場合はタグを除去）
            raw_text = entry.get("summary", "") or entry.get("description", "")
            clean_text = self._strip_html(raw_text)

            # 画像URLを抽出
            images = self._extract_images(raw_text)

            # 公開日時をパース
            published = entry.get("published", "")
            published_formatted = self._format_published_date(published)

            # アカウント名
            author = entry.get("author", f"@{self.username}")

            tweets.append({
                "tweet_id": tweet_id,
                "text": clean_text,
                "url": tweet_url,
                "published": published,
                "published_formatted": published_formatted,
                "images": images,
                "author": author,
            })

        logger.info(f"RSSフィードから {len(tweets)} 件のツイートを取得しました")
        return tweets

    def _strip_html(self, html_text: str) -> str:
        """
        HTML タグを除去してプレーンテキストに変換する。

        Args:
            html_text: HTML を含むテキスト

        Returns:
            タグ除去後のプレーンテキスト
        """
        if not html_text:
            return ""

        # <br> タグを改行に変換
        text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
        # <p> タグの終了を改行に変換
        text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
        # その他のHTMLタグを除去
        text = re.sub(r"<[^>]+>", "", text)
        # HTML エンティティをデコード
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = text.replace("&#39;", "'")
        text = text.replace("&nbsp;", " ")
        # 連続する改行を最大2つに抑制
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    def _extract_images(self, html_text: str) -> list[str]:
        """
        HTML テキストから画像URLを抽出する。

        Args:
            html_text: HTML を含むテキスト

        Returns:
            画像URLのリスト
        """
        if not html_text:
            return []

        # <img> タグの src 属性から画像URLを抽出
        img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
        images = img_pattern.findall(html_text)

        return images

    def _format_published_date(self, published: str) -> str:
        """
        公開日時を表示用にフォーマットする。

        Args:
            published: ISO 8601形式 or RSSの日時文字列

        Returns:
            YYYY/MM/DD HH:MM 形式の文字列
        """
        if not published:
            return ""

        try:
            # ISO 8601 形式の場合
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            return dt.strftime("%Y/%m/%d %H:%M")
        except (ValueError, AttributeError):
            pass

        try:
            # feedparser の time_struct 形式に対応
            import time
            import email.utils
            parsed = email.utils.parsedate_to_datetime(published)
            return parsed.strftime("%Y/%m/%d %H:%M")
        except (ValueError, AttributeError, TypeError):
            return published

    def detect_new_tweets(
        self, current_tweets: list[dict], known_tweet_ids: list[str]
    ) -> list[dict]:
        """
        既知のツイートIDリストと比較して新着ツイートを検出する。

        Args:
            current_tweets: 現在の全ツイートリスト
            known_tweet_ids: 既知のツイートIDリスト

        Returns:
            新着ツイートのリスト
        """
        new_tweets = [
            tweet for tweet in current_tweets
            if tweet["tweet_id"] not in known_tweet_ids
        ]

        if new_tweets:
            logger.info(f"新着ツイートを {len(new_tweets)} 件検出しました")
        else:
            logger.info("新着ツイートはありません")

        return new_tweets
