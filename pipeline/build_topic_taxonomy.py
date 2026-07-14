#!/usr/bin/env python3
"""
build_topic_taxonomy.py — 평면 토픽(354개)을 상위 카테고리로 묶는 택소노미 (강화 C4).

각 토픽을 키워드 규칙으로 상위 카테고리에 배정해 indexes/topic_taxonomy.json 으로
출력한다(토픽별 노트 수 포함). 매칭 안 되면 '기타'. 순서가 우선순위(먼저 매칭 승).

  build:  python3 scripts/build_topic_taxonomy.py --build
"""
import os, sys, json, argparse
import re
from pathlib import Path

import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1])); from kitlib.config import vault_path as _vp; VAULT = _vp()
os.environ.setdefault("OWNTOLOGY_VAULT", str(VAULT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kitlib"))
import vault

# (상위 카테고리, [매칭 키워드(소문자, 부분일치)]) — 구체적인 것부터(먼저 매칭 승)
PARENTS = [
    ("자동매매·트레이딩", ["자동매매", "트레이딩", "코인", "비트코인", "주식", "매매", "거래소", "업비트", "okx", "선물", "스윙", "개미연작"]),
    ("소스·링크·수집", ["링크", "links", "source", "소스", "유튜브", "youtube", "kakao", "체크포인트", "처리 상태", "조회 제한", "전수처리", "배치", "공개 자료", "공개 링크", "민감 링크", "보호 대상 링크", "불완전 링크", "중복 제거", "최신화 완료", "누락 비교", "정정", "기술 분류", "신규 저장소", "증분 처리", "태그정리", "인덱스", "index", "url", "검색품질"]),
    ("AI·온톨로지·데이터", ["ai", "llm", "gpt", "온톨로지", "ontology", "owntology", "머신러닝", "karpathy", "rag", "임베딩", "에이전트", "agent", "mcp", "프롬프트", "claude", "anthropic", "grok", "pytorch", "데이터분석", "lstm", "딥러닝", "신경망", "강화학습", "open webui", "knowledge-management", "knowledge-graph", "knowledge-hygiene", "seedance", "transformer", "stable diffusion", "reinforcement learning", "evolution strategy", "black-box optimization", "policy gradient", "reinforce", "language model", "backpropagation", "optimization", "gradient", "deeplearning", "deep-learning", "artificial-intelligence", "embedding", "embeddings", "rnn", "bptt", "numpy", "torch", "lope", "mlx", "i2v", "qwen", "ann", "minicpm", "multilingual", "gguf", "추론", "모델 비교", "모델 접근", "리더보드", "lm studio", "manus", "yann lecun", "월드 모델", "attention", "automatic differentiation", "floating point", "reproducibility", "latent space", "slerp", "diffusion", "vector", "context-engineering", "prompt-engineering"]),
    ("창작·음악", ["창작", "랩", "가사", "작사", "작곡", "음악", "노래", "초안", "라임", "벌스", "디스", "단체곡", "비트메이킹", "녹음", "뮤직비디오", "영상", "드라마", "animation", "audio", "suno", "kling", "premiere", "트레일러", "스토리보드", "숏폼", "가상 캐릭터", "참고 작품", "instagram", "콘텐츠", "fallen_angel", "vivid", "bbanana", "blink studio", "byteplus", "lumina", "fable", "네로모찌", "단군신화", "신화 재해석", "대사 연기", "보사노바", "서정적 연출", "스토리텔링", "원테이크", "점프컷", "장면 전환", "수정본", "아웃트로", "후반작업", "실사 촬영", "하이브리드 제작", "creative-tools", "videoeditor", "remotion", "2d-game", "아티스트", "플레이리스트"]),
    ("글쓰기·기록", ["블로그", "에세이", "글쓰기", "일기", "편지", "레퍼런스", "기록", "회상", "대나무숲", "도서", "기술 문서", "기술 가이드", "공식 도움말", "공식 사이트", "공식 공지", "공식 문서", "저자 소개", "사용 가이드"]),
    ("성찰·감정", ["자기성찰", "성찰", "자신감", "실존", "열정", "자부심", "현실", "사랑", "감정", "고민", "꿈"]),
    ("취업·커리어", ["취업", "커리어", "이력서", "면접", "자기소개", "자소서", "채용", "지원서", "포트폴리오", "경력", "지원현황", "입사후포부", "지원동기", "직무역량", "직무분석", "software engineer", "sk하이닉스", "아이센스", "현대모비스", "lg cns"]),
    ("학습·자격", ["정보처리기사", "시험", "자격", "공부", "학습", "강의", "수업", "기출", "수험", "변리사"]),
    ("업무", ["업무", "ssis", "회사", "직장", "보고", "회의", "사내", "프로젝트관리", "조직 설계", "커뮤니티 운영", "그룹", "문제해결", "협업", "기본정보", "성과", "정량화", "프로젝트", "방문일정", "인바운드통신", "도메인정보", "외부업체협업", "보건복지행정타운", "일정확인", "고객안내", "담당자협업"]),
    ("비즈니스·사업", ["비즈니스", "사업", "창업", "마케팅", "수익", "스타트업", "런칭", "saas", "marketplace", "pricing", "가격", "서비스 정책", "제휴", "고객지원", "기업 투자", "경쟁 우위", "경영", "프로모션", "추천 프로모션", "크리에이터 프로그램"]),
    ("금융·세무", ["금융", "세금", "세무", "대출", "이자", "자산", "결제", "환율", "보험", "연말정산", "돈", "finance", "하나카드", "투자 분석", "gln", "신한은행", "퇴직연금", "irp", "정기예금", "운용상품", "카드분실"]),
    ("법률·시사", ["법률", "법무", "정치", "시사", "사회", "법령", "공공데이터", "공개 데이터", "정책", "공공기관", "공공주택", "국가통계", "kosis", "vworld", "공간정보", "취약점", "모바일 신분증", "공공서비스", "신원 확인", "서울 열린데이터", "외교부", "부동산", "한국부동산원", "한국산업은행"]),
    ("소통·메시징", ["문자", "카카오", "sms", "web발신", "메일", "채팅", "톡", "rcs", "커뮤니티", "포럼", "네트워킹", "네이버 카페", "imessage", "자동알림"]),
    ("iOS·앱개발", ["ios", "앱", "swift", "swiftui", "xcode", "앱출시", "앱스토어", "테스트플라이트", "macos", "app store", "expo"]),
    ("개발·인프라", ["개발", "코드", "빌드", "배포", "디버그", "리팩", "서버운영", "서버 관리", "시스템운영", "자동화", "automation", "인프라", "클라우드", "cloud", "백엔드", "프론트", "frontend", "api", "github", "git", "데이터베이스", "python", "rust", "go", "dart", "cpp", "c#", "html", "shell", "powershell", "docker", "도커", "typescript", "javascript", "node.js", "npm", "codex", "opencrab", "cli", "sdk", "ui", "ux", "design", "도구", "utility", "reference", "obsidian", "옵시디언", "markdown", "오픈소스", "oss", "repository", "self-hosted", "셀프 호스팅", "자가 호스팅", "자체 호스팅", "android", "n8n", "browser-automation", "모니터링", "네트워크", "엔지니어링", "registry", "pipeline", "netlify", "cloudflare", "터미널", "원격 접속", "하드웨어", "nas", "저장장치", "동기화", "테스트 환경", "라이브러리", "알파 버전", "베타테스트", "wave terminal", "taxmeter", "jhny-kor", "batch-script", "skill", "apple silicon", "java", "oracle", "tomcat", "catalina", "스키마", "포트", "서버", "게이트웨이", "전자문서솔루션", "솔루션전환", "전환테스트", "active-active", "commit message", "cuda", "perl", "crawler", "ast", "code-analysis", "c++", "c-sharp", "csharp", "jupyter notebook", "avx512", "engineering", "productivity", "racket", "browser-use", "scheduling", "calendar", "tauri", "desktop", "php", "coolify", "databases", "deployment", "ebpf", "freebsd", "http", "esp-32", "apps-script", "devtools", "ffmpeg", "framework", "admob", "app-release", "app-store", "remote control", "gpu", "angular-roadmap", "backend-roadmap", "computer-science", "flutter", "chrome-extension", "command-line-tool", "editor", "accessibility"]),
    ("보안·프라이버시", ["보안", "security", "privacy", "폐쇄망", "인증", "개인정보", "개인정보 보호", "사고 대응", "장애 조치", "장애대응", "장애점검", "비인가접근", "데이터유출", "비밀번호변경", "스미싱주의", "피싱주의", "방화벽", "bugbounty", "infosec", "mitm", "anti-bot", "antidetect", "osint", "recon", "investigation"]),
    ("문서·미디어처리", ["stt", "asr", "whisper", "ocr", "docx", "hwpx", "hwp", "paper", "화자분리", "sensevoice", "presentation", "ppt", "slides", "slideshow", "document-parser", "hancom", "document workflow", "electronic signature", "document-analysis", "extract-data", "canvas", "collaboration", "diagrams", "drawing", "speaker diarization"]),
    ("커뮤니티·행사", ["공모전", "해커톤", "밋업", "산업 행사", "참가 신청", "교육 사례", "공모전 목록", "bifan", "bff", "gmaff"]),
    ("상거래·생활서비스", ["패밀리세일", "기획전", "더현대", "현대식품관", "lg전자", "배송 설치", "분양", "입주자 모집", "재공급", "qr 안내", "회원 관리", "가격 페이지", "서비스 도메인", "이용약관", "제휴 상품", "온라인 쇼핑", "공식 쇼핑몰", "아디다스", "다이닝", "매장 안내", "식당가", "식품", "택배", "배송", "재배송", "배송지연", "배송기사", "배송위치", "영업안내", "숙박", "리조트", "회원권", "멤버십", "쿠폰", "포인트", "티빙", "vip", "켄싱턴", "그랜드켄싱턴", "입회금반환", "상담안내", "이동서비스", "차량대여", "발매여부", "출시상태"]),
    ("생활·관계", ["여행", "일상", "건강", "가족", "연애", "친구", "운동", "음식", "취미", "운세", "일본", "영화", "게임", "주짓수", "와이파이도시락", "관상", "팔라버", "메모", "언어유희", "관계도"]),
]


def special_parent(topic: str) -> str | None:
    if re.fullmatch(r"\d+차", topic) or re.fullmatch(r"\d+/\d+", topic) or re.fullmatch(r"\d+개", topic):
        return "소스·링크·수집"
    if re.search(r"\d+포트", topic):
        return "개발·인프라"
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--build", action="store_true")
    ap.parse_args()
    topics = vault.get_all_topics()  # {topic: note_count}

    tax = {label: {"topics": [], "note_count": 0} for label, _ in PARENTS}
    tax["기타"] = {"topics": [], "note_count": 0}

    for topic, cnt in topics.items():
        low = topic.lower()
        placed = special_parent(topic)
        for label, kws in PARENTS:
            if placed:
                break
            if any(k in low for k in kws):
                placed = label
                break
        placed = placed or "기타"
        tax[placed]["topics"].append({"topic": topic, "notes": cnt})
        tax[placed]["note_count"] += cnt

    # 정렬: 카테고리 내 토픽을 노트수 desc
    for v in tax.values():
        v["topics"].sort(key=lambda x: -x["notes"])

    out = VAULT / "indexes" / "topic_taxonomy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total_topics": len(topics),
        "categories": {k: {"topic_count": len(v["topics"]), "note_count": v["note_count"],
                           "topics": [t["topic"] for t in v["topics"]]}
                       for k, v in tax.items() if v["topics"]},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[topic_taxonomy] {len(topics)} topics -> {out.relative_to(VAULT)}\n")
    for label, _ in PARENTS + [("기타", None)]:
        v = tax[label]
        if not v["topics"]:
            continue
        head = ", ".join(t["topic"] for t in v["topics"][:6])
        print(f"  {label} ({len(v['topics'])}토픽, {v['note_count']}노트): {head}{' …' if len(v['topics'])>6 else ''}")
    unmatched = len(tax['기타']['topics'])
    rate = (len(topics) - unmatched) / len(topics) * 100 if topics else 0
    print(f"\n  분류율: {rate:.0f}% (기타 {unmatched})")


if __name__ == "__main__":
    main()
