#!/usr/bin/env python3
"""
Chirpy Post Editor — 개인용 Jekyll(Chirpy) 포스팅 편집기

실행:  python tools/editor/app.py           (저장소 루트에서)
접속:  http://localhost:5000/app
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

# ---------------------------------------------------------------- 경로 설정

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # tools/editor/app.py -> repo root

POSTS_DIR = REPO / "_posts"
DRAFTS_DIR = REPO / "_drafts"
SITE_DIR = REPO / "_site"

# ── 자산 폴더 위치 ────────────────────────────────────────────────
# 포스트별 폴더가 바로 이 아래에 생긴다.
#   IMG_ROOT  / <post_id> / 그림.png
#   FILE_ROOT / <post_id> / 첨부.pdf
# 위치를 바꾸고 싶으면 이 두 줄만 고치면 된다. URL 도 자동으로 따라간다.
IMG_ROOT = REPO / "assets" / "img"
FILE_ROOT = REPO / "assets" / "files"

IMG_URL = "/" + IMG_ROOT.relative_to(REPO).as_posix() + "/"    # "/assets/img/"
FILE_URL = "/" + FILE_ROOT.relative_to(REPO).as_posix() + "/"  # "/assets/files/"

JEKYLL_PORT = int(os.environ.get("JEKYLL_PORT", "4000"))
APP_PORT = int(os.environ.get("EDITOR_PORT", "5000"))
JEKYLL_BASE = f"http://127.0.0.1:{JEKYLL_PORT}"

for d in (POSTS_DIR, DRAFTS_DIR, IMG_ROOT, FILE_ROOT):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Chirpy Post Editor")

# ---------------------------------------------------------------- 유틸


def now_stamp() -> str:
    """Chirpy 스타일 date 문자열."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S +0900")


SAFE_STRIP = r'[\\/:*?"<>|#%&{}$!\'`+=@\s]+'


def safe_filename(name: str, ascii_only: bool = False) -> str:
    """URL에서 안전한 파일명. 한글은 기본 유지(원하면 ascii_only=True)."""
    stem = Path(name).stem
    ext = Path(name).suffix.lower()

    if ascii_only:
        stem = unicodedata.normalize("NFKD", stem)
        stem = stem.encode("ascii", "ignore").decode("ascii")

    stem = re.sub(SAFE_STRIP, "-", stem)
    stem = re.sub(r"[()\[\],;]+", "-", stem)
    stem = re.sub(r"-{2,}", "-", stem).strip("-._")

    if not stem:
        stem = "file"
    return f"{stem}{ext}"


def unique_path(folder: Path, filename: str) -> Path:
    """같은 이름이 있으면 -1, -2 붙이기."""
    target = folder / filename
    if not target.exists():
        return target
    stem, ext = Path(filename).stem, Path(filename).suffix
    for i in range(1, 1000):
        cand = folder / f"{stem}-{i}{ext}"
        if not cand.exists():
            return cand
    raise HTTPException(500, "파일명을 정할 수 없습니다")


def post_id_from_filename(fname: str) -> str:
    """'2026-06-26-slug.md' -> '2026-06-26-slug'"""
    return Path(fname).stem


def slug_from_post_id(post_id: str) -> str:
    """'2026-06-26-slug' -> 'slug'  (permalink /posts/:title/ 용)"""
    m = re.match(r"^\d{4}-\d{2}-\d{2}-(.+)$", post_id)
    return m.group(1) if m else post_id


def asset_dirs(post_id: str) -> tuple[Path, Path]:
    return IMG_ROOT / post_id, FILE_ROOT / post_id


# ---------------------------------------------------------------- front matter

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

# Chirpy 관례 순서
FM_ORDER = [
    "title",
    "description",
    "author",
    "authors",
    "date",
    "categories",
    "tags",
    "image",
    "media_subpath",
    "pin",
    "toc",
    "comments",
    "math",
    "mermaid",
]


