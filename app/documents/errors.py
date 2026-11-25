"""문서 파싱 오류: 손상·암호화·보안 검사 거부 파일을 사용자에게 400과 사유로 알리기 위한 예외."""

from __future__ import annotations


class DocumentParseError(ValueError):
    """사용자가 올린 문서를 열 수 없음(손상, 형식 불일치, XXE·zip bomb 등 보안 거부)."""
