from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import os
import socket
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pypdf import PdfReader

from .contracts import Source, new_id
from .errors import DomainError

MAX_BYTES = 5_000_000
USER_AGENT = "E2PAgent/0.2 Korean public research collector (+local demo)"

# This is a source-ranking aid, not an assertion that every page is methodologically sound.
KOREAN_OFFICIAL_HOSTS = {
    "kosis.kr",
    "data.go.kr",
    "kostat.go.kr",
    "korea.kr",
    "mois.go.kr",
    "mohw.go.kr",
    "molit.go.kr",
    "msit.go.kr",
    "mof.go.kr",
    "nars.go.kr",
    "law.go.kr",
}
KOREAN_RESEARCH_HOSTS = {
    "scienceon.kisti.re.kr",
    "riss.kr",
    "kihasa.re.kr",
    "krivet.re.kr",
    "krihs.re.kr",
    "nars.go.kr",
    "kci.go.kr",
}
DATA_GO_ENDPOINTS = {
    "welfare_list": {
        "endpoint": "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
        "organization": "한국사회보장정보원 / 공공데이터포털",
        "survey_name": "중앙부처 복지서비스 OpenAPI",
    },
    "nps_subscription": {
        "endpoint": "https://apis.data.go.kr/B552015/NpsSbscrbInfoProvdServiceV2/getSbscrbSttusInfoSearchV2",
        "organization": "국민연금공단 / 공공데이터포털",
        "survey_name": "국민연금 가입현황 OpenAPI",
    },
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)
            if self._in_title and not self.title:
                self.title = cleaned


class _SearchExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            label = " ".join("".join(self._text).split())
            self.links.append((self._href, label))
            self._href = None


class _SafeRedirect(HTTPRedirectHandler):
    """Follow only redirects whose destination also passes the public-network gate."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def source_trust(url: str) -> tuple[str, str, str]:
    host = (urlparse(url).hostname or "").lower()
    if any(host == item or host.endswith(f".{item}") for item in KOREAN_OFFICIAL_HOSTS):
        return "korean_official", "official_statistics_or_policy", host
    if any(host == item or host.endswith(f".{item}") for item in KOREAN_RESEARCH_HOSTS):
        return "korean_research", "academic_or_policy_research", host
    return "unreviewed_web", "web_page", host


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise DomainError("UNSAFE_URL", "http(s) 공개 URL만 수집할 수 있습니다.")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise DomainError("UNSAFE_URL", "로컬 또는 사설 네트워크 URL은 수집할 수 없습니다.")
    try:
        addresses = {
            record[4][0] for record in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as error:
        raise DomainError("URL_RESOLUTION_FAILED", "출처 도메인을 해석할 수 없습니다.") from error
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise DomainError("UNSAFE_URL", "사설·loopback·예약 네트워크의 출처는 수집할 수 없습니다.")


def _download(url: str) -> tuple[bytes, str]:
    _assert_public_url(url)
    request = Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/json,text/plain;q=0.8"}
    )
    try:
        with build_opener(_SafeRedirect()).open(request, timeout=20) as response:
            final_url = response.geturl()
            _assert_public_url(final_url)
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise DomainError("SOURCE_TOO_LARGE", "출처 본문은 5MB를 넘을 수 없습니다.")
            return body, response.headers.get_content_type()
    except DomainError:
        raise
    except Exception as error:
        raise DomainError(
            "SOURCE_FETCH_FAILED", "출처를 읽지 못했습니다.", details={"reason": type(error).__name__}
        ) from error


def _extract_pdf_text(body: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(body))
        pages = [page.extract_text() or "" for page in reader.pages[:80]]
        extracted = "\n".join(page.strip() for page in pages if page.strip())
        return extracted or "[PDF는 저장되었지만 텍스트를 추출하지 못했습니다. 원문을 사람이 검토하세요.]"
    except Exception:
        return "[PDF는 저장되었지만 텍스트를 추출하지 못했습니다. 원문을 사람이 검토하세요.]"


def _store_download(
    root: Path, fetch_url: str, display_url: str, metadata: dict[str, object], source_kind: str | None = None
) -> Source:
    body, content_type = _download(fetch_url)
    if source_kind in {"kosis_openapi", "data_go_kr_openapi"}:
        _raise_upstream_api_error(body)
    digest = hashlib.sha256(body).hexdigest()
    is_pdf = "pdf" in content_type or urlparse(display_url).path.lower().endswith(".pdf")
    extension = (
        ".pdf" if is_pdf else ".html" if "html" in content_type else ".json" if "json" in content_type else ".txt"
    )
    destination = root / "data" / "source-cache" / f"{digest}{extension}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(body)

    parser = _TextExtractor()
    raw_text = body.decode("utf-8", errors="replace")
    if is_pdf:
        extracted = _extract_pdf_text(body)
    elif "html" in content_type:
        parser.feed(raw_text)
        extracted = "\n".join(parser.parts)
    else:
        extracted = raw_text
    text_path = destination.with_suffix(".extracted.txt")
    if not text_path.exists():
        text_path.write_text(extracted[:MAX_BYTES], encoding="utf-8")

    sample_size = metadata.get("sample_size")
    if sample_size not in (None, ""):
        try:
            sample_size = int(sample_size)
        except (TypeError, ValueError) as error:
            raise DomainError("INVALID_SAMPLE_SIZE", "표본 크기는 정수여야 합니다.") from error
    tier, inferred_kind, domain = source_trust(display_url)
    return Source(
        id=new_id("src"),
        url=display_url,
        title=str(metadata.get("title") or parser.title or display_url),
        organization=str(metadata.get("organization") or "미지정 기관"),
        survey_name=str(metadata.get("survey_name") or "미지정 조사"),
        published_at=str(metadata.get("published_at") or "미지정"),
        observed_period=str(metadata.get("observed_period") or "미지정"),
        population=str(metadata.get("population") or "미지정"),
        sample_size=sample_size,
        snapshot_hash=digest,
        snapshot_path=str(text_path.relative_to(root)),
        trust_tier=tier,
        source_kind=source_kind or inferred_kind,
        source_domain=domain,
    )


def _raise_upstream_api_error(body: bytes) -> None:
    """Do not turn a machine-readable API error into a durable evidence artifact."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("err") not in (None, "0", 0, "00"):
        raise DomainError(
            "UPSTREAM_API_ERROR",
            "공공 API가 데이터를 반환하지 않았습니다.",
            details={
                "provider_code": str(payload.get("err")),
                "provider_message": str(payload.get("errMsg", ""))[:240],
            },
        )
    header = (payload.get("response") or {}).get("header") if isinstance(payload.get("response"), dict) else None
    if isinstance(header, dict) and str(header.get("resultCode", "00")) not in {"00", "0"}:
        raise DomainError(
            "UPSTREAM_API_ERROR",
            "공공 API가 데이터를 반환하지 않았습니다.",
            details={
                "provider_code": str(header.get("resultCode")),
                "provider_message": str(header.get("resultMsg", ""))[:240],
            },
        )