def split_front_matter(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[m.end():]


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "":
        return '""'
    if re.search(r'[:#\[\]{}",\'*&!|>%@`]', s) or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def dump_front_matter(fm: dict) -> str:
    """Chirpy 관례에 맞춰 사람이 읽기 좋은 front matter 생성."""
    lines = ["---"]
    seen = set()

    def emit(k: str, v: Any):
        if v is None or v == "" or v == []:
            return
        if k == "description":
            body = str(v).strip()
            lines.append("description: >-")
            for ln in body.splitlines():
                lines.append("  " + ln.strip())
            return
        if k in ("categories", "tags") and isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_yaml_scalar(x) for x in v)}]")
            return
        if k == "image" and isinstance(v, dict):
            lines.append("image:")
            for ik in ("path", "lqip", "alt", "w", "h"):
                if v.get(ik):
                    lines.append(f"  {ik}: {_yaml_scalar(v[ik])}")
            return
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_yaml_scalar(x) for x in v)}]")
            return
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for ik, iv in v.items():
                lines.append(f"  {ik}: {_yaml_scalar(iv)}")
            return
        lines.append(f"{k}: {_yaml_scalar(v)}")

    for k in FM_ORDER:
        if k in fm:
            emit(k, fm[k])
            seen.add(k)
    for k, v in fm.items():
        if k not in seen:
            emit(k, v)

    lines.append("---")
    return "\n".join(lines) + "\n"


def read_post(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    fm, body = split_front_matter(raw)
    if isinstance(fm.get("date"), (datetime, date)):
        fm["date"] = str(fm["date"])
    return {"frontmatter": fm, "body": body}


# ---------------------------------------------------------------- 포스트 목록


def list_posts() -> list[dict]:
    out = []
    for folder, is_draft in ((POSTS_DIR, False), (DRAFTS_DIR, True)):
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md"), reverse=True):
            try:
                fm, _ = split_front_matter(p.read_text(encoding="utf-8"))
            except Exception:
                fm = {}
            pid = post_id_from_filename(p.name)
            out.append(
                {
                    "path": str(p.relative_to(REPO)).replace("\\", "/"),
                    "post_id": pid,
                    "slug": slug_from_post_id(pid),
                    "title": fm.get("title") or pid,
                    "date": str(fm.get("date") or ""),
                    "categories": fm.get("categories") or [],
                    "tags": fm.get("tags") or [],
                    "draft": is_draft,
                }
            )
    return out


def resolve_post(rel: str) -> Path:
    p = (REPO / rel).resolve()
    if not str(p).startswith(str(REPO)):
        raise HTTPException(400, "잘못된 경로")
    if p.parent not in (POSTS_DIR, DRAFTS_DIR):
        raise HTTPException(400, "_posts / _drafts 안의 파일만 편집합니다")
    if not p.exists():
        raise HTTPException(404, "파일이 없습니다")
    return p


# ---------------------------------------------------------------- API : 설정/목록


@app.get("/api/config")
def api_config():
    cfg = {}
    try:
        cfg = yaml.safe_load((REPO / "_config.yml").read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {
        "repo": str(REPO),
        "title": cfg.get("title", ""),
        "jekyll_port": JEKYLL_PORT,
        "jekyll_running": jekyll.is_running(),
        "img_url": IMG_URL,
        "file_url": FILE_URL,
    }


@app.get("/api/posts")
def api_posts():
    return {"posts": list_posts()}


@app.get("/api/taxonomy")
def api_taxonomy():
    """기존 포스트에서 카테고리/태그 수집 (자동완성용)."""
    cats: dict[str, int] = {}
    cat_pairs: dict[str, int] = {}
    tags: dict[str, int] = {}
    for folder in (POSTS_DIR, DRAFTS_DIR):
        for p in folder.glob("*.md"):
            try:
                fm, _ = split_front_matter(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            c = fm.get("categories") or []
            if isinstance(c, str):
                c = [c]
            for x in c:
                cats[str(x)] = cats.get(str(x), 0) + 1
            if c:
                key = " > ".join(str(x) for x in c)
                cat_pairs[key] = cat_pairs.get(key, 0) + 1
            t = fm.get("tags") or []
            if isinstance(t, str):
                t = [t]
            for x in t:
                tags[str(x)] = tags.get(str(x), 0) + 1
    return {
        "categories": sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])),
        "category_paths": sorted(cat_pairs.items(), key=lambda kv: (-kv[1], kv[0])),
        "tags": sorted(tags.items(), key=lambda kv: (-kv[1], kv[0])),
    }


