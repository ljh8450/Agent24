from __future__ import annotations

from typing import Any


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _json_body(schema: str, description: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "required": True,
        "content": {"application/json": {"schema": _ref(schema)}},
    }
    if description:
        body["description"] = description
    return body


def _responses(*, success: int = 200, success_description: str = "처리 결과", created: bool = False) -> dict[str, Any]:
    responses: dict[str, Any] = {
        str(success): {"description": success_description, "content": {"application/json": {"schema": _ref("Run")}}},
        "400": {"description": "도메인 또는 입력 오류", "content": {"application/json": {"schema": _ref("ErrorResponse")}}},
        "401": {"description": "인증 실패", "content": {"application/json": {"schema": _ref("ErrorResponse")}}},
        "404": {"description": "실행·출처·artifact 없음", "content": {"application/json": {"schema": _ref("ErrorResponse")}}},
        "500": {"description": "내부 오류", "content": {"application/json": {"schema": _ref("ErrorResponse")}}},
    }
    if created and success != 201:
        responses["201"] = {"description": "생성 결과", "content": {"application/json": {"schema": _ref("Run")}}}
    return responses


def _operation(
    summary: str,
    *,
    request: str | None = None,
    success: int = 200,
    success_description: str = "처리 결과",
    created: bool = False,
    tags: list[str] | None = None,
    description: str | None = None,
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "summary": summary,
        "responses": _responses(
            success=success,
            success_description=success_description,
            created=created,
        ),
        "security": [{"bearerAuth": []}],
    }
    if description:
        operation["description"] = description
    if tags:
        operation["tags"] = tags
    if parameters:
        operation["parameters"] = parameters
    if request:
        operation["requestBody"] = _json_body(request)
    return operation


