# -*- coding: utf-8 -*-
"""Sestaví Kodi repozitář do složky repo/ (servíruje ho GitHub z větve main):

    repo/addons.xml, repo/addons.xml.md5
    repo/zips/<id>/<id>-<verze>.zip (+ icon.png, fanart.jpg)

    python3 tools/build_zip.py
"""
import argparse
import hashlib
import os
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

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
    verify_release()


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


def verify_release():
    """Ověří současné ZIPy, Kodi index a odkazy GitHub Pages."""
    repo = Path(REPO)
    docs = Path(ROOT, "docs")
    manifest = (repo / "addons.xml").read_bytes()
    checksum = (repo / "addons.xml.md5").read_text(encoding="ascii").strip()
    if hashlib.md5(manifest).hexdigest() != checksum:
        raise ValueError("repo/addons.xml.md5 does not match addons.xml")
    expected_versions = {addon_id: ET.fromstring(read_addon_xml(addon_id)).attrib["version"]
                         for addon_id in ADDONS}
    published = ET.fromstring(manifest).findall("addon")
    versions = {addon.attrib["id"]: addon.attrib["version"] for addon in published}
    if versions != expected_versions or len(published) != len(ADDONS):
        raise ValueError("Kodi index does not match source addon IDs and versions")
    expected = set()
    for addon_id, version in versions.items():
        name = "%s-%s.zip" % (addon_id, version)
        expected.add(name)
        archive = repo / "zips" / addon_id / name
        if (docs / name).read_bytes() != archive.read_bytes():
            raise ValueError("Different archives for %s" % name)
        with zipfile.ZipFile(archive) as addon_zip:
            bad_member = addon_zip.testzip()
            if bad_member:
                raise ValueError("Corrupt member in %s: %s" % (name, bad_member))
            addon = ET.fromstring(addon_zip.read(addon_id + "/addon.xml"))
            if (addon.get("id"), addon.get("version")) != (addon_id, version):
                raise ValueError("Wrong addon ID or version inside %s" % name)
    links = re.findall(r'href="([^"]+\.zip)"', (docs / "index.html").read_text(encoding="utf-8"))
    if set(links) != expected or len(links) != len(expected):
        raise ValueError("docs/index.html ZIP links do not match the Kodi index")
    if {path.name for path in docs.glob("*.zip")} != expected:
        raise ValueError("docs contains missing or extra ZIPs")
    print("Kodi ZIPs, index and GitHub Pages links verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify existing release files without rebuilding")
    args = parser.parse_args()
    if args.check:
        verify_release()
    else:
        main()