# ---------------------------------------------------------------- API : 포스트 CRUD


@app.post("/api/posts")
async def api_create_post(req: Request):
    d = await req.json()
    slug = (d.get("slug") or "").strip()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(400, "slug는 영문 소문자/숫자/하이픈만 가능합니다 (예: my-first-post)")

    dstr = (d.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dstr):
        raise HTTPException(400, "날짜 형식은 YYYY-MM-DD 입니다")

    post_id = f"{dstr}-{slug}"
    is_draft = bool(d.get("draft"))
    folder = DRAFTS_DIR if is_draft else POSTS_DIR
    md_path = folder / f"{post_id}.md"
    if md_path.exists():
        raise HTTPException(409, f"이미 존재합니다: {md_path.name}")

    # ★ 1번 기능: 자산 폴더 자동 생성
    img_dir, file_dir = asset_dirs(post_id)
    img_dir.mkdir(parents=True, exist_ok=True)
    file_dir.mkdir(parents=True, exist_ok=True)

    time_part = (d.get("time") or "09:00:00").strip()
    fm = {
        "title": d.get("title") or slug,
        "description": d.get("description") or "",
        "date": f"{dstr} {time_part} +0900",
        "categories": d.get("categories") or [],
        "tags": d.get("tags") or [],
        "media_subpath": f"{IMG_URL}{post_id}/",
        "toc": True,
    }
    for flag in ("pin", "math", "mermaid"):
        if d.get(flag):
            fm[flag] = True

    md_path.write_text(dump_front_matter(fm) + "\n\n", encoding="utf-8")
    return {
        "ok": True,
        "path": str(md_path.relative_to(REPO)).replace("\\", "/"),
        "post_id": post_id,
        "created": [
            str(img_dir.relative_to(REPO)).replace("\\", "/"),
            str(file_dir.relative_to(REPO)).replace("\\", "/"),
        ],
    }


@app.get("/api/post")
def api_get_post(path: str):
    p = resolve_post(path)
    data = read_post(p)
    pid = post_id_from_filename(p.name)
    img_dir, file_dir = asset_dirs(pid)
    return {
        **data,
        "path": path,
        "post_id": pid,
        "slug": slug_from_post_id(pid),
        "draft": p.parent == DRAFTS_DIR,
        "preview_url": f"/posts/{slug_from_post_id(pid)}/",
        "img_dir_exists": img_dir.exists(),
        "file_dir_exists": file_dir.exists(),
    }


@app.post("/api/post")
async def api_save_post(req: Request):
    d = await req.json()
    p = resolve_post(d["path"])
    fm = d.get("frontmatter") or {}
    body = d.get("body") or ""
    p.write_text(dump_front_matter(fm) + body, encoding="utf-8")

    # 자산 폴더가 없으면 이 시점에도 만들어 준다
    pid = post_id_from_filename(p.name)
    for dd in asset_dirs(pid):
        dd.mkdir(parents=True, exist_ok=True)

    return {"ok": True, "saved_at": time.time()}


@app.post("/api/post/delete")
async def api_delete_post(req: Request):
    d = await req.json()
    p = resolve_post(d["path"])
    pid = post_id_from_filename(p.name)
    removed = [str(p.relative_to(REPO))]
    p.unlink()
    if d.get("delete_assets"):
        for dd in asset_dirs(pid):
            if dd.exists():
                shutil.rmtree(dd)
                removed.append(str(dd.relative_to(REPO)))
    return {"ok": True, "removed": removed}


