# Windows 설치 파일과 배포

설치 파일은 공개 GitHub 저장소의 Release 첨부 파일로 제공한다.
README 최상단에서 최신 설치 파일을 바로 다운로드할 수 있다.

## 사용자 설치

1. README 최상단의 Windows 설치 파일 링크로 `notice-peek-setup.exe`를 받는다.
2. 기존 앱을 사용 중이면 닫고 설치 파일을 실행한다. 기본 경로는 `%LOCALAPPDATA%\Programs\notice-peek`다.
3. 설치가 끝나면 바탕화면 또는 시작 메뉴의 **notice-peek**를 실행한다.
4. 처음 실행할 때 받을 게시판을 선택하고 저장한다. 선택을 나중에 바꿀 수도 있다.

지원 대상은 Windows 10/11 x64다. 관리자 권한·Python 설치가 필요하지 않다.
설치는 한국어·라이트 모드로 표시하며 실행 구성요소를 모두 포함한다.
공지 수집에는 인터넷 연결이 필요하다. 설치 파일에 개인 DB는 포함하지 않는다.
서명 인증서는 적용하지 않아 Windows 보안 경고가 표시될 수 있다.
별도 PC에서의 실행은 아직 확인하지 않았으며, 선배 PC에서 최초 설치·선택·수집을 확인한다.

## 업데이트와 제거

- 앱을 닫고 새 설치 파일을 실행하면 기존 위치에 업데이트된다.
- 구독과 공지 DB는 `%LOCALAPPDATA%\notice-peek\notices.sqlite3`에 별도로 보존된다.
- Windows 설치된 앱에서 제거하면 프로그램과 생성한 바로가기를 삭제한다.
  공지 DB는 자동으로 삭제하지 않는다.
- 기존 폴더 배포본에서 설치본으로 전환하면 바탕화면의 같은 이름 바로가기는
  설치본을 가리키게 된다. 기존 프로그램 폴더는 별도로 남는다.

## 빌드

Python 3.11과 Inno Setup 6.7.3 이상이 필요한 빌드 전용 과정이다.
공식 컴파일러: https://jrsoftware.org/isdl.php

```powershell
.\package.ps1 -CompilerPath 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

`package.ps1`은 기존 배포 폴더와 분리한 개발 EXE를 빌드한 뒤
`installer/notice-peek.iss`를 컴파일한다. 앱 버전은 `notice_peek/__init__.py`에서 읽는다.
빌드 환경 Python 라이선스와 배포 구성요소 고지도 포함한다.
이미 검증한 EXE는 `-SkipBuild`로 포장할 수 있다. 이 경우 소스 변경 후 빌드를 생략하지 않는다.

생성물:

- `dist/release/notice-peek-setup.exe`: 매 릴리스 동일한 첨부 파일 이름.
- `dist/release/SHA256SUMS.txt`: 설치 파일 SHA256.

빌드 출력과 임시 컴파일러는 Git에 올리지 않는다.
이번 빌드에서는 공식 Inno Setup 6.7.3의 유효한 Pyrsys B.V. 서명을 확인하고
프로젝트 `.local/toolchain` 안에 portable 모드로 준비했다.

## 재현 가능한 설치 검사

```powershell
.\tools\test-installer.ps1
# 실제 설치된 앱의 실행까지 확인하고 창을 직접 닫으려면:
.\tools\test-installer.ps1 -OpenApp
```

검사는 매번 `.local/installer-check-<고유값>` 안에서 진행한다.
동일한 설치 스크립트에서 테스트용 AppId·경로만 변경하고, 테스트 바로가기는
별도 폴더에 생성한다. 실제 바탕화면·시작 메뉴·앱 제거 등록을 건드리지 않는다.
페이로드 전체 파일의 해시, 바로가기 타깃과 작업 폴더, 업데이트·제거 후 DB 보존,
제거 후 프로그램과 바로가기 삭제를 확인한다. 결과는 테스트 폴더 `result.json`에 남는다.
`-OpenApp`은 Python을 PATH에서 제외하고 임시 LOCALAPPDATA로 설치된 앱을 실행한다.

## 공개 릴리스 순서

1. 공개할 소스와 기존 Git 기록에 개인 데이터·비밀값이 없는지 확인한다.
   초기 Word 기획 문서와 프롬프트도 현재 Git 관리 대상이므로 공개 범위를 함께 확인한다.
2. 검증된 소스·설치 스크립트·문서를 커밋하고 기본 브랜치에도 반영해 푸시한다.
3. 저장소를 public으로 전환하고, 그 소스 커밋을 가리키는 버전 태그로 릴리스를 만든다.
4. 설치 EXE와 SHA256SUMS.txt를 릴리스 첨부 파일로 업로드한다.
5. README의 배포 준비 문구를 정리하고 아래 링크가 로그인 없이 다운로드되는지 확인한다.

`https://github.com/hy2oni/notice-peek/releases/latest/download/notice-peek-setup.exe`

릴리스마다 파일 이름을 유지하므로 README 링크를 매번 바꿀 필요가 없다.
설치 파일은 GitHub Release에 보관하며 소스 저장소에 커밋하지 않는다.
