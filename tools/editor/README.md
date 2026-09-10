# Chirpy 포스트 편집기

Jekyll + Chirpy 블로그 포스팅을 위한 개인용 로컬 편집기.
왼쪽에서 마크다운을 쓰면 오른쪽에서 **실제 Jekyll 서버가 빌드한 결과**를 그대로 보여준다.

```
tools/editor/
├── app.py            # FastAPI 백엔드 (+ Jekyll 리버스 프록시)
├── static/index.html # 편집기 UI (단일 파일)
├── requirements.txt
└── start.sh
```

---

## 실행

```bash
# 저장소 루트에서
bash tools/editor/start.sh
```

Codespace라면 **PORTS 탭에서 5000번 포트**를 열고 `/app` 으로 접속한다.
Jekyll(4000)은 편집기가 같은 origin으로 프록시하므로 따로 열 필요가 없다.
(그 덕분에 iframe 차단·포트포워딩 인증 문제도 안 생긴다.)

접속 후 상단 **Jekyll [시작]** 버튼을 누른다. 첫 빌드는 20~40초 걸린다.

수동 실행:

```bash
pip install -r tools/editor/requirements.txt
python tools/editor/app.py          # http://localhost:5000/app
```

환경변수: `EDITOR_PORT`(기본 5000), `JEKYLL_PORT`(기본 4000)

---

## 기능

### 1. 자동 폴더 생성
**＋ 새 글** → 날짜 + slug 입력 시 아래를 한 번에 생성한다.

```
_posts/2026-06-26-accounting-management-review.md
assets/img/posts/2026-06-26-accounting-management-review/
assets/files/2026-06-26-accounting-management-review/
```

front matter의 `media_subpath`도 자동으로 채워지므로,
본문에서는 이미지 파일명만 쓰면 된다 (`![alt](test_result.png)`).

### 2. 실시간 렌더링
- `bundle exec jekyll serve --incremental --drafts --unpublished --future --watch` 를 편집기가 직접 띄우고 관리한다.
- 타이핑 멈추고 0.9초 뒤 자동 저장 → Jekyll 재빌드 대기 → iframe 자동 새로고침.
- **스크롤 위치는 유지**된다.
- 백엔드가 `_site/posts/<slug>/index.html` 의 mtime 변화를 감시해서 "빌드가 진짜 끝난 시점"에만 새로고침한다.
- 체감 지연 1~3초. 키 입력 즉시 반영은 Jekyll 특성상 불가능하다.

### 3. ＋ 삽입 버튼
커서 위치에 삽입된다.

| 종류 | 결과 |
|---|---|
| 이미지 | `![alt](file.png){: w="800" }` + 아래줄 `_설명_` |
| 첨부파일 | `<a href="/assets/files/<id>/file.pdf" download>원본이름.pdf</a>` |
| 링크 | `[텍스트](URL)` |
| 표 | 아래 참조 |

- 이미지·첨부는 **드래그&드롭** 또는 파일 선택 → 1번에서 만든 폴더에 자동 저장.
- 편집기 본문에 직접 드롭해도 된다 (확장자로 img/file 자동 판별).
- 업로드 시 자동 최적화: 최대 폭 1600px 리사이즈 + 압축, WebP 변환은 선택.
- 파일명은 URL에 안전한 형태로 정리한다 (공백·괄호 → `-`). "영문(ASCII)으로 변환" 옵션도 있다.

#### 표 헤더에 대해
마크다운 표는 **1행 헤더만** 지원한다. 그래서 편집기는 4가지 모드를 준다.

- **없음** — 지금까지 쓰시던 구분선 없는 방식 그대로
- **1열이 헤더** ← 원래 의도했던 것. 마크다운으로는 불가능하므로 HTML 표로 출력한다:

```html
<table>
  <tbody>
    <tr><th scope="row">주최</th><td>삼일회계법인</td></tr>
    <tr><th scope="row">응시자격</th><td>제한없음</td></tr>
  </tbody>
</table>
```

