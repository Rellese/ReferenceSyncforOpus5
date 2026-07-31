from __future__ import annotations

import platform
import subprocess
from pathlib import Path


class BrowserProfileError(RuntimeError):
    pass


def normalize_browser_name(browser: str) -> str:
    normalized = (
        str(browser)
        .strip()
        .lower()
        .replace("ё", "е")
    )

    aliases = {
        "google chrome": "chrome",
        "google-chrome": "chrome",
        "yandex": "yandex",
        "yandex browser": "yandex",
        "yandex.browser": "yandex",
        "яндекс": "yandex",
        "яндекс браузер": "yandex",
        "яндекс.браузер": "yandex",
        "mozilla firefox": "firefox",
    }

    return aliases.get(normalized, normalized)


def yandex_root() -> Path:
    system = platform.system()

    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Yandex"
            / "YandexBrowser"
        )

    if system == "Windows":
        import os

        local_app_data = os.environ.get(
            "LOCALAPPDATA"
        )

        if not local_app_data:
            raise BrowserProfileError(
                "LOCALAPPDATA is not available"
            )

        return (
            Path(local_app_data)
            / "Yandex"
            / "YandexBrowser"
            / "User Data"
        )

    return (
        Path.home()
        / ".config"
        / "yandex-browser"
    )


def profile_has_cookies(profile: Path) -> bool:
    return any(
        candidate.is_file()
        for candidate in (
            profile / "Cookies",
            profile / "Network" / "Cookies",
        )
    )


def find_yandex_profile() -> Path:
    root = yandex_root()

    if not root.is_dir():
        raise BrowserProfileError(
            "Yandex Browser profile directory "
            f"was not found: {root}"
        )

    candidates = []

    default = root / "Default"

    if default.is_dir():
        candidates.append(default)

    candidates.extend(
        child
        for child in root.iterdir()
        if (
            child.is_dir()
            and child.name != "Default"
            and (
                child.name.startswith("Profile ")
                or (child / "Preferences").is_file()
            )
        )
    )

    profiles_with_cookies = [
        profile
        for profile in candidates
        if profile_has_cookies(profile)
    ]

    if not profiles_with_cookies:
        raise BrowserProfileError(
            "No Yandex Browser profile containing "
            "a cookie database was found"
        )

    def cookie_mtime(profile: Path) -> float:
        paths = (
            profile / "Cookies",
            profile / "Network" / "Cookies",
        )

        return max(
            (
                path.stat().st_mtime
                for path in paths
                if path.exists()
            ),
            default=0.0,
        )

    return max(
        profiles_with_cookies,
        key=cookie_mtime,
    )


def keychain_service_exists(
    service: str,
) -> bool:
    try:
        process = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
            ],
            capture_output=True,
            text=False,
            timeout=10,
            check=False,
        )
    except Exception:
        return False

    return process.returncode == 0


def yandex_keyring_service() -> str:
    candidates = (
        "Yandex Safe Storage",
        "YandexBrowser Safe Storage",
        "Yandex Browser Safe Storage",
        "Chrome Safe Storage",
    )

    for service in candidates:
        if keychain_service_exists(service):
            return service

    # Most common macOS service name.
    return "Yandex Safe Storage"


def gallery_dl_browser_spec(
    browser: str,
) -> str:
    normalized = normalize_browser_name(browser)

    if normalized != "yandex":
        return normalized

    profile = find_yandex_profile()
    system = platform.system()

    # gallery-dl accepts a supported keyring backend after "+",
    # not a macOS Keychain service name such as
    # "Yandex Safe Storage". Use the Yandex Chromium profile
    # directly and let gallery-dl select its platform adapter.
    return f"chrome:{profile}"


def public_browser_details(
    browser: str,
) -> dict:
    normalized = normalize_browser_name(browser)

    if normalized != "yandex":
        return {
            "requested_browser": browser,
            "resolved_browser": normalized,
            "custom_profile": False,
        }

    profile = find_yandex_profile()

    return {
        "requested_browser": browser,
        "resolved_browser": "yandex",
        "cookie_engine": "chromium",
        "custom_profile": True,
        "profile_name": profile.name,
        "profile_root_exists": True,
        "cookie_database_exists": (
            profile_has_cookies(profile)
        ),
    }
