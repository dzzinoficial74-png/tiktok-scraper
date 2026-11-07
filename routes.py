"""The 41 TikTok endpoints..."""
import json
from bottle import request, response, route
from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from tiktok import ads, discovery, live, schemas, search, users, videos

def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)

def query_dict():
    return {key: request.query.getunicode(key) for key in request.query.keys()}

def call(label, schema, fn):
    data, error = load_query(schema, query_dict())
    if error:
        return json_response(error, 400)
    try:
        return json_response(fn(**data))
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"tiktok rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"tiktok blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"tiktok {label} failed: {e}"}, 502)
    except Exception as e:
        return json_response({"error": f"tiktok {label} failed: {type(e).__name__}: {e}"}, 500)

def mount(path, schema, fn):
    label = path.strip("/")
    def handler():
        return call(label, schema, fn)
    handler.__name__ = "tiktok_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/tiktok" + path, method="GET")(handler)

ENDPOINTS = [
    ("/videos/details", schemas.VideoSchema, videos.get_details),
    ("/videos/media", schemas.VideoSchema, videos.get_media),
    ("/videos/comments", schemas.VideoPageSchema, videos.get_comments),
    ("/videos/comment-replies", schemas.CommentRepliesSchema, videos.get_comment_replies),
    ("/videos/transcript", schemas.TranscriptSchema, videos.get_transcript),
    ("/videos/related", schemas.VideoSchema, videos.get_related),
    ("/videos/resolve", schemas.VideoSchema, videos.resolve),
    ("/users/profile", schemas.UserSchema, users.get_profile),
    ("/users/videos", schemas.UserPostsSchema, users.get_posts),
    ("/users/reposts", schemas.UserVideosSchema, users.get_reposts),
    ("/users/followers", schemas.UserPageSchema, users.get_followers),
    ("/users/following", schemas.UserPageSchema, users.get_following),
    ("/users/playlists", schemas.UserPageSchema, users.get_playlists),
    ("/users/collections", schemas.UserPageSchema, users.get_collections),
    ("/users/resolve", schemas.UserSchema, users.resolve),
    ("/search/suggestions", schemas.SuggestionsSchema, search.get_suggestions),
    ("/search/videos", schemas.VideoSearchSchema, search.search_videos),
    ("/search/users", schemas.UserSearchSchema, search.search_users),
    ("/search/top", schemas.SearchSchema, search.search_top),
    ("/search/live", schemas.SearchSchema, search.search_live),
    ("/hashtags/details", schemas.HashtagSchema, discovery.get_hashtag),
    ("/hashtags/videos", schemas.HashtagPostsSchema, discovery.get_hashtag_posts),
    ("/music/details", schemas.MusicSchema, discovery.get_music),
    ("/music/videos", schemas.MusicPostsSchema, discovery.get_music_posts),
    ("/playlists/details", schemas.PlaylistSchema, discovery.get_playlist),
    ("/playlists/videos", schemas.PlaylistPostsSchema, discovery.get_playlist_posts),
    ("/collections/videos", schemas.CollectionPostsSchema, discovery.get_collection_posts),
    ("/places/details", schemas.PlaceSchema, discovery.get_place),
    ("/places/videos", schemas.PlacePostsSchema, discovery.get_place_posts),
    ("/effects/details", schemas.EffectSchema, discovery.get_effect),
    ("/feed/trending", schemas.TrendingSchema, discovery.get_trending),
    ("/feed/explore", schemas.ExploreSchema, discovery.get_explore),
    ("/feed/explore/categories", schemas.EmptySchema, discovery.get_explore_categories),
    ("/live/details", schemas.UserSchema, live.get_room),
    ("/ads/search", schemas.AdsSearchSchema, ads.search_library),
    ("/ads/details", schemas.AdSchema, ads.get_library_ad),
    ("/ads/advertisers", schemas.AdvertiserSearchSchema, ads.search_advertisers),
    ("/ads/countries", schemas.EmptySchema, ads.get_library_countries),
    ("/ads/top", schemas.TopAdsSchema, ads.get_top_ads),
    ("/ads/top/details", schemas.AdSchema, ads.get_top_ad),
    ("/ads/top/filters", schemas.EmptySchema, ads.get_top_ad_filters),
]

for _path, _schema, _fn in ENDPOINTS:
    mount(_path, _schema, _fn)

@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, _, _ in ENDPOINTS]})

