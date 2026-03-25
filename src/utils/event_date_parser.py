"""
ツイート本文からイベント情報（タイトル・終了日時）を抽出するモジュール。
「◯◯開催」パターンと日付パターンを正規表現で検出する。
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 「◯◯開催」パターン（「開催」の前までをタイトルとして抽出）
# 「開催」の直後に「！」「!」「中」「 」「\n」等が続くケースに対応
EVENT_TITLE_PATTERN = re.compile(
    r'([^\s、。！!？?\n「」『』【】()（）]+?)開催'
)

# 終了を示すキーワード
END_KEYWORDS = re.compile(r'(まで|〜|～|終了|期限|締切|〆切)')

# 日付パターン: YYYY年MM月DD日 or YYYY/MM/DD or YYYY-MM-DD
DATE_PATTERN_FULL = re.compile(
    r'(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})日?'
)

# 日付パターン: MM月DD日 or MM/DD（年なし）
DATE_PATTERN_SHORT = re.compile(
    r'(?<!\d)(\d{1,2})[月/](\d{1,2})日?'
)

# 時刻パターン: HH:MM or HH時MM分 or HH時
TIME_PATTERN = re.compile(
    r'(\d{1,2})[:時](\d{2})?分?'
)


def extract_event_title(text: str) -> Optional[str]:
    """
    ツイート本文から「◯◯開催」パターンを検索し、◯◯部分を返す。

    Args:
        text: ツイート本文

    Returns:
        イベントタイトル。パターンが見つからない場合は None。
    """
    if not text:
        return None

    match = EVENT_TITLE_PATTERN.search(text)
    if match:
        title = match.group(1).strip()
        # タイトルが空でないかチェック
        if title:
            logger.info(f"イベントタイトルを抽出: 「{title}」")
            return title

    return None


def extract_event_end_date(
    text: str,
    reference_date: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    ツイート本文からイベント終了日時を抽出する。

    Args:
        text: ツイート本文
        reference_date: 基準日時（年の補完・過去日判定に使用）。省略時は現在時刻（JST）。

    Returns:
        抽出された終了日時の datetime。見つからない or 過去の場合は None。
    """
    if not text:
        return None

    if reference_date is None:
        reference_date = datetime.utcnow() + timedelta(hours=9)

    # 全ての日付を抽出
    dates_found = _extract_all_dates(text, reference_date)

    if not dates_found:
        return None

    # 終了キーワードの近くにある日付を優先
    end_date = _find_end_date(text, dates_found, reference_date)

    if end_date is None:
        return None

    # 過去日チェック（日付のみの場合はその日の終わりまで有効）
    end_check = end_date
    if end_date.hour == 0 and end_date.minute == 0:
        end_check = end_date.replace(hour=23, minute=59, second=59)

    if end_check < reference_date:
        logger.info(f"抽出された終了日 {end_date} は過去のため、スキップします")
        return None

    logger.info(f"イベント終了日を抽出: {end_date}")
    return end_date


def _extract_all_dates(text: str, reference_date: datetime) -> list[dict]:
    """
    テキストから全ての日付パターンを抽出する。

    Returns:
        [{"date": datetime, "pos": int, "text": str}, ...] のリスト
    """
    results = []

    # YYYY年MM月DD日 or YYYY/MM/DD パターン
    for match in DATE_PATTERN_FULL.finditer(text):
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            dt = datetime(year, month, day)

            # 日付の直後に時刻があるかチェック
            dt = _try_attach_time(text, match.end(), dt)

            results.append({
                "date": dt,
                "pos": match.start(),
                "text": match.group(0),
            })
        except ValueError:
            continue

    # MM月DD日 or MM/DD パターン（年なし）
    for match in DATE_PATTERN_SHORT.finditer(text):
        # YYYY/MM/DD の一部としてすでにマッチしていないかチェック
        if _is_part_of_full_date(text, match.start()):
            continue

        try:
            month = int(match.group(1))
            day = int(match.group(2))

            # 月が1-12、日が1-31の範囲内かチェック
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue

            # 年を補完（直近の未来日）
            year = reference_date.year
            dt = datetime(year, month, day)
            if dt.date() < reference_date.date():
                # 今年の日付が過去なら来年にする
                dt = datetime(year + 1, month, day)

            # 日付の直後に時刻があるかチェック
            dt = _try_attach_time(text, match.end(), dt)

            results.append({
                "date": dt,
                "pos": match.start(),
                "text": match.group(0),
            })
        except ValueError:
            continue

    return results


def _is_part_of_full_date(text: str, pos: int) -> bool:
    """指定位置の日付が YYYY/MM/DD の一部かどうかチェック"""
    if pos >= 5:
        preceding = text[pos - 5:pos]
        if re.search(r'\d{4}[/\-年]$', preceding):
            return True
    if pos >= 2:
        preceding = text[pos - 2:pos]
        if re.search(r'\d{1,2}[/\-]$', preceding):
            # MM/DD が DD/... の一部でないか（YYYY/MM/DD の MM/DD 部分）
            if pos >= 5 and re.search(r'\d{4}/', text[pos - 5:pos]):
                return True
    return False


def _try_attach_time(text: str, date_end_pos: int, dt: datetime) -> datetime:
    """日付の直後にある時刻を検出し、日付に付与する"""
    # 日付の後ろ10文字以内に時刻があるかチェック
    remaining = text[date_end_pos:date_end_pos + 15]
    time_match = TIME_PATTERN.search(remaining)

    if time_match and time_match.start() <= 5:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            dt = dt.replace(hour=hour, minute=minute)

    return dt


def _find_end_date(
    text: str,
    dates_found: list[dict],
    reference_date: datetime,
) -> Optional[datetime]:
    """
    終了キーワードの近くにある日付、または複数日付のうち最も後の日付を終了日とする。
    """
    if not dates_found:
        return None

    # 終了キーワードの位置を検出
    end_keyword_positions = [m.start() for m in END_KEYWORDS.finditer(text)]

    if end_keyword_positions:
        # 終了キーワードに最も近い日付を探す（前後50文字以内）
        best_date = None
        best_distance = float('inf')

        for kw_pos in end_keyword_positions:
            for date_info in dates_found:
                distance = abs(date_info["pos"] - kw_pos)
                if distance < best_distance and distance <= 50:
                    best_distance = distance
                    best_date = date_info["date"]

        if best_date:
            return best_date

    # 終了キーワードが見つからない場合、最も後の日付を返す
    dates_found.sort(key=lambda x: x["date"])
    return dates_found[-1]["date"]
