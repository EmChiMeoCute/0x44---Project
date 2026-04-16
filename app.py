import os
import re
import json
import threading
import subprocess
import static_ffmpeg
static_ffmpeg.add_paths()
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Track download progress
download_progress = {}


def run_download(task_id, url, quality, audio_only):
    try:
        download_progress[task_id] = {"status": "starting", "percent": 0, "title": "", "speed": "", "eta": ""}

        # Build yt-dlp command
        cmd = ["yt-dlp", "--no-playlist", "--newline"]

        if audio_only:
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            if quality == "best":
                cmd += ["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]
            elif quality == "1080":
                cmd += ["-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"]
            elif quality == "720":
                cmd += ["-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"]
            elif quality == "480":
                cmd += ["-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]"]
            else:
                cmd += ["-f", "best"]

        cmd += [
            "--merge-output-format", "mp4",
            "-o", os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
            url
        ]

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1
        )

        for line in process.stdout:
            line = line.strip()

            # Parse title
            if "[download] Destination:" in line:
                filename = line.split("Destination:")[-1].strip()
                title = os.path.basename(filename)
                download_progress[task_id]["title"] = title

            # Parse progress
            percent_match = re.search(r'(\d+\.?\d*)%', line)
            if percent_match and "[download]" in line:
                percent = float(percent_match.group(1))
                download_progress[task_id]["percent"] = percent

                speed_match = re.search(r'at\s+([\d.]+\w+/s)', line)
                if speed_match:
                    download_progress[task_id]["speed"] = speed_match.group(1)

                eta_match = re.search(r'ETA\s+(\S+)', line)
                if eta_match:
                    download_progress[task_id]["eta"] = eta_match.group(1)

                download_progress[task_id]["status"] = "downloading"

            # Detect merging
            if "[Merger]" in line or "Merging" in line:
                download_progress[task_id]["status"] = "merging"
                download_progress[task_id]["percent"] = 99

        process.wait()
        if process.returncode == 0:
            download_progress[task_id]["status"] = "done"
            download_progress[task_id]["percent"] = 100
        else:
            download_progress[task_id]["status"] = "error"
            download_progress[task_id]["error"] = "Tải thất bại. Kiểm tra URL hoặc cài yt-dlp."

    except FileNotFoundError:
        download_progress[task_id]["status"] = "error"
        download_progress[task_id]["error"] = "yt-dlp chưa được cài đặt. Chạy: pip install yt-dlp"
    except Exception as e:
        download_progress[task_id]["status"] = "error"
        download_progress[task_id]["error"] = str(e)


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
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return jsonify({"error": "Không thể lấy thông tin video. Kiểm tra URL."}), 400

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
        return jsonify({"error": "Quá thời gian. Thử lại sau."}), 408
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

    import uuid
    task_id = str(uuid.uuid4())[:8]
    thread = threading.Thread(target=run_download, args=(task_id, url, quality, audio_only))
    thread.daemon = True
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/api/progress/<task_id>")
def get_progress(task_id):
    progress = download_progress.get(task_id, {"status": "not_found"})
    return jsonify(progress)


@app.route("/api/files")
def list_files():
    files = []
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        if os.path.isfile(path):
            size = os.path.getsize(path)
            files.append({
                "name": f,
                "size": f"{size / (1024*1024):.1f} MB"
            })
    return jsonify(sorted(files, key=lambda x: x["name"]))


@app.route("/downloads/<filename>")
def serve_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)