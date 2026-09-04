from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> int:
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 252, 252), radius=60, fill="#101512")
    draw.ellipse((48, 48, 208, 208), outline="#caff00", width=16)
    draw.ellipse((88, 88, 168, 168), outline="#caff00", width=12)
    draw.line((128, 128, 210, 62), fill="#f4f7f5", width=16)
    draw.ellipse((114, 114, 142, 142), fill="#caff00")
    target = Path(__file__).resolve().parents[1] / "src" / "clipradar" / "resources" / "clipradar.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Generated {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
