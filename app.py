import os
import re
import json
import uuid
import threading
import subprocess
import static_ffmpeg

from flask import Flask, render_template, request, jsonify, send_from_directory

# =========================
# INIT
# =========================
static_ffmpeg.add_paths()

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =========================
# COOKIES AUTO MODE
# =========================
COOKIE_FILE = "/tmp/cookies.txt"

cookie_text = os.getenv("youtube_cookies")

if cookie_text:
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(cookie_text)
else:
    local_cookie = os.path.join(BASE_DIR, "cookies.txt")
    if os.path.exists(local_cookie):
        COOKIE_FILE = local_cookie

# =========================
# DOWNLOAD DIR
# =========================
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

download_progress = {}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# =========================
# HELPERS
# =========================
def build_base_cmd():
    cmd = [
        "yt-dlp",
        "--newline",
        "--no-playlist",
        "--user-agent", USER_AGENT,
        "--extractor-retries", "5",
        "--socket-timeout", "30",
        "--no-warnings"
    ]

    if os.path.exists(COOKIE_FILE):
        cmd += ["--cookies", COOKIE_FILE]

    return cmd


# =========================
# DOWNLOAD THREAD
# =========================
def run_download(task_id, url, quality, audio_only):
    try:
        download_progress[task_id] = {
            "status": "starting",
            "percent": 0,
            "title": "",
            "speed": "",
            "eta": "",
            "error": ""
        }

        cmd = build_base_cmd()

        if audio_only:
            cmd += [
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0"
            ]
        else:
            if quality == "1080":
                cmd += ["-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"]
            elif quality == "720":
                cmd += ["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]"]
            elif quality == "480":
                cmd += ["-f", "bestvideo[height<=480]+bestaudio/best[height<=480]"]
            else:
                cmd += ["-f", "bestvideo+bestaudio/best"]

            cmd += ["--merge-output-format", "mp4"]

        output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")
        cmd += ["-o", output_template, url]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )

        for raw_line in process.stdout:
            line = raw_line.strip()

            if "Destination:" in line:
                filename = line.split("Destination:")[-1].strip()
                title = os.path.basename(filename)
                download_progress[task_id]["title"] = title

            percent_match = re.search(r'(\d+\.?\d*)%', line)

            if percent_match and "[download]" in line:
                download_progress[task_id]["percent"] = float(percent_match.group(1))
                download_progress[task_id]["status"] = "downloading"

                speed_match = re.search(r'at\s+([^\s]+)', line)
                if speed_match:
                    download_progress[task_id]["speed"] = speed_match.group(1)

                eta_match = re.search(r'ETA\s+([^\s]+)', line)
                if eta_match:
                    download_progress[task_id]["eta"] = eta_match.group(1)

            if "Merging formats" in line or "[Merger]" in line:
                download_progress[task_id]["status"] = "merging"
                download_progress[task_id]["percent"] = 99

        process.wait()

        if process.returncode == 0:
            download_progress[task_id]["status"] = "done"
            download_progress[task_id]["percent"] = 100
        else:
            download_progress[task_id]["status"] = "error"
            download_progress[task_id]["error"] = "Download thất bại."

    except Exception as e:
        download_progress[task_id]["status"] = "error"
        download_progress[task_id]["error"] = str(e)


# =========================
# ROUTES
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URL không hợp lệ"}), 400

    try:
        cmd = build_base_cmd()
        cmd += ["--dump-json", url]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=40
        )

        if result.returncode != 0:
            return jsonify({"error": "Không thể lấy thông tin video."}), 400

        info = json.loads(result.stdout)

        return jsonify({
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration_string", "N/A"),
            "uploader": info.get("uploader", "Unknown"),
            "thumbnail": info.get("thumbnail", ""),
            "view_count": f"{info.get('view_count', 0):,}",
            "platform": info.get("extractor_key", "Unknown")
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout. Thử lại."}), 408

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json()

    url = data.get("url", "").strip()
    quality = data.get("quality", "best")
    audio_only = data.get("audio_only", False)

    if not url:
        return jsonify({"error": "URL không hợp lệ"}), 400

    task_id = str(uuid.uuid4())[:8]

    thread = threading.Thread(
        target=run_download,
        args=(task_id, url, quality, audio_only),
        daemon=True
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/api/progress/<task_id>")
def progress(task_id):
    return jsonify(download_progress.get(task_id, {"status": "not_found"}))


@app.route("/api/files")
def files():
    result = []

    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)

        if os.path.isfile(path):
            size = os.path.getsize(path)

            result.append({
                "name": f,
                "size": f"{size / (1024 * 1024):.1f} MB"
            })

    return jsonify(sorted(result, key=lambda x: x["name"], reverse=True))


@app.route("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(
        DOWNLOAD_DIR,
        filename,
        as_attachment=True
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "cookies_loaded": os.path.exists(COOKIE_FILE),
        "cookie_path": COOKIE_FILE,
        "download_dir": DOWNLOAD_DIR
    })


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)