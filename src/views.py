"""
Discord Bot のボタンUI、Modal、セレクトメニューを定義するモジュール。
固定メッセージに表示する Persistent View と、各操作用のUIコンポーネント。
"""

import discord
from discord import ui
from datetime import datetime, date, timedelta
from typing import Optional
import logging
import io
import traceback

from src.utils.sheets_manager import SheetsManager
from src.utils.gantt_generator import generate_gantt_chart

logger = logging.getLogger(__name__)

# 曜日名
WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


# ==============================
# 日付選択ビュー（直近25日セレクト + その他の日付）
# ==============================
class DateSelectView(ui.View):
    """スケジュール追加用の日付選択ビュー（開始日/終了日）"""

    def __init__(self, mode: str = "start"):
        """
        Args:
            mode: "start" = 開始日選択, "end" = 終了日選択
        """
        super().__init__(timeout=120)
        self.mode = mode
        today = date.today()

        # 今日から25日分の日付セレクト（Discordの最大25オプション）
        day_options = []
        for i in range(25):
            d = today + timedelta(days=i)
            weekday = WEEKDAY_NAMES[d.weekday()]
            label = f"{d.year}/{d.month}/{d.day}({weekday})"
            day_options.append(discord.SelectOption(label=label, value=d.isoformat()))

        self.add_item(ui.Select(
            custom_id=f"schedule_{mode}_day_select",
            placeholder="📆 日付を選択...",
            options=day_options,
        ))

        # 時間セレクト（1時間区切り: 0:00〜23:00）
        time_options = [
            discord.SelectOption(
                label=f"{h}:00",
                value=f"{h:02d}:00",
                emoji="🌙" if h < 6 else ("🌅" if h < 9 else ("☀️" if h < 18 else "🌆")),
            )
            for h in range(24)
        ]
        self.add_item(ui.Select(
            custom_id=f"schedule_{mode}_time_select",
            placeholder="🕐 時間を選択（省略可）...",
            options=time_options,
        ))

        # 確定ボタン
        if mode == "start":
            self.add_item(ui.Button(
                label="開始日時を確定 →",
                style=discord.ButtonStyle.primary,
                custom_id="schedule_start_confirm",
                emoji="✅",
            ))
        else:
            self.add_item(ui.Button(
                label="終了日時を確定 →",
                style=discord.ButtonStyle.primary,
                custom_id="schedule_end_confirm",
                emoji="✅",
            ))

        # その他の日付ボタン
        self.add_item(ui.Button(
            label="その他の日付...",
            style=discord.ButtonStyle.secondary,
            custom_id=f"schedule_{mode}_other",
            emoji="📅",
        ))


