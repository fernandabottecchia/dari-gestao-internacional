from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parent.parent
CENTRAL_CONFIG_DIR = PROJECT_DIR / "central_config"


class CentralClientError(RuntimeError):
    pass


class CentralUnavailableError(CentralClientError):
    pass


def _config_text(filename: str) -> str:
    path = CENTRAL_CONFIG_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def server_url() -> str:
    return ''


def api_token() -> str:
    return ''


def configured() -> bool:
    return False


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = server_url()
    token = api_token()

    if not base:
        raise CentralUnavailableError("Endereço do servidor central não configurado.")
    if not token:
        raise CentralUnavailableError("Token do servidor central não configurado.")

    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = Request(
        base + path,
        data=body,
        method=method,
        headers=headers,
    )

    try:
        with urlopen(req, timeout=8) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:
            detail = None
        raise CentralClientError(detail or f"Servidor central respondeu HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CentralUnavailableError(
            "Servidor central indisponível. A visualização local continua disponível, "
            "mas edições exigem conexão com o servidor central."
        ) from exc


def fetch_academic_calendar() -> dict:
    return _request("GET", "/api/v1/calendario-academico")


def save_academic_calendar(*, semester: str, start_date: str, end_date: str, is_current: bool) -> dict:
    return _request("POST", "/api/v1/calendario-academico", {
        "semester": semester, "start_date": start_date, "end_date": end_date,
        "is_current": is_current,
    })


def create_agreement_action(*, source_key: str, next_step: str, deadline_text: str | None, notes: str | None):
    return _request("POST", "/api/v1/acordos/actions", {
        "source_key": source_key,
        "next_step": next_step,
        "deadline_text": deadline_text,
        "notes": notes,
    })


def update_agreement_action(*, central_action_id: int, source_key: str, next_step: str, deadline_text: str | None, notes: str | None):
    return _request("PATCH", f"/api/v1/acordos/actions/{central_action_id}", {
        "source_key": source_key,
        "next_step": next_step,
        "deadline_text": deadline_text,
        "notes": notes,
    })


def toggle_agreement_action(*, central_action_id: int, source_key: str):
    return _request("POST", f"/api/v1/acordos/actions/{central_action_id}/toggle", {
        "source_key": source_key,
    })


def delete_agreement_action(*, central_action_id: int, source_key: str):
    return _request("DELETE", f"/api/v1/acordos/actions/{central_action_id}", {
        "source_key": source_key,
    })
