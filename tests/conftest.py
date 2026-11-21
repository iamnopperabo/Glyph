import io

import pytest
from PIL import Image, ImageDraw

from app import create_app


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "MAX_UPLOADS": 3,
            "CACHE_ITEMS": 3,
        }
    )
    yield application
    application.extensions["glyph_store"].close()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def upload(client):
    def _upload(data, filename="test.png", content_type="image/png"):
        return client.post(
            "/api/upload",
            data={"file": (io.BytesIO(data), filename)},
            content_type="multipart/form-data",
        )

    return _upload


@pytest.fixture()
def png_bytes():
    image = Image.new("RGB", (160, 90), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 5, 140, 120), fill="white")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture()
def gif_bytes():
    frames = []
    for index in range(4):
        image = Image.new("RGB", (120, 80), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10 + index * 12, 20, 40 + index * 12, 60), fill="white")
        frames.append(image)

    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return buffer.getvalue()