# ============================================================
# Endpoints de imagem para o jogo Roblox (avatar + capas de video).
# ============================================================
import json as _json
import struct as _struct
from io import BytesIO as _BytesIO
from urllib.parse import urlparse as _urlparse
from bottle import request as _request, response as _response, route as _route
from curl_cffi import requests as _cffi
from PIL import Image as _Image, ImageOps as _ImageOps

_IMG_MAX_SIDE = 512

def _img_host_allowed(url):
    try:
        host = (_urlparse(url).hostname or "").lower()
    except Exception:
        return False
    # CORREÇÃO: Aceita tiktokcdn.com, ibyteimg.com e byteimg.com
    return ("tiktokcdn" in host and host.endswith(".com")) or host.endswith("ibyteimg.com") or host.endswith("byteimg.com")

def _clamp_side(value, default):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(16, min(_IMG_MAX_SIDE, v))

def _serve_pixels():
    url = _request.query.getunicode("url") or ""
    if not url.startswith("https://") or not _img_host_allowed(url):
        _response.status = 400
        _response.content_type = "application/json"
        return _json.dumps({"error": "url not allowed"})

    size = _request.query.getunicode("size")
    w = _clamp_side(_request.query.getunicode("w") or size, 128)
    h = _clamp_side(_request.query.getunicode("h") or size, w)

    try:
        # CORREÇÃO: Adicionado o Referer para o TikTok não bloquear
        resp = _cffi.get(url, impersonate="chrome", timeout=15, headers={"Referer": "https://www.tiktok.com/"})
        if resp.status_code != 200 or not resp.content:
            _response.status = 502
            _response.content_type = "application/json"
            return _json.dumps({"error": f"image fetch failed: {resp.status_code}"})

        img = _ImageOps.fit(
            _Image.open(_BytesIO(resp.content)).convert("RGBA"),
            (w, h),
            _Image.LANCZOS,
        )
        header = _struct.pack("<II", w, h)
        _response.content_type = "application/octet-stream"
        return header + img.tobytes()
    except Exception as e:
        _response.status = 502
        _response.content_type = "application/json"
        return _json.dumps({"error": f"image failed: {type(e).__name__}: {e}"})

_route("/avatar", method="GET")(_serve_pixels)
_route("/tiktok/avatar", method="GET")(_serve_pixels)
_route("/image", method="GET")(_serve_pixels)
_route("/tiktok/image", method="GET")(_serve_pixels)

# ============================================================
# REPRODUCAO DE VIDEO POR "PIXEL STREAMING" (EditableImage)
# ============================================================
import os as _os
import hashlib as _hashlib
import subprocess as _subprocess
import shutil as _shutil
from urllib.parse import urlparse as _vurlparse

_VID_MAX_W = 128      
_VID_MAX_H = 192      
_VID_DEF_W = 64       
_VID_DEF_H = 112      
_VID_MIN_FPS = 4
_VID_MAX_FPS = 120    
_VID_DEF_FPS = 120    
_VID_MAX_SECS = 3     
_VID_MIN_SECS = 2        
_VID_DEF_SECS = 3     
_VID_MAX_BATCH = 32   
_VID_CACHE_DIR = _os.path.join("/tmp", "ttframes")
_VID_CACHE_MAX = 24   
_VID_DL_MAX = 40 * 1024 * 1024   

_os.makedirs(_VID_CACHE_DIR, exist_ok=True)
_HAS_FFMPEG = _shutil.which("ffmpeg") is not None

def _vid_host_allowed(url):
    try:
        host = (_vurlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host.endswith(".com"):
        return False
    return ("tiktokcdn" in host) or host.endswith("tiktokv.com") or host.endswith("tiktok.com") or host.endswith("byteoversea.com") or host.endswith("ibyteimg.com") or host.endswith("byteimg.com")

def _vid_clamp(value, default, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))

def _vid_params():
    url = _request.query.getunicode("url") or ""
    w = _vid_clamp(_request.query.getunicode("w"), _VID_DEF_W, 16, _VID_MAX_W)
    h = _vid_clamp(_request.query.getunicode("h"), _VID_DEF_H, 16, _VID_MAX_H)
    fps = _vid_clamp(_request.query.getunicode("fps"), _VID_DEF_FPS, _VID_MIN_FPS, _VID_MAX_FPS)
    secs = _vid_clamp(_request.query.getunicode("secs"), _VID_DEF_SECS, _VID_MIN_SECS, _VID_MAX_SECS)
    return url, w, h, fps, secs

def _vid_key(url, w, h, fps, secs):
    raw = f"{url}|{w}|{h}|{fps}|{secs}".encode("utf-8")
    return _hashlib.sha1(raw).hexdigest()

