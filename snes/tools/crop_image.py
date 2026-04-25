#!/usr/bin/env python3
"""
Scale and crop an image to a fixed size and save it as PNG, optionally
reducing it to a fixed-size palette (2bpp = 4 colors, 4bpp = 16 colors,
8bpp = 256 colors).

The source image is scaled (preserving aspect ratio) so that the target
rectangle is fully covered by image content, then a crop of the target
size is taken. The horizontal crop anchor can be chosen (left / center /
right). Vertical cropping is always centered. This maximises how much of
the source image ends up inside the crop while completely filling the
target frame (no bars).

Default target size is 512x448 (SNES 8:7 full-resolution aspect ratio).

Usage:
    python tools/crop_image.py INPUT [-o OUTPUT] [-W WIDTH] [-H HEIGHT]
                               [-a {left,center,right}] [-b {2,4,8}]

Examples:
    python tools/crop_image.py assets/linux_wallpaper.jpg
    python tools/crop_image.py photo.jpg -a right -b 2
    python tools/crop_image.py photo.jpg -o out.png -W 256 -H 224 -b 4
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 448
ALIGN_CHOICES = ("left", "center", "right")
BPP_CHOICES = (2, 4, 8)


def scale_and_crop(
    src: Image.Image,
    target_w: int,
    target_h: int,
    align: str = "center",
) -> Image.Image:
    """Scale `src` to cover (target_w, target_h) and crop with the given
    horizontal alignment (``left``/``center``/``right``). Vertical crop is
    always centered."""
    if target_w <= 0 or target_h <= 0:
        raise ValueError("Target dimensions must be positive")
    if align not in ALIGN_CHOICES:
        raise ValueError(f"align must be one of {ALIGN_CHOICES}, got {align!r}")

    src_w, src_h = src.size
    scale = max(target_w / src_w, target_h / src_h)

    new_w = max(target_w, round(src_w * scale))
    new_h = max(target_h, round(src_h * scale))

    scaled = src.resize((new_w, new_h), Image.LANCZOS)

    if align == "left":
        left = 0
    elif align == "right":
        left = new_w - target_w
    else:
        left = (new_w - target_w) // 2

    top = (new_h - target_h) // 2
    return scaled.crop((left, top, left + target_w, top + target_h))


def reduce_palette(img: Image.Image, bpp: int) -> Image.Image:
    """Quantise `img` to a palette image with 2**bpp colors."""
    if bpp not in BPP_CHOICES:
        raise ValueError(f"bpp must be one of {BPP_CHOICES}, got {bpp!r}")
    colors = 1 << bpp
    return img.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)


def process(
    input_path: Path,
    output_path: Path,
    target_w: int = DEFAULT_WIDTH,
    target_h: int = DEFAULT_HEIGHT,
    align: str = "center",
    bpp: int | None = None,
) -> None:
    with Image.open(input_path) as im:
        im = im.convert("RGB")
        cropped = scale_and_crop(im, target_w, target_h, align)
        if bpp is not None:
            cropped = reduce_palette(cropped, bpp)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(output_path, format="PNG")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scale a JPG (or other image) so a WxH crop contains as much "
            "of the image as possible, and save the crop as PNG. Optionally "
            "quantise to a 2bpp/4bpp/8bpp palette."
        )
    )
    parser.add_argument("input", type=Path, help="Path to input image (e.g. .jpg)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output PNG path (default: "
            "<input stem>_<W>x<H>[_<align>][_<bpp>bpp].png next to input)"
        ),
    )
    parser.add_argument(
        "-W", "--width", type=int, default=DEFAULT_WIDTH, help="Target width in pixels"
    )
    parser.add_argument(
        "-H",
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help="Target height in pixels",
    )
    parser.add_argument(
        "-a",
        "--align",
        choices=ALIGN_CHOICES,
        default="center",
        help="Horizontal crop anchor (default: center)",
    )
    parser.add_argument(
        "-b",
        "--bpp",
        type=int,
        choices=BPP_CHOICES,
        default=None,
        help=(
            "Optional palette bit depth: 2 (4 colors), 4 (16 colors), "
            "8 (256 colors). Omit for full-color output."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.is_file():
        raise SystemExit(f"Input file not found: {args.input}")

    output = args.output
    if output is None:
        align_tag = "" if args.align == "center" else f"_{args.align}"
        bpp_tag = "" if args.bpp is None else f"_{args.bpp}bpp"
        output = args.input.with_name(
            f"{args.input.stem}_{args.width}x{args.height}{align_tag}{bpp_tag}.png"
        )

    process(args.input, output, args.width, args.height, args.align, args.bpp)
    bpp_info = "full color" if args.bpp is None else f"{args.bpp}bpp ({1 << args.bpp} colors)"
    print(f"Wrote {output} ({args.width}x{args.height}, align={args.align}, {bpp_info})")


if __name__ == "__main__":
    main()
