"""ガントチャートのデバッグ: スケジュールデータのフォーマットを確認する"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.sheets_manager import SheetsManager

sheets_id = os.getenv("GOOGLE_SHEETS_ID")
service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

if not sheets_id:
    print("GOOGLE_SHEETS_ID が未設定です")
    sys.exit(1)

manager = SheetsManager(sheets_id, service_account_file)
schedules = manager.get_all_schedules()

print(f"\n=== 全スケジュール ({len(schedules)}件) ===\n")
for s in schedules:
    title = s.get("title", "")
    # スプリングイベントのみ詳細表示
    if "スプリング" in title or "spring" in title.lower():
        start = s.get("start_date", "")
        end = s.get("end_date", "")
        print(f"  ★ タイトル : {title}")
        print(f"     start_date: [{start}] (type={type(start).__name__}, len={len(str(start))})")
        print(f"     end_date  : [{end}] (type={type(end).__name__}, len={len(str(end))})")
        print(f"     repr(start): {repr(start)}")
        print(f"     repr(end)  : {repr(end)}")
        print()
