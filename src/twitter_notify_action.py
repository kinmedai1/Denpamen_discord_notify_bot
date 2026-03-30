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
from src.utils.event_date_parser import extract_event_title, extract_event_dates, periods_overlap, normalize_title
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


def auto_register_event(tweet: dict, sheets_manager: SheetsManager, existing_schedules: list[dict]) -> dict | None:
    """
    ツイートからイベント情報を抽出し、Google Sheetsにスケジュールを登録する。

    登録ルール:
      - イベントタイトルが抽出できない場合はスキップ
      - 開始日・終了日が共に抽出できない場合はスキップ
      - 同名イベントで期間が重なる場合はスキップ（期間が被らなければ登録可能）

    Args:
        tweet: ツイート情報の辞書
        sheets_manager: Google Sheets マネージャー
        existing_schedules: 既に登録済みのスケジュールのリスト

    Returns:
        登録されたスケジュール辞書。登録しなかった場合は None。
    """
    text = tweet.get("text", "")
    url = tweet.get("url", "")

    # 1. イベントタイトルを抽出
    title = extract_event_title(text)
    if not title:
        logger.debug(f"イベントタイトルが見つかりません: {text[:50]}...")
        return None

    # 2. 開始日・終了日を抽出
    now = datetime.utcnow() + timedelta(hours=9)  # JST
    start_date, end_date = extract_event_dates(text, reference_date=now)

    if start_date is None and end_date is None:
        logger.debug(f"日付情報が見つかりません: {text[:50]}...")
        return None

    # 開始日がない場合は現在時刻を使用
    if start_date is None:
        start_date = now.replace(second=0, microsecond=0)
    # 終了日がない場合は開始日と同じ
    if end_date is None:
        end_date = start_date

    # 3. 過去日チェック
    end_check = end_date
    if end_date.hour == 0 and end_date.minute == 0:
        end_check = end_date.replace(hour=23, minute=59, second=59)
    if end_check < now:
        logger.info(f"⏭️ イベント「{title}」の終了日 {end_date} は過去のため、スキップします")
        return None

    # 4. 重複チェック（同名イベントで期間が重なるかどうか）
    if _is_duplicate_event(title, start_date, end_date, existing_schedules):
        logger.info(f"⏭️ スケジュール「{title}」は期間が重なる同名イベントが存在するため、スキップします")
        return None

    # 5. Google Sheetsにスケジュール登録
    start_date_str = start_date.strftime("%Y-%m-%d %H:%M")
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
        # 登録したスケジュールを既存リストに追加（同一バッチ内の重複防止）
        existing_schedules.append(schedule)
        return schedule
    except Exception as e:
        logger.error(f"❌ イベント自動登録に失敗しました: {e}")
        return None


def _is_duplicate_event(
    title: str,
    start_date: datetime,
    end_date: datetime,
    existing_schedules: list[dict],
) -> bool:
    """
    同名のイベントで期間が重なるものが存在するかチェックする。

    Args:
        title: チェック対象のイベントタイトル
        start_date: チェック対象の開始日時
        end_date: チェック対象の終了日時
        existing_schedules: 既存のスケジュールリスト

    Returns:
        重複する場合 True
    """
    for schedule in existing_schedules:
        # スプレッドシート側の過去の登録名に「」が含まれている可能性も考慮し、双方を比較時に正規化
        existing_title = normalize_title(schedule.get("title", ""))
        target_title = normalize_title(title)
        
        if existing_title != target_title:
            continue

        # 同名イベントが見つかった → 期間の重複チェック
        try:
            existing_start_str = str(schedule.get("start_date", "")).strip()
            existing_end_str = str(schedule.get("end_date", existing_start_str)).strip()

            if not existing_start_str:
                continue

            # 開始日時のパース
            existing_start = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    existing_start = datetime.strptime(existing_start_str, fmt)
                    break
                except ValueError:
                    pass

            # 終了日時のパース
            existing_end = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    existing_end = datetime.strptime(existing_end_str, fmt)
                    break
                except ValueError:
                    pass

            if existing_start is None or existing_end is None:
                # パースできない場合は安全のため重複とみなす
                logger.warning(f"既存スケジュール「{title}」の日付パースに失敗: {existing_start_str} 〜 {existing_end_str}")
                return True

            # 終了日が日付のみの場合は23:59:59として扱う
            if len(existing_end_str) == 10:
                existing_end = existing_end.replace(hour=23, minute=59, second=59)

            # 期間の重複チェック
            if periods_overlap(start_date, end_date, existing_start, existing_end):
                logger.debug(
                    f"期間重複検出: 「{title}」"
                    f" 新規={start_date}〜{end_date}"
                    f" 既存={existing_start}〜{existing_end}"
                )
                return True
        except Exception as e:
            logger.warning(f"重複チェック中にエラー: {e}")
            return True  # エラー時は安全のため重複とみなす

    return False


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


def _get_existing_schedules(sheets_manager: SheetsManager) -> list[dict]:
    """
    既存スケジュールのリストを取得する。
    期間重複チェックのために日付情報も含む。
    """
    try:
        return sheets_manager.get_all_schedules()
    except Exception as e:
        logger.error(f"❌ 既存スケジュールの取得に失敗しました: {e}")
        return []


def main():
    """メイン処理"""
    webhook_url = os.getenv("DISCORD_TWITTER_WEBHOOK_URL")
    username = os.getenv("TWITTER_USERNAME")
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    
    # GitHub Actions で未設定の変数は空文字 "" になるため、or 演算子でフォールバックさせる
    rapidapi_host = os.getenv("RAPIDAPI_HOST", "") or "twitter241.p.rapidapi.com"
    rapidapi_url = os.getenv("RAPIDAPI_URL", "")

    if not webhook_url:
        logger.error("❌ DISCORD_TWITTER_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    if not username:
        logger.error("❌ TWITTER_USERNAME が設定されていません")
        sys.exit(1)

    if not rapidapi_key:
        logger.error("❌ RAPIDAPI_KEY が設定されていません。RapidAPIのキーを取得して設定してください。")
        sys.exit(1)

    # 1. 既知ツイートの読み込み
    logger.info("📂 既知ツイートデータを読み込み中...")
    known_data = load_known_tweets()
    known_tweet_ids = known_data.get("tweet_ids", [])
    is_first_run = len(known_tweet_ids) == 0

    # 2. ツイートを取得
    logger.info(f"🐦 Twitter情報の取得を開始... (@{username})")
    checker = TwitterChecker(username, rapidapi_key, rapidapi_host, rapidapi_url)
    current_tweets = checker.fetch_tweets()

    if not current_tweets:
        logger.warning("⚠️ ツイートを取得できませんでした")
        logger.warning("  → APIキーやホスト名が正しいか、RapidAPIの無料枠制限に達していないか確認してください")
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
    existing_schedules = []
    if sheets_manager:
        logger.info("📊 既存スケジュールを確認中...")
        existing_schedules = _get_existing_schedules(sheets_manager)
        logger.info(f"📋 既存スケジュール: {len(existing_schedules)} 件")

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
                schedule = auto_register_event(tweet, sheets_manager, existing_schedules)
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
