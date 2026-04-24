"""
ツイート本文からイベント情報（タイトル・開始日時・終了日時）を抽出するモジュール。
複数のツイートパターンに対応する。

対応パターン:
  - ◯◯開催 (直接結合パターン)
  - 「◯◯」...開催/オープン (カギ括弧パターン)
  - 開催中の ◯◯ は (インラインパターン)
  - Ver.X.X.Xの配信 (バージョンアップデートパターン)
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ==============================================================================
# タイトル抽出パターン
# ==============================================================================

# パターン1: ◯◯開催 (直接結合)
# 例: "電波人間コロシアム開催！", "つりチャレンジ！開催中"
# ！!を含むタイトルに対応、最低2文字以上
PATTERN_DIRECT_KAISAI = re.compile(
    r'([^\s、。？?\n「」『』【】()（）！!]{2,}[！!]?)開催'
)

# パターン2: 「◯◯」...開催/オープン (括弧パターン)
# 例: 「幻帝のどうくつ」が開催中, 「電波人間コロシアム」がオープン
PATTERN_BRACKET_EVENT = re.compile(
    r'「([^」]+)」[^\n]*?(?:開催|オープン)'
)

# パターン2b: 連続する「◯◯」「◯◯」...開催/オープン (複数ステージ一括キャプチャ)
# 例: 「幻帝のどうくつ 弐」「幻帝のどうくつ 破」がオープン
# 連続する「」ブロック全体をキャプチャし、後で個別タイトルに分解する
PATTERN_MULTI_BRACKET_EVENT = re.compile(
    r'((?:「[^」]+」\s*){2,})[^\n]*?(?:開催|オープン)'
)

# 「」内のテキストを個別抽出するパターン
PATTERN_BRACKET_CONTENT = re.compile(r'「([^」]+)」')

# パターン3: 開催中の ◯◯ は/まで (インラインパターン)
# 例: "開催中の ハイスコアチャレンジ! は明日11日まで"
PATTERN_INLINE_EVENT = re.compile(
    r'開催中の\s*(.+?)\s*(?:は|まで)'
)

# パターン4: Ver.X.X.Xの配信 (バージョンアップデートパターン)
# 例: "Ver.8.0.11の配信を予定"
PATTERN_VERSION = re.compile(
    r'(Ver\.[\d.]+)\s*の配信'
)

# ==============================================================================
# 日付抽出パターン
# ==============================================================================

# 終了を示すキーワード
END_KEYWORDS = re.compile(r'(まで|〜|～|終了|期限|締切|〆切)')

# 開始を示すキーワード
START_KEYWORDS = re.compile(r'(から|より)')

# 日付パターン: YYYY年MM月DD日 or YYYY/MM/DD or YYYY-MM-DD
DATE_PATTERN_FULL = re.compile(
    r'(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})日?'
)

# 日付パターン: MM月DD日 or MM/DD（年なし）
DATE_PATTERN_SHORT = re.compile(
    r'(?<!\d)(\d{1,2})[月/](\d{1,2})日?'
)

# 日付パターン: DD日（月なし、「明日11日」等）
# 前後の文脈から月を推定する
DATE_PATTERN_DAY_ONLY = re.compile(
    r'(?:明日|明後日|あす|あさって)\s*(\d{1,2})日'
)

# 日付パターン: DD日（月なし、前置詞なし）
# 「4月10日(金) 15時から23日(木) 14時59分まで」の「23日」のような場合
# MM月DD日パターンにマッチしない孤立した「DD日」を検出する
DATE_PATTERN_DAY_BARE = re.compile(
    r'(?<!\d月)(?<!\d)(\d{1,2})日'
)

# テキスト中に出現する「MM月」パターン（月の推定用）
MONTH_PATTERN = re.compile(
    r'(\d{1,2})月'
)

# 時刻パターン: HH:MM or HH時MM分 or HH時
TIME_PATTERN = re.compile(
    r'(\d{1,2})[:時](\d{2})?分?'
)

# 「ごろ」パターン（おおよその時刻）
GORO_PATTERN = re.compile(
    r'(\d{1,2})時ごろ'
)

# 半角→全角 変換マップ（タイトル正規化用）
HALF_TO_FULL = str.maketrans({
    '!': '！',
    '?': '？',
})


# ==============================================================================
# タイトル抽出
# ==============================================================================

def normalize_title(title: str) -> str:
    """
    タイトルの正規化処理。
    半角の！?を全角に変換し、前後の空白や装飾カッコを除去する。
    """
    if not title:
        return ""
    title = title.strip()
    title = title.translate(HALF_TO_FULL)
    # 前後の装飾カッコを除去
    title = title.strip('「」『』【】()（）<>＜＞"\' 　')
    return title.strip()


def _extract_common_prefix(titles: list[str]) -> str | None:
    """
    複数のイベントタイトルから共通プレフィックスを抽出する。

    共通部分の末尾の空白・区切り文字を除去し、意味のあるタイトルを返す。
    最低2文字以上の共通部分がない場合は None を返す。

    例:
      - [「幻帝のどうくつ 弐」, 「幻帝のどうくつ 破」] → 「幻帝のどうくつ」
      - [「電波人間コロシアム A」, 「電波人間コロシアム B」] → 「電波人間コロシアム」
      - [「イベントA」, 「まったく異なるイベント」] → None

    Args:
        titles: イベントタイトルのリスト（2つ以上）

    Returns:
        共通プレフィックス。見つからない場合は None。
    """
    if not titles or len(titles) < 2:
        return None

    # os.path.commonprefix と同等の文字単位の共通プレフィックスを取得
    prefix = titles[0]
    for title in titles[1:]:
        # 文字単位で一致する部分を取得
        new_prefix = []
        for c1, c2 in zip(prefix, title):
            if c1 == c2:
                new_prefix.append(c1)
            else:
                break
        prefix = "".join(new_prefix)
        if not prefix:
            return None

    # 末尾の空白・区切り文字を除去（「幻帝のどうくつ 」→「幻帝のどうくつ」）
    prefix = prefix.rstrip(" 　・:：-ー")

    # 最低2文字以上の共通部分が必要
    if len(prefix) < 2:
        return None

    return prefix


def extract_event_title(text: str) -> Optional[str]:
    """
    ツイート本文からイベントタイトルを抽出する。
    複数のパターンを優先順位付きで試行し、最初にマッチしたものを返す。

    優先順位:
      1. ◯◯開催 (直接結合パターン)
      2. 「◯◯」...開催/オープン (カギ括弧パターン)
      3. 開催中の ◯◯ は (インラインパターン)
      4. Ver.X.X.Xの配信 (バージョンアップデートパターン)

    Args:
        text: ツイート本文

    Returns:
        正規化されたイベントタイトル。パターンが見つからない場合は None。
    """
    if not text:
        return None

    # パターン1: ◯◯開催 (直接結合)
    match = PATTERN_DIRECT_KAISAI.search(text)
    if match:
        title = normalize_title(match.group(1))
        if title:
            logger.info(f"イベントタイトルを抽出（直接パターン）: 「{title}」")
            return title

    # パターン2: 「◯◯」...開催/オープン (括弧パターン)
    # まず複数ステージの一括パターン（2b）を試行し、次に単一パターン（2）にフォールバック

    # パターン2b: 連続する「◯◯」「◯◯」...開催/オープン
    multi_match = PATTERN_MULTI_BRACKET_EVENT.search(text)
    if multi_match:
        # 連続する「」ブロックから個別タイトルを抽出
        bracket_block = multi_match.group(1)
        individual_titles = PATTERN_BRACKET_CONTENT.findall(bracket_block)
        titles = [normalize_title(t) for t in individual_titles if normalize_title(t)]
        if len(titles) >= 2:
            # 複数マッチ → 共通プレフィックスを抽出
            common = _extract_common_prefix(titles)
            if common:
                logger.info(f"イベントタイトルを抽出（括弧パターン・共通プレフィックス）: 「{common}」（元: {titles}）")
                return common
            else:
                # 共通プレフィックスがない場合は最初のタイトルを使用
                logger.info(f"イベントタイトルを抽出（括弧パターン・共通なし）: 「{titles[0]}」")
                return titles[0]

    # パターン2: 単一の「◯◯」...開催/オープン
    match = PATTERN_BRACKET_EVENT.search(text)
    if match:
        title = normalize_title(match.group(1))
        if title:
            logger.info(f"イベントタイトルを抽出（括弧パターン）: 「{title}」")
            return title

    # パターン3: 開催中の ◯◯ は/まで (インラインパターン)
    match = PATTERN_INLINE_EVENT.search(text)
    if match:
        title = normalize_title(match.group(1))
        if title:
            logger.info(f"イベントタイトルを抽出（インラインパターン）: 「{title}」")
            return title

    # パターン4: Ver.X.X.Xの配信 (バージョンアップデートパターン)
    match = PATTERN_VERSION.search(text)
    if match:
        title = normalize_title(match.group(1))
        if title:
            logger.info(f"イベントタイトルを抽出（バージョンパターン）: 「{title}」")
            return title

    return None


# ==============================================================================
# 日付抽出
# ==============================================================================

def extract_event_dates(
    text: str,
    reference_date: Optional[datetime] = None,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    ツイート本文からイベントの開始日時・終了日時を抽出する。

    ルール:
      - 「から」「より」の近くにある日付を開始日、「まで」「終了」等の近くにある日付を終了日とする
      - 開始日のみの場合は、最も後の日付を終了日として補完
      - 終了日のみの場合は、reference_date を開始日とする
      - 「ごろ」がある場合は ±1時間のウィンドウを作成
      - 日付情報が一切ない場合は (None, None) を返す

    Args:
        text: ツイート本文
        reference_date: 基準日時（年の補完・過去日判定に使用）。省略時は現在時刻（JST）。

    Returns:
        (start_date, end_date) のタプル。日付が見つからない場合は (None, None)。
    """
    if not text:
        return None, None

    if reference_date is None:
        reference_date = datetime.utcnow() + timedelta(hours=9)

    # 全ての日付を抽出
    dates_found = _extract_all_dates(text, reference_date)

    # 日付が見つからない場合、「ごろ」パターンを単独チェック
    if not dates_found:
        return None, None

    # 「ごろ」パターンのチェック（日付に「ごろ」が付随しているか）
    goro_result = _check_goro_pattern(text, dates_found, reference_date)
    if goro_result:
        return goro_result

    # 開始・終了キーワードの位置を検出
    start_date = _find_date_near_keyword(
        text, dates_found, START_KEYWORDS, max_distance=30
    )
    end_date = _find_date_near_keyword(
        text, dates_found, END_KEYWORDS, max_distance=50
    )

    # 結果の組み立て
    if start_date and end_date:
        # 両方見つかった場合
        logger.info(f"開始日・終了日を抽出: {start_date} 〜 {end_date}")
        return start_date, end_date
    elif end_date:
        # 終了日のみ → 開始日は現在時刻
        logger.info(f"終了日のみ抽出: 〜{end_date}（開始日=現在時刻）")
        return reference_date.replace(second=0, microsecond=0), end_date
    elif start_date:
        # 開始日のみ → 最も後の日付を終了日とする
        dates_found.sort(key=lambda x: x["date"])
        latest = dates_found[-1]["date"]
        if latest > start_date:
            logger.info(f"開始日のみ抽出: {start_date}〜{latest}")
            return start_date, latest
        else:
            # 同一日付しかない場合
            logger.info(f"開始日のみ抽出（同一日付）: {start_date}")
            return start_date, start_date
    else:
        # キーワードなし → 日付がある場合は最も後の日付を終了日
        if dates_found:
            dates_found.sort(key=lambda x: x["date"])
            latest = dates_found[-1]["date"]
            logger.info(f"キーワードなし（最終日付を終了日に）: 〜{latest}")
            return reference_date.replace(second=0, microsecond=0), latest
        return None, None


