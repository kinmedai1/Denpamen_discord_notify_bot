"""
GitHub Actions から実行するTwitter新着ツイート通知スクリプト。
RSSHub 経由で新着ツイートを検出し、Discord Webhook で通知する。
"""

import os
import sys
import json
import logging
import time
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.twitter_checker import TwitterChecker
from src.utils.event_date_parser import extract_event_title, extract_event_end_date
from src.utils.sheets_manager import SheetsManager

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        TimedRotatingFileHandler(
            filename="twitter_bot.log",
            when="D",
            interval=1,
            backupCount=7,
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

# 既知ツイートの保存先
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
KNOWN_TWEETS_FILE = os.path.join(DATA_DIR, "known_tweets.json")


def load_known_tweets() -> dict:
    """
    既知のツイートデータを読み込む。

    Returns:
        {"tweet_ids": [...], "last_updated": "..."} 形式の辞書
    """
    if not os.path.exists(KNOWN_TWEETS_FILE):
        return {"tweet_ids": [], "last_updated": ""}

    try:
        with open(KNOWN_TWEETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"既知ツイートファイルの読み込みに失敗しました: {e}")
        return {"tweet_ids": [], "last_updated": ""}


def save_known_tweets(data: dict):
    """既知のツイートデータを保存する。"""
    os.makedirs(DATA_DIR, exist_ok=True)

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(KNOWN_TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"既知ツイートデータを保存しました ({len(data['tweet_ids'])} 件)")


def build_notification_embed(tweet: dict) -> dict:
    """
    新着ツイートの通知用 Embed を作成する（1ツイートにつき1 Embed）。

    Args:
        tweet: ツイート情報の辞書

    Returns:
        Discord Embed 辞書
    """
    text = tweet.get("text", "")
    url = tweet.get("url", "")
    published = tweet.get("published_formatted", "")
    images = tweet.get("images", [])
    author = tweet.get("author", "")

    # ツイート本文が長い場合は短縮（Discord Embed の description 上限は 4096文字）
    if len(text) > 2000:
        text = text[:1997] + "..."

    embed = {
        "title": "🐦 新着ツイート",
        "description": text,
        "url": url,
        "color": 0x1DA1F2,  # Twitterブランドカラー
        "fields": [],
        "footer": {"text": "denpamen bot (Twitter通知)"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    if author:
        embed["author"] = {"name": f"𝕏 {author}"}

    if published:
        embed["fields"].append({
            "name": "📅 投稿日時",
            "value": published,
            "inline": True,
        })

    # ツイートリンク
    if url:
        embed["fields"].append({
            "name": "🔗 ツイートリンク",
            "value": f"[𝕏で見る]({url})",
            "inline": True,
        })

    # 最初の画像をEmbedに添付
    if images:
        embed["image"] = {"url": images[0]}

    return embed


def auto_register_event(tweet: dict, sheets_manager: SheetsManager, existing_titles: set) -> dict | None:
    """
    ツイートからイベント情報を抽出し、Google Sheetsにスケジュールを登録する。

    Args:
        tweet: ツイート情報の辞書
        sheets_manager: Google Sheets マネージャー
        existing_titles: 既に登録済みのスケジュールタイトルのセット

    Returns:
        登録されたスケジュール辞書。登録しなかった場合は None。
    """
    text = tweet.get("text", "")
    url = tweet.get("url", "")

    # 1. 「◯◯開催」パターンからタイトル抽出
    title = extract_event_title(text)
    if not title:
        logger.debug(f"「開催」パターンが見つかりません: {text[:50]}...")
        return None

    # 2. 重複チェック（同名タイトルが既に存在するか）
    if title in existing_titles:
        logger.info(f"⏭️ スケジュール「{title}」は既に登録済みのため、スキップします")
        return None

    # 3. 終了日時を抽出
    now = datetime.utcnow() + timedelta(hours=9)  # JST
    end_date = extract_event_end_date(text, reference_date=now)
    if not end_date:
        logger.debug(f"終了日時が抽出できません: {text[:50]}...")
        return None

    # 4. Google Sheetsにスケジュール登録
    start_date_str = now.strftime("%Y-%m-%d %H:%M")
    end_date_str = end_date.strftime("%Y-%m-%d %H:%M") if end_date.hour or end_date.minute else end_date.strftime("%Y-%m-%d")

    try:
        schedule = sheets_manager.add_schedule(
            title=title,
            start_date=start_date_str,
            end_date=end_date_str,
            description=url,
            assignee="Twitter自動登録",
        )
        logger.info(f"📅 イベントを自動登録しました: 「{title}」 ({start_date_str} 〜 {end_date_str})")
        # 登録した名前をセットに追加（同一バッチ内の重複防止）
        existing_titles.add(title)
        return schedule
    except Exception as e:
        logger.error(f"❌ イベント自動登録に失敗しました: {e}")
        return None


def build_summary_embed(new_tweets: list[dict]) -> dict:
    """
    複数の新着ツイートのサマリー Embed を作成する。

    Args:
        new_tweets: 新着ツイートのリスト

    Returns:
        Discord Embed 辞書
    """
    embed = {
        "title": "🔔 Twitter新着ツイートのお知らせ！",
        "description": f"🆕 新着ツイートが **{len(new_tweets)}件** 投稿されました",
        "color": 0x1DA1F2,
        "footer": {"text": "denpamen bot (Twitter通知)"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    return embed


def send_webhook(webhook_url: str, embeds: list[dict]):
    """Discord Webhook でメッセージを送信する。"""
    import requests

    payload = {
        "embeds": embeds,
        "username": "電波人間Twitter通知",
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


def _init_sheets_manager():
    """
    Google Sheets マネージャーを初期化する。
    環境変数が設定されていない場合は None を返す。
    """
    sheets_id = os.getenv("GOOGLE_SHEETS_ID")
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

    if not sheets_id:
        logger.warning("⚠️ GOOGLE_SHEETS_ID が未設定のため、イベント自動登録は無効です")
        return None

    try:
        return SheetsManager(sheets_id, service_account_file)
    except Exception as e:
        logger.error(f"❌ Google Sheets の初期化に失敗しました: {e}")
        return None


def _get_existing_schedule_titles(sheets_manager: SheetsManager) -> set:
    """
    既存スケジュールのタイトル一覧をセットで取得する。
    """
    try:
        all_schedules = sheets_manager.get_all_schedules()
        return {s.get("title", "") for s in all_schedules if s.get("title")}
    except Exception as e:
        logger.error(f"❌ 既存スケジュールの取得に失敗しました: {e}")
        return set()


def main():
    """メイン処理"""
    webhook_url = os.getenv("DISCORD_TWITTER_WEBHOOK_URL")
    username = os.getenv("TWITTER_USERNAME")
    rss_url = os.getenv("TWITTER_RSS_URL")  # RSS.app 等の外部 RSS URL

    if not webhook_url:
        logger.error("❌ DISCORD_TWITTER_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    if not username:
        logger.error("❌ TWITTER_USERNAME が設定されていません")
        sys.exit(1)

    # 1. 既知ツイートの読み込み
    logger.info("📂 既知ツイートデータを読み込み中...")
    known_data = load_known_tweets()
    known_tweet_ids = known_data.get("tweet_ids", [])
    is_first_run = len(known_tweet_ids) == 0

    # 2. ツイートを取得
    logger.info(f"🐦 Twitter情報の取得を開始... (@{username})")
    checker = TwitterChecker(username, rss_url)
    current_tweets = checker.fetch_tweets()

    if not current_tweets:
        logger.warning("⚠️ ツイートを取得できませんでした")
        if not rss_url:
            logger.warning("  💡 安定した取得のために RSS.app 等の外部 RSS 生成サービスの利用を推奨します")
            logger.warning("     (TWITTER_RSS_URL 環境変数を設定してください)")
        else:
            logger.warning("  → 設定された RSS URL からデータを取得できませんでした。URL が正しいか確認してください")
        sys.exit(1)

    logger.info(f"📋 {len(current_tweets)} 件のツイートを取得しました")

    # 3. 新着ツイートの検出
    new_tweets = checker.detect_new_tweets(current_tweets, known_tweet_ids)

    # 4. 新着がなければ終了
    if not new_tweets:
        logger.info("ℹ️ 新着ツイートはありません")
        # 既知ツイートIDリストを更新
        known_data["tweet_ids"] = [t["tweet_id"] for t in current_tweets]
        save_known_tweets(known_data)
        return

    # 5. 通知順を古い順にする（RSSは新しい順のため逆にする）
    new_tweets = new_tweets[::-1]

    if is_first_run:
        logger.info(f"🔰 初回実行: 全 {len(new_tweets)} 件のツイートを古い順に通知します")

    # 5.5 Google Sheetsマネージャーの初期化（イベント自動登録用）
    sheets_manager = _init_sheets_manager()
    existing_titles = set()
    if sheets_manager:
        logger.info("📊 既存スケジュールを確認中...")
        existing_titles = _get_existing_schedule_titles(sheets_manager)
        logger.info(f"📋 既存スケジュールのタイトル: {len(existing_titles)} 件")

    # 6. Discord 通知を送信 & イベント自動登録
    logger.info("📤 Discord 通知を送信中...")

    # Discord Webhookは1リクエストあたり最大10 Embedsまで
    max_embeds_per_request = 10
    registered_events = []  # 自動登録されたイベントのリスト

    if len(new_tweets) > 1:
        # サマリーを最初に送信
        summary_embed = build_summary_embed(new_tweets)
        send_webhook(webhook_url, [summary_embed])
        time.sleep(1)  # レート制限対策

    # ツイートを個別に通知（バッチ処理）& イベント自動登録
    for i in range(0, len(new_tweets), max_embeds_per_request):
        if i > 0:
            logger.info(f"  ⏳ 次の通知まで2秒待機...")
            time.sleep(2)  # レート制限対策

        batch = new_tweets[i:i + max_embeds_per_request]
        embeds = []
        for tweet in batch:
            embed = build_notification_embed(tweet)

            # イベント自動登録を試行
            if sheets_manager:
                schedule = auto_register_event(tweet, sheets_manager, existing_titles)
                if schedule:
                    registered_events.append(schedule)
                    # Embedに自動登録情報を追加
                    embed["fields"].append({
                        "name": "📅 イベント自動登録",
                        "value": (
                            f"✅ **{schedule['title']}** をスケジュールに登録しました\n"
                            f"期間: {schedule['start_date']} 〜 {schedule['end_date']}"
                        ),
                        "inline": False,
                    })

            embeds.append(embed)

        send_webhook(webhook_url, embeds)

    # 7. 既知ツイートリストを更新
    known_data["tweet_ids"] = [t["tweet_id"] for t in current_tweets]
    save_known_tweets(known_data)

    logger.info(f"🎉 処理完了: 新着 {len(new_tweets)} 件を通知しました")
    if registered_events:
        logger.info(f"📅 イベント自動登録: {len(registered_events)} 件を登録しました")


if __name__ == "__main__":
    main()
