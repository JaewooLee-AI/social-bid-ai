# SocialBidAI — 사회적기업 공공입찰·제안서·사업계획서 자동화 앱

`info/사회적기업 PyWebview 제안서·사업계획서 아키텍처.docx` 설계서를 바탕으로 구현된 **월 고정비 0원 로컬 데스크톱 AI 스튜디오**입니다.

## 아키텍처 구성
- **프레젠테이션 계층**: PyWebview (Windows Edge WebView2 / macOS WKWebView) + React 19 + Tailwind CSS v4 (`frontend/DESIGN.md` Google Stitch 규격)
- **이중 브리지(Dual-Bridge)**:
  - `FastAPI + Uvicorn (127.0.0.1)` 루프백 REST & SSE 스트리밍 (`X-PyWebView-Token` 세션 CSRF 검증)
  - `PyWebview js_api` (`app/bridge/native_api.py`): 네이티브 파일 열기/저장 대화상자, 폴더 열기, `pywebviewFullPath` 드래그앤드롭
- **로컬 하이브리드 RAG**: 단일 SQLite DB (`FTS5 BM25` + `sqlite-vec` 768차원 Matryoshka + `RRF` 융합)
- **상용 안전 문서 파이프라인**:
  - `.hwpx` / `.hwp 5.0`: `python-hwpx` (Apache-2.0) 읽기 및 `Patch Mode` 서식 보존 출력
  - `.pdf`: `pdfplumber` (MIT) + `pypdfium2` (Apache-2.0) + 스캔 PDF Vision LLM 폴백
  - `.docx` / `.md`: `python-docx` (MIT) + 마크다운 내보내기
- **데이터 완전 분리**: `%APPDATA%\SocialBidAI` (macOS `~/Library/Application Support/SocialBidAI`)에 `config.json`, `company_profile.json`, `svi_rubric.json`, `prompts/*.yaml`, `app.db`, `downloads/`, `exports/` 분리 저장

## 로컬 실행 (macOS / Linux)
```bash
# 1. PyWebview 데스크톱 앱 실행 (frontend/ui_dist 자동 사용)
./scripts/dev.sh

# 2. 브라우저 분리 개발 모드 (Vite HMR + FastAPI Dev Server)
./scripts/dev.sh dev

# 3. 전체 테스트 실행
.venv/bin/pytest -v
```

## Windows `setup.exe` 자동 빌드 (GitHub Actions)
- GitHub 저장소에 `v*.*.*` 태그를 푸시하거나 Actions에서 `Build & Release Windows Setup`을 수동 실행하면 `windows-latest` 러너가 React 빌드 → PyInstaller `--onedir`(`app.spec`) → Inno Setup(`installer/setup.iss`, WebView2 사전 검사 포함) → `SHA256SUMS.txt`까지 자동 릴리즈합니다.