def search_public_web(query: str, trusted_korean_only: bool = False) -> list[dict[str, str]]:
    """Keyless candidate finder. Results are candidates, never automatic statistical evidence."""
    cleaned = " ".join(query.split())
    if not cleaned:
        raise DomainError("EMPTY_QUERY", "검색어가 비어 있습니다.")
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    query_attempts = [f"KOSIS 공공데이터 {cleaned}", cleaned] if trusted_korean_only else [cleaned]
    for search_attempt in query_attempts:
        body, _ = _download(f"https://www.bing.com/search?q={quote_plus(search_attempt)}")
        parser = _SearchExtractor()
        parser.feed(body.decode("utf-8", errors="replace"))
        for href, title in parser.links:
            parsed = urlparse(href)
            if parsed.hostname and parsed.hostname.endswith("bing.com") and parsed.path.startswith("/ck/"):
                encoded = parse_qs(parsed.query).get("u", [""])[0]
                try:
                    href = base64.urlsafe_b64decode(encoded.removeprefix("a1") + "===").decode()
                except (ValueError, UnicodeDecodeError):
                    continue
            if not href.startswith(("https://", "http://")) or href in seen or not title:
                continue
            tier, kind, domain = source_trust(href)
            if trusted_korean_only and tier == "unreviewed_web":
                continue
            seen.add(href)
            results.append(
                {"title": html.unescape(title), "url": href, "domain": domain, "trust_tier": tier, "source_kind": kind}
            )
            if len(results) == 10:
                return results
    return results


def fetch_source(root: Path, url: str, metadata: dict[str, object]) -> Source:
    return _store_download(root, url, url, metadata)


