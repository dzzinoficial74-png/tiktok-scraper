"""User endpoints."""
import threading
import time
import json
import re
import datetime
import requests
from curl_cffi import requests as cffi_requests

import config
from . import parsers as P
from . import shared
from .fetch import TikTokBlocked, TikTokNotFound, TikTokUpstreamError, api, country_order, eu_country, get_page

LOOKUP_TTL = 600
_lookup_lock = threading.Lock()
_lookups = {}           
POSTS_PAGE = 35
CREATOR_PAGE = 15
LIST_PAGE = 30
SORTS = {"latest": 0, "popular": 1, "oldest": 2}
FOLLOW_SCENES = {"followers": 67, "following": 21}
LIST_HIDDEN = 10222

def _describe(user): return f"@{user['username']}" if user.get("username") else "that secUid"

def _remember(info):
    raw = info.get("user") or {}
    expires = time.time() + LOOKUP_TTL
    with _lookup_lock:
        if len(_lookups) > 5000: _lookups.clear()
        if raw.get("uniqueId"): _lookups[("username", str(raw["uniqueId"]).lower())] = (expires, info)
        if raw.get("secUid"): _lookups[("sec_uid", raw["secUid"])] = (expires, info)

def get_exact_followers(username: str) -> int:
    if not username: return 0
    url = f"https://www.tiktok.com/@{username}?lang=en"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
    try:
        resp = cffi_requests.get(url, headers=headers, impersonate="chrome", timeout=15)
        if resp.status_code == 200:
            match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.*?)</script>', resp.text)
            if match:
                data = json.loads(match.group(1))
                stats = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {}).get("stats", {})
                return int(stats.get("followerCount", 0))
    except Exception: pass
    return 0

def lookup(user, force_refresh=False):
    key = ("username", user["username"]) if user.get("username") else ("sec_uid", user["sec_uid"])
    if not force_refresh:
        with _lookup_lock:
            hit = _lookups.get(key)
            if hit and hit[0] > time.time(): return hit[1]
                
    params = {"uniqueId": user.get("username") or "", "secUid": user.get("sec_uid") or ""}
    label = f"user {_describe(user)}"
    try:
        payload = api("/api/user/detail/", params, countries=(eu_country(),), not_found=shared.USER_NOT_FOUND, label=label)
        info = payload.get("userInfo")
    except TikTokNotFound: raise
    except TikTokUpstreamError:
        if not user.get("username"): raise
        try:
            scope = get_page(f"/@{user['username']}")
            detail = scope.get("webapp.user-detail") or {}
            if detail.get("statusCode") in shared.USER_NOT_FOUND: raise TikTokNotFound(f"{label} not found")
            info = detail.get("userInfo")
        except Exception:
            raise TikTokNotFound(f"{label} not found")
        
    if not isinstance(info, dict) or not (info.get("user") or {}).get("secUid"): raise TikTokNotFound(f"{label} not found")
    _remember(info)
    return info

def _identity(user):
    info = lookup(user)
    return info["user"]["secUid"], P.user_ref(info["user"])

def resolve(user, force_refresh=False):
    info = lookup(user, force_refresh=force_refresh)
    out = P.user_ref(info["user"])
    out["follower_count"] = P.user_stats(info.get("stats"), info.get("statsV2"))["follower_count"]
    return out

def get_profile(user, force=False): 
    try:
        info = lookup(user, force_refresh=force)
        parsed = P.profile(info)
        if parsed is None: raise TikTokNotFound(f"user {_describe(user)} not found")
        if force and user.get("username"):
            exact = get_exact_followers(user["username"])
            if exact > 0: parsed["followers"] = exact
        return parsed
    except Exception as e:
        raise e