def extract_event_end_date(
    text: str,
    reference_date: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    後方互換性のためのラッパー。終了日時のみを返す。

    Args:
        text: ツイート本文
        reference_date: 基準日時

    Returns:
        抽出された終了日時の datetime。見つからない場合は None。
    """
    _, end_date = extract_event_dates(text, reference_date)

    if end_date is None:
        return None

    if reference_date is None:
        reference_date = datetime.utcnow() + timedelta(hours=9)

    # 過去日チェック（日付のみの場合はその日の終わりまで有効）
    end_check = end_date
    if end_date.hour == 0 and end_date.minute == 0:
        end_check = end_date.replace(hour=23, minute=59, second=59)

    if end_check < reference_date:
        logger.info(f"抽出された終了日 {end_date} は過去のため、スキップします")
        return None

    return end_date


# ==============================================================================
# 内部ヘルパー関数
# ==============================================================================

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

    # DD日（月なし）パターン: 「明日11日」「明後日5日」等
    for match in DATE_PATTERN_DAY_ONLY.finditer(text):
        try:
            day = int(match.group(1))
            if not (1 <= day <= 31):
                continue

            # 月は基準日の月を使用
            month = reference_date.month
            year = reference_date.year

            # 日が基準日より前の場合は翌月
            if day < reference_date.day:
                month += 1
                if month > 12:
                    month = 1
                    year += 1

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

    # DD日（月なし・前置詞なし）パターン: 「23日(木)」等
    # 他のパターン（MM月DD日、明日DD日）で既に抽出済みの位置は除外する
    already_matched_positions = {r["pos"] for r in results}
    for match in DATE_PATTERN_DAY_BARE.finditer(text):
        # 既に他のパターンでマッチ済みの位置ならスキップ
        if _is_already_matched(match.start(), results):
            continue

        # MM月DD日 パターンの一部（DD日部分）としてマッチしていないかチェック
        # 直前に「月」があればスキップ（MM月DD日として既に処理済み）
        if match.start() >= 2:
            preceding_char = text[match.start() - 1:match.start()]
            if preceding_char == '月':
                continue

        try:
            day = int(match.group(1))
            if not (1 <= day <= 31):
                continue

            # ---- 月の推定ロジック ----
            # テキスト中でこの位置より前に出現した最も近い「MM月」から月を推定
            month = _infer_month_from_context(text, match.start(), reference_date)
            year = reference_date.year

            dt = datetime(year, month, day)

            # 基準日より過去の場合は翌年
            if dt.date() < reference_date.date():
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


def _is_already_matched(pos: int, results: list[dict]) -> bool:
    """
    指定位置が既に他のパターンで抽出済みの日付範囲内かどうかチェックする。

    Args:
        pos: チェック対象のテキスト位置
        results: 既に抽出済みの日付リスト

    Returns:
        既に抽出済みの場合 True
    """
    for r in results:
        r_start = r["pos"]
        r_end = r_start + len(r["text"])
        # 抽出済みの日付テキストの範囲内に含まれていたらスキップ
        if r_start <= pos < r_end:
            return True
    return False


def _infer_month_from_context(text: str, pos: int, reference_date: datetime) -> int:
    """
    テキスト中の指定位置より前にある最も近い「MM月」から月を推定する。
    見つからない場合は基準日の月を返す。

    例: 「4月10日(金) 15時から23日(木) 14時59分まで」
        → 「23日」の位置から前を見ると「4月」が見つかるので、4月と推定

    Args:
        text: テキスト
        pos: 対象の「DD日」の位置
        reference_date: 基準日時（フォールバック用）

    Returns:
        推定された月（1-12）
    """
    best_month = None
    best_distance = float('inf')

    for match in MONTH_PATTERN.finditer(text):
        month_end = match.end()
        # 「MM月」が対象位置より前にある場合のみ
        if month_end <= pos:
            distance = pos - month_end
            if distance < best_distance:
                best_distance = distance
                month_val = int(match.group(1))
                if 1 <= month_val <= 12:
                    best_month = month_val

    if best_month is not None:
        return best_month

    return reference_date.month


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
    """
    日付の直後にある時刻を検出し、日付に付与する。
    曜日を示す括弧 (月)、(火) 等をスキップする。
    """
    # 日付の後ろの文字列を取得（十分な範囲）
    remaining = text[date_end_pos:date_end_pos + 30]

    # 曜日括弧をスキップ: (月), (火), (水), (木), (金), (土), (日)
    remaining = re.sub(r'^\s*[（(][月火水木金土日][）)]', '', remaining)

    # 「の」「 」などの区切り文字をスキップ
    remaining = re.sub(r'^[\sの]+', '', remaining)

    time_match = TIME_PATTERN.search(remaining)

    if time_match and time_match.start() <= 5:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            dt = dt.replace(hour=hour, minute=minute)

    return dt


def _find_date_near_keyword(
    text: str,
    dates_found: list[dict],
    keyword_pattern: re.Pattern,
    max_distance: int = 50,
) -> Optional[datetime]:
    """
    指定されたキーワードパターンの直前にある日付を返す。
    「から」「まで」等のキーワードは、その直前の日付を指すため、
    キーワードより前にある日付を優先する。

    Args:
        text: テキスト
        dates_found: 抽出済みの日付リスト
        keyword_pattern: キーワードの正規表現パターン
        max_distance: キーワードと日付の最大距離（文字数）

    Returns:
        最も近い日付の datetime。見つからない場合は None。
    """
    keyword_positions = [m.start() for m in keyword_pattern.finditer(text)]

    if not keyword_positions:
        return None

    best_date = None
    best_distance = float('inf')

    for kw_pos in keyword_positions:
        for date_info in dates_found:
            date_pos = date_info["pos"]
            # キーワードの位置との距離
            distance = abs(date_pos - kw_pos)

            if distance > max_distance:
                continue

            # キーワードより前にある日付を優先（重み付け）
            # 「から」の直前の日付、「まで」の直前の日付を正しくマッチさせる
            if date_pos <= kw_pos:
                # 日付がキーワードの前にある → 優先度高（距離をそのまま使用）
                weighted_distance = distance
            else:
                # 日付がキーワードの後にある → 優先度低（距離にペナルティ）
                weighted_distance = distance + max_distance

            if weighted_distance < best_distance:
                best_distance = weighted_distance
                best_date = date_info["date"]

    return best_date


def _check_goro_pattern(
    text: str,
    dates_found: list[dict],
    reference_date: datetime,
) -> Optional[tuple[datetime, datetime]]:
    """
    「ごろ」パターンがある場合、±1時間のウィンドウを作成する。
    例: "15時ごろ" → (date 14:00, date 16:00)

    Args:
        text: テキスト
        dates_found: 抽出済みの日付リスト
        reference_date: 基準日時

    Returns:
        (start_date, end_date) のタプル。パターンがない場合は None。
    """
    goro_match = GORO_PATTERN.search(text)
    if not goro_match:
        return None

    goro_hour = int(goro_match.group(1))
    if not (0 <= goro_hour <= 23):
        return None

    goro_pos = goro_match.start()

    # 「ごろ」に最も近い日付を探す
    best_date = None
    best_distance = float('inf')

    for date_info in dates_found:
        distance = abs(date_info["pos"] - goro_pos)
        if distance < best_distance and distance <= 80:
            best_distance = distance
            best_date = date_info["date"]

    if best_date is None:
        return None

    # 日付部分のみ使用（時刻は「ごろ」から取得）
    base_date = best_date.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = base_date.replace(hour=max(0, goro_hour - 1))
    end_dt = base_date.replace(hour=min(23, goro_hour + 1))

    logger.info(f"「ごろ」パターン検出: {goro_hour}時ごろ → {start_dt} 〜 {end_dt}")
    return start_dt, end_dt


def periods_overlap(
    start1: datetime, end1: datetime,
    start2: datetime, end2: datetime,
) -> bool:
    """
    2つの期間が重なるかチェックする。

    Args:
        start1, end1: 期間1
        start2, end2: 期間2

    Returns:
        期間が重なる場合 True
    """
    return start1 < end2 and start2 < end1