@app.post("/api/post/publish")
async def api_publish(req: Request):
    """_drafts -> _posts 이동. 날짜가 바뀌면 자산 폴더까지 같이 rename."""
    d = await req.json()
    p = resolve_post(d["path"])
    if p.parent != DRAFTS_DIR:
        raise HTTPException(400, "초안이 아닙니다")

    old_id = post_id_from_filename(p.name)
    dstr = (d.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    slug = slug_from_post_id(old_id)
    new_id = f"{dstr}-{slug}"
    target = POSTS_DIR / f"{new_id}.md"
    if target.exists():
        raise HTTPException(409, f"이미 존재합니다: {target.name}")

    text = p.read_text(encoding="utf-8")
    fm, body = split_front_matter(text)

    renamed = []
    if new_id != old_id:
        for root in (IMG_ROOT, FILE_ROOT):
            src, dst = root / old_id, root / new_id
            if src.exists() and not dst.exists():
                src.rename(dst)
                renamed.append(f"{src.name} → {dst.name}")
        body = body.replace(f"{FILE_URL}{old_id}/", f"{FILE_URL}{new_id}/")
        body = body.replace(f"{IMG_URL}{old_id}/", f"{IMG_URL}{new_id}/")

    fm["media_subpath"] = f"{IMG_URL}{new_id}/"
    time_part = (d.get("time") or "09:00:00").strip()
    fm["date"] = f"{dstr} {time_part} +0900"

    target.write_text(dump_front_matter(fm) + body, encoding="utf-8")
    p.unlink()
    for dd in asset_dirs(new_id):
        dd.mkdir(parents=True, exist_ok=True)

    return {
        "ok": True,
        "path": str(target.relative_to(REPO)).replace("\\", "/"),
        "post_id": new_id,
        "renamed": renamed,
    }


# ---------------------------------------------------------------- API : 업로드

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}


@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    post_id: str = Form(...),
    kind: str = Form("auto"),          # auto | img | file
    ascii_name: str = Form("false"),
    optimize: str = Form("true"),
    max_width: str = Form("1600"),
    to_webp: str = Form("false"),
):
    if not re.fullmatch(r"[\w.\-]+", post_id):
        raise HTTPException(400, "잘못된 post_id")

    raw = await file.read()
    orig_name = file.filename or "upload.bin"
    ext = Path(orig_name).suffix.lower()

    if kind == "auto":
        kind = "img" if ext in IMG_EXT else "file"

    img_dir, file_dir = asset_dirs(post_id)
    folder = img_dir if kind == "img" else file_dir
    folder.mkdir(parents=True, exist_ok=True)

    fname = safe_filename(orig_name, ascii_only=(ascii_name == "true"))
    note = ""

    # ★ 이미지 자동 최적화
    if kind == "img" and optimize == "true" and ext in {".png", ".jpg", ".jpeg", ".webp"}:
        try:
            from PIL import Image

            im = Image.open(io.BytesIO(raw))
            before = len(raw)
            mw = int(max_width or 1600)
            if mw > 0 and im.width > mw:
                ratio = mw / im.width
                im = im.resize((mw, int(im.height * ratio)), Image.LANCZOS)

            buf = io.BytesIO()
            if to_webp == "true":
                if im.mode in ("P", "LA"):
                    im = im.convert("RGBA")
                im.save(buf, "WEBP", quality=85, method=6)
                fname = Path(fname).stem + ".webp"
            elif ext == ".png":
                im.save(buf, "PNG", optimize=True)
            else:
                if im.mode in ("RGBA", "P", "LA"):
                    im = im.convert("RGB")
                im.save(buf, "JPEG", quality=88, optimize=True, progressive=True)

            if buf.tell() and (buf.tell() < before or to_webp == "true" or mw < 100000):
                raw = buf.getvalue()
                note = f"{before // 1024}KB → {len(raw) // 1024}KB ({im.width}×{im.height})"
        except ImportError:
            note = "Pillow 미설치 — 원본 그대로 저장"
        except Exception as e:  # 최적화 실패해도 원본은 저장
            note = f"최적화 건너뜀 ({e.__class__.__name__})"

    dest = unique_path(folder, fname)
    dest.write_bytes(raw)

    if kind == "img":
        # media_subpath 덕분에 파일명만 쓰면 됨
        markdown = f'![{Path(orig_name).stem}]({dest.name}){{: w="800" }}\n_설명_'
        url = f"{IMG_URL}{post_id}/{dest.name}"
    else:
        url = f"{FILE_URL}{post_id}/{dest.name}"
        markdown = f'<a href="{url}" download>{orig_name}</a>'

    return {
        "ok": True,
        "filename": dest.name,
        "original": orig_name,
        "url": url,
        "kind": kind,
        "markdown": markdown,
        "bytes": len(raw),
        "note": note,
    }