def get_posts(user, sort="latest", cursor=None, country=None, force=False):
    try:
        sec_uid, compact = _identity(user)
        head = {"user": compact, "sort": sort}
        username = user.get("username")
        
        if not username:
            return {**head, "video_count": 0, "videos": [], "next_cursor": None, "has_more": False}

        # 1. TIKWM API (A MAIS CONFIÁVEL PARA O RENDER)
        try:
            url = f"https://www.tikwm.com/api/user/posts?unique_id={username}&count=35&cursor={cursor or 0}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0 and data.get("data") and data["data"].get("videos"):
                    raw_videos = data["data"]["videos"]
                    videos = []
                    for v in raw_videos:
                        play_link = v.get("play", "")
                        if play_link.startswith("//"): play_link = "https:" + play_link
                        cover = v.get("cover", "")
                        if cover.startswith("//"): cover = "https:" + cover
                        ts = v.get("create_time", 0)
                        created_at = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
                        
                        videos.append({
                            "id": str(v.get("video_id")),
                            "description": v.get("title", ""),
                            "created_at": created_at,
                            "stats": {
                                "play_count": v.get("play_count", 0),
                                "like_count": v.get("digg_count", 0),
                                "comment_count": v.get("comment_count", 0),
                                "share_count": v.get("share_count", 0),
                            },
                            "video": {
                                "duration_seconds": v.get("duration", 0),
                                "play_link": play_link,
                            },
                            "cover": cover,
                            "link": f"https://www.tiktok.com/@{username}/video/{v.get('video_id')}",
                            "is_pinned": v.get("is_top", 0) == 1
                        })
                    
                    has_more = data["data"].get("hasMore", False)
                    next_cursor = data["data"].get("cursor", 0)
                    return {**head, "video_count": len(videos), "videos": videos, "next_cursor": str(next_cursor) if has_more else None, "has_more": has_more}
        except Exception as e:
            print(f"[TikWM Error] {e}")

        # 2. Fallback: API Mobile do TikTok
        try:
            url = f"https://api.tiktokv.com/aweme/v1/aweme/post/?user_id={sec_uid}&max_cursor={cursor or 0}&count=20&aid=1128"
            headers = {"User-Agent": "com.ss.android.ugc.trill/2613 (Linux; U; Android 10; en_US; Pixel 4; Build/QQ3A.200805.001; Cronet/58.0.2991.0)"}
            resp = cffi_requests.get(url, headers=headers, impersonate="chrome", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                aweme_list = data.get("aweme_list", [])
                if aweme_list:
                    videos = []
                    for item in aweme_list:
                        play_link = item.get("video", {}).get("play_addr", {}).get("url_list", [""])[0]
                        cover = item.get("video", {}).get("cover", {}).get("url_list", [""])[0]
                        videos.append({
                            "id": item.get("aweme_id"),
                            "description": item.get("desc", ""),
                            "created_at": datetime.datetime.fromtimestamp(item.get("create_time", 0)).strftime("%Y-%m-%d"),
                            "stats": {
                                "play_count": item.get("statistics", {}).get("play_count", 0),
                                "like_count": item.get("statistics", {}).get("digg_count", 0),
                                "comment_count": item.get("statistics", {}).get("comment_count", 0),
                                "share_count": item.get("statistics", {}).get("share_count", 0),
                            },
                            "video": {"duration_seconds": item.get("video", {}).get("duration", 0) // 1000, "play_link": play_link},
                            "cover": cover,
                            "link": f"https://www.tiktok.com/@{username}/video/{item.get('aweme_id')}",
                            "is_pinned": False
                        })
                    return {**head, "video_count": len(videos), "videos": videos, "next_cursor": str(data.get("max_cursor", 0)) if data.get("has_more") else None, "has_more": data.get("has_more") == 1}
        except Exception as e:
            print(f"[Mobile Error] {e}")

        # 3. Fallback: API Oficial
        try:
            params = {"secUid": sec_uid, "count": POSTS_PAGE, "cursor": cursor or 0, "post_item_list_request_type": SORTS.get(sort, 0), "coverFormat": 2, "needPinnedItemIds": "true"}
            payload = api("/api/post/item_list/", params, countries=country_order(country, eu_country()), budget=("posts", config.TIKTOK_POSTS_CALLS_PER_EXIT), label=f"videos of {_describe(user)}")
            if payload.get("itemList"): return shared.video_page(payload, **head)
        except Exception as e:
            print(f"[Official Error] {e}")

        return {**head, "video_count": 0, "videos": [], "next_cursor": None, "has_more": False}
    except Exception as e:
        raise e

def get_reposts(user, cursor=None, country=None):
    sec_uid, compact = _identity(user)
    payload = api("/api/repost/item_list/", {"secUid": sec_uid, "count": LIST_PAGE, "cursor": cursor or 0}, country=country, signed=False)
    return shared.video_page(payload, user=compact)

def _follow_list(user, kind, cursor):
    sec_uid, compact = _identity(user)
    payload = api("/api/user/list/", {"secUid": sec_uid, "scene": FOLLOW_SCENES[kind], "count": LIST_PAGE, "minCursor": cursor or 0, "maxCursor": 0}, countries=country_order(None, eu_country()), signed=False, ok=(0, LIST_HIDDEN), label=f"{kind} of {_describe(user)}")
    if payload.get("statusCode") == LIST_HIDDEN: raise TikTokNotFound(f"{_describe(user)} keeps its {kind} list private")
    rows = P.users(payload.get("userList"), P.user_with_stats)
    next_cursor = shared.next_cursor(payload, "minCursor")
    return {"user": compact, "total_count": P.to_int(payload.get("total")), "user_count": len(rows), "users": rows, "next_cursor": next_cursor, "has_more": next_cursor is not None}

def get_followers(user, cursor=None): return _follow_list(user, "followers", cursor)
def get_following(user, cursor=None): return _follow_list(user, "following", cursor)

def get_playlists(user, cursor=None):
    sec_uid, compact = _identity(user)
    payload = api("/api/user/playlist/", {"secUid": sec_uid, "count": 20, "cursor": cursor or 0}, signed=False)
    playlists = [p for p in (P.playlist(row) for row in payload.get("playList") or []) if p]
    next_cursor = shared.next_cursor(payload)
    return {"user": compact, "playlist_count": len(playlists), "playlists": playlists, "next_cursor": next_cursor, "has_more": next_cursor is not None}

def get_collections(user, cursor=None):
    sec_uid, compact = _identity(user)
    payload = api("/api/user/collection_list/", {"secUid": sec_uid, "count": 20, "cursor": cursor or 0})
    collections = [c for c in (P.collection(row) for row in payload.get("collectionList") or []) if c]
    next_cursor = shared.next_cursor(payload)
    return {"user": compact, "total_count": P.to_int(payload.get("total")), "collection_count": len(collections), "collections": collections, "next_cursor": next_cursor, "has_more": next_cursor is not None}
