"""共通プレフィックス抽出のテスト"""
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

from src.utils.event_date_parser import extract_event_title, _extract_common_prefix

# テスト1: 複数ステージ（弐+破）
text1 = '4月24日(金)15時から4月27日(月)14時59分までの3日間、「幻帝のどうくつ 弐」「幻帝のどうくつ 破」がオープンします。'
title1 = extract_event_title(text1)
print(f"テスト1(複数ステージ): {title1}")
assert title1 == "幻帝のどうくつ", f"期待: 幻帝のどうくつ, 実際: {title1}"

# テスト2: 単一ステージ
text2 = '「幻帝のどうくつ 弐」がオープンします。'
title2 = extract_event_title(text2)
print(f"テスト2(単一ステージ): {title2}")
assert title2 == "幻帝のどうくつ 弐", f"期待: 幻帝のどうくつ 弐, 実際: {title2}"

# テスト3: 共通プレフィックス関数の直接テスト
r3 = _extract_common_prefix(["幻帝のどうくつ 弐", "幻帝のどうくつ 破"])
print(f"テスト3(prefix): {r3}")
assert r3 == "幻帝のどうくつ", f"期待: 幻帝のどうくつ, 実際: {r3}"

# テスト4: 3つのステージ
r4 = _extract_common_prefix(["つりチャレンジ！ 初級", "つりチャレンジ！ 上級", "つりチャレンジ！ 超級"])
print(f"テスト4(3ステージ): {r4}")
assert r4 == "つりチャレンジ！", f"期待: つりチャレンジ！, 実際: {r4}"

# テスト5: 共通部分なし
r5 = _extract_common_prefix(["イベントA", "まったく異なる"])
print(f"テスト5(共通なし): {r5}")
assert r5 is None, f"期待: None, 実際: {r5}"

# テスト6: 直接開催パターン（パターン1が優先されるケース）
text6 = "電波人間コロシアム開催！"
title6 = extract_event_title(text6)
print(f"テスト6(直接パターン): {title6}")
assert title6 == "電波人間コロシアム", f"期待: 電波人間コロシアム, 実際: {title6}"

print("\n✅ 全テスト合格！")