# ---------------------------------------------------------------- API : 자산 정리

REF_PATTERNS = [
    re.compile(r"!\[[^\]]*\]\(([^)\s]+)"),          # ![alt](path)
    re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)"),     # [text](path)
    re.compile(r'href="([^"]+)"'),
    re.compile(r'src="([^"]+)"'),
]


@app.get("/api/assets")
def api_assets(path: str):
    """포스트 본문에서 참조하지 않는 파일(고아 파일)을 찾아낸다."""
    p = resolve_post(path)
    pid = post_id_from_filename(p.name)
    body = p.read_text(encoding="utf-8")

    referenced: set[str] = set()
    for pat in REF_PATTERNS:
        for m in pat.finditer(body):
            referenced.add(Path(m.group(1).split("?")[0].split("#")[0]).name)

    def is_used(name: str) -> bool:
        # 마크다운/HTML 참조로 잡히거나,
        # 파일명이 문서 어디에든 그냥 나오면 (front matter의 image: path: 등) 사용 중으로 본다.
        # 지우는 기능이므로 애매하면 "사용 중" 쪽으로 판단한다.
        return name in referenced or name in body

    out = []
    for kind, folder in (("img", IMG_ROOT / pid), ("file", FILE_ROOT / pid)):
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file():
                out.append(
                    {
                        "kind": kind,
                        "name": f.name,
                        "rel": str(f.relative_to(REPO)).replace("\\", "/"),
                        "bytes": f.stat().st_size,
                        "used": is_used(f.name),
                    }
                )
    return {"post_id": pid, "assets": out, "orphans": sum(1 for a in out if not a["used"])}


@app.post("/api/assets/delete")
async def api_assets_delete(req: Request):
    d = await req.json()
    post_id = (d.get("post_id") or "").strip()
    if not re.fullmatch(r"[\w.\-]+", post_id):
        raise HTTPException(400, "post_id 가 필요합니다")

    # 이 포스트의 자산 폴더 두 개 안에 있는 파일만 지울 수 있다.
    # (IMG_ROOT 바로 밑의 favicons/, profile/ 같은 공용 자산은 건드리지 못한다)
    allowed = {(IMG_ROOT / post_id).resolve(), (FILE_ROOT / post_id).resolve()}

    removed, skipped = [], []
    for rel in d.get("files", []):
        f = (REPO / rel).resolve()
        if f.parent in allowed and f.is_file():
            f.unlink()
            removed.append(rel)
        else:
            skipped.append(rel)
    return {"ok": True, "removed": removed, "skipped": skipped}


# ---------------------------------------------------------------- API : Git


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8"
    )
    if check and r.returncode != 0:
        raise HTTPException(400, (r.stderr or r.stdout or "git 오류").strip())
    return (r.stdout or "").strip()


