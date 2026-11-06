"""Read-only Xiaohongshu search via BrowserSkill. SSOT: data/persona_dossier.json.

The configured, independently verified browser instance supplies login state in
an owned Agent Window. Chat text supplies only an encoded search term: never a
command, selector, executable, browser id, JavaScript, or destination hostname.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit

from preview_mode import is_preview

ORIGIN = "https://www.xiaohongshu.com"
MAX_SECONDS = 50.0
MAX_OUTPUT_BYTES = 1_000_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_NOTE_PATH = re.compile(r"/(?:explore|search_result)/([a-fA-F0-9]{24})/?\Z")


class BskSearchError(RuntimeError):
    """A real browser/CLI/protocol failure, never a substitute search result."""

    def __init__(self, code, *, outcome_unknown=None):
        self.code = code
        if outcome_unknown is None:
            outcome_unknown = code in {
                "browser_skill_timeout", "browser_skill_invalid_json",
                "browser_skill_unexpected_json", "browser_skill_output_too_large",
                "browser_skill_response_error",
            } or code.startswith("browser_skill_cli_exit_") or code.endswith("_cleanup_unconfirmed")
        self.outcome_unknown = bool(outcome_unknown)
        super().__init__(code)


def _identifier(value, label):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise BskSearchError(f"browser_skill_invalid_{label}")
    return value


def _query_url(query):
    if not isinstance(query, str):
        raise BskSearchError("browser_skill_invalid_query")
    query = re.sub(r"[\x00-\x1f\x7f]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()[:160]
    if not query:
        raise BskSearchError("browser_skill_empty_query")
    return ORIGIN + "/search_result?" + urlencode({"keyword": query, "source": "web_search_result_notes", "type": "51"})


def _note_urls(value):
    """Keep observed signatures only for navigation; expose a canonical source URL."""
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
        return None
    if value.startswith("/") and not value.startswith("//"):
        value = ORIGIN + value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.netloc != "www.xiaohongshu.com"
                or parsed.username or parsed.password or parsed.fragment):
            return None
        match = _NOTE_PATH.fullmatch(parsed.path)
        if not match:
            return None
        return urlunsplit(parsed), ORIGIN + "/explore/" + match.group(1)
    except ValueError:
        return None


def _execute(argv, timeout):
    """Timeout kills only this CLI child, never a shared browser/daemon process."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, shell=False,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # TimeoutExpired includes argv, potentially a signed note link. Do not
        # propagate that raw command into the caller's exception logs.
        raise BskSearchError("browser_skill_timeout") from None
    except OSError:
        raise BskSearchError("browser_skill_cli_unavailable") from None
    if result.returncode:
        # Mac mini 0.3.3 reports a definite pre-execution request rejection as
        # top-level {code:"invalid_params", message, hint, exit_code}. Retain
        # only the fixed code, never raw messages or signed links.
        try:
            failure = json.loads(result.stdout) if len(result.stdout) <= MAX_OUTPUT_BYTES else None
        except (ValueError, TypeError):
            failure = None
        if isinstance(failure, dict) and failure.get("code") == "invalid_params":
            raise BskSearchError("browser_skill_invalid_params", outcome_unknown=False)
        # CLI/page output can contain private browser state; don't copy it into chat logs.
        raise BskSearchError(f"browser_skill_cli_exit_{result.returncode}")
    if len(result.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise BskSearchError("browser_skill_output_too_large")
    try:
        parsed = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise BskSearchError("browser_skill_invalid_json") from exc
    if not isinstance(parsed, (dict, list)):
        raise BskSearchError("browser_skill_unexpected_json")
    return parsed


class _Commands:
    def __init__(self, opts):
        path = opts.get("bsk_path")
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise BskSearchError("browser_skill_invalid_cli_path")
        self.path = path
        self.browser = _identifier(opts.get("browser_instance_id"), "browser_instance")
        try:
            seconds = min(MAX_SECONDS, float(opts.get("timeout_sec", MAX_SECONDS)))
        except (TypeError, ValueError) as exc:
            raise BskSearchError("browser_skill_invalid_timeout") from exc
        if not 1 <= seconds <= MAX_SECONDS:
            raise BskSearchError("browser_skill_invalid_timeout")
        self.deadline = time.monotonic() + seconds
        # Reserve cleanup time even when the page stalls or shows a challenge.
        self.cleanup_budget = min(5.0, seconds * .2)
        self.work_deadline = self.deadline - self.cleanup_budget
        self.request_id = f"{int((time.time() + 120) * 1000)}:{uuid.uuid4()}"

    def run(self, *args, cleanup=False):
        remaining = (self.deadline if cleanup else self.work_deadline) - time.monotonic()
        if remaining <= 0:
            # No CLI invocation occurred, so this cannot have created a window.
            raise BskSearchError("browser_skill_timeout", outcome_unknown=False)
        return _execute([self.path, *args], min(remaining, self.cleanup_budget if cleanup else 15.0))

    def browsers(self):
        return self.run("browsers", "--json")

    def start(self):
        return self.run("session", "start", "--browser", self.browser,
                        "--request-id", self.request_id, "--name", "xiaotian-readonly-search", "--json")

    def prepare(self):
        return self.run("session", "request", self.request_id, "--prepare", "--json")

    def navigate(self, session, url):
        return self.run("navigate", url, "--session", _identifier(session, "session"), "--json")

    def observe(self, session):
        return self.run("observe", "--session", _identifier(session, "session"), "--max-tokens", "1600", "--json")

    def wait_visible(self, session, selector):
        return self.run("wait-for-element", selector, "--state", "visible", "--timeout", "6s",
                        "--session", _identifier(session, "session"), "--json")

    def extract(self, session, *, container, item, fields, limit):
        return self.run("extract", "list", "--session", _identifier(session, "session"),
                        "--selector", container, "--item-selector", item,
                        "--fields-json", json.dumps({"fields": fields}, ensure_ascii=False),
                        "--max-rows", str(limit), "--max-bytes", "100000", "--timeout-ms", "4000", "--json")

    def stop(self, session):
        return self.run("session", "stop", _identifier(session, "session"), "--json", cleanup=True)


# Verified against Mac mini BrowserSkill 0.3.3 captures from 2026-10-29:
# browser-skill/xhs-bsk-probe-20261029/{browsers,session-start,navigate,
# observe,extract-list,session-stop}.json. No recursive "find any plausible URL"
# parsing: consume only the documented, observed result fields.
_LIST_FIELDS = [
    {"key": "title", "selector": "a.title", "read": "text"},
    {"key": "href", "selector": "a.cover", "read": "href"},
]
_BODY_FIELDS = [{"key": "text", "selector": ":scope", "read": "text"}]
_BLOCKING_CONTROL = re.compile(
    r'(?:dialog|heading|textbox) "(?:请先登录|扫码登录|手机验证码|安全验证|人机验证|验证码|滑块验证)[^"\n]*"')


def _object(value):
    if isinstance(value, dict) and value.get("code") == "invalid_params":
        raise BskSearchError("browser_skill_invalid_params", outcome_unknown=False)
    if not isinstance(value, dict) or value.get("error") or value.get("ok") is False:
        raise BskSearchError("browser_skill_response_error")
    return value


def _check_browser(value, instance):
    if not isinstance(value, list):
        raise BskSearchError("browser_skill_invalid_browser_inventory")
    matches = [row for row in value if isinstance(row, dict) and row.get("instance_id") == instance]
    if len(matches) != 1 or matches[0].get("unresponsive") is not False:
        raise BskSearchError("browser_skill_required_browser_unavailable")


def _page_url(value, *, note=None):
    if not isinstance(value, str):
        raise BskSearchError("browser_skill_missing_page_url")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise BskSearchError("browser_skill_unexpected_page") from None
    if parsed.scheme != "https" or parsed.netloc != "www.xiaohongshu.com" or parsed.fragment:
        raise BskSearchError("browser_skill_unexpected_page")
    if note:
        pair = _note_urls(value)
        if not pair or pair[1] != note:
            raise BskSearchError("browser_skill_unexpected_note")
    # Mac mini 0.3.3 v3 trace (2026-10-29) shows the site normalizes
    # /search_result to /search_result/. These are the only search routes.
    elif parsed.path not in ("/search_result", "/search_result/"):
        raise BskSearchError("browser_skill_search_page_unavailable")
    return value


def _navigation(value, *, note=None):
    data = _object(value)
    _page_url(data.get("final_url"), note=note)
    if type(data.get("tab_id")) is not int:
        raise BskSearchError("browser_skill_missing_owned_tab")
    return data["tab_id"]


def _observe(value, tab):
    data = _object(value)
    if data.get("tab_id") != tab or not isinstance(data.get("text"), str):
        raise BskSearchError("browser_skill_observation_mismatch")
    if _BLOCKING_CONTROL.search(data["text"]):
        raise BskSearchError("browser_skill_login_or_verification_required")
    # Observation is used only to check readiness/blockers, never as a snippet.


def _rows(value, keys, tab, *, note=None):
    data = _object(value)
    source = data.get("source") or {}
    coverage = data.get("coverage") or {}
    columns = data.get("columns") or []
    if (data.get("schema_version") != 1 or data.get("kind") != "list"
            or data.get("tab_id") != tab or not isinstance(source, dict)
            or not isinstance(coverage, dict) or not isinstance(columns, list)):
        raise BskSearchError("browser_skill_extract_schema_mismatch")
    _page_url(source.get("page_url"), note=note)
    _page_url(source.get("frame_url"), note=note)
    actual_keys = [col.get("key") for col in columns if isinstance(col, dict)]
    if (any(not isinstance(key, str) for key in actual_keys)
            or not set(keys).issubset(actual_keys) or len(actual_keys) != len(set(actual_keys))):
        raise BskSearchError("browser_skill_extract_columns_mismatch")
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) > 50 or any(not isinstance(row, dict) for row in rows):
        raise BskSearchError("browser_skill_extract_rows_mismatch")
    if coverage.get("scope") != "loaded_dom" or coverage.get("rows_returned") != len(rows):
        raise BskSearchError("browser_skill_extract_coverage_mismatch")
    if data.get("warnings"):
        raise BskSearchError("browser_skill_extract_warnings")
    # A bounded row_limit is expected; this never asserts complete search coverage.
    if coverage.get("truncated") and coverage.get("stop_reason") != "row_limit":
        raise BskSearchError("browser_skill_extract_incomplete")
    return rows


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", value)).strip()[:limit]