# ==============================
# 日付手動入力モーダル（「その他の日付」用）
# ==============================
class DateInputModal(ui.Modal, title="📅 日付入力"):
    """その他の日付を手動入力するためのモーダル"""

    date_input = ui.TextInput(
        label="日付 (YYYY/MM/DD)",
        placeholder="例: 2026/03/15",
        required=True,
        max_length=20,
    )

    time_input = ui.TextInput(
        label="時間（省略可）",
        placeholder="例: 14:00",
        required=False,
        max_length=10,
    )

    def __init__(self, mode: str, bot):
        super().__init__()
        self.mode = mode  # "start" or "end"
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        """フォーム送信時の処理"""
        date_str = self.date_input.value.strip()

        # 日付をパース（複数フォーマットに対応）
        parsed_date = None
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"):
            try:
                parsed_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if not parsed_date:
            await interaction.response.send_message(
                "❌ 日付の形式が正しくありません。\n"
                "`YYYY/MM/DD` の形式で入力してください。（例: 2026/03/15）",
            )
            return

        # 日付文字列を組み立て
        result_date = parsed_date.isoformat()
        time_str = self.time_input.value.strip() if self.time_input.value else ""
        if time_str:
            result_date += f" {time_str}"

        pending = self.bot._pending_schedules.setdefault(interaction.user.id, {})

        if self.mode == "start":
            pending["start_date"] = result_date
            await interaction.response.defer()
            await self.bot._cleanup_bot_messages(interaction.channel)
            view = EndDateOptionView()
            embed = discord.Embed(
                title="📅 スケジュール追加 — 終了日",
                description=f"開始日: **{result_date}**\n\n終了日を選択してください。",
                color=0x7289DA,
            )
            await interaction.followup.send(embed=embed, view=view)
        else:
            pending["end_date"] = result_date
            await interaction.response.defer()
            await self.bot._cleanup_bot_messages(interaction.channel)
            # モーダルからモーダルは開けないため、ボタンで遷移
            view = ProceedToDetailView()
            embed = discord.Embed(
                title="📅 スケジュール追加 — 確認",
                description=(
                    f"開始日: **{pending.get('start_date', '未選択')}**\n"
                    f"終了日: **{result_date}**\n\n"
                    "下のボタンを押してタイトル・説明を入力してください。"
                ),
                color=0x7289DA,
            )
            await interaction.followup.send(embed=embed, view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"DateInputModal エラー: {error}")
        logger.error(traceback.format_exc())
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 予期しないエラーが発生しました。")
            else:
                await interaction.followup.send("❌ 予期しないエラーが発生しました。")
        except Exception:
            pass


# ==============================
# 詳細入力へ進むビュー（モーダル経由の日付入力後）
# ==============================
class ProceedToDetailView(ui.View):
    """「その他の日付」モーダル経由で終了日を設定した後、詳細入力へ遷移するビュー"""

    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ui.Button(
            label="📝 タイトル・説明を入力",
            style=discord.ButtonStyle.primary,
            custom_id="schedule_proceed_detail",
        ))


# ==============================
# 終了日選択オプション
# ==============================
class EndDateOptionView(ui.View):
    """終了日の選択方法を選ぶビュー"""

    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ui.Button(
            label="開始日と同じ（1日のみ）",
            style=discord.ButtonStyle.secondary,
            custom_id="schedule_end_same",
            emoji="🔄",
        ))
        self.add_item(ui.Button(
            label="終了日を選択する",
            style=discord.ButtonStyle.primary,
            custom_id="schedule_end_custom",
            emoji="📅",
        ))


# ==============================
# スケジュール詳細入力 Modal（日付選択後）
# ==============================
class ScheduleDetailModal(ui.Modal, title="📅 スケジュール詳細"):
    """日付選択後のタイトル・説明入力モーダル"""

    schedule_title = ui.TextInput(
        label="タイトル",
        placeholder="例: チームミーティング",
        required=True,
        max_length=100,
    )

    description = ui.TextInput(
        label="説明（省略可）",
        placeholder="例: 週次の進捗報告会",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, sheets_manager: SheetsManager, dates: dict, bot=None):
        super().__init__()
        self.sheets_manager = sheets_manager
        self.dates = dates  # {"start_date": "2026-03-15", "end_date": "2026-03-16"}
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        """フォーム送信時の処理"""
        try:
            await interaction.response.defer()

            # 操作チャンネルの過去メッセージを削除
            if self.bot:
                await self.bot._cleanup_bot_messages(interaction.channel)

            # Google Sheets に追加
            result = self.sheets_manager.add_schedule(
                title=self.schedule_title.value.strip(),
                start_date=self.dates["start_date"],
                end_date=self.dates.get("end_date"),
                description=self.description.value.strip() if self.description.value else "",
                assignee="",
            )

            # 成功メッセージ（Embed）
            embed = discord.Embed(
                title="✅ スケジュールを登録しました",
                color=0x81C784,
                timestamp=datetime.now(),
            )
            embed.add_field(name="📌 タイトル", value=result["title"], inline=False)
            embed.add_field(name="📆 開始日", value=result["start_date"], inline=True)
            if result["end_date"] != result["start_date"]:
                embed.add_field(name="📆 終了日", value=result["end_date"], inline=True)
            if result["description"]:
                embed.add_field(name="📝 説明", value=result["description"], inline=False)
            embed.set_footer(text=f"ID: {result['id']}")

            await interaction.followup.send(embed=embed)
            logger.info(f"スケジュール追加: {result['title']} ({result['id']})")

        except Exception as e:
            logger.error(f"スケジュール追加エラー: {e}")
            logger.error(traceback.format_exc())
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ エラー: {str(e)}")
                else:
                    await interaction.followup.send(f"❌ エラー: {str(e)}")
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Modal エラー: {error}")
        logger.error(traceback.format_exc())
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 予期しないエラーが発生しました。")
            else:
                await interaction.followup.send("❌ 予期しないエラーが発生しました。")
        except Exception:
            pass