@app.get("/api/git/status")
def api_git_status():
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        raw = git("status", "--porcelain")
        files = []
        for line in raw.splitlines():
            if line.strip():
                files.append({"status": line[:2].strip() or "?", "file": line[3:]})
        ahead = "0"
        try:
            ahead = git("rev-list", "--count", "@{u}..HEAD")
        except Exception:
            ahead = "?"
        return {"ok": True, "branch": branch, "files": files, "ahead": ahead}
    except HTTPException as e:
        return {"ok": False, "error": e.detail}


@app.post("/api/git/commit")
async def api_git_commit(req: Request):
    d = await req.json()
    msg = (d.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "커밋 메시지를 입력하세요")
    paths = d.get("paths") or []
    if paths:
        git("add", "--", *paths)
    else:
        git("add", "-A")
    staged = git("diff", "--cached", "--name-only")
    if not staged:
        raise HTTPException(400, "커밋할 변경사항이 없습니다")
    out = git("commit", "-m", msg)
    result = {"ok": True, "output": out, "pushed": False}
    if d.get("push"):
        try:
            result["push_output"] = git("push")
            result["pushed"] = True
        except HTTPException as e:
            result["push_error"] = e.detail
    return result


@app.post("/api/git/push")
def api_git_push():
    try:
        return {"ok": True, "output": git("push")}
    except HTTPException as e:
        return {"ok": False, "error": e.detail}


# ---------------------------------------------------------------- Jekyll 프로세스


class Jekyll:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.log: list[str] = []
        self._reader: Optional[asyncio.Task] = None

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> dict:
        if self.is_running():
            return {"ok": True, "already": True}
        cmd = [
            "bundle", "exec", "jekyll", "serve",
            "--host", "127.0.0.1",
            "--port", str(JEKYLL_PORT),
            "--drafts",
            "--unpublished",
            "--future",
            "--watch",
            "--trace",
        ]
        # --incremental 은 기본으로 쓰지 않는다.
        # 바뀐 파일만 다시 만들기 때문에 home/tags/categories 같은 목록 페이지가
        # 갱신되지 않는다. 글을 지우면 목록엔 남아있는데 눌러보면 404 가 나고,
        # 새 글은 본문 페이지만 생기고 목록엔 안 뜨는 문제가 생긴다.
        # 사이트가 커져서 빌드가 느려지면 JEKYLL_INCREMENTAL=1 로 켤 수 있다.
        if os.environ.get("JEKYLL_INCREMENTAL") == "1":
            cmd.insert(4, "--incremental")
        self.log = [f"$ {' '.join(cmd)}"]
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=REPO,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env={**os.environ, "JEKYLL_ENV": "development"},
            )
        except FileNotFoundError:
            return {"ok": False, "error": "bundle 명령을 찾을 수 없습니다. `bundle install` 먼저 실행하세요."}

        import threading

        def pump():
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                self.log.append(line.rstrip())
                if len(self.log) > 400:
                    del self.log[:200]

        threading.Thread(target=pump, daemon=True).start()
        return {"ok": True, "pid": self.proc.pid}

    def stop(self) -> dict:
        if not self.is_running():
            return {"ok": True, "already": True}
        assert self.proc
        try:
            if os.name == "nt":
                self.proc.terminate()
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        self.proc = None
        return {"ok": True}


jekyll = Jekyll()


@app.get("/api/jekyll/status")
async def api_jekyll_status():
    reachable = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get(JEKYLL_BASE + "/")
            reachable = r.status_code < 500
    except Exception:
        pass
    return {
        "running": jekyll.is_running(),
        "reachable": reachable,
        "port": JEKYLL_PORT,
        "log": jekyll.log[-60:],
    }


@app.post("/api/jekyll/start")
def api_jekyll_start():
    return jekyll.start()


@app.post("/api/jekyll/stop")
def api_jekyll_stop():
    return jekyll.stop()


