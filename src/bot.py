"""
Discord スケジュール通知Bot のエントリーポイント。
固定メッセージのボタンUIで操作、discord.ext.tasks で定期通知を実行する。
"""

import discord
import os
import sys
import logging
import traceback
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from logging.handlers import TimedRotatingFileHandler

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.views import (
    ScheduleView, DateSelectView, EndDateOptionView,
    ScheduleDetailModal, DateInputModal, ProceedToDetailView,
    ScheduleDeleteView, build_control_panel_embed,
)
from src.utils.sheets_manager import SheetsManager
from src.utils.notifier import ScheduleNotifier
from src.utils.gantt_generator import generate_gantt_chart

# .env 読み込み
load_dotenv()

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        TimedRotatingFileHandler(
            filename="bot.log",
            when="D",
            interval=1,
            backupCount=7,
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)


class ScheduleBot(discord.Client):
    """スケジュール通知Bot"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        super().__init__(intents=intents)

        # ハートビートタスク
        self.heartbeat_task = None

        # 環境変数から設定を読み込み
        self.operation_channel_id = int(os.getenv("OPERATION_CHANNEL_ID", "0"))
        self.notification_channel_id = int(os.getenv("NOTIFICATION_CHANNEL_ID", "0"))
        self.control_message_id = os.getenv("CONTROL_MESSAGE_ID", "")

        # Google Sheets マネージャー
        self.sheets_manager = SheetsManager(
            spreadsheet_id=os.getenv("GOOGLE_SHEETS_ID", ""),
            service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        )

        # ボタンUI View
        self.schedule_view = ScheduleView(self.sheets_manager)

        # 定期通知
        self.notifier = ScheduleNotifier(self, self.sheets_manager)

        # スケジュール追加用の一時データ {user_id: {"start_date": ..., "end_date": ...}}
        self._pending_schedules = {}

    async def setup_hook(self):
        """Bot起動時の初期設定（on_readyより前に呼ばれる）"""
        self.add_view(self.schedule_view)
        logger.info("Persistent View を登録しました")

    async def on_ready(self):
        """Bot がDiscordに接続完了した時の処理"""
        logger.info(f"ログイン成功: {self.user} (ID: {self.user.id})")
        logger.info(f"接続サーバー数: {len(self.guilds)}")

        # 固定メッセージの設定
        await self._setup_control_message()

        # 定期通知ループを開始
        self.notifier.start()

        # ハートビートタスクを開始
        if self.heartbeat_task is None or self.heartbeat_task.done():
            self.heartbeat_task = self.loop.create_task(self._heartbeat())

        logger.info("Bot の起動が完了しました 🟢")

    async def _setup_control_message(self):
        """操作チャンネルに固定メッセージ（コントロールパネル）を設置する"""
        channel = self.get_channel(self.operation_channel_id)
        if not channel:
            logger.error(f"操作チャンネルが見つかりません (ID: {self.operation_channel_id})")
            return

        embed = build_control_panel_embed()

        # 既存の固定メッセージがあるか確認
        if self.control_message_id:
            try:
                message_id = int(self.control_message_id)
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed, view=self.schedule_view)
                logger.info(f"既存の固定メッセージを更新しました (ID: {message_id})")
                return
            except (discord.NotFound, discord.HTTPException, ValueError):
                logger.warning("既存の固定メッセージが見つかりません。新規作成します。")

        # 新規メッセージを送信
        message = await channel.send(embed=embed, view=self.schedule_view)
        self.control_message_id = str(message.id)
        logger.info(f"新しい固定メッセージを作成しました (ID: {message.id})")
        self._update_env_file("CONTROL_MESSAGE_ID", str(message.id))

    # ==============================
    # メッセージクリーンアップ
    # ==============================

    async def _cleanup_bot_messages(self, channel):
        """操作チャンネルのBot送信メッセージを削除（コントロールパネルは除く）"""
        if not channel:
            return

        try:
            control_msg_id = int(self.control_message_id) if self.control_message_id else 0
        except (ValueError, TypeError):
            control_msg_id = 0

        try:
            messages_to_delete = []
            async for message in channel.history(limit=50):
                if message.author.id == self.user.id and message.id != control_msg_id:
                    messages_to_delete.append(message)

            for msg in messages_to_delete:
                try:
                    await msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

            if messages_to_delete:
                logger.info(f"メッセージを {len(messages_to_delete)} 件削除しました")
        except Exception as e:
            logger.error(f"メッセージクリーンアップエラー: {e}")

    # ==============================
    # インタラクション処理
    # ==============================

    async def on_interaction(self, interaction: discord.Interaction):
        """全インタラクションを処理する"""
        custom_id = ""
        if interaction.data:
            custom_id = interaction.data.get("custom_id", "")

        logger.info(
            f"インタラクション受信: type={interaction.type}, "
            f"custom_id={custom_id}, user={interaction.user}"
        )

        if interaction.type == discord.InteractionType.component:
            try:
                if custom_id == "schedule_add":
                    # ステップ1: 開始日選択ビューを送信
                    await interaction.response.defer()
                    await self._cleanup_bot_messages(interaction.channel)
                    view = DateSelectView(mode="start")
                    embed = discord.Embed(
                        title="📅 スケジュール追加 — 開始日",
                        description="日付を選択してください。\n一覧にない日付は「その他の日付」から入力できます。",
                        color=0x7289DA,
                    )
                    await interaction.followup.send(embed=embed, view=view)

                # --- 日付セレクト（開始/終了共通） ---
                elif custom_id in ("schedule_start_day_select", "schedule_end_day_select"):
                    values = interaction.data.get("values", []) if interaction.data else []
                    if values:
                        key = "start_day_raw" if "start" in custom_id else "end_day_raw"
                        self._pending_schedules.setdefault(interaction.user.id, {})[key] = values[0]
                    await interaction.response.defer()

                # --- 時間セレクト（開始/終了共通） ---
                elif custom_id in ("schedule_start_time_select", "schedule_end_time_select"):
                    values = interaction.data.get("values", []) if interaction.data else []
                    if values:
                        key = "start_time" if "start" in custom_id else "end_time"
                        self._pending_schedules.setdefault(interaction.user.id, {})[key] = values[0]
                    await interaction.response.defer()

                # --- 開始日を確定 ---
                elif custom_id == "schedule_start_confirm":
                    pending = self._pending_schedules.get(interaction.user.id, {})
                    if "start_day_raw" not in pending:
                        await interaction.response.send_message("❌ 日付を選択してください。")
                        return
                    # 開始日時を組み立て
                    start_time = pending.get('start_time', '')
                    start_date = pending['start_day_raw']  # ISO形式の日付
                    if start_time:
                        start_date += f" {start_time}"
                    pending["start_date"] = start_date

                    # ステップ2: 終了日選択オプションを表示
                    await interaction.response.defer()
                    await self._cleanup_bot_messages(interaction.channel)
                    view = EndDateOptionView()
                    embed = discord.Embed(
                        title="📅 スケジュール追加 — 終了日",
                        description=f"開始日: **{start_date}**\n\n終了日を選択してください。",
                        color=0x7289DA,
                    )
                    await interaction.followup.send(embed=embed, view=view)

                # --- 「その他の日付」ボタン ---
                elif custom_id in ("schedule_start_other", "schedule_end_other"):
                    mode = "start" if "start" in custom_id else "end"
                    modal = DateInputModal(mode=mode, bot=self)
                    await interaction.response.send_modal(modal)

                # --- 終了日 = 開始日と同じ ---
                elif custom_id == "schedule_end_same":
                    pending = self._pending_schedules.get(interaction.user.id, {})
                    if "start_date" not in pending:
                        await interaction.response.send_message("❌ 最初からやり直してください。")
                        return
                    pending["end_date"] = pending["start_date"]
                    # ステップ3: モーダルを開く
                    dates = {"start_date": pending["start_date"], "end_date": pending["end_date"]}
                    modal = ScheduleDetailModal(self.sheets_manager, dates, bot=self)
                    await interaction.response.send_modal(modal)
                    self._pending_schedules.pop(interaction.user.id, None)

                # --- 終了日をカスタム選択 ---
                elif custom_id == "schedule_end_custom":
                    await interaction.response.defer()
                    await self._cleanup_bot_messages(interaction.channel)
                    pending = self._pending_schedules.get(interaction.user.id, {})
                    view = DateSelectView(mode="end")
                    embed = discord.Embed(
                        title="📅 スケジュール追加 — 終了日を選択",
                        description=f"開始日: **{pending.get('start_date', '未選択')}**\n\n終了日を選択してください。",
                        color=0x7289DA,
                    )
                    await interaction.followup.send(embed=embed, view=view)

                # --- 終了日を確定 ---
                elif custom_id == "schedule_end_confirm":
                    pending = self._pending_schedules.get(interaction.user.id, {})
                    if "end_day_raw" not in pending:
                        await interaction.response.send_message("❌ 日付を選択してください。")
                        return
                    end_time = pending.get('end_time', '')
                    end_date = pending['end_day_raw']  # ISO形式の日付
                    if end_time:
                        end_date += f" {end_time}"
                    pending["end_date"] = end_date
                    # ステップ3: モーダルを開く
                    dates = {"start_date": pending["start_date"], "end_date": end_date}
                    modal = ScheduleDetailModal(self.sheets_manager, dates, bot=self)
                    await interaction.response.send_modal(modal)
                    self._pending_schedules.pop(interaction.user.id, None)

                # --- 詳細入力へ進む（「その他の日付」モーダル経由） ---
                elif custom_id == "schedule_proceed_detail":
                    pending = self._pending_schedules.get(interaction.user.id, {})
                    if "start_date" not in pending or "end_date" not in pending:
                        await interaction.response.send_message("❌ 最初からやり直してください。")
                        return
                    dates = {"start_date": pending["start_date"], "end_date": pending["end_date"]}
                    modal = ScheduleDetailModal(self.sheets_manager, dates, bot=self)
                    await interaction.response.send_modal(modal)
                    self._pending_schedules.pop(interaction.user.id, None)

                elif custom_id == "schedule_list":
                    await self._handle_list(interaction)

                elif custom_id == "schedule_delete":
                    await self._handle_delete(interaction)

                elif custom_id == "schedule_gantt":
                    await self._handle_gantt(interaction)

                elif custom_id == "schedule_delete_select":
                    await self._handle_delete_select(interaction)

                else:
                    logger.warning(f"未知の custom_id: {custom_id}")

            except Exception as e:
                logger.error(f"インタラクション処理エラー: {e}")
                logger.error(traceback.format_exc())
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(f"❌ エラー: {e}")
                    else:
                        await interaction.followup.send(f"❌ エラー: {e}")
                except Exception:
                    pass

    async def _handle_list(self, interaction: discord.Interaction):
        """スケジュール一覧を表示する"""
        await interaction.response.defer()
        await self._cleanup_bot_messages(interaction.channel)

        schedules = self.sheets_manager.get_upcoming_manual_schedules(days=30)

        embed = discord.Embed(
            title="📋 スケジュール一覧",
            description="今後30日間のスケジュール",
            color=0x4FC3F7,
            timestamp=datetime.now(),
        )

        if schedules:
            for s in schedules[:15]:
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
                embed.set_footer(text=f"他 {len(schedules) - 15} 件のスケジュールがあります")
            else:
                embed.set_footer(text="denpamen bot")
        else:
            embed.add_field(
                name="📭 予定なし",
                value="今後30日間のスケジュールはありません",
                inline=False,
            )
            embed.set_footer(text="denpamen bot")

        await interaction.followup.send(embed=embed)

    async def _handle_delete(self, interaction: discord.Interaction):
        """スケジュール削除メニューを表示する"""
        await interaction.response.defer()
        await self._cleanup_bot_messages(interaction.channel)

        schedules = self.sheets_manager.get_all_schedules()

        if not schedules:
            await interaction.followup.send("📭 削除するスケジュールがありません。")
            return

        view = ScheduleDeleteView(self.sheets_manager, schedules)
        await interaction.followup.send(
            "🗑️ 削除するスケジュールを選択してください:", view=view
        )

    async def _handle_gantt(self, interaction: discord.Interaction):
        """ガントチャートを生成して表示する"""
        await interaction.response.defer()
        await self._cleanup_bot_messages(interaction.channel)

        schedules = self.sheets_manager.get_all_schedules()
        image_buf = generate_gantt_chart(schedules)
        file = discord.File(image_buf, filename="gantt_chart.png")
        await interaction.followup.send("📊 **ガントチャート**", file=file)

    async def _handle_delete_select(self, interaction: discord.Interaction):
        """削除セレクトメニューの選択を処理する"""
        selected_id = interaction.data.get("values", [None])[0] if interaction.data else None
        if not selected_id:
            return

        await interaction.response.defer()
        await self._cleanup_bot_messages(interaction.channel)

        try:
            success = self.sheets_manager.delete_schedule(selected_id)
            if success:
                embed = discord.Embed(
                    title="🗑️ スケジュールを削除しました",
                    description=f"ID: `{selected_id}`",
                    color=0xE57373,
                    timestamp=datetime.now(),
                )
                await interaction.followup.send(embed=embed)
                logger.info(f"スケジュール削除: {selected_id}")
            else:
                await interaction.followup.send("❌ スケジュールが見つかりませんでした。")
        except Exception as e:
            logger.error(f"スケジュール削除エラー: {e}")
            await interaction.followup.send(f"❌ エラー: {str(e)}")

    # ==============================
    # .env 更新 & 切断処理
    # ==============================

    def _update_env_file(self, key: str, value: str):
        """
        .env ファイルの指定キーを更新する。
        キーが存在しない場合は追加する。
        """
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )

        if not os.path.exists(env_path):
            logger.warning(f".env ファイルが見つかりません: {env_path}")
            return

        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            updated = False
            new_lines = []
            for line in lines:
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={value}\n")
                    updated = True
                else:
                    new_lines.append(line)

            if not updated:
                new_lines.append(f"{key}={value}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            logger.info(f".env を更新しました: {key}={value}")
        except Exception as e:
            logger.error(f".env の更新に失敗しました: {e}")

    async def on_disconnect(self):
        """Bot が切断された時の処理"""
        logger.warning("Discord から切断されました。")

    async def on_resumed(self):
        """セッションが再開された時の処理"""
        logger.info("Discord セッションが正常に再開（RESUME）されました。")

    async def _heartbeat(self):
        """生存確認ログを定期的に出力する"""
        await self.wait_until_ready()
        while not self.is_closed():
            logger.info("【生存確認】Botは稼働中です。 (Connection: OK)")
            await asyncio.sleep(300)  # 5分おき


def main():
    """Bot を起動する"""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN が設定されていません。.env ファイルを確認してください。")
        sys.exit(1)

    bot = ScheduleBot()

    try:
        logger.info("Bot を起動しています...")
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        logger.error("ログインに失敗しました。Bot トークンが正しいか確認してください。")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot を停止しています...")
    except Exception as e:
        logger.error(f"予期しないエラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
