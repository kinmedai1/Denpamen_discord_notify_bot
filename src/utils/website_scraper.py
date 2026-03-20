"""
電波人間公式サイト（https://newdenpafree.ap-gs.com/）のスクレイピングモジュール。
ニュース記事の取得、新着検出、イベント期間の抽出を行う。
"""

import re
import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 公式サイトのベースURL
BASE_URL = "https://newdenpafree.ap-gs.com"
NEWS_URL = f"{BASE_URL}/news"

# カテゴリ判定用のセクションID
CATEGORY_SECTION_IDS = {
    "h.qmv6nf7xhjqy": "配信情報",
    "h.fl4njortdox4": "イベント情報",
    "h.axj59m55504p": "その他",
}

# イベント期間のパターン（複数パターンに対応）
# 例: 【イベント期間】3月11日 15時00分 ～ 3月18日 14時59分
# 例: 【イベント期間】3月11日 15時00分～3月18日 14時59分
EVENT_PERIOD_PATTERN = re.compile(
    r"【イベント期間】\s*"
    r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{2})分\s*"
    r"[～〜~]\s*"
    r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{2})分"
)

# 配信日のパターン（期間がない記事用）
# 例: 3月2日 11時に、Ver.8.0.0の配信を予定
DELIVERY_DATE_PATTERN = re.compile(
    r"(\d{1,2})月(\d{1,2})日\s*(?:(\d{1,2})時)?"
)

# 記事リンクのURLパターン
ARTICLE_URL_PATTERN = re.compile(r"/news/news_\d{8}\d{3}")


