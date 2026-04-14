"""ガントチャートのデバッグ: スケジュールデータを確認する"""
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
    start = s.get("start_date", "")
    end = s.get("end_date", "")
    group = s.get("group", "")
    assignee = s.get("assignee", "")
    print(f"  タイトル: {title}")
    print(f"  開始日  : [{start}] (len={len(str(start))})")
    print(f"  終了日  : [{end}] (len={len(str(end))})")
    print(f"  グループ: {group}")
    print(f"  担当者  : {assignee}")
    print(f"  ---")
