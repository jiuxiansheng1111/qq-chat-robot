import base64
from io import BytesIO

from PIL import Image

import app.main as main_module


def _image_b64(image: Image.Image, fmt: str = "PNG") -> str:
    output = BytesIO()
    image.save(output, format=fmt)
    return "base64://" + base64.b64encode(output.getvalue()).decode()


def test_ultraman_payload_rejects_solid_black_image():
    black = Image.new("RGB", (900, 1200), (0, 0, 0))
    assert not main_module._ultraman_image_payload_usable(_image_b64(black))


def test_ultraman_payload_rejects_near_black_image_with_small_noisy_strip():
    image = Image.new("RGB", (900, 1200), (0, 0, 0))
    pixels = image.load()
    for y in range(72):
        for x in range(900):
            pixels[x, y] = (
                (x * 7 + y * 3) % 256,
                (x * 11 + y * 5) % 256,
                (x * 13 + y * 17) % 256,
            )
    assert not main_module._ultraman_image_payload_usable(_image_b64(image))


def test_ultraman_payload_accepts_realistic_nonuniform_image():
    image = Image.new("RGB", (900, 1200), (30, 40, 70))
    pixels = image.load()
    for y in range(1200):
        for x in range(900):
            pixels[x, y] = (
                (x * 3 + y) % 256,
                (x + y * 2) % 256,
                (x * 2 + y * 3) % 256,
            )
    assert main_module._ultraman_image_payload_usable(_image_b64(image))


def test_qq_safe_variant_is_baseline_rgb_jpeg():
    source = Image.new("RGBA", (1600, 900), (120, 50, 210, 120))
    normalized = main_module._qq_safe_image_variant(_image_b64(source))
    assert normalized is not None
    raw = base64.b64decode(normalized.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.mode == "RGB"
        assert decoded.width <= 1280
        assert decoded.height <= 1280


def test_qq_safe_variant_flattens_transparency_without_black_background():
    source = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    source.paste((220, 40, 40, 255), (120, 80, 280, 340))
    normalized = main_module._qq_safe_image_variant(_image_b64(source))
    assert normalized is not None
    raw = base64.b64decode(normalized.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as decoded:
        corner = decoded.convert("RGB").getpixel((10, 10))
        assert min(corner) > 220
