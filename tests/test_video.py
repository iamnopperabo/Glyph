import subprocess

import imageio_ffmpeg
import pytest


def make_video(path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x90:rate=12:duration=1",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)


def test_upload_and_convert_mp4(client, upload, tmp_path):
    path = tmp_path / "clip.mp4"
    try:
        make_video(path)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg could not generate the test clip")

    with path.open("rb") as video:
        response = upload(video.read(), "clip.mp4", "video/mp4")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"]["kind"] == "video"
    assert payload["source"]["width"] == 160
    assert payload["source"]["height"] == 90

    result = client.post(
        "/api/convert",
        json={
            "token": payload["token"],
            "options": {"width": 30, "fps": 6},
        },
    ).get_json()

    assert result["columns"] == 30
    assert 4 <= len(result["frames"]) <= 7
    assert len(result["delays"]) == len(result["frames"])
    assert result["delays"][0] > 100
