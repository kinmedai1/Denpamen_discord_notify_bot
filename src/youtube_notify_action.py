"""
GitHub Actions から実行するYouTube新着動画通知スクリプト。
新着動画を検出し、Discord Webhook で通知する。
"""

import os
import sys
import json
import logging
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.youtube_checker import YouTubeChecker

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        TimedRotatingFileHandler(
            filename="youtube_bot.log",
            when="D",
            interval=1,
            backupCount=7,
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

# 既知動画の保存先
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
KNOWN_VIDEOS_FILE = os.path.join(DATA_DIR, "known_videos.json")


def load_known_videos() -> dict:
    """
    既知の動画データを読み込む。

    Returns:
        {"video_ids": [...], "last_updated": "..."} 形式の辞書
    """
    if not os.path.exists(KNOWN_VIDEOS_FILE):
        return {"video_ids": [], "last_updated": ""}

    try:
        with open(KNOWN_VIDEOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"既知動画ファイルの読み込みに失敗しました: {e}")
        return {"video_ids": [], "last_updated": ""}


def save_known_videos(data: dict):
    """既知の動画データを保存する。"""
    os.makedirs(DATA_DIR, exist_ok=True)

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(KNOWN_VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"既知動画データを保存しました ({len(data['video_ids'])} 件)")


def build_notification_embed(video: dict) -> dict:
    """
    新着動画の通知用 Embed を作成する（1動画につき1 Embed）。

    Args:
        video: 動画情報の辞書

    Returns:
        Discord Embed 辞書
    """
    title = video.get("title", "無題")
    url = video.get("url", "")
    published = video.get("published_formatted", "")
    thumbnail = video.get("thumbnail", "")
    author = video.get("author", "")

    embed = {
        "title": f"🎬 {title}",
        "description": f"📅 公開日時: {published}",
        "url": url,
        "color": 0xFF0000,  # YouTubeの赤
        "fields": [],
        "footer": {"text": "denpamen bot (YouTube通知)"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    if author:
        embed["author"] = {"name": f"📺 {author}"}

    if thumbnail:
        embed["image"] = {"url": thumbnail}

    embed["fields"].append({
        "name": "🔗 動画リンク",
        "value": f"[YouTubeで視聴する]({url})",
        "inline": False,
    })

    return embed


def build_summary_embed(new_videos: list[dict]) -> dict:
    """
    複数の新着動画のサマリー Embed を作成する。

    Args:
        new_videos: 新着動画のリスト

    Returns:
        Discord Embed 辞書
    """
    embed = {
        "title": "🔔 YouTube新着動画のお知らせ！",
        "description": f"🆕 新着動画が **{len(new_videos)}本** 投稿されました",
        "color": 0xFF0000,
        "footer": {"text": "denpamen bot (YouTube通知)"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    return embed


def send_webhook(webhook_url: str, embeds: list[dict]):
    """Discord Webhook でメッセージを送信する。"""
    import requests

    payload = {
        "embeds": embeds,
        "username": "電波人間YouTube通知",
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


def main():
    """メイン処理"""
    webhook_url = os.getenv("DISCORD_YOUTUBE_WEBHOOK_URL")
    channel_id = os.getenv("YOUTUBE_CHANNEL_ID")

    if not webhook_url:
        logger.error("❌ DISCORD_YOUTUBE_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    if not channel_id:
        logger.error("❌ YOUTUBE_CHANNEL_ID が設定されていません")
        sys.exit(1)

    # 1. 既知動画の読み込み
    logger.info("📂 既知動画データを読み込み中...")
    known_data = load_known_videos()
    known_video_ids = known_data.get("video_ids", [])
    is_first_run = len(known_video_ids) == 0

    # 2. YouTube RSSフィードから動画を取得
    logger.info("🎬 YouTube RSSフィードを取得中...")
    checker = YouTubeChecker(channel_id)
    current_videos = checker.fetch_videos()

    if not current_videos:
        logger.warning("⚠️ 動画を取得できませんでした")
        sys.exit(1)

    logger.info(f"📋 {len(current_videos)} 件の動画を取得しました")

    # 3. 新着動画の検出
    new_videos = checker.detect_new_videos(current_videos, known_video_ids)

    # 4. 新着がなければ終了
    if not new_videos:
        logger.info("ℹ️ 新着動画はありません")
        # 既知動画IDリストを更新
        known_data["video_ids"] = [v["video_id"] for v in current_videos]
        save_known_videos(known_data)
        return

    # 5. 通知順を古い順にする（RSSは新しい順のため逆にする）
    new_videos = new_videos[::-1]

    if is_first_run:
        logger.info(f"🔰 初回実行: 全 {len(new_videos)} 件の動画を古い順に通知します")

    # 6. Discord 通知を送信
    logger.info("📤 Discord 通知を送信中...")

    # 複数動画がある場合はサマリーEmbed + 個別Embedを送信
    # Discord Webhookは1リクエストあたり最大10 Embedsまで
    max_embeds_per_request = 10

    if len(new_videos) > 1:
        # サマリーを最初に送信
        summary_embed = build_summary_embed(new_videos)
        send_webhook(webhook_url, [summary_embed])
        time.sleep(1)  # レート制限対策

    # 動画を個別に通知（バッチ処理）
    for i in range(0, len(new_videos), max_embeds_per_request):
        if i > 0:
            logger.info(f"  ⏳ 次の通知まで2秒待機...")
            time.sleep(2)  # レート制限対策

        batch = new_videos[i:i + max_embeds_per_request]
        embeds = [build_notification_embed(video) for video in batch]
        send_webhook(webhook_url, embeds)

    # 7. 既知動画リストを更新
    known_data["video_ids"] = [v["video_id"] for v in current_videos]
    save_known_videos(known_data)

    logger.info(f"🎉 処理完了: 新着 {len(new_videos)} 件を通知しました")


if __name__ == "__main__":
    main()
