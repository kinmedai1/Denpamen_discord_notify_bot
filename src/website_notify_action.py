"""
GitHub Actions から実行する公式サイト通知スクリプト。
新着記事を検出し、Discord Webhook で通知 + Google Sheets にスケジュール自動登録する。
"""

import os
import sys
import json
import logging
import requests
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.website_scraper import WebsiteScraper
from src.utils.sheets_manager import SheetsManager

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        TimedRotatingFileHandler(
            filename="website_bot.log",
            when="D",
            interval=1,
            backupCount=7,
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

# 既知記事の保存先
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
KNOWN_ARTICLES_FILE = os.path.join(DATA_DIR, "known_articles.json")


def load_known_articles() -> dict:
    """
    既知の記事データを読み込む。

    Returns:
        {"urls": [...], "last_updated": "..."} 形式の辞書
    """
    if not os.path.exists(KNOWN_ARTICLES_FILE):
        return {"urls": [], "last_updated": ""}

    try:
        with open(KNOWN_ARTICLES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"既知記事ファイルの読み込みに失敗しました: {e}")
        return {"urls": [], "last_updated": ""}


def save_known_articles(data: dict):
    """既知の記事データを保存する。"""
    os.makedirs(DATA_DIR, exist_ok=True)

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(KNOWN_ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"既知記事データを保存しました ({len(data['urls'])} 件)")


def build_notification_embed(new_articles: list[dict], details: dict) -> dict:
    """
    新着記事の通知用 Embed を作成する。

    Args:
        new_articles: 新着記事のリスト
        details: 各記事のURL -> 詳細情報のマッピング

    Returns:
        Discord Embed 辞書
    """
    embed = {
        "title": "📢 電波人間公式サイト 新着情報！",
        "description": f"🆕 新着記事が **{len(new_articles)}件** 見つかりました",
        "color": 0xFF6B35,
        "fields": [],
        "footer": {"text": "denpamen bot (公式サイト監視)"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    for article in new_articles:
        category = article.get("category", "")
        title = article.get("title", "無題")
        date = article.get("date", "")
        url = article.get("url", "")

        value = f"📅 {date}"

        # イベント期間があれば表示
        detail = details.get(url)
        if detail and detail.get("period"):
            period = detail["period"]
            value += f"\n📆 イベント期間: {period['start']} ～ {period['end']}"
            value += "\n✅ スケジュールに自動登録しました"

        value += f"\n🔗 [詳細ページ]({url})"

        embed["fields"].append({
            "name": f"▸ 【{category}】{title}",
            "value": value,
            "inline": False,
        })

    return embed


def send_webhook(webhook_url: str, embed: dict):
    """Discord Webhook でメッセージを送信する。"""
    payload = {
        "embeds": [embed],
        "username": "電波人間公式サイト通知",
        "avatar_url": "",
    }

    response = requests.post(
        webhook_url,
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    if response.status_code in (200, 204):
        logger.info("✅ Discord 通知を送信しました")
    else:
        logger.error(f"❌ Discord 通知の送信に失敗しました: {response.status_code} {response.text}")
        sys.exit(1)


def register_schedule(sheets_manager: SheetsManager, article: dict, period: dict):
    """
    記事のイベント期間をスケジュールに登録する。

    Args:
        sheets_manager: SheetsManager インスタンス
        article: 記事情報
        period: {"start": "YYYY-MM-DD HH:MM", "end": "YYYY-MM-DD HH:MM"}
    """
    title = article.get("title", "公式サイト記事")
    url = article.get("url", "")
    schedule_title = title
    # タイトルが長すぎる場合は短縮
    if len(schedule_title) > 50:
        schedule_title = schedule_title[:47] + "..."

    try:
        result = sheets_manager.add_schedule(
            title=schedule_title,
            start_date=period["start"],
            end_date=period["end"],
            description=f"公式サイトより自動登録\n{url}",
            assignee="公式サイト",
        )
        logger.info(f"✅ スケジュール登録: {result['title']} (ID: {result['id']})")
    except Exception as e:
        logger.error(f"❌ スケジュール登録に失敗しました: {e}")


def main():
    """メイン処理"""
    webhook_url = os.getenv("DISCORD_WEBSITE_WEBHOOK_URL")
    sheets_id = os.getenv("GOOGLE_SHEETS_ID")
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

    if not webhook_url:
        logger.error("❌ DISCORD_WEBSITE_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    # 1. 既知記事の読み込み
    logger.info("📂 既知記事データを読み込み中...")
    known_data = load_known_articles()
    known_urls = known_data.get("urls", [])
    is_first_run = len(known_urls) == 0

    # 2. 公式サイトをスクレイピング
    logger.info("🌐 公式サイトをスクレイピング中...")
    scraper = WebsiteScraper()
    current_articles = scraper.fetch_articles()

    if not current_articles:
        logger.warning("⚠️ 記事を取得できませんでした")
        sys.exit(1)

    logger.info(f"📋 {len(current_articles)} 件の記事を取得しました")

    # 3. 新着記事の検出
    new_articles = scraper.detect_new_articles(current_articles, known_urls)

    # 4. 新着がなければ終了
    if not new_articles:
        logger.info("ℹ️ 新着記事はありません")
        # 既知URLリストを更新（記事が削除された場合を考慮して現在のURLで上書き）
        known_data["urls"] = [a["url"] for a in current_articles]
        save_known_articles(known_data)
        return

    # 5. 通知順を古い順にする（取得データが新しい順のため逆にする）
    new_articles = new_articles[::-1]
    
    if is_first_run:
        logger.info(f"🔰 初回実行: 全 {len(new_articles)} 件の記事を古い順に通知します")

    # 6. 新着記事の詳細を取得
    logger.info(f"🔍 新着 {len(new_articles)} 件の詳細を取得中...")
    details = {}
    for article in new_articles:
        url = article["url"]
        detail = scraper.fetch_article_detail(url)
        if detail:
            details[url] = detail
            if detail.get("period"):
                logger.info(
                    f"  📆 期間検出: {article['title']} "
                    f"({detail['period']['start']} ～ {detail['period']['end']})"
                )

    # 7. Discord 通知を送信（10件ごとにチャンク分割）
    logger.info("📤 Discord 通知を送信中...")
    chunk_size = 10
    chunks = [new_articles[i:i + chunk_size] for i in range(0, len(new_articles), chunk_size)]
    
    for i, chunk in enumerate(chunks):
        if i > 0:
            logger.info(f"  ⏳ 次の通知まで2秒待機... ({i+1}/{len(chunks)})")
            time.sleep(2)  # Discordのレート制限(Rate Limit)対策
            
        embed = build_notification_embed(chunk, details)
        
        # 複数回に分ける場合、タイトルの表現を調整
        if len(chunks) > 1:
            embed["title"] = f"📢 電波人間公式サイト 新着情報！ ({i+1}/{len(chunks)})"
            
        send_webhook(webhook_url, embed)


    # 8. Google Sheets にスケジュール登録（期間がある記事のみ）
    if sheets_id:
        sheets_manager = SheetsManager(sheets_id, service_account_file)
        for article in new_articles:
            url = article["url"]
            detail = details.get(url)
            if detail and detail.get("period"):
                register_schedule(sheets_manager, article, detail["period"])
    else:
        logger.warning("⚠️ GOOGLE_SHEETS_ID が設定されていないため、スケジュール登録をスキップします")

    # 9. 既知記事リストを更新
    known_data["urls"] = [a["url"] for a in current_articles]
    save_known_articles(known_data)

    logger.info(f"🎉 処理完了: 新着 {len(new_articles)} 件を通知しました")


if __name__ == "__main__":
    main()
