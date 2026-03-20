"""
GitHub Actions から実行する定期通知スクリプト。
Discord Webhook を使って通知を送信し、Bot トークンを使って過去の通知を削除する。
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.sheets_manager import SheetsManager
from src.utils.gantt_generator import generate_gantt_chart


def delete_previous_messages(token: str, channel_id: str):
    """通知チャンネルの以前のメッセージを削除する"""
    if not token or not channel_id:
        print("⚠️ DISCORD_BOT_TOKEN または NOTIFICATION_CHANNEL_ID が設定されていないため、過去メッセージの削除をスキップします")
        return

    headers = {
        "Authorization": f"Bot {token}"
    }
    
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=50"
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        print(f"⚠️ メッセージ取得エラー: {response.status_code} {response.text}")
        return
        
    messages = response.json()
    deleted_count = 0
    
    for msg in messages:
        # Webhook または Bot 自身のメッセージを削除
        if msg.get("author", {}).get("bot") or "webhook_id" in msg:
            del_url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{msg['id']}"
            del_res = requests.delete(del_url, headers=headers)
            if del_res.status_code == 204:
                deleted_count += 1
                
    if deleted_count > 0:
        print(f"🗑️ 過去の通知メッセージを {deleted_count} 件削除しました")
    else:
        print("ℹ️ 削除する過去のメッセージはありませんでした")


def send_webhook(webhook_url: str, embed: dict, image_buf=None):
    """Discord Webhook でメッセージ（と画像）を送信する"""
    payload = {
        "embeds": [embed],
        "username": "denpamen bot",
        "avatar_url": "",
    }

    if image_buf is not None:
        # 画像を Embed にアタッチする
        embed["image"] = {"url": "attachment://gantt_chart.png"}
        
        files = {
            "payload_json": (None, json.dumps(payload), "application/json"),
            "file": ("gantt_chart.png", image_buf, "image/png")
        }
        response = requests.post(webhook_url, files=files)
    else:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"}
        )

    if response.status_code in (200, 204):
        print("✅ 通知を送信しました")
    else:
        print(f"❌ 通知の送信に失敗しました: {response.status_code} {response.text}")
        sys.exit(1)


def build_schedule_embed(schedules: list[dict], days: int) -> dict:
    """スケジュール通知用の Embed を作成する"""
    title = "📅 今日のスケジュール"
    
    embed = {
        "title": title,
        "description": "現在開催中の予定をお知らせします",
        "color": 0x4FC3F7,
        "fields": [],
        "footer": {"text": "denpamen bot (GitHub Actions)"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    if schedules:
        for s in schedules[:15]:
            title_text = s.get("title", "無題")
            start = s.get("start_date", "")
            end = s.get("end_date", "")
            assignee = s.get("assignee", "")
            desc = s.get("description", "")

            value = f"📆 {start}"
            if end and end != start:
                value += f" 〜 {end}"
            if assignee:
                value += f"\n👤 {assignee}"
            if desc:
                value += f"\n📝 {desc}"
                
            embed["fields"].append({
                "name": f"▸ {title_text}",
                "value": value,
                "inline": False,
            })
    else:
        embed["fields"].append({
            "name": "📭 予定なし",
            "value": "スケジュールの登録はありません",
            "inline": False,
        })

    return embed


def main():
    """メイン処理"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    sheets_id = os.getenv("GOOGLE_SHEETS_ID")
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    
    bot_token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id = os.getenv("NOTIFICATION_CHANNEL_ID")

    if not webhook_url:
        print("❌ DISCORD_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    if not sheets_id:
        print("❌ GOOGLE_SHEETS_ID が設定されていません")
        sys.exit(1)

    # 1. 過去メッセージの削除
    print("🧹 過去のメッセージをクリーンアップ中...")
    delete_previous_messages(bot_token, channel_id)

    # 2. スケジュールの取得 (30日分のスケジュールを取得してガントチャートに含める)
    print("📊 Google Sheets からスケジュールを取得中...")
    sheets = SheetsManager(sheets_id, service_account_file)
    
    # 手動登録かつ現在開催中のスケジュール（テキスト通知用）
    active_manual_schedules = sheets.get_active_manual_schedules()
    # ガントチャート用に全スケジュールを取得
    all_schedules = sheets.get_all_schedules()

    print(f"📋 {len(active_manual_schedules)} 件の現在開催中の手動スケジュールが見つかりました")

    # 3. ガントチャートの生成
    print("📈 ガントチャートを生成中...")
    image_buf = generate_gantt_chart(all_schedules)

    # 4. 通知の送信
    embed = build_schedule_embed(active_manual_schedules, days=0)
    send_webhook(webhook_url, embed, image_buf)


if __name__ == "__main__":
    main()