class WebsiteScraper:
    """電波人間公式サイトのスクレイパー"""

    def __init__(self, timeout: int = 30):
        """
        Args:
            timeout: HTTPリクエストのタイムアウト（秒）
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DenpamenBot/1.0 (Discord Notification Bot)",
            "Accept-Language": "ja,en;q=0.9",
        })

    def fetch_articles(self) -> list[dict]:
        """
        ニュース一覧ページから全記事を取得する。

        Returns:
            記事情報のリスト。各記事は以下のキーを持つ:
            - date: 日付文字列 (YYYY/MM/DD)
            - title: 記事タイトル
            - url: 記事の完全URL
            - category: カテゴリ名（配信情報/イベント情報/その他）
        """
        try:
            response = self.session.get(NEWS_URL, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
        except requests.RequestException as e:
            logger.error(f"ニュース一覧の取得に失敗しました: {e}")
            return []

        return self._parse_articles(response.text)

    def _parse_articles(self, html: str) -> list[dict]:
        """
        HTMLからニュース記事を抽出する。
        DOM内での位置関係を使ってカテゴリを判定する。

        Args:
            html: ニュース一覧ページのHTML

        Returns:
            記事情報のリスト
        """
        soup = BeautifulSoup(html, "html.parser")
        articles = []

        # DOM内の全要素をリスト化して位置インデックスを使う
        all_elements = list(soup.descendants)

        # セクションIDの位置を特定
        section_positions = []
        for i, elem in enumerate(all_elements):
            if hasattr(elem, "get"):
                elem_id = elem.get("id", "")
                if elem_id in CATEGORY_SECTION_IDS:
                    section_positions.append((i, CATEGORY_SECTION_IDS[elem_id]))

        # 位置順にソート
        section_positions.sort(key=lambda x: x[0])

        # 各記事リンクの位置を特定してカテゴリを割り当て
        for i, elem in enumerate(all_elements):
            if not (hasattr(elem, "name") and elem.name == "a" and hasattr(elem, "get")):
                continue

            href = elem.get("href", "")
            if not ARTICLE_URL_PATTERN.search(href):
                continue

            # 完全URLに変換
            if href.startswith("/"):
                full_url = BASE_URL + href
            else:
                full_url = href

            # 英語版のリンクは除外
            if "/en/" in full_url:
                continue

            # タイトルを取得
            title = elem.get_text(strip=True)
            if not title:
                continue

            # 日付を取得
            date_str = self._extract_date_from_context(elem)

            # DOM位置ベースのカテゴリ判定
            category = "その他"
            for sec_pos, sec_name in reversed(section_positions):
                if i > sec_pos:
                    category = sec_name
                    break

            # 重複チェック
            if not any(a["url"] == full_url for a in articles):
                articles.append({
                    "date": date_str,
                    "title": title,
                    "url": full_url,
                    "category": category,
                })

        logger.info(f"ニュース一覧から {len(articles)} 件の記事を取得しました")
        return articles

    def _extract_date_from_context(self, link_element) -> str:
        """
        リンク要素の前後のテキストから日付を抽出する。

        Args:
            link_element: BeautifulSoupのリンク要素

        Returns:
            日付文字列 (YYYY/MM/DD) またはURL中の日付
        """
        # リンクの親要素のテキストから日付パターンを探す
        parent = link_element.parent
        if parent:
            parent_text = parent.get_text()
            date_match = re.search(r"(\d{4}/\d{2}/\d{2})", parent_text)
            if date_match:
                return date_match.group(1)

        # 親の親要素も確認
        if parent and parent.parent:
            grandparent_text = parent.parent.get_text()
            date_match = re.search(r"(\d{4}/\d{2}/\d{2})", grandparent_text)
            if date_match:
                return date_match.group(1)

        # URLから日付を抽出（最終手段）
        href = link_element.get("href", "")
        url_date_match = re.search(r"news_(\d{4})(\d{2})(\d{2})", href)
        if url_date_match:
            return f"{url_date_match.group(1)}/{url_date_match.group(2)}/{url_date_match.group(3)}"

        return ""

    def fetch_article_detail(self, url: str) -> Optional[dict]:
        """
        記事の詳細ページからイベント期間等の情報を取得する。

        Args:
            url: 記事のURL

        Returns:
            詳細情報の辞書。以下のキーを含む:
            - period: イベント期間 {"start": "YYYY-MM-DD HH:MM", "end": "YYYY-MM-DD HH:MM"} or None
            - full_text: 記事の全文テキスト
        """
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
        except requests.RequestException as e:
            logger.error(f"記事詳細の取得に失敗しました ({url}): {e}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        full_text = soup.get_text()

        # イベント期間を抽出
        period = self._extract_event_period(full_text, url)

        return {
            "period": period,
            "full_text": full_text,
        }

    def _extract_event_period(self, text: str, url: str) -> Optional[dict]:
        """
        テキストからイベント期間を抽出する。

        Args:
            text: 記事の全文テキスト
            url: 記事URL（年の推定に使用）

        Returns:
            {"start": "YYYY-MM-DD HH:MM", "end": "YYYY-MM-DD HH:MM"} or None
        """
        # URLから記事の年を推定
        year = self._guess_year_from_url(url)

        # 【イベント期間】パターンを検索
        match = EVENT_PERIOD_PATTERN.search(text)
        if match:
            start_month, start_day = int(match.group(1)), int(match.group(2))
            start_hour, start_min = int(match.group(3)), int(match.group(4))
            end_month, end_day = int(match.group(5)), int(match.group(6))
            end_hour, end_min = int(match.group(7)), int(match.group(8))

            # 年をまたぐ場合のチェック（12月開始→1月終了）
            start_year = year
            end_year = year
            if end_month < start_month:
                end_year = year + 1

            try:
                start_dt = datetime(start_year, start_month, start_day, start_hour, start_min)
                end_dt = datetime(end_year, end_month, end_day, end_hour, end_min)
                return {
                    "start": start_dt.strftime("%Y-%m-%d %H:%M"),
                    "end": end_dt.strftime("%Y-%m-%d %H:%M"),
                }
            except ValueError as e:
                logger.warning(f"日付のパースに失敗しました: {e}")
                return None

        return None

    def _guess_year_from_url(self, url: str) -> int:
        """
        記事URLから年を推定する。

        Args:
            url: news_YYYYMMDDNNN パターンのURL

        Returns:
            年（YYYY）
        """
        match = re.search(r"news_(\d{4})\d{4}\d{3}", url)
        if match:
            return int(match.group(1))
        return datetime.now().year

    def detect_new_articles(
        self, current_articles: list[dict], known_urls: list[str]
    ) -> list[dict]:
        """
        既知の記事URLリストと比較して新着記事を検出する。

        Args:
            current_articles: 現在の全記事リスト
            known_urls: 既知の記事URLリスト

        Returns:
            新着記事のリスト
        """
        new_articles = [
            article for article in current_articles
            if article["url"] not in known_urls
        ]

        if new_articles:
            logger.info(f"新着記事を {len(new_articles)} 件検出しました")
        else:
            logger.info("新着記事はありません")

        return new_articles