Chirpy는 렌더링된 HTML 표에도 테마 스타일(`.table-wrapper`)을 그대로 입히므로 겉보기는 동일하고,
차이점은 1열이 진짜 `<th>` 로 강조되고 스크린리더/SEO에도 헤더로 인식된다는 것.

- **1행이 헤더** — 일반 마크다운 표 (`|---|---|`)
- **둘 다** — HTML 표

행/열 추가·삭제, 엑셀에서 탭 구분 붙여넣기 지원.

### 4. 줄바꿈 자동화
툴바의 **"줄바꿈 자동"** 체크박스로 켜고 끈다.

| 상황 | 동작 |
|---|---|
| 글자 있는 줄에서 Enter | 줄 끝에 스페이스 2칸 추가 후 개행 (soft break) |
| 빈 줄에서 Enter | `&nbsp;` 여백 블록 삽입 |
| 리스트 항목에서 Enter | 스페이스 2칸 + 다음 항목 마커 자동 (`- `, `2.` …) |
| 빈 리스트 항목에서 Enter | 리스트 탈출 |
| `>` 인용/prompt 안에서 Enter | `> ` 자동 이어쓰기 |
| 제목(`#`), `{: ...}`, `_캡션_` | 스페이스 없이 개행 |
| 코드블록(```` ``` ````) 안, 표(`|`) 안 | 자동화 끔 |
| **Shift+Enter** | 언제나 순수 개행 |

### 5. prompt 박스
tip / info / warning / danger 를 색깔로 구분해 선택하고 내용을 쓰면:

```markdown
> **3줄 요약**
> 1. 비전공자 + 직장 병행 순수 2주 소요
{: .prompt-tip }
```

여러 줄 입력 시 각 줄에 `> ` 를 자동으로 붙인다.

---

## 추가된 기능

### Git 커밋/푸시
상단 **⑂ Git** → 변경 파일 목록 확인 → 커밋 메시지 (기본값 `post: <제목>`) → 커밋 + push.
push 하면 GitHub Actions가 배포한다.

### 자산 정리
상단 **🗂 자산 정리** → 본문에서 참조하지 않는 파일(고아 파일)을 찾아 표시하고 선택 삭제.
미사용 파일은 기본으로 체크되어 있다.

### 카테고리 / 태그
- 기존 모든 포스트에서 카테고리·태그를 수집해 자동완성으로 제공한다 (사용 횟수 표시).
- 칩(chip) UI로 추가·삭제. Enter 또는 쉼표로 추가, Backspace로 삭제.

### Front matter 폼 편집
title / description / 날짜 / 시각 / categories / tags / 대표이미지 / toc / pin / math / mermaid 를 폼으로 편집.
저장할 때 Chirpy 관례 순서와 스타일(`description: >-`, `categories: [a, b]`)로 다시 써진다.

### 초안(_drafts) 워크플로우
- 새 글 만들 때 "초안으로 시작" 체크 → `_drafts/` 에 생성 (공개 안 됨, 미리보기는 정상).
- **발행** 버튼 → 날짜 지정 → `_posts/` 로 이동.
  날짜가 바뀌면 `assets/img/posts/<id>/`, `assets/files/<id>/` 폴더 이름과
  본문 안의 경로, `media_subpath` 까지 한꺼번에 고쳐준다.

---

## 단축키

| 키 | 동작 |
|---|---|
| `⌘/Ctrl + S` | 저장 |
| `Shift + Enter` | 자동화 없는 순수 개행 |
| `Esc` | 모달 닫기 |

---

## 알아둘 점

- `tools/` 는 `_config.yml` 의 `exclude` 에 들어있어 블로그에 배포되지 않는다.
- 자동 저장은 파일에 바로 쓴다. 되돌리기는 Git으로.
- Jekyll 빌드가 실패하면 미리보기가 갱신되지 않는다. 상단 **📄** 버튼으로 로그를 확인.
- `--incremental` 은 가끔 캐시가 꼬인다. 이상하면 Jekyll [정지] → `rm -rf .jekyll-cache _site` → [시작].
