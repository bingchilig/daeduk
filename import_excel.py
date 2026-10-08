# -*- coding: utf-8 -*-
"""
여운 — 엑셀 작품 리스트 가져오기
=================================
대덕전자 '장애 예술인 작품 리스트(관리용)' 엑셀을 읽어
  data/yeoun.db        (SQLite DB: 작품·전시실·작가 표시명·소개문 상태·위치)
  data/images/full/    (감상용 이미지, 긴 변 최대 1600px)
  data/images/thumb/   (목록·전시관 전체 보기용, 긴 변 최대 400px)
를 만든다.

- 엑셀 '2.작품 리스트_RAWDATA' 시트의 '사진' 칸에 들어 있는 셀 내 이미지(richData)를 찾아 꺼낸다.
- '위치' 시트의 설치 장소 목록도 함께 읽어 관리자 페이지의 위치 선택지로 쓴다.
- 이미 만든 DB가 있으면, 담당자가 관리자 페이지에서 고친 내용(소개문 승인, 위치,
  전시실 배정, 작가 표시명 등)은 그대로 두고 엑셀 내용만 새로 반영한다.
- 원본 엑셀은 읽기만 하고 수정하지 않는다.

실행:  python import_excel.py            (config.json의 excel_path 사용)
       python import_excel.py "엑셀 경로.xlsx"
필요:  Python 3.9+ (표준 라이브러리만으로 동작) / Pillow가 있으면 이미지 크기를 줄여 저장
"""
import json
import os
import re
import shutil
import sys
import zipfile
import posixpath
import datetime as dt
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
IMG_FULL = os.path.join(DATA, "images", "full")
IMG_THUMB = os.path.join(DATA, "images", "thumb")
ARTWORKS = os.path.join(DATA, "artworks.json")

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "rd": "http://schemas.microsoft.com/office/spreadsheetml/2017/richdata",
    "rv": "http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel",
    "xlrd": "http://schemas.microsoft.com/office/spreadsheetml/2017/richdata",
}
R_ID = "{%s}id" % NS["r"]

MAIN_SHEET_HINT = "작품 리스트_RAWDATA"   # 시트 이름에 이 글자가 들어 있으면 작품 목록으로 본다
LOCATION_SHEET = "위치"

# 엑셀 머리글 → 내부 필드 (머리글 글자가 조금 달라도 찾을 수 있게 포함 관계로 비교)
HEADER_MAP = [
    ("no", ["no"]),
    ("artist", ["작가명", "작가"]),
    ("title", ["작품명"]),
    ("year", ["제작연도"]),
    ("received", ["인수일자"]),
    ("theme", ["테마"]),
    ("material", ["재료"]),
    ("size", ["크기"]),
    ("photo", ["사진"]),
    ("desc", ["작품 설명", "작품설명"]),
    ("submitted", ["제출일자"]),
    ("location", ["설치위치", "설치 위치"]),
    ("note", ["비고"]),
]

DEFAULT_ROOMS = [
    {"id": 1, "name": "1전시실", "theme": "가족", "subtitle": "곁에 있는 사람들", "match": {"theme": ["가족"]}},
    {"id": 2, "name": "2전시실", "theme": "이야기", "subtitle": "일상과 상상의 장면", "match": {"theme": ["이야기"]}},
    {"id": 3, "name": "3전시실", "theme": "자연 Ⅰ", "subtitle": "2025–2026년에 들어온 작품", "match": {"theme": ["자연"], "year_min": 2025}},
    {"id": 4, "name": "4전시실", "theme": "자연 Ⅱ", "subtitle": "2023–2024년에 들어온 작품", "match": {"theme": ["자연"], "year_max": 2024}},
    {"id": 5, "name": "5전시실", "theme": "감정", "subtitle": "색과 형태로 전하는 마음", "match": {"theme": ["감정"]}},
]


