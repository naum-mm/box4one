from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT_DIR = Path(
    "/opt/ytdlp-web/frontend"
)


def get_font(
    size: int,
):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    for candidate in candidates:

        path = Path(
            candidate
        )

        if path.exists():
            return ImageFont.truetype(
                str(path),
                size=size,
            )

    return ImageFont.load_default()


def make_icon(
    size: int,
    filename: str,
):
    scale = size / 512

    image = Image.new(
        "RGB",
        (
            size,
            size,
        ),
        (
            9,
            9,
            11,
        ),
    )

    draw = ImageDraw.Draw(
        image
    )

    margin = int(
        42 * scale
    )

    radius = int(
        105 * scale
    )


    # Subtle inner surface
    draw.rounded_rectangle(
        (
            margin,
            margin,
            size - margin,
            size - margin,
        ),
        radius=radius,
        fill=(
            20,
            20,
            23,
        ),
    )


    # Purple accent
    draw.rounded_rectangle(
        (
            int(92 * scale),
            int(370 * scale),
            int(260 * scale),
            int(397 * scale),
        ),
        radius=int(
            14 * scale
        ),
        fill=(
            138,
            92,
            255,
        ),
    )


    # Yellow accent
    draw.rounded_rectangle(
        (
            int(252 * scale),
            int(370 * scale),
            int(420 * scale),
            int(397 * scale),
        ),
        radius=int(
            14 * scale
        ),
        fill=(
            255,
            216,
            74,
        ),
    )


    font = get_font(
        int(
            270 * scale
        )
    )


    text = "4"


    bbox = draw.textbbox(
        (
            0,
            0,
        ),
        text,
        font=font,
    )


    text_width = (
        bbox[2]
        - bbox[0]
    )

    text_height = (
        bbox[3]
        - bbox[1]
    )


    x = (
        size
        - text_width
    ) / 2


    y = (
        size
        - text_height
    ) / 2 - int(
        34 * scale
    )


    draw.text(
        (
            x,
            y,
        ),
        text,
        font=font,
        fill=(
            246,
            246,
            247,
        ),
    )


    image.save(
        OUTPUT_DIR
        / filename,
        format="PNG",
        optimize=True,
    )


make_icon(
    180,
    "apple-touch-icon.png",
)

make_icon(
    192,
    "icon-192.png",
)

make_icon(
    512,
    "icon-512.png",
)


print(
    "BOX4ONE icons created"
)
