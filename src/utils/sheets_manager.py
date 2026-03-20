"""
Google Sheets のスケジュールデータを管理するモジュール。
gspread を使用してCRUD操作を提供する。
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date
from typing import Optional
import uuid
import os
import json
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# スプレッドシートのカラム定義
COLUMNS = ["ID", "タイトル", "開始日", "終了日", "説明", "担当者", "作成日"]


class SheetsManager:
    """Google Sheets を使ったスケジュール管理クラス"""

    def __init__(self, spreadsheet_id: str, service_account_file: str):
        """
        Args:
            spreadsheet_id: Google Sheets のスプレッドシートID
            service_account_file: サービスアカウントJSONファイルのパス
        """
        self.spreadsheet_id = spreadsheet_id
        self.service_account_file = service_account_file
        self._client = None
        self._sheet = None

    def _connect(self):
        """Google Sheets に接続する（遅延初期化）"""
        if self._client is None:
            creds = Credentials.from_service_account_file(
                self.service_account_file, scopes=SCOPES
            )
            self._client = gspread.authorize(creds)
        if self._sheet is None:
            spreadsheet = self._client.open_by_key(self.spreadsheet_id)
            self._sheet = spreadsheet.sheet1
            # ヘッダーがなければ作成
            self._ensure_headers()

    def _ensure_headers(self):
        """ヘッダー行が存在しない場合は作成する"""
        try:
            first_row = self._sheet.row_values(1)
            if not first_row or first_row[0] != COLUMNS[0]:
                self._sheet.insert_row(COLUMNS, 1)
        except Exception:
            self._sheet.insert_row(COLUMNS, 1)

    def _generate_id(self) -> str:
        """短いユニークIDを生成する"""
        return uuid.uuid4().hex[:8]

    def add_schedule(
        self,
        title: str,
        start_date: str,
        end_date: Optional[str] = None,
        description: str = "",
        assignee: str = "",
    ) -> dict:
        """
        スケジュールを追加する。

        Args:
            title: スケジュールのタイトル
            start_date: 開始日 (YYYY-MM-DD)
            end_date: 終了日 (YYYY-MM-DD)、省略時は開始日と同じ
            description: 説明
            assignee: 担当者名

        Returns:
            追加されたスケジュールの辞書
        """
        self._connect()

        schedule_id = self._generate_id()
        if not end_date:
            end_date = start_date
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        row = [schedule_id, title, start_date, end_date, description, assignee, created_at]
        self._sheet.append_row(row, value_input_option="USER_ENTERED")

        return {
            "id": schedule_id,
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "description": description,
            "assignee": assignee,
            "created_at": created_at,
        }

    def get_all_schedules(self) -> list[dict]:
        """全スケジュールを取得する"""
        self._connect()

        records = self._sheet.get_all_records()
        schedules = [self._row_to_dict(record) for record in records]
        
        # 定期イベント（設定ファイルから）を展開して60日分追加
        recurring = self._get_recurring_events(days_ahead=60)
        schedules.extend(recurring)
        
        return schedules

    def get_upcoming_schedules(self, days: int = 7) -> list[dict]:
        """
        今日から指定日数以内のスケジュールを取得する。

        Args:
            days: 何日先までのスケジュールを取得するか

        Returns:
            スケジュールのリスト
        """
        from datetime import timedelta, datetime

        all_schedules = self.get_all_schedules()
        today = (datetime.utcnow() + timedelta(hours=9)).date()
        end_range = today + timedelta(days=days)

        upcoming = []
        for schedule in all_schedules:
            try:
                # 時間が含まれている場合を考慮し、前方10文字(YYYY-MM-DD)だけを抽出
                start_str = str(schedule.get("start_date", ""))[:10]
                if not start_str:
                    continue
                start = datetime.strptime(start_str, "%Y-%m-%d").date()
                if today <= start <= end_range:
                    upcoming.append(schedule)
            except (ValueError, KeyError):
                continue

        return sorted(upcoming, key=lambda x: x.get("start_date", ""))

    def get_upcoming_manual_schedules(self, days: int = 30) -> list[dict]:
        """
        今日から指定日数以内の手動登録スケジュールのみを取得する。
        定期イベント（SYSTEM生成）は除外される。

        Args:
            days: 何日先までのスケジュールを取得するか

        Returns:
            手動登録スケジュールのリスト
        """
        schedules = self.get_upcoming_schedules(days=days)
        # 自動生成イベント（SYSTEM）を除外
        return [
            s for s in schedules
            if s.get("created_at") != "SYSTEM" and s.get("assignee") != "System"
        ]

    def get_active_manual_schedules(self) -> list[dict]:
        """手動登録かつ現在開催中のスケジュールを取得する"""
        from datetime import datetime, timedelta

        all_schedules = self.get_all_schedules()
        now = datetime.utcnow() + timedelta(hours=9)
        active = []

        for schedule in all_schedules:
            # 機械的に登録された自動生成イベント(SYSTEM)は除外
            if schedule.get("created_at") == "SYSTEM" or schedule.get("assignee") == "System":
                continue

            try:
                start_str = str(schedule.get("start_date", "")).strip()
                end_str = str(schedule.get("end_date", start_str)).strip()

                if not start_str:
                    continue

                # 開始日時のパース
                start_dt = None
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        start_dt = datetime.strptime(start_str, fmt)
                        break
                    except ValueError:
                        pass

                # 終了日時のパース
                end_dt = None
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        end_dt = datetime.strptime(end_str, fmt)
                        break
                    except ValueError:
                        pass

                if start_dt and end_dt:
                    # 終了日が日付のみ(10文字)の場合、その日の終わり(23:59:59)までとする
                    if len(end_str) == 10:
                        end_dt = end_dt.replace(hour=23, minute=59, second=59)

                    if start_dt <= now <= end_dt:
                        active.append(schedule)
            except Exception:
                continue

        return sorted(active, key=lambda x: x.get("start_date", ""))

    def get_todays_schedules(self) -> list[dict]:
        """今日のスケジュールを取得する"""
        from datetime import datetime, timedelta
        all_schedules = self.get_all_schedules()
        today_str = (datetime.utcnow() + timedelta(hours=9)).date().strftime("%Y-%m-%d")

        return [
            s for s in all_schedules
            if str(s.get("start_date", ""))[:10] <= today_str <= str(s.get("end_date", s.get("start_date", "")))[:10]
        ]

    def delete_schedule(self, schedule_id: str) -> bool:
        """
        スケジュールを削除する。

        Args:
            schedule_id: 削除するスケジュールのID

        Returns:
            削除成功ならTrue
        """
        self._connect()

        # 全セルからIDを検索
        cell = self._sheet.find(schedule_id)
        if cell and cell.col == 1:  # ID列（A列）にある場合のみ
            self._sheet.delete_rows(cell.row)
            return True
        return False

    def _row_to_dict(self, record: dict) -> dict:
        """gspread の record を統一された辞書形式に変換する"""
        return {
            "id": str(record.get("ID", "")),
            "title": str(record.get("タイトル", "")),
            "start_date": str(record.get("開始日", "")),
            "end_date": str(record.get("終了日", "")),
            "description": str(record.get("説明", "")),
            "assignee": str(record.get("担当者", "")),
            "created_at": str(record.get("作成日", "")),
        }

    def _get_recurring_events(self, days_ahead: int = 60) -> list[dict]:
        """config.jsonから定期イベント設定を読み込み、指定日数ぶん展開する"""
        from datetime import timedelta, datetime
        
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.json"
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                rules = config.get("recurring_events", [])
        except (FileNotFoundError, json.JSONDecodeError):
            rules = []

        if not rules:
            return []

        today = (datetime.utcnow() + timedelta(hours=9)).date()
        generated = []

        for rule in rules:
            title = rule.get("title", "定期イベント")
            desc = rule.get("description", "自動生成")
            assignee = rule.get("assignee", "System")
            rtype = rule.get("type")
            days_of_week = rule.get("days_of_week", [])

            # 前日(i=-1)のイベントから生成することで、現在進行中のイベントも含める
            for i in range(-1, days_ahead + 1):
                target_date = today + timedelta(days=i)
                hit = False

                if rtype == "weekly" and target_date.weekday() in days_of_week:
                    hit = True
                elif rtype == "even_days" and target_date.day % 2 == 0:
                    hit = True
                elif rtype == "odd_days" and target_date.day % 2 != 0:
                    hit = True
                elif rtype == "daily":
                    hit = True

                if hit:
                    # 15:00開始、翌日の15:00終了
                    target_next = target_date + timedelta(days=1)
                    start_str = target_date.strftime("%Y-%m-%d 15:00")
                    end_str = target_next.strftime("%Y-%m-%d 15:00")
                    
                    # groupの付与（weeklyイベントなら共通のグループ名にする）
                    group_name = "日替わりイベント" if rtype == "weekly" else title
                    
                    # IDは削除時に無視されるよう、スプレッドシートとは被らない固定ID
                    generated.append({
                        "id": f"REC-{target_date.strftime('%Y%m%d')}-{abs(hash(title))%1000}",
                        "title": title,
                        "start_date": start_str,
                        "end_date": end_str,
                        "description": desc,
                        "assignee": assignee,
                        "created_at": "SYSTEM",
                        "group": group_name
                    })

        return generated