def _vid_cleanup_cache():
    try:
        files = [_os.path.join(_VID_CACHE_DIR, f) for f in _os.listdir(_VID_CACHE_DIR) if f.endswith(".raw")]
        if len(files) <= _VID_CACHE_MAX:
            return
        files.sort(key=lambda p: _os.path.getmtime(p))
        for p in files[:len(files) - _VID_CACHE_MAX]:
            try:
                _os.remove(p)
            except OSError:
                pass
    except OSError:
        pass

def _vid_prepare(url, w, h, fps, secs):
    key = _vid_key(url, w, h, fps, secs)
    raw_path = _os.path.join(_VID_CACHE_DIR, key + ".raw")
    frame_bytes = w * h * 4

    if _os.path.exists(raw_path) and _os.path.getsize(raw_path) >= frame_bytes:
        _os.utime(raw_path, None)
        return raw_path, _os.path.getsize(raw_path) // frame_bytes, None

    if not _HAS_FFMPEG:
        return None, 0, "ffmpeg nao instalado no servidor"

    mp4_path = _os.path.join(_VID_CACHE_DIR, key + ".mp4")
    try:
        resp = _cffi.get(url, impersonate="chrome", timeout=30, headers={"Referer": "https://www.tiktok.com/"})
        if resp.status_code != 200 or not resp.content:
            return None, 0, f"download do video falhou: {resp.status_code}"
        if len(resp.content) > _VID_DL_MAX:
            return None, 0, "video grande demais"
        with open(mp4_path, "wb") as fh:
            fh.write(resp.content)
    except Exception as e:
        return None, 0, f"download: {type(e).__name__}"

    vf = (f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=rgba")
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-t", str(secs), "-i", mp4_path,
        "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgba", raw_path,
    ]
    try:
        proc = _subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            return None, 0, "ffmpeg falhou"
    except Exception as e:
        return None, 0, f"ffmpeg: {type(e).__name__}"
    finally:
        try:
            _os.remove(mp4_path)
        except OSError:
            pass

    if not _os.path.exists(raw_path) or _os.path.getsize(raw_path) < frame_bytes:
        return None, 0, "nenhum frame extraido"

    _vid_cleanup_cache()
    return raw_path, _os.path.getsize(raw_path) // frame_bytes, None

def _serve_video_info():
    url, w, h, fps, secs = _vid_params()
    if not url.startswith("https://") or not _vid_host_allowed(url):
        _response.status = 400
        _response.content_type = "application/json"
        return _json.dumps({"error": "url not allowed"})

    raw_path, total, err = _vid_prepare(url, w, h, fps, secs)
    if err:
        _response.status = 502
        _response.content_type = "application/json"
        return _json.dumps({"error": err})

    _response.content_type = "application/json"
    return _json.dumps({
        "width": w, "height": h, "fps": fps,
        "total_frames": total,
        "max_batch": _VID_MAX_BATCH,
        "duration_s": secs,
        "duration_capped_s": _VID_MAX_SECS,
    })

def _serve_video_frames():
    url, w, h, fps, secs = _vid_params()
    if not url.startswith("https://") or not _vid_host_allowed(url):
        _response.status = 400
        _response.content_type = "application/json"
        return _json.dumps({"error": "url not allowed"})

    start = _vid_clamp(_request.query.getunicode("start"), 0, 0, 10_000_000)
    count = _vid_clamp(_request.query.getunicode("count"), _VID_MAX_BATCH, 1, _VID_MAX_BATCH)

    raw_path, total, err = _vid_prepare(url, w, h, fps, secs)
    if err:
        _response.status = 502
        _response.content_type = "application/json"
        return _json.dumps({"error": err})

    frame_bytes = w * h * 4
    if start >= total:
        n = 0
        chunk = b""
    else:
        n = min(count, total - start)
        with open(raw_path, "rb") as fh:
            fh.seek(start * frame_bytes)
            chunk = fh.read(n * frame_bytes)
        n = len(chunk) // frame_bytes

    header = _struct.pack("<III", w, h, n)
    _response.content_type = "application/octet-stream"
    _response.set_header("X-Total-Frames", str(total))
    _response.set_header("X-Fps", str(fps))
    return header + chunk

_route("/video_info", method="GET")(_serve_video_info)
_route("/tiktok/video_info", method="GET")(_serve_video_info)
_route("/video_frames", method="GET")(_serve_video_frames)
_route("/tiktok/video_frames", method="GET")(_serve_video_frames)
