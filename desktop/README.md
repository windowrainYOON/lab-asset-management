# Asset Manager (데스크톱 앱)

연구실 자원(항체, 효소, Reagent, 플라스미드, Cell line, RNA, Peptide …)을 관리하는 데스크톱 대시보드입니다.

- **Common Asset / Personal Asset**: 최상위 구분. 각각에 카테고리를 자유롭게 추가합니다.
- **카테고리 = 표**: 컬럼을 직접 정의하거나 CSV/XLSX/XLS를 가져와 만듭니다. 시트는 하위 그룹이 됩니다.
- **세부 조건·상태**: 항목마다 키-값 메모(보관 조건, 농도, 희석 배수 …)와 변경 이력을 기록합니다.
- **재고 판정**: ‘수량’ 역할 컬럼이 부족 기준(기본 1) 이하면 부족, 0이면 소진. ‘재고 유무’ 역할 컬럼은 `X`/없음이면 소진.
- **통합 검색 (⌘K)**: `BamHI`, `HeLa vials<2`, `cat:plasmid state:소진`, `location:-80`, `scope:personal`.
- **프로젝트 파일**: `*.labproj` 하나(SQLite)에 모든 데이터가 즉시 저장됩니다. 마지막으로 연 프로젝트가 자동으로 열립니다.
- **Google Drive 폴더**: 폴더 링크로 연결하면 폴더 안의 엑셀/CSV가 그대로 카테고리(파일)·그룹(시트)·항목(행)으로 보입니다. 열 때마다 폴더 전체를 새로 읽고, 앱에서 바꾼 값·추가/삭제한 행·열은 원본 파일의 같은 셀에 저장되어 Drive에 올라갑니다. 카테고리 종류·부족 기준·컬럼 역할·항목 메모 같은 앱 설정만 폴더의 `Asset Manager 설정.json`에 따로 저장됩니다. `.xls`는 처음 수정할 때 `.xlsx`로 저장되고 원본은 `_원본 xls` 폴더로 옮겨집니다.

## 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.main            # 또는: python -m app.main path/to/project.labproj
pytest
```

## 샘플 데이터

`data/`는 커밋하지 않습니다. 예시 엑셀을 `data/samples/`에 두고 `python scripts/build_sample.py`를 실행하면
`data/sample.labproj`가 만들어지고, 처음 실행할 때 자동으로 열립니다.

## macOS 앱 빌드

```bash
./scripts/build_macos.sh   # dist/Asset Manager.app 생성 후 /Applications에 설치
```

## 구조

```
app/core/        데이터 모델, 프로젝트 파일, 가져오기/내보내기, 설정
app/core/services/  검색·재고 리포트 (UI와 무관, Slack 봇 등에서 재사용)
app/ui/          PySide6 화면 (사이드바, 대시보드, 카테고리 표, 상세 패널, 대화상자)
app/ui/theme/    기본 스타일시트 — 디자인 시스템은 여기에 덮어씁니다
app/integrations/  Slack, Google Drive 확장 자리 (각 README 참고)
```

## Folder layout on the developer's Mac

Everything for this project lives in one folder, apart from other projects:

```
~/Developer/asset-manager/
  lab-asset-management/   this repository (desktop/ = this app: code, .venv, build/, dist/)
  state/                  what the app keeps: Google login token, settings, Drive cache, logs
  secrets/                OAuth client JSON (never committed)
  backups/                copies taken before each install
  releases/               source archives sent to the Mac (unpacked/ holds their contents)
  logs/  screenshots/  orders/  tools/
```

`scripts/build_macos.sh` bakes the project's `state/` into the app when that folder exists
(`../state`, or `../../state` when the app sits in the repository's `desktop/` folder), so the
installed app reads and writes its state there; without it (a lab member's build) the app
uses `~/Library/Application Support/AssetManager`. `$ASSET_MANAGER_HOME` overrides both.
