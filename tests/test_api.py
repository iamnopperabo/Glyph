from PIL import Image


def test_index_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"GLYPH" in response.data


def test_upload_and_convert_png(client, upload, png_bytes):
    response = upload(png_bytes)
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["source"]["kind"] == "image"
    assert payload["source"]["width"] == 160
    assert payload["source"]["height"] == 90

    response = client.post(
        "/api/convert",
        json={
            "token": payload["token"],
            "options": {"width": 40, "ramp": "classic"},
        },
    )
    assert response.status_code == 200

    result = response.get_json()
    assert result["columns"] == 40
    assert result["rows"] == 12
    assert len(result["frames"]) == 1
    assert result["delays"] == []
    assert result["trimmed"] is False

    lines = result["frames"][0].splitlines()
    assert len(lines) == 12
    assert all(len(line) <= 40 for line in lines)
    assert all(line == line.rstrip() for line in lines)


def test_invert_changes_the_output(client, upload, png_bytes):
    token = upload(png_bytes).get_json()["token"]
    normal = client.post(
        "/api/convert",
        json={"token": token, "options": {"width": 30}},
    ).get_json()["frames"][0]
    inverted = client.post(
        "/api/convert",
        json={"token": token, "options": {"width": 30, "invert": True}},
    ).get_json()["frames"][0]

    assert normal != inverted
    assert normal.splitlines()[0] != inverted.splitlines()[0]


def test_animated_gif_keeps_frame_delays(client, upload, gif_bytes):
    response = upload(gif_bytes, "test.gif", "image/gif")
    payload = response.get_json()
    assert payload["source"]["kind"] == "animation"
    assert payload["source"]["frames"] == 4

    result = client.post(
        "/api/convert",
        json={"token": payload["token"], "options": {"width": 30}},
    ).get_json()

    assert len(result["frames"]) == 4
    assert result["delays"] == [100, 100, 100, 100]


def test_blocks_ramp_returns_unicode(client, upload, png_bytes):
    token = upload(png_bytes).get_json()["token"]
    response = client.post(
        "/api/convert",
        json={"token": token, "options": {"width": 30, "ramp": "blocks"}},
    )

    assert response.status_code == 200
    frame = response.get_json()["frames"][0]
    assert any(character in frame for character in "█▓▒░")


def test_width_is_clamped(client, upload, png_bytes):
    token = upload(png_bytes).get_json()["token"]
    result = client.post(
        "/api/convert",
        json={"token": token, "options": {"width": 9999}},
    ).get_json()
    assert result["columns"] == 240


def test_unknown_upload_token(client):
    response = client.post(
        "/api/convert",
        json={"token": "not-a-real-token", "options": {}},
    )
    assert response.status_code == 404


def test_rejects_text_file(upload):
    response = upload(b"this is not media", "notes.txt", "text/plain")
    assert response.status_code == 422
    assert "Unsupported file" in response.get_json()["error"]


def test_uploaded_image_is_readable(client, upload, png_bytes):
    token = upload(png_bytes).get_json()["token"]
    record = client.application.extensions["glyph_store"].get(token)
    assert record is not None

    with Image.open(record.path) as image:
        assert image.size == (160, 90)
