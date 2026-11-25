"""Připraví pro okno Windows verze stejné ikony a malby jako má aplikace pro Android TV.

- ``res/drawable/ic_*.xml`` (vektorové ikony Androidu) → ``ui/assets/ic_*.svg`` (barva ``currentColor``),
- ``res/drawable-nodpi/*.webp|png`` (malby dlaždic, avatary, pozadí) → ``ui/assets/`` beze změny,
- ``ui/assets/icon.png``: ikona programu (kruh s trojúhelníkem z loga na papírovém podkladu),
- ``ui/assets/manifest.json``: seznam názvů, ať rozhraní ví, co je k dispozici.

Zdroj je jen jeden (aplikace pro Android); složka ``ui/assets`` se vytváří při sestavení a do
repozitáře nepatří. Spuštění: ``python3 windows/build_assets.py``.
"""

import json
import os
import shutil
import sys
import xml.etree.ElementTree as ElementTree

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(HERE), "android", "app", "src", "main", "res")
OUT = os.path.join(HERE, "ui", "assets")
ANDROID = "{http://schemas.android.com/apk/res/android}"


def _attr(node, name, default=None):
    return node.get(ANDROID + name, default)


def vector_to_svg(source):
    """Vektorová ikona Androidu jako SVG; ``None``, když to není ``<vector>``."""
    root = ElementTree.parse(source).getroot()
    if root.tag != "vector":
        return None
    width = _attr(root, "viewportWidth", "24")
    height = _attr(root, "viewportHeight", "24")
    parts = []

    def walk(node, transform=""):
        for child in node:
            if child.tag == "group":
                moves = []
                if _attr(child, "translateX") or _attr(child, "translateY"):
                    moves.append("translate(%s %s)" % (_attr(child, "translateX", "0"), _attr(child, "translateY", "0")))
                if _attr(child, "rotation"):
                    moves.append("rotate(%s %s %s)" % (_attr(child, "rotation"), _attr(child, "pivotX", "0"),
                                                       _attr(child, "pivotY", "0")))
                if _attr(child, "scaleX") or _attr(child, "scaleY"):
                    moves.append("scale(%s %s)" % (_attr(child, "scaleX", "1"), _attr(child, "scaleY", "1")))
                parts.append('<g transform="%s">' % " ".join(moves) if moves else "<g>")
                walk(child)
                parts.append("</g>")
            elif child.tag == "path":
                data = _attr(child, "pathData", "")
                fill = "currentColor" if _attr(child, "fillColor") else "none"
                stroke = "currentColor" if _attr(child, "strokeColor") else "none"
                extra = ""
                if stroke != "none":
                    extra = ' stroke-width="%s" stroke-linecap="%s" stroke-linejoin="%s"' % (
                        _attr(child, "strokeWidth", "1"), _attr(child, "strokeLineCap", "butt"),
                        _attr(child, "strokeLineJoin", "miter"))
                if _attr(child, "fillAlpha"):
                    extra += ' fill-opacity="%s"' % _attr(child, "fillAlpha")
                if _attr(child, "fillType") == "evenOdd":
                    extra += ' fill-rule="evenodd"'
                parts.append('<path d="%s" fill="%s" stroke="%s"%s/>' % (data, fill, stroke, extra))

    walk(root)
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s">%s</svg>\n' % (width, height, "".join(parts))


def build(out=OUT):
    os.makedirs(out, exist_ok=True)
    names = {"icons": [], "art": []}
    for name in sorted(os.listdir(os.path.join(RES, "drawable"))):
        if name.startswith("ic_") and name.endswith(".xml"):
            svg = vector_to_svg(os.path.join(RES, "drawable", name))
            if svg:
                with open(os.path.join(out, name[:-4] + ".svg"), "w", encoding="utf-8") as target:
                    target.write(svg)
                names["icons"].append(name[:-4])
    for name in sorted(os.listdir(os.path.join(RES, "drawable-nodpi"))):
        if name.endswith((".webp", ".png")):
            shutil.copyfile(os.path.join(RES, "drawable-nodpi", name), os.path.join(out, name))
            names["art"].append(name)
    app_icon(os.path.join(out, "icon.png"))
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as target:
        json.dump(names, target, ensure_ascii=False)
    return names


ICON_SIZE = 256
ICON_PAPER = (227, 219, 200, 255)   # @color/panel


def app_icon(target):
    """Ikona programu: znak z loga (kruh s červeným trojúhelníkem, bez nápisu – ten je v malé ikoně
    nečitelný) na zaobleném papírovém čtverci, jako ikona aplikace na televizi. Bez knihovny Pillow
    se použije celé logo."""
    logo = os.path.join(RES, "drawable-nodpi", "logo.png")
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        shutil.copyfile(logo, target)
        return False
    source = Image.open(logo).convert("RGBA")
    alpha = source.split()[3]
    columns = [x for x in range(source.width) if alpha.crop((x, 0, x + 1, source.height)).getextrema()[1] > 20]
    end = next((b for a, b in zip(columns, columns[1:]) if b - a > 5), None)   # mezera mezi znakem a nápisem
    end = columns[columns.index(end) - 1] + 1 if end else source.width
    mark = source.crop((0, 0, end, source.height))                     # znak vlevo, bez nápisu
    mark = mark.crop(mark.getbbox() or (0, 0, mark.width, mark.height))
    icon = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(icon).rounded_rectangle((8, 8, ICON_SIZE - 9, ICON_SIZE - 9), radius=52, fill=ICON_PAPER)
    side = int(ICON_SIZE * 0.74)
    scale = float(side) / max(mark.width, mark.height)
    mark = mark.resize((int(mark.width * scale), int(mark.height * scale)), Image.LANCZOS)
    icon.alpha_composite(mark, ((ICON_SIZE - mark.width) // 2, (ICON_SIZE - mark.height) // 2))
    icon.save(target)
    return True


if __name__ == "__main__":
    found = build(sys.argv[1] if len(sys.argv) > 1 else OUT)
    print("ikon %d, maleb %d" % (len(found["icons"]), len(found["art"])))