# ----------------------------------------------------------------------------
# xlsx 읽기 (표준 라이브러리)
# ----------------------------------------------------------------------------
class Xlsx:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self.names = set(self.z.namelist())
        self.shared = self._shared_strings()
        self.sheets = self._sheet_targets()
        self.vm_to_image = self._rich_value_images()

    def xml(self, name):
        return ET.fromstring(self.z.read(name))

    def rels(self, part):
        """part(예: xl/workbook.xml)의 관계 파일을 읽어 {rId: 절대경로}"""
        d, f = posixpath.split(part)
        rp = posixpath.join(d, "_rels", f + ".rels")
        out = {}
        if rp not in self.names:
            return out
        for rel in self.xml(rp).findall("pr:Relationship", NS):
            tgt = rel.get("Target")
            if rel.get("TargetMode") == "External":
                continue
            full = posixpath.normpath(posixpath.join(d, tgt)) if not tgt.startswith("/") else tgt.lstrip("/")
            out[rel.get("Id")] = full
        return out

    def _shared_strings(self):
        if "xl/sharedStrings.xml" not in self.names:
            return []
        out = []
        for si in self.xml("xl/sharedStrings.xml").findall("m:si", NS):
            # 윗주(rPh) 제외하고 텍스트 이어 붙이기
            parts = []
            t = si.find("m:t", NS)
            if t is not None and t.text:
                parts.append(t.text)
            for r in si.findall("m:r", NS):
                rt = r.find("m:t", NS)
                if rt is not None and rt.text:
                    parts.append(rt.text)
            out.append("".join(parts))
        return out

    def _sheet_targets(self):
        wb = self.xml("xl/workbook.xml")
        rels = self.rels("xl/workbook.xml")
        out = {}
        for s in wb.find("m:sheets", NS).findall("m:sheet", NS):
            out[s.get("name")] = {"path": rels.get(s.get(R_ID)), "state": s.get("state", "visible")}
        return out

    def _rich_value_images(self):
        """셀의 vm 번호(1부터) → 이미지 파일 경로(zip 안)"""
        need = ["xl/metadata.xml", "xl/richData/rdrichvalue.xml", "xl/richData/richValueRel.xml"]
        if not all(n in self.names for n in need):
            return {}
        meta = self.xml("xl/metadata.xml")
        types = [t.get("name") for t in meta.find("m:metadataTypes", NS).findall("m:metadataType", NS)]
        future = []
        for fm in meta.findall("m:futureMetadata", NS):
            if fm.get("name") != "XLRICHVALUE":
                continue
            for bk in fm.findall("m:bk", NS):
                rvb = bk.find(".//xlrd:rvb", NS)
                future.append(int(rvb.get("i")) if rvb is not None else None)
        vm_list = []
        vmeta = meta.find("m:valueMetadata", NS)
        if vmeta is not None:
            for bk in vmeta.findall("m:bk", NS):
                rc = bk.find("m:rc", NS)
                t = int(rc.get("t")) - 1
                v = int(rc.get("v"))
                vm_list.append(future[v] if t < len(types) and types[t] == "XLRICHVALUE" and v < len(future) else None)

        # 구조: 어떤 키 위치에 이미지 관계 번호가 있는지
        struct_key_pos = []
        if "xl/richData/rdrichvaluestructure.xml" in self.names:
            for s in self.xml("xl/richData/rdrichvaluestructure.xml").findall("rd:s", NS):
                keys = [k.get("n") for k in s.findall("rd:k", NS)]
                pos = keys.index("_rvRel:LocalImageIdentifier") if "_rvRel:LocalImageIdentifier" in keys else None
                struct_key_pos.append(pos)
        rich = []
        for rv in self.xml("xl/richData/rdrichvalue.xml").findall("rd:rv", NS):
            s = int(rv.get("s", 0))
            vals = [v.text for v in rv.findall("rd:v", NS)]
            pos = struct_key_pos[s] if s < len(struct_key_pos) else 0
            rich.append(int(vals[pos]) if pos is not None and pos < len(vals) else None)

        rel_ids = [r.get(R_ID) for r in self.xml("xl/richData/richValueRel.xml").findall("rv:rel", NS)]
        rel_map = self.rels("xl/richData/richValueRel.xml")

        out = {}
        for vm_index, rv_index in enumerate(vm_list, start=1):
            if rv_index is None or rv_index >= len(rich):
                continue
            rel_index = rich[rv_index]
            if rel_index is None or rel_index >= len(rel_ids):
                continue
            target = rel_map.get(rel_ids[rel_index])
            if target:
                out[vm_index] = target
        return out

    def read_sheet(self, name):
        """{(row, col): {'v': 값, 'vm': 번호}} 형태로 시트를 읽는다 (col은 1부터)."""
        info = self.sheets.get(name)
        if not info:
            return {}
        root = self.xml(info["path"])
        cells = {}
        for c in root.iter("{%s}c" % NS["m"]):
            ref = c.get("r")
            row, col = ref_to_rc(ref)
            t = c.get("t")
            v = c.find("m:v", NS)
            val = None
            if t == "s" and v is not None:
                val = self.shared[int(v.text)]
            elif t == "inlineStr":
                is_ = c.find("m:is", NS)
                val = "".join(x.text or "" for x in is_.iter("{%s}t" % NS["m"])) if is_ is not None else ""
            elif t in ("str", "e"):
                val = v.text if v is not None else None
            elif t == "b":
                val = (v.text == "1") if v is not None else None
            elif v is not None and v.text is not None:
                try:
                    f = float(v.text)
                    val = int(f) if f.is_integer() else f
                except ValueError:
                    val = v.text
            vm = c.get("vm")
            cells[(row, col)] = {"v": val, "vm": int(vm) if vm else None, "t": t}
        return cells


