#!/usr/bin/env python3
"""Složí ze znaku aplikace ikonu, banner pro Android TV a logo na hlavní obrazovku.

    python3 android/tools/make_brand.py <Znak.png> <písmo.ttf> [název] [android/app/src/main/res]

Znak je kruh tuší s trojúhelníkem „přehrát“ uprostřed na čistě bílém pozadí (čtverec). Trojúhelník se
obarví rumělkou, znak se položí na barvu papíru a vedle něj se vysází název. Písmo je tenký bezpatkový
řez (v aplikaci Roboto Light); soubor písma se do repozitáře neukládá. Potřebuje knihovnu Pillow.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

from import_graphics import ACCENT, PAPER, ink

INK = (0x1D, 0x1B, 0x18)
CORE = 0.26   # podíl šířky znaku od středu, kde leží trojúhelník (kruh je dál)


def sign(path):
    """Znak na průhledném pozadí, oříznutý na motiv; trojúhelník uprostřed rumělkou, kruh tuší."""
    image = ink(Image.open(path))
    image = image.crop(image.getchannel("A").point([255 if value > 24 else 0 for value in range(256)]).getbbox())
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    alpha = square.getchannel("A")
    core = Image.new("L", square.size, 0)
    reach = int(side * CORE)
    ImageDraw.Draw(core).ellipse((side // 2 - reach, side // 2 - reach, side // 2 + reach, side // 2 + reach), fill=255)
    painted = Image.new("RGBA", square.size, INK + (0,))
    painted.paste(Image.new("RGBA", square.size, ACCENT + (0,)), (0, 0), core)
    painted.putalpha(alpha)
    return painted


def sheet(size):
    corners = Image.new("RGB", (2, 2))
    middle = tuple((a + b) // 2 for a, b in zip(*PAPER))
    corners.putdata([PAPER[0], middle, middle, PAPER[1]])
    return corners.resize(size, Image.BILINEAR).convert("RGBA")


def lettering(name, font_path, height):
    """Název jako obrázek na průhledném pozadí; ``height`` je výška písma v bodech obrázku."""
    font = ImageFont.truetype(font_path, height)
    left, top, right, bottom = font.getbbox(name)
    image = Image.new("RGBA", (right - left + 4, bottom - top + 4), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((2 - left, 2 - top), name, font=font, fill=INK + (255,))
    return image


def beside(mark, text, gap):
    """Znak a vedle něj název, svisle na střed."""
    row = Image.new("RGBA", (mark.width + gap + text.width, max(mark.height, text.height)), (0, 0, 0, 0))
    row.alpha_composite(mark, (0, (row.height - mark.height) // 2))
    row.alpha_composite(text, (mark.width + gap, (row.height - text.height) // 2))
    return row


def main(source, font_path, name, res):
    mark = sign(source)
    # ikona: znak na papíře
    icon = sheet((512, 512))
    small = mark.resize((400, 400), Image.LANCZOS)
    icon.alpha_composite(small, (56, 56))
    icon.convert("RGB").resize((192, 192), Image.LANCZOS).save(
        os.path.join(res, "mipmap-xhdpi", "ic_launcher.png"), optimize=True)
    # banner: znak a název na papíře (kreslí se ve dvojnásobku a zmenšuje)
    banner = sheet((1280, 720))
    row = beside(mark.resize((300, 300), Image.LANCZOS), lettering(name, font_path, 150), 56)
    if row.width > 1140:
        row = row.resize((1140, row.height * 1140 // row.width), Image.LANCZOS)
    banner.alpha_composite(row, ((1280 - row.width) // 2, (720 - row.height) // 2))
    banner = banner.convert("RGB")
    banner.resize((640, 360), Image.LANCZOS).save(os.path.join(res, "drawable-xxxhdpi", "banner.png"), optimize=True)
    banner.resize((320, 180), Image.LANCZOS).save(os.path.join(res, "drawable-xhdpi", "banner.png"), optimize=True)
    # logo na hlavní obrazovce: průhledné, výška 160, zarovnané vpravo
    row = beside(mark.resize((150, 150), Image.LANCZOS), lettering(name, font_path, 92), 26)
    logo = Image.new("RGBA", (max(730, row.width + 10), 160), (0, 0, 0, 0))
    logo.alpha_composite(row, (logo.width - row.width - 6, (160 - row.height) // 2))
    logo.save(os.path.join(res, "drawable-nodpi", "logo.png"), optimize=True)
    print("Složeno: ikona, banner, logo (%s)" % name)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "Promítárna",
         sys.argv[4] if len(sys.argv) > 4 else os.path.join(here, "..", "app", "src", "main", "res"))
