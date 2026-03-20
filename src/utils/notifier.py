"""
discord.ext.tasks を使った定期通知ロジック。
config.json の設定に基づいて、スケジュール通知を送信する。
"""

import discord
from discord.ext import tasks
from datetime import datetime, timedelta, time
import json
import os
import logging

logger = logging.getLogger(__name__)

# 曜日名 → weekday番号のマッピング
DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class ScheduleNotifier:
    """スケジュールの定期通知を管理するクラス"""

    def __init__(self, bot: discord.Client, sheets_manager):
        """
        Args:
            bot: Discord Client インスタンス
            sheets_manager: SheetsManager インスタンス
        """
        self.bot = bot
        self.sheets_manager = sheets_manager
        self.config = self._load_config()
        self.notification_channel_id = int(os.getenv("NOTIFICATION_CHANNEL_ID", "0"))
        # 通知済みスケジュールを追跡（重複防止）
        self._notified_today = set()
        self._last_reset_date = None

    def _load_config(self) -> dict:
        """config.json を読み込む"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.json"
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"weekly_notifications": [], "reminder_minutes_before": [15, 60]}

    def start(self):
        """通知ループを開始する"""
        if not self.check_notifications.is_running():
            self.check_notifications.start()
            logger.info("通知ループを開始しました")

    def stop(self):
        """通知ループを停止する"""
        if self.check_notifications.is_running():
            self.check_notifications.cancel()
            logger.info("通知ループを停止しました")

    @tasks.loop(minutes=1)
    async def check_notifications(self):
        """毎分実行: スケジュール通知をチェックする"""
        try:
            now = datetime.now()
            today = now.date()

            # 日付が変わったら通知履歴をリセット
            if self._last_reset_date != today:
                self._notified_today.clear()
                self._last_reset_date = today

            channel = self.bot.get_channel(self.notification_channel_id)
            if not channel:
                return

            # 1. 定期通知（曜日・時間指定）のチェック
            await self._check_weekly_notifications(channel, now)

            # 2. リマインダー通知のチェック
            await self._check_reminders(channel, now)

        except Exception as e:
            logger.error(f"通知チェック中にエラーが発生しました: {e}")

    @check_notifications.before_loop
    async def before_check(self):
        """Bot の準備ができるまで待機する"""
        await self.bot.wait_until_ready()

    async def _check_weekly_notifications(self, channel: discord.TextChannel, now: datetime):
        """定期通知（曜日・時間指定）をチェックして送信する"""
        current_day = now.strftime("%A").lower()
        current_time = now.strftime("%H:%M")

        for notification in self.config.get("weekly_notifications", []):
            day = notification.get("day", "").lower()
            notify_time = notification.get("time", "")
            message = notification.get("message", "📅 スケジュール通知です")

            # 曜日と時間が一致するか確認
            if day == current_day and notify_time == current_time:
                notify_key = f"weekly_{day}_{notify_time}"
                if notify_key not in self._notified_today:
                    self._notified_today.add(notify_key)

                    # 今週のスケジュールを取得
                    schedules = self.sheets_manager.get_upcoming_schedules(days=7)
                    embed = self._build_weekly_embed(message, schedules)
                    await channel.send(embed=embed)
                    logger.info(f"定期通知を送信しました: {day} {notify_time}")

    async def _check_reminders(self, channel: discord.TextChannel, now: datetime):
        """リマインダー通知をチェックして送信する"""
        reminder_minutes = self.config.get("reminder_minutes_before", [15, 60])

        # 今日と明日のスケジュールを取得
        schedules = self.sheets_manager.get_upcoming_schedules(days=1)

        for schedule in schedules:
            try:
                # 開始日時をパース（時間情報がある場合）
                start_str = str(schedule.get("start_date", ""))
                if len(start_str) == 10:  # YYYY-MM-DD のみの場合はスキップ
                    continue

                start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M")

                for minutes in reminder_minutes:
                    reminder_time = start_dt - timedelta(minutes=minutes)
                    # 現在時刻がリマインダー時刻と一致するか（±30秒の許容範囲）
                    diff = abs((now - reminder_time).total_seconds())
                    if diff < 30:
                        notify_key = f"reminder_{schedule['id']}_{minutes}min"
                        if notify_key not in self._notified_today:
                            self._notified_today.add(notify_key)

                            embed = self._build_reminder_embed(schedule, minutes)
                            await channel.send(embed=embed)
                            logger.info(
                                f"リマインダーを送信: {schedule['title']} ({minutes}分前)"
                            )
            except (ValueError, KeyError):
                continue

    def _build_weekly_embed(self, message: str, schedules: list[dict]) -> discord.Embed:
        """定期通知用の Embed を作成する"""
        embed = discord.Embed(
            title="📅 定期スケジュール通知",
            description=message,
            color=0x4FC3F7,
            timestamp=datetime.now(),
        )

        if schedules:
            for schedule in schedules[:10]:  # 最大10件
                title = schedule.get("title", "無題")
                start = schedule.get("start_date", "")
                end = schedule.get("end_date", "")
                assignee = schedule.get("assignee", "")

                value = f"📆 {start}"
                if end and end != start:
                    value += f" 〜 {end}"
                if assignee:
                    value += f"\n👤 {assignee}"

                embed.add_field(name=f"▸ {title}", value=value, inline=False)
        else:
            embed.add_field(
                name="📭 予定なし",
                value="今週のスケジュールはありません",
                inline=False,
            )

        embed.set_footer(text="denpamen bot")
        return embed

    def _build_reminder_embed(self, schedule: dict, minutes: int) -> discord.Embed:
        """リマインダー用の Embed を作成する"""
        embed = discord.Embed(
            title=f"⏰ リマインダー（{minutes}分前）",
            description=f"**{schedule.get('title', '無題')}** がまもなく始まります",
            color=0xFF6B6B,
            timestamp=datetime.now(),
        )

        if schedule.get("description"):
            embed.add_field(name="📝 説明", value=schedule["description"], inline=False)

        if schedule.get("assignee"):
            embed.add_field(name="👤 担当者", value=schedule["assignee"], inline=True)

        embed.set_footer(text="denpamen bot")
        return embed