def fetch_kosis_statistics(root: Path, payload: dict[str, object]) -> Source:
    api_key = os.getenv("KOSIS_API_KEY")
    if not api_key:
        raise DomainError(
            "KOSIS_KEY_REQUIRED", "KOSIS_API_KEY를 환경 변수로 설정하면 KOSIS OpenAPI를 내려받을 수 있습니다."
        )
    user_stats_id = str(payload.get("user_stats_id", "")).strip()
    parameters: dict[str, str] = {"method": "getList", "apiKey": api_key, "format": "json", "jsonVD": "Y"}
    if user_stats_id:
        parameters["userStatsId"] = user_stats_id
        parameters["prdSe"] = str(payload.get("period_type", "Y"))
        parameters["newEstPrdCnt"] = str(payload.get("newest_period_count", 3))
    else:
        required = {
            "org_id": "orgId",
            "table_id": "tblId",
            "item_id": "itmId",
            "period_type": "prdSe",
            "classification_1": "objL1",
        }
        missing = [name for name in required if not str(payload.get(name, "")).strip()]
        if missing:
            raise DomainError(
                "KOSIS_PARAMETERS_REQUIRED",
                "KOSIS에는 user_stats_id 또는 통계표 필수 파라미터가 필요합니다.",
                details={"missing": missing},
            )
        for input_name, api_name in required.items():
            parameters[api_name] = str(payload[input_name])
        for index in range(2, 9):
            value = str(payload.get(f"classification_{index}", "")).strip()
            if value:
                parameters[f"objL{index}"] = value
        for key, api_name in (
            ("start_period", "startPrdDe"),
            ("end_period", "endPrdDe"),
            ("newest_period_count", "newEstPrdCnt"),
        ):
            value = str(payload.get(key, "")).strip()
            if value:
                parameters[api_name] = value
    endpoint = (
        "https://kosis.kr/openapi/statisticsData.do"
        if user_stats_id
        else "https://kosis.kr/openapi/Param/statisticsParameterData.do"
    )
    fetch_url = f"{endpoint}?{urlencode(parameters)}"
    display_parameters = {key: ("[configured]" if key == "apiKey" else value) for key, value in parameters.items()}
    display_url = f"{endpoint}?{urlencode(display_parameters)}"
    return _store_download(
        root,
        fetch_url,
        display_url,
        {
            "title": str(payload.get("title") or "KOSIS OpenAPI 통계표"),
            "organization": "국가통계포털 KOSIS",
            "survey_name": str(payload.get("survey_name") or "KOSIS OpenAPI"),
            "published_at": str(payload.get("published_at") or "API 수집 시점"),
            "observed_period": str(payload.get("observed_period") or parameters.get("prdSe", "미지정")),
            "population": str(payload.get("population") or "통계표 원문에서 검토 필요"),
            "sample_size": payload.get("sample_size"),
        },
        "kosis_openapi",
    )


def fetch_data_go_api(root: Path, payload: dict[str, object]) -> Source:
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY")
    if not service_key:
        raise DomainError(
            "DATA_GO_KEY_REQUIRED",
            "DATA_GO_KR_SERVICE_KEY를 환경 변수로 설정하면 공공데이터포털 API를 내려받을 수 있습니다.",
        )
    service = str(payload.get("service", "")).strip()
    entry = DATA_GO_ENDPOINTS.get(service)
    if not entry:
        raise DomainError(
            "UNKNOWN_PUBLIC_DATA_SERVICE",
            "지원하지 않는 공공데이터 서비스입니다.",
            details={"available": sorted(DATA_GO_ENDPOINTS)},
        )
    supplied = payload.get("parameters", {})
    if not isinstance(supplied, dict) or any(isinstance(value, (dict, list)) for value in supplied.values()):
        raise DomainError("INVALID_PUBLIC_DATA_PARAMETERS", "parameters는 단순 키-값 JSON 객체여야 합니다.")
    parameters = {str(key): str(value) for key, value in supplied.items()}
    parameters.update({"serviceKey": service_key, "_type": "json"})
    fetch_url = f"{entry['endpoint']}?{urlencode(parameters)}"
    display_parameters = {key: ("[configured]" if key == "serviceKey" else value) for key, value in parameters.items()}
    display_url = f"{entry['endpoint']}?{urlencode(display_parameters)}"
    return _store_download(
        root,
        fetch_url,
        display_url,
        {
            "title": str(payload.get("title") or entry["survey_name"]),
            "organization": entry["organization"],
            "survey_name": entry["survey_name"],
            "published_at": str(payload.get("published_at") or "API 수집 시점"),
            "observed_period": str(payload.get("observed_period") or "API 응답 기준"),
            "population": str(payload.get("population") or "원문 데이터 설명에서 검토 필요"),
            "sample_size": payload.get("sample_size"),
        },
        "data_go_kr_openapi",
    )


def source_excerpt(root: Path, source: dict[str, object], maximum_characters: int = 12000) -> str:
    root = root.resolve()
    relative = Path(str(source["snapshot_path"]))
    candidate = (root / relative).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise DomainError("SOURCE_ARTIFACT_MISSING", "출처 스냅샷 파일을 찾을 수 없습니다.")
    return candidate.read_text(encoding="utf-8", errors="replace")[:maximum_characters]
