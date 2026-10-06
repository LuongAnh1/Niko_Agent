"""Client tối thiểu cho Ollama `/v1/systemone`.

Nimble trong repo này chỉ làm nhiệm vụ "ra quyết định nhanh": chọn một label
trong schema đã cho. Vì vậy client cố tình dùng stdlib (`urllib`) thay vì SDK
ngoài, giữ phần triage nhẹ, dễ test và không kéo thêm dependency vào bot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from niko.config import env_value


DEFAULT_DECISION_MODEL_BASE_URL = "http://localhost:11434"
DEFAULT_DECISION_MODEL_NAME = "nimble"
DEFAULT_DECISION_MODEL_TIMEOUT_SECONDS = 10.0
DEFAULT_DECISION_MODEL_KEEP_ALIVE = "-1"


@dataclass(frozen=True)
class ChoiceDecision:
    """Kết quả thô của một câu hỏi `choice` từ System One."""

    choice: str
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionModelConfig:
    """Cấu hình Ollama/Nimble đọc từ env hoặc truyền trực tiếp trong test."""

    base_url: str = DEFAULT_DECISION_MODEL_BASE_URL
    model: str = DEFAULT_DECISION_MODEL_NAME
    timeout_seconds: float = DEFAULT_DECISION_MODEL_TIMEOUT_SECONDS
    keep_alive: int | float | str | None = -1


def load_decision_model_config() -> DecisionModelConfig:
    """Đọc cấu hình decision model từ `NIKO_DECISION_MODEL_*`."""
    timeout_raw = env_value(
        "NIKO_DECISION_MODEL_TIMEOUT_SECONDS",
        str(DEFAULT_DECISION_MODEL_TIMEOUT_SECONDS),
    ).strip()
    try:
        timeout_seconds = max(0.1, float(timeout_raw))
    except ValueError:
        timeout_seconds = DEFAULT_DECISION_MODEL_TIMEOUT_SECONDS

    return DecisionModelConfig(
        base_url=env_value("NIKO_DECISION_MODEL_BASE_URL", DEFAULT_DECISION_MODEL_BASE_URL).strip(),
        model=env_value("NIKO_DECISION_MODEL_NAME", DEFAULT_DECISION_MODEL_NAME).strip(),
        timeout_seconds=timeout_seconds,
        keep_alive=parse_keep_alive(
            env_value("NIKO_DECISION_MODEL_KEEP_ALIVE", DEFAULT_DECISION_MODEL_KEEP_ALIVE).strip()
        ),
    )


def parse_keep_alive(raw_value: str) -> int | float | str | None:
    """Parse `keep_alive` theo đúng kiểu Ollama nhận: số, duration string hoặc None."""
    if not raw_value:
        return None
    lowered = raw_value.lower()
    if lowered in {"none", "null"}:
        return None
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        return raw_value


def systemone_choice(
    *,
    state: Any,
    question_name: str,
    instructions: Any,
    criteria: dict[str, Any],
    config: DecisionModelConfig | None = None,
) -> ChoiceDecision:
    """Gửi một câu hỏi choice tới Ollama và trả về label được chọn."""
    config = config or load_decision_model_config()
    payload = build_systemone_choice_payload(
        state=state,
        question_name=question_name,
        instructions=instructions,
        criteria=criteria,
        config=config,
    )
    response = post_json(
        f"{config.base_url.rstrip('/')}/v1/systemone",
        payload,
        timeout_seconds=config.timeout_seconds,
    )
    return parse_systemone_choice_response(response, question_name)


def unload_decision_model(
    config: DecisionModelConfig | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Yeu cau Ollama unload model khoi RAM/VRAM neu dang duoc giu loaded."""
    config = config or load_decision_model_config()
    payload = build_ollama_unload_payload(config)
    return post_json(
        f"{config.base_url.rstrip('/')}/api/generate",
        payload,
        timeout_seconds=timeout_seconds or config.timeout_seconds,
    )


def build_systemone_choice_payload(
    *,
    state: Any,
    question_name: str,
    instructions: Any,
    criteria: dict[str, Any],
    config: DecisionModelConfig,
) -> dict[str, Any]:
    """Dựng payload System One; caller quyết định state/instructions/criteria."""
    payload: dict[str, Any] = {
        "model": config.model,
        "state": state,
        "questions": {
            question_name: {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }
        },
    }
    if config.keep_alive is not None:
        payload["keep_alive"] = config.keep_alive
    return payload


def build_ollama_unload_payload(config: DecisionModelConfig) -> dict[str, Any]:
    """Payload unload theo API Ollama: prompt rong + keep_alive=0."""
    return {
        "model": config.model,
        "prompt": "",
        "stream": False,
        "keep_alive": 0,
    }


def post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """POST JSON và đổi lỗi mạng/HTTP thành RuntimeError dễ fallback trong graph."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama decision model HTTP {exc.code}: {body.strip()}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"Ollama decision model timed out after {timeout_seconds:.1f}s.") from exc
    except URLError as exc:
        raise RuntimeError(f"Khong ket noi duoc Ollama decision model: {exc.reason}") from exc

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama decision model tra ve JSON khong hop le.") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Ollama decision model response phai la JSON object.")
    return decoded


def parse_systemone_choice_response(response: dict[str, Any], question_name: str) -> ChoiceDecision:
    """Validate response vừa đủ để graph không phải xử lý shape Ollama trực tiếp."""
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise RuntimeError("Ollama decision model response thieu answers.")
    answer = answers.get(question_name)
    if not isinstance(answer, dict):
        raise RuntimeError(f"Ollama decision model response thieu answer {question_name!r}.")

    answer_type = str(answer.get("type", "")).strip().lower()
    if answer_type and answer_type != "choice":
        raise RuntimeError(f"Ollama decision model answer khong phai choice: {answer_type}.")

    choice = str(answer.get("choice", "")).strip()
    if not choice:
        raise RuntimeError("Ollama decision model khong tra ve choice.")

    confidence = parse_optional_float(answer.get("confidence"))
    probabilities = parse_probabilities(answer.get("probabilities"))
    model = str(response.get("model", "")).strip()
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return ChoiceDecision(
        choice=choice,
        confidence=confidence,
        probabilities=probabilities,
        model=model,
        usage=usage,
    )


def parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_probabilities(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    probabilities: dict[str, float] = {}
    for key, raw_probability in value.items():
        probability = parse_optional_float(raw_probability)
        if probability is not None:
            probabilities[str(key)] = probability
    return probabilities
