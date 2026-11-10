from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from engine.flaws.models import ContractStepSpec

VAR_PATTERN = re.compile(r"\{([a-zA-Z_]\w*)\}")


class ContractStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_index: int
    passed: bool
    method: str
    path: str
    expected_status: int
    actual_status: int | None = None
    expected_keys: list[str] = Field(default_factory=list)
    missing_keys: list[str] = Field(default_factory=list)
    saved_values: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class ContractResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    step_results: list[ContractStepResult] = Field(default_factory=list)
    failure_message: str | None = None


def _substitute_variables(
    val: Any,
    context: dict[str, Any],
) -> tuple[Any, list[str]]:
    """Recursively substitute {var} in string, dict, or list.

    Returns (substituted_val, missing_vars).
    """
    missing: list[str] = []

    def _sub(item: Any) -> Any:
        if isinstance(item, str):
            # Check for exact whole-string match first to preserve non-str types
            m = re.fullmatch(r"\{([a-zA-Z_]\w*)\}", item)
            if m:
                var_name = m.group(1)
                if var_name in context:
                    return context[var_name]
                missing.append(var_name)
                return item

            def repl(match: re.Match) -> str:
                var_name = match.group(1)
                if var_name in context:
                    return str(context[var_name])
                missing.append(var_name)
                return match.group(0)

            return VAR_PATTERN.sub(repl, item)
        if isinstance(item, dict):
            return {k: _sub(v) for k, v in item.items()}
        if isinstance(item, list):
            return [_sub(v) for v in item]
        return item

    res = _sub(val)
    return res, missing


