# SocialBidAI Design System (`DESIGN.md` — Google Stitch Spec)

## 1. Brand & Aesthetic Direction
- **Identity**: 신뢰감 있는 공공·B2G 조달 전문가 워크벤치 + 사회적 가치(SVI) 임팩트 대시보드
- **Color Palette**:
  - **Primary Navy (Trust & B2G)**: `#0F2942` (Primary Deep Navy), `#1E3A5F` (Hover Navy)
  - **Accent Teal (Social Value & SVI 120% Priority)**: `#0D9488` (Teal 600), `#CCFBF1` (Teal Tint)
  - **Slate Neutrals**: `#F6F8FB` (App Canvas), `#FFFFFF` (Elevated Surface), `#E2E8F0` (Hairline Border), `#0F172A` (Ink Primary), `#475569` (Ink Secondary)
  - **Semantic Status**:
    - Success / Pass: `#059669` on `#ECFDF5`
    - Warning / Direct Production Check: `#D97706` on `#FFFBEB`
    - Danger / Red-Flag Alert: `#DC2626` on `#FEF2F2`
- **Typography**:
  - Primary Font: `"Pretendard Variable", "Pretendard", "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif`
  - Monospace (Code / Bid No / Amounts): `"JetBrains Mono", ui-monospace, monospace`
  - Numeric alignment: `font-variant-numeric: tabular-nums` on all KRW amounts, SVI scores, and BEP tables.
- **Layout & Density**:
  - Fixed Left App Rail (`240px`) + Fluid Main Workspace (`1fr`)
  - High-density scannable B2G tables with sticky headers, explicit crisp `1px` borders, `8px`–`12px` card radii, minimal shadow elevation.

## 2. Five-Screen Stitch Blueprint
1. **대시보드 & 인사이트 (`/dashboard`)**: KPI 4열 카드 + SVI 5단계 게이지(75점+ 120% 우선구매 강조) + 마감 임박 수의계약 기회 + 최근 제안서·사업계획서 + 일일 API 쿼터
2. **맞춤 공고 인박스 (`/bids`)**: 상단 `<search>` 필터 바 + 소스 탭(나라장터/진흥원/기업마당) + 좌측 반응형 고밀도 공고 테이블(수의계약 5천만/1억 배지, 적합도 링 차트) + 우측 HITL 검토 상세 드로어
3. **제안서 워크스페이스 (`/proposals`)**: 좌우 Split-View(35% / 65%) — 좌측 RFP 요약·배점표·드래그/정렬 가능 목차 트리·Steering Note·HWPX 드롭존 / 우측 실시간 SSE 마크다운 에디터·RAG 인용 출처·HWPX 패치/DOCX/MD 내보내기
4. **소셜 사업계획서 위저드 (`/bizplan`)**: 상단 4단계 스텝퍼 + 중앙 캔버스(Pain Point·이중 고객 / 내재형 수익·실시간 단위경제성·BEP 계산기 / 사회적목적 5유형·SVI 14개 지표·3개년 인증 로드맵 / PSST·예비사회적기업·HWPX 패치 전환) + 우측 실시간 심사관 레드플래그 5종 Critic 패널
5. **마스터 프로필 & 설정 (`/settings`)**: 좌측 서브 내비게이션 + LLM 키·단가 / 공공 API 키·통신테스트 / 회사 마스터 프로필(복수 업종·직접생산) / 로컬 RAG 인덱싱·재인덱싱 경고 / 외부 프롬프트 YAML·원격 동기화
