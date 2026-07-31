"""One-off patch: teach instagram_discover to scan collections."""

from pathlib import Path

TARGET = Path("app/instagram_discover.py")

RUN_OLD = '''    started_at = datetime.now()

    process = run_gallery_dl(
        command,
        mode=args.search_mode,
        scan_speed=args.scan_speed,
        timeout_seconds=(
            2 * 60 * 60
            if args.search_mode in {"full", "smart"}
            else 15 * 60
        ),
    )

    finished_at = datetime.now()

    sanitized_stderr = redact(process.stderr)
    sanitized_stdout = redact(process.stdout)

    parsed_values = parse_json_stream(process.stdout)

    metadata_records: list[dict[str, Any]] = []
    for value in parsed_values:
        collect_metadata(value, metadata_records)
'''

RUN_NEW = '''    scan_targets = (
        list(collection_targets)
        if collection_targets
        else [(None, None, saved_url)]
    )

    started_at = datetime.now()

    metadata_records: list[dict[str, Any]] = []
    container_map: dict[str, list[dict[str, str]]] = {}
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    scanned_urls: list[str] = []
    last_returncode = 0

    for (
        container_id,
        container_name,
        target_url,
    ) in scan_targets:
        target_command = list(command)
        target_command.append(target_url)
        scanned_urls.append(target_url)

        process = run_gallery_dl(
            target_command,
            mode=args.search_mode,
            scan_speed=args.scan_speed,
            timeout_seconds=(
                2 * 60 * 60
                if args.search_mode in {"full", "smart"}
                else 15 * 60
            ),
        )

        stdout_parts.append(process.stdout)
        stderr_parts.append(process.stderr)

        if process.returncode:
            last_returncode = process.returncode

        target_records: list[dict[str, Any]] = []

        for value in parse_json_stream(process.stdout):
            collect_metadata(value, target_records)

        if container_id is not None:
            for metadata in target_records:
                post_id = normalize_post_id(
                    metadata.get("post_id")
                )

                if not post_id:
                    continue

                entries = container_map.setdefault(
                    post_id,
                    [],
                )

                if any(
                    entry["id"] == container_id
                    for entry in entries
                ):
                    continue

                entries.append({
                    "platform": "instagram",
                    "kind": "collection",
                    "id": container_id,
                    "name": container_name,
                })

        metadata_records.extend(target_records)

    finished_at = datetime.now()

    sanitized_stderr = redact("\\n".join(stderr_parts))
    sanitized_stdout = redact("\\n".join(stdout_parts))
'''

BUILD_OLD = '''    posts = build_post_records(
        metadata_records,
        known_post_ids,
        known_shortcodes,
        known_urls,
        registry_state,
    )
'''

BUILD_NEW = BUILD_OLD + '''
    for post in posts:
        post["containers"] = list(
            container_map.get(
                str(post.get("post_id") or ""),
                [],
            )
        )
'''

URL_OLD = '''    saved_url = (
        f"https://www.instagram.com/"
        f"{username}/saved/all-posts/"
    )
'''

URL_NEW = URL_OLD + '''
    collection_targets: list[tuple[str, str, str]] = []

    for entry in args.collection or []:
        raw_id, _, raw_name = str(entry).partition(":")
        collection_id = raw_id.strip()

        if not collection_id:
            continue

        collection_targets.append((
            collection_id,
            raw_name.strip() or collection_id,
            (
                f"https://www.instagram.com/"
                f"{username}/saved/collection/"
                f"{collection_id}/"
            ),
        ))
'''

REPLACEMENTS = [
    (
        '''    parser.add_argument(
        "--scan-speed",
''',
        '''    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        metavar="ID:NAME",
        help=(
            "Scan a saved collection instead of the "
            "general saved feed. Can be repeated."
        ),
    )
    parser.add_argument(
        "--scan-speed",
''',
    ),
    (URL_OLD, URL_NEW),
    (
        '''        "--http-timeout",
        "30",
        saved_url,
    ])
''',
        '''        "--http-timeout",
        "30",
    ])
''',
    ),
    (RUN_OLD, RUN_NEW),
    (BUILD_OLD, BUILD_NEW),
    (
        '''    status_info = classify_failure(
        process.returncode,
''',
        '''    status_info = classify_failure(
        last_returncode,
''',
    ),
    (
        '''        "gallery_dl_returncode": process.returncode,
''',
        '''        "gallery_dl_returncode": last_returncode,
        "container_mode": bool(collection_targets),
        "containers_requested": [
            {
                "id": container_id,
                "name": container_name,
            }
            for container_id, container_name, _ in (
                collection_targets
            )
        ],
        "scanned_urls": scanned_urls,
''',
    ),
    (
        '''                "post_id": post["post_id"],
                "shortcode": post["post_shortcode"],
''',
        '''                "post_id": post["post_id"],
                "containers": post.get("containers", []),
                "shortcode": post["post_shortcode"],
''',
    ),
]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    for number, (old, new) in enumerate(REPLACEMENTS, 1):
        found = text.count(old)

        if found != 1:
            print(
                f"ANCHOR {number}: found {found} times "
                "— nothing was changed"
            )
            return 1

        text = text.replace(old, new)

    TARGET.write_text(text, encoding="utf-8")
    print("PATCH OK")
    return 0


raise SystemExit(main())
