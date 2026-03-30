"""
Twitter（X）アカウントの新着ツイートチェックモジュール。
RapidAPI上のサードパーティAPIを利用してツイート情報を取得する。
"""

import os
import json
import logging
import re
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

class TwitterChecker:
    """RapidAPI 経由で Twitter アカウントのツイートを取得するクラス"""

    def __init__(self, username: str, rapidapi_key: str, rapidapi_host: str, api_url: Optional[str] = None):
        """
        Args:
            username: Twitterユーザー名（@なし）
            rapidapi_key: RapidAPI の X-RapidAPI-Key
            rapidapi_host: RapidAPI の X-RapidAPI-Host
            api_url: APIのエンドポイントURL
        """
        self.username = username
        self.rapidapi_key = rapidapi_key
        self.rapidapi_host = rapidapi_host

        self.api_url = api_url or f"https://{self.rapidapi_host}/user-tweets"

    def fetch_tweets(self) -> list[dict]:
        """
        ツイート一覧を取得する。RapidAPI を利用する。

        Returns:
            ツイート情報のリスト。各ツイートは以下のキーを持つ:
            - tweet_id: ツイートの一意識別子（ID）
            - text: ツイート本文
            - url: ツイートのURL
            - published: 公開日時
            - published_formatted: 表示用の日時文字列（YYYY/MM/DD HH:MM）
            - images: 画像URLのリスト
            - author: アカウント表示名
        """
        if not self.rapidapi_key or not self.rapidapi_host:
            logger.error("RapidAPI のキーまたはホストが設定されていません。")
            return []

        logger.info(f"RapidAPI ({self.rapidapi_host}) を利用して @{self.username} のツイートを取得中...")
        return self._fetch_from_rapidapi()

    def _fetch_from_rapidapi(self) -> list[dict]:
        """RapidAPI からユーザーのツイートを取得する"""
        headers = {
            "X-RapidAPI-Key": self.rapidapi_key,
            "X-RapidAPI-Host": self.rapidapi_host
        }
        
        querystring = {
            "username": self.username,
            "user": self.username,
            "limit": "20",
            "count": "20",
            "include_replies": "false",
            "include_pinned": "false"
        }

        try:
            response = requests.get(self.api_url, headers=headers, params=querystring, timeout=15)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"RapidAPI からの取得に失敗しました: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return []
        except ValueError as e:
            logger.error(f"RapidAPI のレスポンス解析に失敗しました: {e}")
            return []

        # APIごとのレスポンス構造の差異を柔軟に吸収
        tweets_data = []
        if isinstance(data, list):
            tweets_data = data
        elif isinstance(data, dict):
            # Twitter241 などの GraphQL Timeline 形式
            if "result" in data and "timeline" in data["result"]:
                tweets_data = self._extract_graphql_tweets(data)
            
            # Twitter154 などは {"results": [...]} などの形で返すことがある
            elif "results" in data:
                tweets_data = data["results"]
            elif "data" in data and "tweets" in data["data"]:
                tweets_data = data["data"]["tweets"]
            elif "timeline" in data:
                tweets_data = data["timeline"]
            else:
                # 辞書内のリスト要素を探す
                for key, value in data.items():
                    if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                        tweets_data = value
                        break

        if not tweets_data:
            import json
            logger.warning(f"ツイートデータが見つかりませんでした。Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            logger.warning(f"Response: {json.dumps(data, ensure_ascii=False)[:3000]}")
            return []

        tweets = []
        for tweet_data in tweets_data:
            try:
                # ID
                tweet_id = str(tweet_data.get("tweet_id", tweet_data.get("id", tweet_data.get("id_str", ""))))
                if not tweet_id:
                    continue

                # テキスト
                text = tweet_data.get("text", tweet_data.get("full_text", ""))
                
                # 画像
                images = []
                # Twitter154: media_url リスト形式
                if "media_url" in tweet_data and isinstance(tweet_data["media_url"], list):
                    images = [img for img in tweet_data["media_url"] if isinstance(img, str)]
                # entities.media 形式
                elif "entities" in tweet_data and "media" in tweet_data["entities"]:
                    for media in tweet_data["entities"]["media"]:
                        if media.get("type") == "photo" and "media_url_https" in media:
                            images.append(media["media_url_https"])

                # 日時
                created_at = tweet_data.get("creation_date", tweet_data.get("created_at", ""))
                published_formatted = self._format_date(created_at)

                # アカウント名
                user_data = tweet_data.get("user", {})
                author = user_data.get("name", f"@{self.username}")
                screen_name = user_data.get("screen_name", self.username)
                
                # URL
                tweet_url = tweet_data.get("expanded_url", f"https://x.com/{screen_name}/status/{tweet_id}")

                # クリーンアップ
                clean_text = self._strip_html(text)

                tweets.append({
                    "tweet_id": tweet_id,
                    "text": clean_text,
                    "url": tweet_url,
                    "published": created_at,
                    "published_formatted": published_formatted,
                    "images": images,
                    "author": author,
                })
            except Exception as e:
                logger.error(f"ツイートパース中にエラー: {e}")

        logger.info(f"{len(tweets)} 件のツイート情報を抽出しました")
        return tweets

    def _extract_graphql_tweets(self, data: dict) -> list[dict]:
        """TwitterのGraphQL形式(Result -> Timeline -> Instructions)からツイートを取り出す"""
        tweets_data = []
        try:
            instructions = data.get("result", {}).get("timeline", {}).get("instructions", [])
            for inst in instructions:
                # TimelineAddEntries に複数ツイートが含まれる
                if inst.get("type") == "TimelineAddEntries" and "entries" in inst:
                    for entry in inst["entries"]:
                        content = entry.get("content", {})
                        if content.get("entryType") == "TimelineTimelineItem":
                            tweet_res = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                            self._append_graphql_tweet(tweet_res, tweets_data)
                
                # TimelinePinEntry など単独のツイートが含まれる
                elif "entry" in inst:
                    content = inst["entry"].get("content", {})
                    if content.get("entryType") == "TimelineTimelineItem":
                        tweet_res = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                        self._append_graphql_tweet(tweet_res, tweets_data)
        except Exception as e:
            logger.debug(f"GraphQL解析エラー: {e}")
            
        return tweets_data

    def _append_graphql_tweet(self, tweet_res: dict, tweets_data: list):
        if not tweet_res:
            return
            
        # リツイートなどで別のキーに入っている場合
        if tweet_res.get("__typename") == "TweetWithVisibilityResults":
            tweet_res = tweet_res.get("tweet", {})

        legacy = tweet_res.get("legacy")
        if not legacy:
            return
            
        # IDが legacy の中にない場合（通常 core のほうにあるが、legacy にセットしておく）
        if "id_str" not in legacy:
            legacy["id_str"] = tweet_res.get("rest_id", "")
            
        # user情報をマージしておく (既存のパース処理をそのまま活かすため)
        core = tweet_res.get("core", {})
        user_legacy = core.get("user_results", {}).get("result", {}).get("legacy", {})
        if user_legacy:
            legacy["user"] = user_legacy
            
        tweets_data.append(legacy)

    def _strip_html(self, html_text: str) -> str:
        """HTML タグを除去してプレーンテキストにする"""
        if not html_text: return ""
        text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
            text = text.replace(entity, char)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _format_date(self, published: str) -> str:
        """API の日付文字列をフォーマットする"""
        if not published: return ""
        try:
            # 様々なフォーマットへの対応 (Mon Mar 30 08:00:00 +0000 2026)
            if "+0000" in published:
                dt = datetime.strptime(published.replace("+0000", "").strip(), "%a %b %d %H:%M:%S %Y")
                return dt.strftime("%Y/%m/%d %H:%M")
            return published
        except Exception:
            return published

    def detect_new_tweets(self, current_tweets: list[dict], known_tweet_ids: list[str]) -> list[dict]:
        """新着ツイートを検出する"""
        new_tweets = [t for t in current_tweets if str(t["tweet_id"]) not in known_tweet_ids]
        if new_tweets:
            logger.info(f"新着ツイートを {len(new_tweets)} 件検出しました")
        else:
            logger.info("新着ツイートはありません")
        return new_tweets
