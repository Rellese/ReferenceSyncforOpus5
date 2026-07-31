from __future__ import annotations

"""
ReferenceSync gallery-dl launcher with a read-only progress channel.

gallery-dl's DataJob normally collects the complete --dump-json
document and writes it only after extraction finishes. This wrapper
reports every unique logical Instagram post to stderr while preserving
the original JSON document on stdout.
"""

import json
import os
import sys
from typing import Any


PROGRESS_PREFIX = "RS_GDL_POST "
PROFILE_PREFIX = "RS_GDL_PROFILE "

# REFERENCE_SYNC_SPEED_RUNTIME_V643
DISCOVERY_SPEED_PROFILES = {
    "safe": (6.0, 12.0),
    "balanced": (3.0, 6.0),
}


def apply_discovery_speed_profile() -> tuple[str, tuple[float, float]]:
    """
    Apply the ReferenceSync Saved-pagination profile before gallery-dl
    creates its Instagram extractor and HTTP request session.

    Both the extractor class attribute and gallery-dl's official
    --sleep-request CLI option are set. The CLI option ensures that an
    existing gallery-dl configuration does not silently restore the
    default Instagram interval.
    """
    requested = (
        os.environ.get(
            "REFERENCE_SYNC_DISCOVERY_SPEED",
            "safe",
        )
        .strip()
        .lower()
    )

    profile = (
        requested
        if requested in DISCOVERY_SPEED_PROFILES
        else "safe"
    )
    interval = DISCOVERY_SPEED_PROFILES[profile]

    from gallery_dl.extractor.instagram import (
        InstagramExtractor,
        InstagramSavedExtractor,
    )

    InstagramExtractor.request_interval = interval
    InstagramSavedExtractor.request_interval = interval

    # Do not add a second option if one was explicitly supplied.
    has_sleep_request = any(
        argument == "--sleep-request"
        or argument.startswith("--sleep-request=")
        for argument in sys.argv[1:]
    )

    interval_argument = (
        f"{interval[0]:g}-{interval[1]:g}"
    )

    if not has_sleep_request:
        sys.argv[1:1] = [
            "--sleep-request",
            interval_argument,
        ]

    payload = {
        "profile": profile,
        "requested_profile": requested,
        "request_interval": list(interval),
        "sleep_request_argument": interval_argument,
        "cli_option_injected": not has_sleep_request,
    }

    print(
        PROFILE_PREFIX
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )

    return profile, interval


def normalized(value: Any) -> str:
    return str(value or "").strip()


def install_progress_hook() -> None:
    from gallery_dl.job import DataJob

    original_handle_url = DataJob.handle_url

    def progress_handle_url(
        self,
        url,
        kwdict,
    ):
        state = getattr(
            self,
            "_reference_sync_progress_state",
            None,
        )

        if state is None:
            state = {
                "post_ids": set(),
                "fallback_keys": set(),
                "count": 0,
            }
            setattr(
                self,
                "_reference_sync_progress_state",
                state,
            )

        metadata = (
            kwdict
            if isinstance(kwdict, dict)
            else {}
        )

        post_id = normalized(
            metadata.get("post_id")
        )
        shortcode = normalized(
            metadata.get("post_shortcode")
            or metadata.get("sidecar_shortcode")
        )
        post_url = normalized(
            metadata.get("post_url")
        )

        is_new_logical_post = False

        if post_id:
            if post_id not in state["post_ids"]:
                state["post_ids"].add(post_id)
                is_new_logical_post = True
        else:
            fallback_key = (
                shortcode,
                post_url,
            )

            if (
                any(fallback_key)
                and fallback_key
                not in state["fallback_keys"]
            ):
                state["fallback_keys"].add(
                    fallback_key
                )
                is_new_logical_post = True

        if is_new_logical_post:
            state["count"] += 1

            payload = {
                "count": state["count"],
                "post_id": post_id or None,
                "shortcode": shortcode or None,
                "post_url": post_url or None,
            }

            print(
                PROGRESS_PREFIX
                + json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                file=sys.stderr,
                flush=True,
            )

        return original_handle_url(
            self,
            url,
            kwdict,
        )

    DataJob.handle_url = progress_handle_url


def main() -> int:
    apply_discovery_speed_profile()
    install_progress_hook()

    import gallery_dl

    result = gallery_dl.main()

    try:
        return int(result or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