def run_contract(
    steps: list[ContractStepSpec],
    target_url: str,
    probe_token: str | None = None,
    fixtures: dict[str, Any] | None = None,
    timeout_s: float = 5.0,
) -> ContractResult:
    """Execute contract steps sequentially against target URL.

    Probe token is passed via header 'X-Game-Probe' so gateway excludes it from stats.
    """
    context: dict[str, Any] = dict(fixtures or {})
    step_results: list[ContractStepResult] = []

    client = httpx.Client(timeout=timeout_s)
    try:
        for idx, step in enumerate(steps, start=1):
            # Substitute variables in path
            resolved_path, missing_path_vars = _substitute_variables(step.path, context)
            if missing_path_vars:
                msg = f"Step {idx} ({step.method} {step.path}): undefined variable(s) in path: {missing_path_vars}"
                step_res = ContractStepResult(
                    step_index=idx,
                    passed=False,
                    method=step.method,
                    path=step.path,
                    expected_status=step.expect_status,
                    error_message=msg,
                )
                step_results.append(step_res)
                return ContractResult(
                    passed=False,
                    step_results=step_results,
                    failure_message=msg,
                )

            # Substitute variables in headers
            resolved_headers, missing_header_vars = _substitute_variables(
                dict(step.headers), context
            )
            if missing_header_vars:
                msg = f"Step {idx} ({step.method} {step.path}): undefined variable(s) in headers: {missing_header_vars}"
                step_res = ContractStepResult(
                    step_index=idx,
                    passed=False,
                    method=step.method,
                    path=step.path,
                    expected_status=step.expect_status,
                    error_message=msg,
                )
                step_results.append(step_res)
                return ContractResult(
                    passed=False,
                    step_results=step_results,
                    failure_message=msg,
                )

            # Substitute variables in json_body
            resolved_json = None
            if step.json_body is not None:
                resolved_json, missing_body_vars = _substitute_variables(
                    step.json_body, context
                )
                if missing_body_vars:
                    msg = f"Step {idx} ({step.method} {step.path}): undefined variable(s) in body: {missing_body_vars}"
                    step_res = ContractStepResult(
                        step_index=idx,
                        passed=False,
                        method=step.method,
                        path=step.path,
                        expected_status=step.expect_status,
                        error_message=msg,
                    )
                    step_results.append(step_res)
                    return ContractResult(
                        passed=False,
                        step_results=step_results,
                        failure_message=msg,
                    )

            # Add probe token header
            req_headers = {**resolved_headers}
            if probe_token:
                req_headers["X-Game-Probe"] = probe_token

            clean_target = target_url.rstrip("/")
            clean_sub = resolved_path.lstrip("/")
            url = f"{clean_target}/{clean_sub}"

            try:
                resp = client.request(
                    method=step.method,
                    url=url,
                    headers=req_headers,
                    json=resolved_json,
                )
            except Exception as exc:
                msg = f"Step {idx} ({step.method} {step.path}): connection error: {exc}"
                step_res = ContractStepResult(
                    step_index=idx,
                    passed=False,
                    method=step.method,
                    path=step.path,
                    expected_status=step.expect_status,
                    error_message=msg,
                )
                step_results.append(step_res)
                return ContractResult(
                    passed=False,
                    step_results=step_results,
                    failure_message=msg,
                )

            # Check status code
            if resp.status_code != step.expect_status:
                body_sample = resp.text[:200]
                msg = f"Step {idx} ({step.method} {step.path}): expected status {step.expect_status}, got {resp.status_code} (body: {body_sample!r})"
                step_res = ContractStepResult(
                    step_index=idx,
                    passed=False,
                    method=step.method,
                    path=step.path,
                    expected_status=step.expect_status,
                    actual_status=resp.status_code,
                    error_message=msg,
                )
                step_results.append(step_res)
                return ContractResult(
                    passed=False,
                    step_results=step_results,
                    failure_message=msg,
                )

            resp_data: Any = None
            if step.expect_keys or step.save:
                try:
                    resp_data = resp.json()
                except Exception:
                    body_sample = resp.text[:200]
                    msg = f"Step {idx} ({step.method} {step.path}): expected JSON response but could not parse (body: {body_sample!r})"
                    step_res = ContractStepResult(
                        step_index=idx,
                        passed=False,
                        method=step.method,
                        path=step.path,
                        expected_status=step.expect_status,
                        actual_status=resp.status_code,
                        error_message=msg,
                    )
                    step_results.append(step_res)
                    return ContractResult(
                        passed=False,
                        step_results=step_results,
                        failure_message=msg,
                    )

                if not isinstance(resp_data, dict):
                    msg = f"Step {idx} ({step.method} {step.path}): expected JSON object, got {type(resp_data).__name__}"
                    step_res = ContractStepResult(
                        step_index=idx,
                        passed=False,
                        method=step.method,
                        path=step.path,
                        expected_status=step.expect_status,
                        actual_status=resp.status_code,
                        error_message=msg,
                    )
                    step_results.append(step_res)
                    return ContractResult(
                        passed=False,
                        step_results=step_results,
                        failure_message=msg,
                    )

                missing_keys = [k for k in step.expect_keys if k not in resp_data]
                if missing_keys:
                    msg = f"Step {idx} ({step.method} {step.path}): missing expected key(s) {missing_keys} in response"
                    step_res = ContractStepResult(
                        step_index=idx,
                        passed=False,
                        method=step.method,
                        path=step.path,
                        expected_status=step.expect_status,
                        actual_status=resp.status_code,
                        expected_keys=list(step.expect_keys),
                        missing_keys=missing_keys,
                        error_message=msg,
                    )
                    step_results.append(step_res)
                    return ContractResult(
                        passed=False,
                        step_results=step_results,
                        failure_message=msg,
                    )

            # Save variables if defined
            saved: dict[str, Any] = {}
            if step.save and isinstance(resp_data, dict):
                for var_name, key in step.save.items():
                    if key not in resp_data:
                        msg = f"Step {idx} ({step.method} {step.path}): cannot save variable '{var_name}', key '{key}' not in response"
                        step_res = ContractStepResult(
                            step_index=idx,
                            passed=False,
                            method=step.method,
                            path=step.path,
                            expected_status=step.expect_status,
                            actual_status=resp.status_code,
                            error_message=msg,
                        )
                        step_results.append(step_res)
                        return ContractResult(
                            passed=False,
                            step_results=step_results,
                            failure_message=msg,
                        )
                    val = resp_data[key]
                    context[var_name] = val
                    saved[var_name] = val

            step_res = ContractStepResult(
                step_index=idx,
                passed=True,
                method=step.method,
                path=step.path,
                expected_status=step.expect_status,
                actual_status=resp.status_code,
                expected_keys=list(step.expect_keys),
                saved_values=saved,
            )
            step_results.append(step_res)

        return ContractResult(
            passed=True,
            step_results=step_results,
            failure_message=None,
        )
    finally:
        client.close()
