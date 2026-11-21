from __future__ import annotations

import argparse
import atexit
import math
import os
import secrets
import shutil
import tempfile
import threading
import time
import webbrowser
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from converter import (
    RAMPS,
    AsciiOptions,
    MediaError,
    MediaInfo,
    convert_media,
    inspect_media,
)

VERSION = "1.0.0"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FRAMES = 240
MAX_SECONDS = 20.0

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}

@dataclass
class StoredMedia:
    token: str
    path: Path
    name: str
    size: int
    info: MediaInfo
    created_at: float

    def public(self) -> dict:
        return {
            "name": self.name,
            "kind": self.info.kind,
            "format": self.info.format,
            "width": self.info.width,
            "height": self.info.height,
            "frames": self.info.frames,
            "duration_ms": self.info.duration_ms,
            "fps": self.info.fps,
            "size": self.size,
        }


class MediaStore:
    """Temporary uploads plus a tiny LRU cache for repeated renders."""

    def __init__(
        self,
        max_items: int = 12,
        cache_items: int = 8,
        max_age_seconds: int = 3600,
    ) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="glyph-"))
        self.max_items = max_items
        self.cache_items = cache_items
        self.max_age_seconds = max_age_seconds
        self._items: OrderedDict[str, StoredMedia] = OrderedDict()
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = threading.RLock()
        self._closed = False
        atexit.register(self.close)

    def save(self, upload, filename: str, content_type: str) -> StoredMedia:
        token = secrets.token_urlsafe(12)
        display_name = _clean_filename(filename)
        suffix = Path(display_name).suffix.lower()
        if len(suffix) > 8 or not suffix[1:].isalnum():
            suffix = ""

        path = self.root / f"{token}{suffix}"
        upload.save(path)
        size = path.stat().st_size

        if size == 0:
            path.unlink(missing_ok=True)
            raise MediaError("That file is empty.")

        try:
            info = inspect_media(path, display_name, content_type)
        except Exception:
            path.unlink(missing_ok=True)
            raise

        record = StoredMedia(
            token=token,
            path=path,
            name=display_name,
            size=size,
            info=info,
            created_at=time.time(),
        )

        with self._lock:
            self._purge_locked()
            while len(self._items) >= self.max_items:
                old_token, old = self._items.popitem(last=False)
                old.path.unlink(missing_ok=True)
                self._drop_cache_locked(old_token)
            self._items[token] = record

        return record

    def get(self, token: str) -> StoredMedia | None:
        with self._lock:
            self._purge_locked()
            record = self._items.get(token)
            if record is None:
                return None
            self._items.move_to_end(token)
            return record

    def cached(self, key: tuple) -> dict | None:
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def remember(self, key: tuple, value: dict) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_items:
                self._cache.popitem(last=False)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._items.clear()
            self._cache.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def _purge_locked(self) -> None:
        cutoff = time.time() - self.max_age_seconds
        expired = [
            token
            for token, record in self._items.items()
            if record.created_at < cutoff
        ]
        for token in expired:
            record = self._items.pop(token)
            record.path.unlink(missing_ok=True)
            self._drop_cache_locked(token)

    def _drop_cache_locked(self, token: str) -> None:
        for key in [key for key in self._cache if key[0] == token]:
            self._cache.pop(key, None)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        MAX_FRAMES=MAX_FRAMES,
        MAX_SECONDS=MAX_SECONDS,
        MAX_UPLOADS=6,
        CACHE_ITEMS=8,
        MAX_AGE_SECONDS=3600,
        PUBLIC_DEPLOYMENT=_env_flag("GLYPH_PUBLIC"),
    )
    if test_config:
        app.config.update(test_config)

    app.json.ensure_ascii = False
    store = MediaStore(
        max_items=app.config["MAX_UPLOADS"],
        cache_items=app.config["CACHE_ITEMS"],
        max_age_seconds=app.config["MAX_AGE_SECONDS"],
    )
    app.extensions["glyph_store"] = store

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            version=VERSION,
            max_upload_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        )

    @app.post("/api/upload")
    def upload():
        upload_file = request.files.get("file")
        if upload_file is None or not upload_file.filename:
            return jsonify(error="No file was attached."), 400

        try:
            record = store.save(
                upload_file,
                upload_file.filename,
                upload_file.mimetype or "",
            )
        except MediaError as exc:
            return jsonify(error=str(exc)), 422
        except Exception:
            app.logger.exception("Upload failed for %s", upload_file.filename)
            return jsonify(error="Couldn't read that file. Check the terminal for details."), 500

        return jsonify(token=record.token, source=record.public())

    @app.post("/api/convert")
    def convert():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON body."), 400

        token = payload.get("token")
        record = store.get(token) if isinstance(token, str) else None
        if record is None:
            return jsonify(error="That upload expired. Drop the file again."), 404

        options = _parse_options(payload.get("options"))
        cache_key = (record.token, options)
        cached = store.cached(cache_key)
        if cached is not None:
            return jsonify(cached)

        started = time.perf_counter()
        try:
            result = convert_media(
                record.path,
                record.info.kind,
                options,
                max_frames=app.config["MAX_FRAMES"],
                max_seconds=app.config["MAX_SECONDS"],
            )
        except MediaError as exc:
            return jsonify(error=str(exc)), 422
        except Exception:
            app.logger.exception("Conversion failed for %s", record.name)
            return jsonify(error="Conversion failed. Check the terminal for details."), 500

        response = {
            "columns": result.columns,
            "rows": result.rows,
            "frames": result.frames,
            "delays": result.delays,
            "trimmed": result.trimmed,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "source": record.public(),
        }
        store.remember(cache_key, response)
        return jsonify(response)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return jsonify(error=f"That file is over {limit_mb} MB."), 413

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return app


def _parse_options(raw) -> AsciiOptions:
    if not isinstance(raw, dict):
        raw = {}

    ramp = raw.get("ramp")
    if not isinstance(ramp, str) or ramp not in RAMPS:
        ramp = "classic"

    return AsciiOptions(
        width=_clamp_int(raw.get("width"), 100, 24, 240),
        ramp=ramp,
        invert=raw.get("invert") is True,
        brightness=round(_clamp_float(raw.get("brightness"), 1.0, 0.5, 1.6), 2),
        contrast=round(_clamp_float(raw.get("contrast"), 1.0, 0.5, 2.0), 2),
        fps=_clamp_int(raw.get("fps"), 12, 4, 24),
    )


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_float(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(low, min(high, number))


def _clean_filename(filename: str) -> str:
    name = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character.isprintable())
    return (name.strip() or "upload")[:120]


def _browser_host(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def main() -> None:
    parser = argparse.ArgumentParser(description="Glyph - local media to ASCII")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "5000")),
        help="port (default: 5000 or $PORT)",
    )
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--debug", action="store_true", help="enable Flask debug mode")
    args = parser.parse_args()

    app = create_app()
    url = f"http://{_browser_host(args.host)}:{args.port}"

    print(f"\n  Glyph {VERSION}  |  {url}\n  Ctrl+C to stop.\n")

    if not args.no_browser:
        timer = threading.Timer(1.2, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()

    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            use_reloader=False,
        )
    except OSError as exc:
        print(f"Couldn't start the server: {exc}")
        print(f"Try a different port: python app.py --port {args.port + 1}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