def _search_cards(value, tab, limit):
    cards, seen = [], set()
    for row in _rows(value, ("title", "href"), tab):
        title, urls = _text(row.get("title"), 180), _note_urls(row.get("href"))
        if not title or not urls or urls[1] in seen:
            continue
        navigation, canonical = urls
        cards.append(({"title": title, "url": canonical, "snippet": title,
                       "content_scope": "search_title"}, navigation))
        seen.add(canonical)
        if len(cards) == limit:
            break
    if not cards:
        raise BskSearchError("browser_skill_no_search_results")
    return cards


def _ready(cmd, session, tab, selector):
    _observe(cmd.observe(session), tab)
    ready = _object(cmd.wait_visible(session, selector))
    if ready.get("tab_id") != tab or ready.get("satisfied") is not True or ready.get("visible") is not True:
        raise BskSearchError("browser_skill_page_not_ready")
    _observe(cmd.observe(session), tab)


def _stop(cmd, session):
    reply = _object(cmd.stop(session))
    if (session not in (reply.get("stopped") or []) or reply.get("failed")
            or reply.get("return_failures")):
        raise BskSearchError("browser_skill_cleanup_unconfirmed")


def search(query, opts):
    """Return bounded title/URL/visible-body evidence, or raise the actual failure.

    opts are trusted developer configuration, never values taken from a message.
    No alternate browser/provider is attempted. All commands belong to the new
    Agent Window. At most one note body is read, without comments or account data.
    """
    if is_preview():
        raise BskSearchError("browser_skill_preview_disabled")
    url = _query_url(query)
    if not isinstance(opts, dict):
        raise BskSearchError("browser_skill_invalid_config")
    cmd = _Commands(opts)
    try:
        limit = max(1, min(5, int(opts.get("max_results", 5))))
    except (TypeError, ValueError) as exc:
        raise BskSearchError("browser_skill_invalid_result_limit") from exc
    session = None
    try:
        _check_browser(cmd.browsers(), cmd.browser)
        # This 0.3.3 daemon requires prepare before --request-id. Preparation
        # does not create a browser window. Its optional metadata is unused;
        # the subsequent start response must prove the session/window identity.
        _object(cmd.prepare())
        try:
            started = _object(cmd.start())
        except BskSearchError as exc:
            if not exc.outcome_unknown:
                raise
            # Repeat-start/claim semantics have not been verified for this
            # daemon. Do not create a second session or infer another task's
            # ownership. Report the unknown creation/cleanup outcome honestly.
            raise BskSearchError("browser_skill_session_start_unknown_cleanup_unconfirmed",
                                 outcome_unknown=True) from exc
        try:
            session = _identifier(started.get("session_id"), "session")
        except BskSearchError as exc:
            raise BskSearchError("browser_skill_session_start_unknown_cleanup_unconfirmed") from exc
        if started.get("browser_instance_id") != cmd.browser:
            raise BskSearchError("browser_skill_session_browser_mismatch")
        if type(started.get("agent_window_id")) is not int:
            raise BskSearchError("browser_skill_missing_owned_window")
        tab = _navigation(cmd.navigate(session, url))
        _ready(cmd, session, tab, "section.note-item")
        cards = _search_cards(cmd.extract(session, container=".feeds-container", item="section.note-item",
                                         fields=_LIST_FIELDS, limit=min(15, limit * 3)), tab, limit)
        if opts.get("read_first_note", True):
            source, navigation = cards[0]
            tab = _navigation(cmd.navigate(session, navigation), note=source["url"])
            _ready(cmd, session, tab, "#detail-desc")
            rows = _rows(cmd.extract(session, container="body", item="#detail-desc", fields=_BODY_FIELDS, limit=1),
                         ("text",), tab, note=source["url"])
            body = _text(rows[0].get("text"), 650) if len(rows) == 1 else ""
            if not body:
                raise BskSearchError("browser_skill_note_body_unavailable")
            source.update(snippet=body, content_scope="visible_note_body")
        return [source for source, _ in cards]
    finally:
        if session is not None:
            try:
                _stop(cmd, session)
            except Exception as exc:
                raise BskSearchError("browser_skill_cleanup_unconfirmed") from exc