# ==============================
# スケジュール削除用 View（セレクトメニュー）
# ==============================
class ScheduleDeleteView(ui.View):
    """スケジュールを選択して削除するためのセレクトメニュー"""

    def __init__(self, sheets_manager: SheetsManager, schedules: list[dict]):
        super().__init__(timeout=60)
        self.sheets_manager = sheets_manager

        # セレクトメニューの選択肢を作成
        options = []
        for s in schedules[:25]:  # Discord の制限: 最大25個
            label = f"{s['title']}"
            description = f"{s['start_date']}"
            if s.get("end_date") and s["end_date"] != s["start_date"]:
                description += f" 〜 {s['end_date']}"
            options.append(
                discord.SelectOption(
                    label=label[:100],  # ラベルの最大長
                    description=description[:100],
                    value=s["id"],
                )
            )

        if options:
            self.select_menu = ui.Select(
                custom_id="schedule_delete_select",
                placeholder="削除するスケジュールを選択...",
                min_values=1,
                max_values=1,
                options=options,
            )
            self.select_menu.callback = self.select_callback
            self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):
        """セレクトメニューで選択された時の処理"""
        selected_id = self.select_menu.values[0]

        try:
            success = self.sheets_manager.delete_schedule(selected_id)

            if success:
                embed = discord.Embed(
                    title="🗑️ スケジュールを削除しました",
                    description=f"ID: `{selected_id}`",
                    color=0xE57373,
                    timestamp=datetime.now(),
                )
                await interaction.response.edit_message(
                    content=None, embed=embed, view=None
                )
                logger.info(f"スケジュール削除: {selected_id}")
            else:
                await interaction.response.edit_message(
                    content="❌ スケジュールが見つかりませんでした。", view=None
                )
        except Exception as e:
            logger.error(f"スケジュール削除エラー: {e}")
            await interaction.response.edit_message(
                content=f"❌ エラーが発生しました: {str(e)}", view=None
            )


