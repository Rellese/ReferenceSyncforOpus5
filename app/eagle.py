from __future__ import annotations

import httpx


def safe_application_info(payload: dict) -> dict:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}

    return {
        "version": data.get("version"),
        "prerelease_version": data.get("prereleaseVersion"),
        "build_version": data.get("buildVersion"),
        "platform": data.get("platform"),
    }


def check_eagle(api_url: str) -> dict:
    endpoints = (
        "/api/v2/application/info",
        "/api/application/info",
    )

    errors = []

    for endpoint in endpoints:
        url = f"{api_url}{endpoint}"

        try:
            response = httpx.get(url, timeout=2.0)

            if response.is_success:
                try:
                    payload = response.json()
                except Exception:
                    payload = {}

                return {
                    "connected": True,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "application": safe_application_info(payload),
                }

            errors.append(
                {
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                }
            )

        except Exception as exc:
            errors.append(
                {
                    "endpoint": endpoint,
                    "error": str(exc),
                }
            )

    return {
        "connected": False,
        "errors": errors,
        "note": "Open Eagle and make sure a library is loaded.",
    }
