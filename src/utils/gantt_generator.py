"""
matplotlib を使ってガントチャート画像を生成するモジュール。
"""

import matplotlib
matplotlib.use("Agg")  # GUIなしバックエンド

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
from datetime import datetime, timedelta
from typing import Optional
import io
import json
import os
import platform


def _setup_japanese_font():
    """日本語フォントを設定する"""
    # Windowsの場合
    if platform.system() == "Windows":
        font_candidates = [
            "Yu Gothic",
            "MS Gothic",
            "Meiryo",
            "MS Mincho",
        ]
        for font_name in font_candidates:
            try:
                fm.findfont(fm.FontProperties(family=font_name), fallback_to_default=False)
                plt.rcParams["font.family"] = font_name
                return
            except Exception:
                continue

    # Linux（GitHub Actions等）の場合
    elif platform.system() == "Linux":
        font_candidates = [
            "Noto Sans CJK JP",
            "IPAGothic",
            "IPAPGothic",
        ]
        for font_name in font_candidates:
            try:
                fm.findfont(fm.FontProperties(family=font_name), fallback_to_default=False)
                plt.rcParams["font.family"] = font_name
                return
            except Exception:
                continue

    # フォールバック: sans-serif
    plt.rcParams["font.family"] = "sans-serif"


def _load_config() -> dict:
    """config.json からガントチャート設定を読み込む"""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.json"
    )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("gantt_chart", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def generate_gantt_chart(
    schedules: list[dict],
    title: str = "スケジュール ガントチャート",
    start_range: Optional[datetime] = None,
    end_range: Optional[datetime] = None,
) -> io.BytesIO:
    """
    スケジュールデータからガントチャート画像を生成する。

    Args:
        schedules: スケジュールのリスト（各要素に start_date, end_date, title が必要）
        title: チャートのタイトル
        start_range: 表示開始日（省略時は今日の3日前）
        end_range: 表示終了日（省略時は最も遅いスケジュールの終了日+3日）

    Returns:
        PNG画像データの BytesIO オブジェクト
    """
    _setup_japanese_font()
    config = _load_config()

    # 色の設定
    colors = config.get("colors", [
        "#4FC3F7", "#81C784", "#FFB74D", "#E57373",
        "#BA68C8", "#4DD0E1", "#AED581", "#FF8A65",
    ])

    # スケジュールがない場合
    if not schedules:
        return _generate_empty_chart(title)

    # 日付をパースして有効なスケジュールのみ抽出
    parsed_schedules = []
    for s in schedules:
        try:
            start_str = str(s["start_date"]).strip()
            end_str = str(s.get("end_date", s["start_date"])).strip()

            # 時間付き ("2026-03-15 14:00") or 日付のみ ("2026-03-15")
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    start = datetime.strptime(start_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                continue

            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    end = datetime.strptime(end_str, fmt) if end_str else start
                    # 日付のみの指定の場合、その日を含めるために1日足す
                    if fmt == "%Y-%m-%d":
                        end += timedelta(days=1)
                    break
                except ValueError:
                    continue
            else:
                end = start

            # 同日の場合は1日分の幅を持たせる
            if end <= start:
                end = start + timedelta(days=1)
            parsed_schedules.append({
                "title": s.get("title", "無題"),
                "start": start,
                "end": end,
                "group": s.get("group", s.get("title", "無題")),
            })
        except (ValueError, KeyError):
            continue

    if not parsed_schedules:
        return _generate_empty_chart(title)

    # 日付でソート（開始日が早い順）
    parsed_schedules.sort(key=lambda x: x["start"])

    # 表示範囲: 現在時刻の12時間前 〜 現在時刻の6日後
    now = datetime.utcnow() + timedelta(hours=9)
    if start_range is None:
        start_range = now - timedelta(hours=12)
    if end_range is None:
        end_range = now + timedelta(days=6)

    # 表示範囲内のスケジュールのみ表示
    visible_schedules = [
        s for s in parsed_schedules
        if s["end"] > start_range and s["start"] < end_range
    ]

    if not visible_schedules:
        visible_schedules = parsed_schedules  # フォールバック

    # 固有のグループ（Y軸パラメーター）の一覧を作成
    # 順序を保持しつつ重複を排除（Python 3.7+ の dict は順序保持）
    y_labels = list(dict.fromkeys(s["group"] for s in visible_schedules))

    # チャートサイズ
    width = config.get("width", 12)
    height = max(config.get("height", 6), len(y_labels) * 0.6 + 2)

    fig, ax = plt.subplots(figsize=(width, height))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    # 各スケジュールをバーで描画
    for i, schedule in enumerate(visible_schedules):
        # Y軸のどのインデックス（行）に描画するか
        y_index = y_labels.index(schedule["group"])
        
        # 色はY軸（グループ）ごとに変えるか、すべて別にするか（今回はグループごとに設定）
        color = colors[y_index % len(colors)]
        
        duration = (schedule["end"] - schedule["start"]).total_seconds() / 86400.0
        if duration < 0.1:  # 最低限の長さを確保
            duration = 0.1

        bars = ax.barh(
            y_index,
            duration,
            left=mdates.date2num(schedule["start"]),
            height=0.5,
            color=color,
            alpha=0.85,
            edgecolor="white",
            linewidth=0.5,
        )

        # バー内にタイトルを表示（1日以上のすべてのバーに表示）
        fontsize = 7 if duration <= 1 else 9
        ax.text(
            mdates.date2num(schedule["start"]) + duration / 2,
            y_index,
            schedule["title"],
            ha="center",
            va="center",
            color="white",
            fontsize=fontsize,
            fontweight="bold",
            clip_path=bars[0],
            clip_on=True
        )

    # 軸の設定
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels, color="white", fontsize=10)
    ax.invert_yaxis()

    # X軸の表示範囲を固定（今日ラインを左寄せ）
    ax.set_xlim(mdates.date2num(start_range), mdates.date2num(end_range))

    # X軸の日付フォーマット
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.tick_params(axis="x", colors="white", labelsize=9)

    # 今日の線
    today = mdates.date2num(now)
    ax.axvline(x=today, color="#FF6B6B", linewidth=2, linestyle="--", alpha=0.8, label="今日")

    # グリッド
    ax.grid(axis="x", alpha=0.2, color="white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("white")
    ax.spines["left"].set_color("white")

    # タイトル
    ax.set_title(title, color="white", fontsize=14, fontweight="bold", pad=15)

    # 凡例
    ax.legend(loc="upper right", facecolor="#1a1a2e", edgecolor="white", labelcolor="white")

    plt.tight_layout()

    # BytesIO に出力
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)

    return buf


def _generate_empty_chart(title: str) -> io.BytesIO:
    """スケジュールがない場合の空チャートを生成する"""
    _setup_japanese_font()

    fig, ax = plt.subplots(figsize=(8, 3))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")
    ax.text(
        0.5, 0.5,
        "📭 スケジュールが登録されていません",
        ha="center", va="center",
        color="white", fontsize=14,
        transform=ax.transAxes,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, color="white", fontsize=14, fontweight="bold")
    for spine in ax.spines.values():
        spine.set_visible(False)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)

    return buf