def ref_to_rc(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    letters, num = m.groups()
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return int(num), col


XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"


def sheet_pictures(x, sheet_name):
    """시트 위에 떠 있는 그림(drawing) 목록: [{row, target, crop}] (row는 그림 중심이 걸친 행)"""
    info = x.sheets.get(sheet_name)
    if not info:
        return []
    out = []
    for target in x.rels(info["path"]).values():
        if "/drawings/" not in target or target not in x.names:
            continue
        d = x.xml(target)
        drels = x.rels(target)
        for anc in list(d):
            frm = anc.find("{%s}from" % XDR)
            if frm is None:
                continue
            r0 = int(frm.find("{%s}row" % XDR).text)
            to = anc.find("{%s}to" % XDR)
            r1 = int(to.find("{%s}row" % XDR).text) if to is not None else r0
            for pic in anc.iter("{%s}pic" % XDR):
                blip = pic.find(".//{%s}blip" % DML)
                emb = blip.get("{%s}embed" % NS["r"]) if blip is not None else None
                img = drels.get(emb)
                if not img or img not in x.names:
                    continue
                src = pic.find(".//{%s}srcRect" % DML)
                crop = None
                if src is not None:
                    crop = {k: max(0, int(src.get(k, 0))) / 100000.0 for k in ("l", "t", "r", "b")}
                out.append({"row": (r0 + r1) // 2 + 1, "row_from": r0 + 1, "target": img, "crop": crop})
    return out


# 그림을 믿을 수 있는 정도: 목록 시트(가공용·테마별·작품 목록)에 붙은 그림은 원본 비율,
# 설치 계획 시트(B1·M1·HQ)는 칸에 맞춰 늘린 경우가 있어 우선순위를 낮춘다.
SOURCE_PRIORITY = [("가공용", 3), ("테마별", 3), (MAIN_SHEET_HINT, 3), ("B1", 1), ("M1", 1), ("HQ", 1)]
SKIP_SHEETS = ("작가사진", "Sheet2")   # 작가 사진(개인정보)·액자 견적 사진은 쓰지 않음
MATCH_MIN = 0.95                      # 같은 그림으로 볼 최소 유사도


def picture_pool(x):
    """통합 문서 전체에 떠 있는 그림을 한 번씩만 열어 둔다 (작가 사진 시트 제외)."""
    pool, cache = [], {}
    if not HAVE_PIL:
        return pool
    for sn in x.sheets:
        if any(k in sn for k in SKIP_SHEETS):
            continue
        prio = next((v for k, v in SOURCE_PRIORITY if k in sn), 1)
        for p in sheet_pictures(x, sn):
            key = (p["target"], tuple(sorted((p["crop"] or {}).items())))
            if key not in cache:
                try:
                    im = open_image(x.z.read(p["target"]), p["crop"])
                    cache[key] = (im.size, _signature(im))
                except Exception:
                    cache[key] = None
            if cache[key]:
                pool.append(dict(p, sheet=sn, prio=prio, size=cache[key][0], sig=cache[key][1]))
    return pool


def _signature(im):
    g = im.convert("L").resize((24, 24))
    px = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
    m = sum(px) / len(px)
    sd = (sum((v - m) ** 2 for v in px) / len(px)) ** 0.5 or 1
    return [(v - m) / sd for v in px]


def sig_similarity(sa, sb):
    """두 그림이 같은 작품인지 비교 (-1~1). 크기·비율을 무시하고 형태만 본다."""
    return sum(p * q for p, q in zip(sa, sb)) / len(sa)


# ----------------------------------------------------------------------------
# 값 정리
# ----------------------------------------------------------------------------
def clean(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = s.strip()
    return "" if s in ("#VALUE!", "None", "-") else s


def one_line(s):
    return re.sub(r"\s*\n\s*", " ", s).strip()


def excel_serial_to_date(n):
    return (dt.date(1899, 12, 30) + dt.timedelta(days=int(n))).isoformat()


def norm_date(v):
    """'23.11.07' / '2026.08.18' / 엑셀 날짜 숫자 → 'YYYY-MM-DD' (모르면 원문)"""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)) and v > 20000:
        return excel_serial_to_date(v)
    s = clean(v)
    m = re.match(r"^(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        y = int(y)
        if y < 100:
            y += 2000
        try:
            return dt.date(y, int(mo), int(d)).isoformat()
        except ValueError:
            return s
    return s


def mask_name(name):
    """익명 표시명: 가운데 글자를 ○로 (예: 강주혜 → 강○혜, 김환 → 김○)"""
    n = name.strip()
    if len(n) <= 1:
        return n + "○"
    if len(n) == 2:
        return n[0] + "○"
    return n[0] + "○" * (len(n) - 2) + n[-1]


def split_material(mat):
    """'수채물감 /아크릴 액자' → ('수채물감', '아크릴 액자')"""
    if not mat:
        return "", ""
    s = one_line(mat)
    if "/" in s:
        a, b = s.split("/", 1)
        return a.strip(" ,"), b.strip(" ,").replace("→", "→ ").replace("  ", " ")
    return s, ""


def status_from_location(loc):
    if not loc:
        return "확인 필요"
    if "보관" in loc or "창고" in loc:
        return "보관 중"
    return "설치됨"


def year_int(v):
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# 이미지 저장
# ----------------------------------------------------------------------------
try:
    from PIL import Image, ImageOps
    try:
        from PIL import ImageCms
    except Exception:  # littlecms가 없는 환경
        ImageCms = None
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False


def open_image(raw, crop=None):
    """엑셀에 보이는 모습 그대로(자르기 반영) 이미지를 연다. Pillow 필요."""
    import io
    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im)  # 사진 회전 정보만 반영
    if crop and any(crop.values()):
        w, h = im.size
        box = (round(w * crop["l"]), round(h * crop["t"]), round(w * (1 - crop["r"])), round(h * (1 - crop["b"])))
        if box[2] - box[0] > 10 and box[3] - box[1] > 10:
            info = im.info
            im = im.crop(box)
            im.info.update(info)
    return im


def save_image(raw, work_id, ext, crop=None):
    """원본 바이트 → full/thumb 저장. 비율 유지, 색 보정·필터 없음."""
    os.makedirs(IMG_FULL, exist_ok=True)
    os.makedirs(IMG_THUMB, exist_ok=True)
    if not HAVE_PIL:
        name = work_id + ext
        for d in (IMG_FULL, IMG_THUMB):
            with open(os.path.join(d, name), "wb") as f:
                f.write(raw)
        return {"full": "images/full/" + name, "thumb": "images/thumb/" + name, "w": None, "h": None}

    import io
    im = open_image(raw, crop)
    icc = im.info.get("icc_profile")
    # 다른 색 공간(ICC) 사진은 sRGB로 '변환'만 한다 (화면에서 원래 색으로 보이게)
    if icc and ImageCms is not None:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            im = ImageCms.profileToProfile(im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im,
                                           src, dst, outputMode="RGB")
        except Exception:
            pass
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        alpha = rgba.getchannel("A")
        if alpha.getextrema()[0] < 255:  # 실제로 투명한 부분이 있으면 흰 바탕에 올림
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=alpha)
            im = bg
        else:
            im = rgba.convert("RGB")
    elif im.mode != "RGB":
        im = im.convert("RGB")

    w, h = im.size
    full = im.copy()
    full.thumbnail((1600, 1600), Image.LANCZOS)
    thumb = im.copy()
    thumb.thumbnail((400, 400), Image.LANCZOS)
    name = work_id + ".jpg"
    full.save(os.path.join(IMG_FULL, name), "JPEG", quality=92, subsampling=0, optimize=True)
    thumb.save(os.path.join(IMG_THUMB, name), "JPEG", quality=85, optimize=True)
    return {"full": "images/full/" + name, "thumb": "images/thumb/" + name,
            "w": full.size[0], "h": full.size[1], "orig_w": w, "orig_h": h}


# ----------------------------------------------------------------------------
# 가져오기
# ----------------------------------------------------------------------------
def find_header(cells):
    rows = {}
    for (r, c), cell in cells.items():
        rows.setdefault(r, {})[c] = clean(cell["v"])
    for r in sorted(rows):
        texts = rows[r]
        if any("작품명" in t for t in texts.values()) and any("작가" in t for t in texts.values()):
            colmap = {}
            for key, names in HEADER_MAP:
                for c, t in sorted(texts.items()):
                    tl = t.replace("\n", "").replace(" ", "").lower()
                    if any(n.replace(" ", "").lower() in tl for n in names) and c not in colmap.values():
                        if key == "no" and tl != "no":
                            continue
                        colmap[key] = c
                        break
            return r, colmap
    raise SystemExit("작품 목록 머리글(작가명·작품명)을 찾지 못했습니다.")


def read_locations(x):
    name = next((n for n in x.sheets if n.strip() == LOCATION_SHEET), None)
    if not name:
        return []
    cells = x.read_sheet(name)
    rows = {}
    for (r, c), cell in cells.items():
        rows.setdefault(r, {})[c] = clean(cell["v"])
    out, group = [], ""
    for r in sorted(rows):
        row = rows[r]
        g, place, cnt, memo = row.get(2, ""), row.get(3, ""), row.get(4, ""), row.get(5, "")
        if g and g not in ("구분", "計", "계") and not g.startswith("※") and not g.startswith("예술인"):
            group = g
        if place and place != "위치" and group:
            out.append({"group": group, "place": place, "count": cnt, "memo": memo})
    return out


def assign_room(work, rooms):
    y = year_int(work.get("received")) or year_int(work.get("year"))
    for room in rooms:
        m = room.get("match") or {}
        if m.get("theme") and work.get("theme") not in m["theme"]:
            continue
        if m.get("year_max") and (y is None or y > m["year_max"]):
            continue
        if m.get("year_min") and (y is not None and y < m["year_min"]):
            continue
        return room["id"]
    return rooms[-1]["id"] if rooms else 1


def build_draft(text, real_name, display_name):
    """소개문 초안 = 엑셀 '작품 설명'(제공자료)을 줄바꿈만 정리한 것. AI 생성 아님.
    원문에 작가 실명이 있으면 표시명으로 바꾸고 표시해 둔다."""
    if not text:
        return "", False
    t = re.sub(r"[ \t]{2,}", " ", text)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    replaced = False
    if real_name and real_name in t and display_name and display_name != real_name:
        t = t.replace(real_name, display_name)
        replaced = True
    return t, replaced


def run(excel_path, quiet=False):
    log = (lambda *a: None) if quiet else print
    if not os.path.exists(excel_path):
        raise SystemExit("엑셀 파일을 찾을 수 없습니다: " + excel_path)
    log("엑셀 읽는 중:", os.path.basename(excel_path))
    x = Xlsx(excel_path)
    sheet = next((n for n in x.sheets if MAIN_SHEET_HINT in n), None)
    if not sheet:
        raise SystemExit("'%s' 시트를 찾지 못했습니다. 시트 목록: %s" % (MAIN_SHEET_HINT, ", ".join(x.sheets)))
    cells = x.read_sheet(sheet)
    header_row, col = find_header(cells)
    missing = [k for k in ("artist", "title") if k not in col]
    if missing:
        raise SystemExit("필수 머리글이 없습니다: " + ", ".join(missing))
    title_cell = clean(cells.get((1, 2), {}).get("v"))
    pool = picture_pool(x)

    # 기존 데이터(담당자 수정 내용) 불러오기
    import db
    old = {}
    if db.exists():
        old = db.load_all()
    elif os.path.exists(ARTWORKS):   # 예전 JSON 버전에서 넘어온 경우
        with open(ARTWORKS, encoding="utf-8") as f:
            old = json.load(f)
    old_works = {w.get("source_key"): w for w in old.get("artworks", []) if w.get("source_key")}
    old_artists = old.get("artists", {})
    rooms = old.get("rooms") or DEFAULT_ROOMS

    max_row = max(r for r, _ in cells)
    artists, artist_ids = {}, {}
    # 기존 작가 id·표시명 유지
    for aid, a in old_artists.items():
        artists[aid] = a
        artist_ids[a["real_name"]] = aid

    works, seen_keys, n_img, n_noimg = [], set(), 0, 0
    for r in range(header_row + 1, max_row + 1):
        g = lambda k: cells.get((r, col[k]), {}).get("v") if k in col else None
        title_raw, artist = clean(g("title")), clean(g("artist"))
        if not title_raw or not artist:
            continue
        title = one_line(title_raw)
        no = clean(g("no"))
        year = clean(g("year"))
        received = norm_date(g("received"))
        key = "|".join([artist, title, year, received])
        if key in seen_keys:
            key += "|r%d" % r
        seen_keys.add(key)

        if artist not in artist_ids:
            aid = "a%02d" % (len(artist_ids) + 1)
            while aid in artists:
                aid = "a%02d" % (int(aid[1:]) + 1)
            artist_ids[artist] = aid
            artists[aid] = {"real_name": artist, "display_name": mask_name(artist), "name_public": False}
        aid = artist_ids[artist]
        disp = artists[aid]["name_public"] and artists[aid]["real_name"] or artists[aid]["display_name"]

        prev = old_works.get(key, {})
        wid = prev.get("id") or ("w%03d" % int(no) if no.isdigit() else "w_r%03d" % r)
        material, frame_note = split_material(clean(g("material")))
        loc_excel = one_line(clean(g("location")))
        source_text = clean(g("desc"))

        # 이미지 고르기
        #  - '사진' 칸의 셀 내 이미지는 칸 크기(약 196×140)에 맞춰 눌린 미리보기라 비율이 원본과 다르다.
        #  - 그래서 통합 문서 안의 다른 그림 중 '같은 그림'(유사도 0.95 이상)을 찾아, 목록 시트 그림 →
        #    해상도 큰 그림 순으로 쓴다. 못 찾으면 셀 내 이미지를 쓰고 '비율 확인 필요'로 표시한다.
        image = None
        vm = cells.get((r, col["photo"]), {}).get("vm") if "photo" in col else None
        cell_target = x.vm_to_image.get(vm) if vm else None
        picked, ratio_ok = None, False
        if cell_target and cell_target in x.names and HAVE_PIL:
            try:
                ref_sig = _signature(open_image(x.z.read(cell_target)))
                matches = [p for p in pool if sig_similarity(ref_sig, p["sig"]) >= MATCH_MIN]
            except Exception:
                matches = []
            if matches:
                picked = max(matches, key=lambda p: (p["prio"], p["size"][0] * p["size"][1]))
                ratio_ok = picked["prio"] >= 3
            else:
                picked = {"target": cell_target, "crop": None, "sheet": sheet + " (셀 내 이미지)"}
        elif cell_target and cell_target in x.names:
            picked = {"target": cell_target, "crop": None, "sheet": sheet + " (셀 내 이미지)"}
        else:
            # 셀 내 이미지가 없으면 같은 행에 떠 있는 그림
            row_pics = [p for p in pool if p["sheet"] == sheet and r in (p["row"], p["row_from"])]
            if row_pics:
                picked = max(row_pics, key=lambda p: p["size"][0] * p["size"][1])
                ratio_ok = True
        if picked:
            ext = os.path.splitext(picked["target"])[1].lower() or ".png"
            try:
                image = save_image(x.z.read(picked["target"]), wid, ext, picked.get("crop"))
                image["source"] = posixpath.basename(picked["target"])
                image["source_sheet"] = picked["sheet"]
                image["ratio_ok"] = ratio_ok
                n_img += 1
            except Exception as e:  # 손상된 이미지 등
                log("  ! 이미지 저장 실패", wid, title, e)
        if image is None:
            n_noimg += 1

        draft, replaced = build_draft(source_text, artist, artists[aid]["display_name"])
        intro = prev.get("intro") or {}
        if not intro or intro.get("source_text") != source_text:
            # 엑셀 원문이 바뀌었으면 초안을 새로 만들고 다시 검토 받도록
            intro = {
                "draft": draft,
                "approved": intro.get("approved", "") if intro.get("status") == "approved" and intro.get("source_text") == source_text else "",
                "status": "draft" if draft else "none",
                "source_text": source_text,
                "name_replaced": replaced,
                "updated_at": "",
                "reviewer_note": "",
            }

        w = {
            "id": wid,
            "no": int(no) if no.isdigit() else None,
            "source_key": key,
            "origin": "excel",
            "artist_id": aid,
            "title": title,
            "year": year,
            "received": received,
            "theme": clean(g("theme")),
            "material": material,
            "frame_note": frame_note,
            "size": one_line(clean(g("size"))),
            "submitted": clean(g("submitted")),
            "note": one_line(clean(g("note"))),
            "excel_location": loc_excel,
            "excel_row": r,
            "image": image,
            "intro": intro,
            # 담당자가 고칠 수 있는 값 — 기존 수정 내용 우선
            "status": prev.get("status") if prev.get("location_source") == "admin" else status_from_location(loc_excel),
            "location": prev.get("location") if prev.get("location_source") == "admin" else (loc_excel or ""),
            "location_source": prev.get("location_source", "excel"),
            "location_updated_at": prev.get("location_updated_at", ""),
            "room": prev.get("room"),
            "order": prev.get("order"),
            "online": prev.get("online", image is not None),
            "is_example": False,
        }
        if w["room"] is None:
            w["room"] = assign_room(w, rooms)
        works.append(w)

    # 관리자 페이지에서 직접 등록한 작품은 그대로 유지
    for ow in old.get("artworks", []):
        if ow.get("origin") == "admin":
            works.append(ow)

    # 전시실 안 순서: 기존 순서 → 인수일자 → 번호
    for room in rooms:
        rw = [w for w in works if w["room"] == room["id"]]
        rw.sort(key=lambda w: (w["order"] if w.get("order") is not None else 10 ** 6, w.get("received") or "9999", w.get("no") or 0))
        for i, w in enumerate(rw):
            w["order"] = i

    locations = read_locations(x)
    excel_places = sorted({w["excel_location"] for w in works if w.get("excel_location")})
    data = {
        "meta": {
            "title": title_cell,
            "source_file": os.path.basename(excel_path),
            "source_sheet": sheet,
            "header_row": header_row,
            "imported_at": dt.datetime.now().isoformat(timespec="seconds"),
            "counts": {
                "works": len(works),
                "with_image": sum(1 for w in works if w.get("image")),
                "without_image": sum(1 for w in works if not w.get("image")),
                "artists": len({w["artist_id"] for w in works}),
            },
            "pillow": HAVE_PIL,
            "note": "작품명·제작연도·재료·크기·작품 설명·설치위치는 엑셀 원문 기준. 작가명은 익명 표시명으로 공개.",
        },
        "rooms": rooms,
        "artists": artists,
        "locations": {"sheet": locations, "excel_values": excel_places},
        "artworks": works,
    }
    os.makedirs(DATA, exist_ok=True)
    db.replace_import(data)   # data/yeoun.db (SQLite)에 저장
    log("완료: 작품 %d점 (이미지 %d / 없음 %d), 작가 %d명" % (len(works), n_img, n_noimg, data["meta"]["counts"]["artists"]))
    for room in rooms:
        log("  %s %-6s %d점" % (room["name"], room["theme"], sum(1 for w in works if w["room"] == room["id"])))
    if not HAVE_PIL:
        log("  (Pillow가 없어 원본 크기 그대로 복사했습니다. pip install pillow 후 다시 실행하면 가벼워집니다.)")
    return data


def default_excel_path():
    cfg_path = os.path.join(ROOT, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            p = json.load(f).get("excel_path", "")
        if p:
            p = p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))
            if os.path.exists(p):
                return p
    # 상위 폴더에서 '작품 리스트'가 들어간 xlsx 찾기
    for d in (ROOT, os.path.dirname(ROOT)):
        for n in sorted(os.listdir(d)):
            if n.lower().endswith(".xlsx") and "작품" in n and not n.startswith("~$"):
                return os.path.join(d, n)
    raise SystemExit("엑셀 파일을 찾지 못했습니다. config.json의 excel_path를 확인하세요.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else default_excel_path()
    run(path)