def build_openapi_document() -> dict[str, Any]:
    run_id = {"$ref": "#/components/parameters/RunId"}
    source_id = {"$ref": "#/components/parameters/SourceId"}
    filename = {"$ref": "#/components/parameters/Filename"}

    paths: dict[str, Any] = {
        "/openapi.json": {
            "get": {
                "summary": "OpenAPI 문서 조회",
                "security": [],
                "responses": {"200": {"description": "OpenAPI 3.0.3 문서"}},
            }
        },
        "/api/health": {
            "get": {
                "summary": "서버 상태 확인",
                "tags": ["system"],
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "정상 상태", "content": {"application/json": {"schema": _ref("HealthResponse")}}}},
            }
        },
        "/api/source-catalog": {
            "get": {
                "summary": "공개 출처 카탈로그 조회",
                "tags": ["sources"],
                "responses": {"200": {"description": "출처 목록", "content": {"application/json": {"schema": _ref("SourceCatalog")}}}},
            }
        },
        "/api/chat": {
            "post": _operation(
                "정책 검토 실행 시작",
                request="ChatRequest",
                success=201,
                success_description="새 실행과 초기 정책 계획",
                tags=["workflow"],
            )
        },
        "/api/agent/review": {
            "post": _operation(
                "정책 검토 전체 실행",
                request="ChatRequest",
                success=201,
                success_description="자동 검토 완료 결과",
                tags=["workflow"],
            )
        },
        "/api/agent/review/stream": {
            "post": _operation(
                "정책 검토 SSE 스트림",
                request="ChatRequest",
                success=201,
                success_description="text/event-stream 이벤트 스트림",
                tags=["workflow"],
                description="review.accepted, tool.update, run.updated, research.completed, review.completed, error 이벤트를 전송합니다.",
            )
        },
        "/api/runs/{run_id}": {
            "get": _operation("실행 상태 조회", parameters=[run_id], tags=["runs"])
        },
        "/api/runs/{run_id}/policy/plan": {
            "post": _operation("정책 계획 조회", parameters=[run_id], tags=["policy"])
        },
        "/api/runs/{run_id}/policy/research": {
            "post": _operation("정책 출처 탐색", parameters=[run_id], tags=["policy", "sources"])
        },
        "/api/runs/{run_id}/policy/panel-review": {
            "post": _operation("가중 합성 패널 정책 검토", parameters=[run_id], tags=["policy"])
        },
        "/api/runs/{run_id}/schema": {
            "post": _operation("변수 스키마 설정", request="SchemaRequest", parameters=[run_id], tags=["statistics"])
        },
        "/api/runs/{run_id}/sources/search": {
            "post": _operation("출처 검색", request="SourceSearchRequest", parameters=[run_id], tags=["sources"])
        },
        "/api/runs/{run_id}/sources/fetch": {
            "post": _operation("출처 원문 스냅샷 저장", request="SourceFetchRequest", success=201, parameters=[run_id], tags=["sources"])
        },
        "/api/runs/{run_id}/sources/kosis": {
            "post": _operation("KOSIS 통계표 조회", request="KosisRequest", success=201, parameters=[run_id], tags=["sources"])
        },
        "/api/runs/{run_id}/sources/data-go": {
            "post": _operation("공공데이터포털 API 조회", request="DataGoRequest", success=201, parameters=[run_id], tags=["sources"])
        },
        "/api/runs/{run_id}/sources/{source_id}/extract": {
            "post": _operation("출처에서 통계 제약 후보 추출", parameters=[run_id, source_id], tags=["constraints"])
        },
        "/api/runs/{run_id}/constraints": {
            "post": _operation("통계 제약 추가", request="ConstraintRequest", success=201, parameters=[run_id], tags=["constraints"])
        },
        "/api/runs/{run_id}/constraints/approve": {
            "post": _operation("통계 제약 승인", request="ApproveConstraintsRequest", parameters=[run_id], tags=["constraints"])
        },
        "/api/runs/{run_id}/compute": {
            "post": _operation("식별구간 및 결합분포 계산", request="ComputeRequest", parameters=[run_id], tags=["statistics"])
        },
        "/api/runs/{run_id}/personas": {
            "post": _operation("합성 페르소나 생성", request="PersonasRequest", parameters=[run_id], tags=["personas"])
        },
        "/api/runs/{run_id}/survey": {
            "post": _operation("합성 설문 실행", request="SurveyRequest", parameters=[run_id], tags=["personas"])
        },
        "/api/runs/{run_id}/narratives": {
            "post": _operation("페르소나 서술 생성", parameters=[run_id], tags=["personas"])
        },
        "/api/runs/{run_id}/persona-chat": {
            "post": _operation("합성 페르소나 속성 응답", request="PersonaChatRequest", parameters=[run_id], tags=["personas"])
        },
        "/api/runs/{run_id}/holdout/seal": {
            "post": _operation("홀드아웃 예측 봉인", parameters=[run_id], tags=["evaluation"])
        },
        "/api/runs/{run_id}/holdout/evaluate": {
            "post": _operation("홀드아웃 평가", request="HoldoutEvaluateRequest", parameters=[run_id], tags=["evaluation"])
        },
        "/api/runs/{run_id}/report": {
            "post": _operation("보고서 및 artifact 생성", parameters=[run_id], tags=["artifacts"])
        },
        "/api/runs/{run_id}/artifacts/{filename}": {
            "get": {
                "summary": "실행 artifact 조회",
                "tags": ["artifacts"],
                "parameters": [run_id, filename],
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "report.md, panel.jsonl, interviews.jsonl 또는 run.json 본문"},
                    "401": {"description": "인증 실패", "content": {"application/json": {"schema": _ref("ErrorResponse")}}},
                    "404": {"description": "허용되지 않은 artifact 또는 파일 없음", "content": {"application/json": {"schema": _ref("ErrorResponse")}}},
                },
            }
        },
    }

    schemas: dict[str, Any] = {
        "ErrorResponse": {
            "type": "object",
            "required": ["error"],
            "properties": {
                "error": {
                    "type": "object",
                    "required": ["code", "message", "details"],
                    "properties": {
                        "code": {"type": "string", "example": "INVALID_JSON"},
                        "message": {"type": "string"},
                        "details": {"type": "object", "additionalProperties": True},
                    },
                }
            },
        },
        "HealthResponse": {
            "type": "object",
            "required": ["status", "root"],
            "properties": {"status": {"type": "string", "enum": ["ok"]}, "root": {"type": "string"}},
        },
        "ChatRequest": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 4, "description": "정책·서비스 검토 질문"},
                "event_id": {"type": "string", "nullable": True, "description": "멱등성에 사용하는 클라이언트 이벤트 ID"},
            },
            "additionalProperties": False,
        },
        "Variable": {
            "type": "object",
            "required": ["id", "categories"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "label": {"type": "string"},
                "categories": {"type": "array", "minItems": 2, "uniqueItems": True, "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "SchemaRequest": {
            "type": "object",
            "required": ["variables"],
            "properties": {"variables": {"type": "array", "minItems": 1, "maxItems": 7, "items": _ref("Variable")}},
            "additionalProperties": False,
        },
        "SourceSearchRequest": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "trusted_korean_only": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        "SourceFetchRequest": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "format": "uri", "pattern": "^https?://"},
                "metadata": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        "KosisRequest": {
            "type": "object",
            "description": "user_stats_id 방식 또는 통계표 파라미터 방식을 사용합니다. API 키는 요청에 포함하지 않습니다.",
            "oneOf": [
                {
                    "required": ["user_stats_id"],
                    "properties": {
                        "user_stats_id": {"type": "string"},
                        "period_type": {"type": "string", "default": "Y"},
                        "newest_period_count": {"type": "integer", "minimum": 1, "default": 3},
                    },
                },
                {
                    "required": ["org_id", "table_id", "item_id", "period_type", "classification_1"],
                    "properties": {
                        "org_id": {"type": "string"},
                        "table_id": {"type": "string"},
                        "item_id": {"type": "string"},
                        "period_type": {"type": "string"},
                        "classification_1": {"type": "string"},
                        "classification_2": {"type": "string"},
                        "classification_3": {"type": "string"},
                        "start_period": {"type": "string"},
                        "end_period": {"type": "string"},
                        "newest_period_count": {"type": "integer", "minimum": 1},
                    },
                },
            ],
            "properties": {
                "title": {"type": "string"},
                "survey_name": {"type": "string"},
                "published_at": {"type": "string"},
                "observed_period": {"type": "string"},
                "population": {"type": "string"},
                "sample_size": {"type": "integer", "minimum": 0, "nullable": True},
            },
        },
        "DataGoRequest": {
            "type": "object",
            "required": ["service"],
            "properties": {
                "service": {"type": "string", "enum": ["welfare_list", "nps_subscription"]},
                "parameters": {"type": "object", "additionalProperties": {"type": "string"}},
                "title": {"type": "string"},
                "published_at": {"type": "string"},
                "observed_period": {"type": "string"},
                "population": {"type": "string"},
                "sample_size": {"type": "integer", "minimum": 0, "nullable": True},
            },
            "additionalProperties": False,
        },
        "ConstraintRequest": {
            "type": "object",
            "required": ["source_id", "where", "relation", "value", "population_compatibility"],
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "source_id": {"type": "string"},
                "where": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "string"}},
                "relation": {"type": "string", "enum": ["eq", "gte", "lte"]},
                "value": {"type": "number", "minimum": 0, "maximum": 1},
                "population_compatibility": {"type": "string", "enum": ["exact", "restricted", "broader", "overlap_unknown", "incompatible"]},
                "raw_statement": {"type": "string"},
                "source_categories": {"type": "object", "additionalProperties": {"type": "string"}},
                "mapping_note": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "ApproveConstraintsRequest": {
            "type": "object",
            "required": ["constraint_ids"],
            "properties": {
                "constraint_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
                "override_notes": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "ComputeRequest": {
            "type": "object",
            "required": ["estimand"],
            "properties": {
                "estimand": {
                    "type": "object",
                    "required": ["numerator"],
                    "properties": {
                        "numerator": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "string"}},
                        "denominator": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "string"}, "nullable": True},
                    },
                    "additionalProperties": False,
                },
                "dag_candidates": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "selected_model": {"type": "string", "enum": ["maximum_entropy"], "default": "maximum_entropy"},
            },
            "additionalProperties": False,
        },
        "PersonasRequest": {
            "type": "object",
            "required": ["adult_population_confirmed"],
            "properties": {
                "adult_population_confirmed": {"type": "boolean", "const": True},
                "count": {"type": "integer", "minimum": 1, "maximum": 3, "default": 3},
                "seed": {"type": "integer", "default": 20260801},
            },
            "additionalProperties": False,
        },
        "SurveyRequest": {
            "type": "object",
            "required": ["policy_question"],
            "properties": {"policy_question": {"type": "string", "minLength": 4}},
            "additionalProperties": False,
        },
        "PersonaChatRequest": {
            "type": "object",
            "required": ["persona_id", "question"],
            "properties": {
                "persona_id": {"type": "string"},
                "question": {"type": "string", "minLength": 1},
                "allowed_variable": {"type": "string", "nullable": True},
            },
            "additionalProperties": False,
        },
        "HoldoutEvaluateRequest": {
            "type": "object",
            "required": ["actual_distribution"],
            "properties": {
                "actual_distribution": {"type": "array", "minItems": 1, "items": {"type": "number", "minimum": 0}},
            },
            "additionalProperties": False,
        },
        "Run": {"type": "object", "description": "실행 상태와 워크플로 결과. 단계별 result/events 필드는 실행 상태에 따라 달라집니다.", "additionalProperties": True},
        "SourceCatalog": {"type": "object", "required": ["sources", "data_go_services", "warning"], "additionalProperties": True},
    }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "E2P Agent API",
            "version": "0.1.0",
            "description": "근거 기반 정책 연구 에이전트의 HTTP API입니다. API 키는 요청에 포함하지 않고 서버 환경변수로 주입합니다.",
        },
        "servers": [{"url": "/", "description": "현재 서버"}],
        "tags": [
            {"name": "system", "description": "상태 확인"},
            {"name": "workflow", "description": "정책 검토 실행"},
            {"name": "runs", "description": "실행 상태"},
            {"name": "policy", "description": "정책 계획·패널 검토"},
            {"name": "sources", "description": "공개 출처 탐색·수집"},
            {"name": "constraints", "description": "통계 제약 검토"},
            {"name": "statistics", "description": "변수·식별구간 계산"},
            {"name": "personas", "description": "합성 페르소나·설문"},
            {"name": "evaluation", "description": "홀드아웃 평가"},
            {"name": "artifacts", "description": "실행 산출물"},
        ],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "GATEWAY_TOKEN"}
            },
            "parameters": {
                "RunId": {"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "정책 검토 실행 ID"},
                "SourceId": {"name": "source_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "같은 run에 저장된 출처 ID"},
                "Filename": {"name": "filename", "in": "path", "required": True, "schema": {"type": "string", "enum": ["report.md", "panel.jsonl", "interviews.jsonl", "run.json"]}, "description": "허용된 artifact 파일명"},
            },
            "schemas": schemas,
        },
    }