@app.post("/api/jekyll/reset")
def api_jekyll_reset():
    """
    빌드 캐시를 비우고 Jekyll 을 다시 시작한다.
    목록 페이지가 실제와 안 맞을 때 (지운 글이 남아있거나, 새 글이 안 보일 때) 쓴다.
    """
    was_running = jekyll.is_running()
    jekyll.stop()
    removed = []
    for name in (".jekyll-cache", "_site", ".jekyll-metadata"):
        t = REPO / name
        try:
            if t.is_dir():
                shutil.rmtree(t)
                removed.append(name + "/")
            elif t.exists():
                t.unlink()
                removed.append(name)
        except OSError as e:
            return {"ok": False, "error": f"{name} 삭제 실패: {e}"}
    r = jekyll.start() if was_running else {"ok": True, "restarted": False}
    return {**r, "removed": removed, "restarted": was_running}


@app.get("/api/build/wait")
async def api_build_wait(slug: str, since: float = 0.0, timeout: float = 25.0):
    """
    저장 후 Jekyll이 해당 포스트를 다시 빌드할 때까지 기다린다.
    _site/posts/<slug>/index.html 의 mtime 이 since 보다 커지면 완료.
    """
    target = SITE_DIR / "posts" / slug / "index.html"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if target.exists() and target.stat().st_mtime > since:
                await asyncio.sleep(0.35)  # 파일 쓰기 완료 여유
                return {"ok": True, "mtime": target.stat().st_mtime, "waited": True}
        except OSError:
            pass
        await asyncio.sleep(0.3)
    return {
        "ok": False,
        "timeout": True,
        "exists": target.exists(),
        "log": jekyll.log[-25:],
    }


@app.get("/api/build/mtime")
def api_build_mtime(slug: str):
    target = SITE_DIR / "posts" / slug / "index.html"
    return {"mtime": target.stat().st_mtime if target.exists() else 0.0}


# ---------------------------------------------------------------- 에디터 UI


@app.get("/app", response_class=HTMLResponse)
def editor_page():
    return (HERE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/_editor/{fname:path}")
def editor_static(fname: str):
    f = (HERE / "static" / fname).resolve()
    if not str(f).startswith(str(HERE / "static")) or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


# ---------------------------------------------------------------- Jekyll 리버스 프록시
# 위에서 안 잡힌 모든 경로는 로컬 Jekyll 서버로 넘긴다.
# 같은 origin 이라 iframe 임베드 / 절대경로(/assets/...) 모두 그대로 동작한다.

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}


@app.api_route("/{full_path:path}", methods=["GET", "HEAD", "POST"])
async def proxy_to_jekyll(full_path: str, request: Request):
    url = f"{JEKYLL_BASE}/{full_path}"
    if request.url.query:
        url += "?" + request.url.query
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as c:
            r = await c.request(
                request.method,
                url,
                content=await request.body(),
                headers={
                    k: v for k, v in request.headers.items()
                    if k.lower() not in {"host", "accept-encoding", "connection"}
                },
            )
        headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP_HEADERS}
        headers.pop("x-frame-options", None)
        headers["cache-control"] = "no-store"
        return Response(content=r.content, status_code=r.status_code, headers=headers)
    except httpx.ConnectError:
        return HTMLResponse(
            "<div style='font:14px/1.7 system-ui;padding:32px;color:#555'>"
            "<b>Jekyll 서버가 아직 안 떠 있습니다.</b><br><br>"
            "상단 <b>Jekyll ▶ 시작</b> 버튼을 누르거나, 터미널에서 "
            "<code>bundle exec jekyll serve --drafts --unpublished --future</code> 를 실행하세요.<br>"
            "첫 빌드는 20~40초 걸릴 수 있습니다.</div>",
            status_code=503,
        )
    except Exception as e:
        return HTMLResponse(f"<pre>프록시 오류: {e}</pre>", status_code=502)


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    print(f"\n  저장소 : {REPO}")
    print(f"  에디터 : http://localhost:{APP_PORT}/app")
    print(f"  Jekyll : http://127.0.0.1:{JEKYLL_PORT} (프록시됨)\n")
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT, log_level="warning")
