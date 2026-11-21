# -*- coding: utf-8 -*-
"""Sestaví Kodi repozitář do složky repo/ (servíruje ho GitHub z větve main):

    repo/addons.xml, repo/addons.xml.md5
    repo/zips/<id>/<id>-<verze>.zip (+ icon.png, fanart.jpg)

    python3 tools/build_zip.py
"""
import hashlib
import os
import re
import shutil
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ADDONS = ["repository.petermikula", "plugin.video.streamujtv", "plugin.video.prehrajto.mojevidea"]
REPO = os.path.join(ROOT, "repo")
ZIPS = os.path.join(REPO, "zips")


def read_addon_xml(addon_id):
    with open(os.path.join(ROOT, addon_id, "addon.xml"), encoding="utf-8") as f:
        return f.read()


def build(addon_id):
    addon_dir = os.path.join(ROOT, addon_id)
    version = re.search(r'<addon[^>]*\bversion="([^"]+)"', read_addon_xml(addon_id)).group(1)
    out_dir = os.path.join(ZIPS, addon_id)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)  # jen složka tohoto doplňku v repo/zips
    os.makedirs(out_dir)
    target = os.path.join(out_dir, "%s-%s.zip" % (addon_id, version))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(addon_dir):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(folder, name)
                z.write(path, os.path.relpath(path, ROOT))
    for asset in ("icon.png", "fanart.jpg"):
        src = os.path.join(addon_dir, asset)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, asset))
    print(os.path.relpath(target, ROOT))


def main():
    os.makedirs(ZIPS, exist_ok=True)
    parts = []
    for addon_id in ADDONS:
        build(addon_id)
        xml = re.sub(r"^<\?xml[^>]*\?>\s*", "", read_addon_xml(addon_id).strip())
        parts.append(xml)
    addons_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n'
                  + "\n".join(parts) + "\n</addons>\n")
    with open(os.path.join(REPO, "addons.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(addons_xml)
    with open(os.path.join(REPO, "addons.xml.md5"), "w", encoding="ascii", newline="\n") as f:
        f.write(hashlib.md5(addons_xml.encode("utf-8")).hexdigest())
    print("repo/addons.xml")
    publish_docs()


def publish_docs():
    """docs/ = GitHub Pages: jednoduchý výpis souborů, který Kodi umí procházet
    jako zdroj ve Správci souborů (raw.githubusercontent.com výpis složek nemá)."""
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    for name in os.listdir(docs):
        if name.endswith(".zip"):
            os.remove(os.path.join(docs, name))  # jen staré ZIPy v docs/
    links = []
    for addon_id in ADDONS:
        version = re.search(r'<addon[^>]*\bversion="([^"]+)"', read_addon_xml(addon_id)).group(1)
        name = "%s-%s.zip" % (addon_id, version)
        shutil.copyfile(os.path.join(ZIPS, addon_id, name), os.path.join(docs, name))
        links.append(name)
    rows = "\n".join('<a href="%s">%s</a><br>' % (n, n) for n in links)
    page = ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>Repozitář petermikula</title></head>\n"
            "<body>\n<h1>Repozitář petermikula pro Kodi</h1>\n"
            "<p>V Kodi nainstalujte <b>repository.petermikula</b> (Doplňky → Instalovat ze ZIP souboru) "
            "a doplňky pak instalujte z repozitáře.</p>\n" + rows + "\n</body></html>\n")
    with open(os.path.join(docs, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    open(os.path.join(docs, ".nojekyll"), "a").close()
    print("docs/index.html")


if __name__ == "__main__":
    main()