# ==============================
# メインコントロールパネル（固定メッセージ用 Persistent View）
# ==============================
class ScheduleView(ui.View):
    """
    固定メッセージに表示するメインのコントロールパネル。
    timeout=None で永続的に動作する。
    """

    def __init__(self, sheets_manager: SheetsManager):
        super().__init__(timeout=None)
        self.sheets_manager = sheets_manager

    @ui.button(
        label="追加",
        emoji="➕",
        style=discord.ButtonStyle.primary,
        custom_id="schedule_add",
    )
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        """スケジュール追加ボタン"""
        try:
            logger.info(f"追加ボタンが押されました (User: {interaction.user})")
            modal = ScheduleAddModal(self.sheets_manager)
            await interaction.response.send_modal(modal)
            logger.info("モーダルを送信しました")
        except Exception as e:
            logger.error(f"追加ボタンエラー: {e}")
            logger.error(traceback.format_exc())
            try:
                await interaction.response.send_message(
                    f"❌ エラーが発生しました: {str(e)}", ephemeral=True
                )
            except Exception:
                pass

    @ui.button(
        label="一覧",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="schedule_list",
    )
    async def list_button(self, interaction: discord.Interaction, button: ui.Button):
        """スケジュール一覧ボタン"""
        try:
            schedules = self.sheets_manager.get_upcoming_manual_schedules(days=30)

            embed = discord.Embed(
                title="📋 スケジュール一覧",
                description="今後30日間のスケジュール",
                color=0x4FC3F7,
                timestamp=datetime.now(),
            )

            if schedules:
                for s in schedules[:15]:  # 最大15件表示
                    title = s.get("title", "無題")
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
                        value += f"\n📝 {desc[:50]}"
                    value += f"\n`ID: {s.get('id', '')}`"

                    embed.add_field(name=f"▸ {title}", value=value, inline=False)

                if len(schedules) > 15:
                    embed.set_footer(
                        text=f"他 {len(schedules) - 15} 件のスケジュールがあります"
                    )
                else:
                    embed.set_footer(text="denpamen bot")
            else:
                embed.add_field(
                    name="📭 予定なし",
                    value="今後30日間のスケジュールはありません",
                    inline=False,
                )
                embed.set_footer(text="denpamen bot")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"スケジュール一覧エラー: {e}")
            await interaction.response.send_message(
                f"❌ エラーが発生しました: {str(e)}", ephemeral=True
            )

    @ui.button(
        label="削除",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="schedule_delete",
    )
    async def delete_button(self, interaction: discord.Interaction, button: ui.Button):
        """スケジュール削除ボタン"""
        try:
            schedules = self.sheets_manager.get_all_schedules()

            if not schedules:
                await interaction.response.send_message(
                    "📭 削除するスケジュールがありません。", ephemeral=True
                )
                return

            view = ScheduleDeleteView(self.sheets_manager, schedules)
            await interaction.response.send_message(
                "🗑️ 削除するスケジュールを選択してください:", view=view, ephemeral=True
            )

        except Exception as e:
            logger.error(f"スケジュール削除メニューエラー: {e}")
            await interaction.response.send_message(
                f"❌ エラーが発生しました: {str(e)}", ephemeral=True
            )

    @ui.button(
        label="ガントチャート",
        emoji="📊",
        style=discord.ButtonStyle.success,
        custom_id="schedule_gantt",
    )
    async def gantt_button(self, interaction: discord.Interaction, button: ui.Button):
        """ガントチャート表示ボタン"""
        try:
            # 処理中の応答（画像生成に時間がかかる場合）
            await interaction.response.defer(ephemeral=True)

            schedules = self.sheets_manager.get_all_schedules()
            image_buf = generate_gantt_chart(schedules)

            file = discord.File(image_buf, filename="gantt_chart.png")
            await interaction.followup.send(
                "📊 **ガントチャート**", file=file, ephemeral=True
            )

        except Exception as e:
            logger.error(f"ガントチャート生成エラー: {e}")
            await interaction.followup.send(
                f"❌ エラーが発生しました: {str(e)}", ephemeral=True
            )


    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item):
        """ボタンのエラーハンドラ"""
        logger.error(f"ScheduleView エラー (item: {item}): {error}")
        logger.error(traceback.format_exc())
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ エラーが発生しました: {str(error)}", ephemeral=True
                )
        except Exception:
            pass


def build_control_panel_embed() -> discord.Embed:
    """固定メッセージ用の Embed を作成する"""
    embed = discord.Embed(
        title="📅 スケジュール管理パネル",
        description=(
            "ボタンを押してスケジュールを管理できます。\n\n"
            "**➕ 追加** — 新しいスケジュールを登録\n"
            "**📋 一覧** — 今後のスケジュールを表示\n"
            "**🗑️ 削除** — スケジュールを削除\n"
            "**📊 ガントチャート** — ガントチャート画像を生成"
        ),
        color=0x7289DA,
    )
    embed.set_footer(text="denpamen bot | 常時稼働中 🟢")
    return embed
