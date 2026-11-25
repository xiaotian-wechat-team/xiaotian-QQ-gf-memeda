"""Backend drafts and releases. Persona SSOT: data/persona_dossier.json.

Uses role_release's immutable definition/assets/active contract. This module
never starts transports, mutates grants, or writes chat/memory/state files.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from personalization_schema import FIELDS, SECTIONS, fields_for, get_path, initial_values, project, role_values, validate
from role_registry import identifier
import role_release

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def checked(path):
    path = Path(path).expanduser().absolute()
    # macOS exposes its system temporary roots through these fixed aliases.
    for alias, actual in (("/var", "/private/var"), ("/tmp", "/private/tmp")):
        source = Path(alias)
        if source.is_symlink() and source.resolve() == Path(actual) and path.is_relative_to(source):
            path = Path(actual) / path.relative_to(source)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("受管目录不能包含符号链接")
    return path


def read_json(path, default=None):
    path = checked(path)
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)


def write_json(path, value):
    path = checked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root=None, *, environment="dev", persona="xiaotian", relation=None):
        if environment not in {"dev", "test", "prod"}:
            raise ValueError("invalid environment")
        self.persona = identifier(persona)
        self.relation = identifier(relation or os.environ.get("ROLE_RELATION", "partner"))
        self.environment = environment
        self.root = checked(Path(root or os.environ.get("XIAOTIAN_PERSONALIZATION_ROOT") or ROOT / "data" / "persona_personalization"))
        self.destination = self.root / environment
        self.directory = self.destination / "console" / persona / "relationships" / self.relation
        self.role_dir = self.destination / "roles" / persona

    @property
    def is_companion(self):
        return self.relation == "partner" and re.fullmatch(r"companion_[a-f0-9]{32}", self.persona) is not None

    @property
    def runtime_root(self):
        return self.directory / "instance"

    @contextmanager
    def lock(self):
        checked(self.directory).mkdir(parents=True, exist_ok=True)
        fd = os.open(self.directory / ".draft.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    @contextmanager
    def role_lock(self):
        checked(self.role_dir).mkdir(parents=True, exist_ok=True)
        fd = os.open(self.role_dir / ".personalization.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def seed(self):
        """Read definitions only. Never read any contact's runtime files."""
        registry = os.environ.get("ROLE_REGISTRY_DIR")
        if registry:
            if self.persona != os.environ.get("ROLE_ID") or self.environment != os.environ.get("ROLE_ENVIRONMENT"):
                raise PermissionError("控制台角色与运行环境不匹配")
            role = read_json(Path(registry) / self.persona / "role.json")
            if not role or role.get("role_id") != self.persona:
                raise ValueError("角色定义不存在")
            relation_id = self.relation
            return role_values(role, relation_id), role, relation_id
        if self.is_companion:
            from persona_onboarding import seed
            return seed(self)
        if self.persona not in {"xiaotian", "jisoo"}:
            raise PermissionError("角色未注册")
        if self.relation != "partner":
            raise PermissionError("关系未注册")
        empty = self.persona != "xiaotian"
        dossier = read_json(ROOT / "data" / "persona_dossier.json", {}) if not empty else {}
        settings = read_json(ROOT / "config.json", {}) if not empty else {}
        values = initial_values(dossier, settings, empty=empty)
        if not empty and dossier.get("school", {}).get("institution") == "北京舞蹈学院":
            values["content.campus_sources"] = ["https://news.bda.edu.cn/", "https://www.bda.edu.cn/"]
        role = {"schema_version": 2, "role_id": self.persona, "adapter": "legacy" if not empty else "unbound",
                "dossier": copy.deepcopy(dossier), "settings": {}, "workflows": {},
                "relationships": {"partner": {"kind": values.get("relationship.kind"), "bindings": []}}}
        # Fixed person facts are a trusted relationship projection, never editable routing.
        role["relationships"]["partner"]["person"] = {k: v for k, v in (dossier.get("user_ref") or {}).items()
                                                         if k in {"name", "gender", "address", "city", "occupation"}}
        return values, role, "partner"

    def selected(self):
        active = self.role_dir / "active"
        if not active.is_symlink():
            if active.exists():
                raise ValueError("invalid active selection")
            return None, None
        target = active.resolve(strict=True)
        if target.parent != self.role_dir / "releases":
            raise ValueError("release selection escapes role")
        manifest = read_json(target / "manifest.json")
        role_path = target / "definition" / "role.json"
        role_bytes = checked(role_path).read_bytes()
        sources = {name: checked(target / name) for name in manifest["files"]}
        verified = role_release.plan(self.persona, manifest["engine_sha256"], sources)
        if verified != manifest or manifest["release_id"] != target.name:
            raise ValueError("人物版本内容已损坏")
        return manifest["release_id"], json.loads(role_bytes)

    def personalization(self, role):
        value = (role or {}).get("personalization") or {}
        return value["relationships"].get(self.relation, {}) if "relationships" in value else value

    def values_for(self, role):
        values, _, _ = self.seed()
        if role is None:
            return values
        return role_values(role, self.relation, defaults=values)

    def _migrate_draft(self):
        # The first console stored only the legacy partner draft here. Never
        # infer another relationship's ownership or overwrite a newer draft.
        old = self.destination / "console" / self.persona / "draft.json"
        if self.relation == "partner":
            legacy_tasks = checked(old.parent / "appearance")
            for source in legacy_tasks.glob("*/task.json"):
                task = read_json(source)
                if task.get("role_id") != self.persona or task.get("environment") != self.environment or task.get("relation_id", "partner") != self.relation:
                    continue
                from appearance_tasks import task_dir
                target = task_dir(self, source.parent.name)
                if target.exists():
                    continue
                for path in source.parent.rglob("*"):
                    checked(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix=".migration-", dir=target.parent))
                try:
                    shutil.copytree(source.parent, temporary, dirs_exist_ok=True)
                    try:
                        os.rename(temporary, target)
                    except OSError:
                        if not target.is_dir():
                            raise
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
        if self.relation == "partner" and not (self.directory / "draft.json").exists() and old.exists():
            saved = read_json(old)
            defaults, _, _ = self.seed()
            saved["values"] = {**defaults, **saved["values"]}
            saved.setdefault("candidate", None)
            saved.setdefault("base_version", None)
            checked(self.directory).mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=self.directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(saved, stream, ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.directory / "draft.json")
                except FileExistsError:
                    pass
            finally:
                Path(temporary).unlink(missing_ok=True)

    def draft(self):
        self._migrate_draft()
        saved = read_json(self.directory / "draft.json")
        if saved is not None:
            defaults, _, _ = self.seed()
            saved["values"] = {**defaults, **saved["values"]}
            return saved
        version, role = self.selected()
        values = self.values_for(role)
        return dict(schema_version=1, revision=0, values=values, candidate=self.personalization(role).get("candidate"),
                    base_version=version, saved_at=None)

    def save(self, values, *, revision, candidate=None):
        with self.lock():
            draft = self.draft()
            if type(revision) is not int or revision != draft["revision"]:
                raise Conflict("草稿已被其他窗口修改，请重新读取后合并")
            baseline, seed_role, _ = self.seed()
            version, role = self.selected()
            before = self.values_for(role)
            prepared = copy.deepcopy(values)
            if isinstance(prepared, dict):
                if prepared.get("school.institution") != before.get("school.institution") and prepared.get("content.campus_sources") == before.get("content.campus_sources"):
                    prepared["content.campus_sources"] = []
                if prepared.get("relationship.kind") != before.get("relationship.kind") and prepared.get("identity.relationship_to_user") == before.get("identity.relationship_to_user"):
                    prepared["identity.relationship_to_user"] = None
            clean = validate(prepared, baseline=baseline, fields=fields_for(role or seed_role))
            if self.is_companion:
                from persona_onboarding import validate_adult
                validate_adult(clean)
            if candidate is not None and not self._uses_released_candidate(role, candidate, clean):
                from appearance_tasks import approved_candidate
                approved_candidate(self, candidate, clean)
            next_draft = dict(schema_version=1, revision=revision + 1, values=clean, candidate=candidate,
                              base_version=draft["base_version"], saved_at=timestamp())
            write_json(self.directory / "draft.json", next_draft)
            return next_draft

    def _uses_released_candidate(self, role, candidate, values):
        from appearance_tasks import fingerprint
        return bool(role and candidate and candidate == self.personalization(role).get("candidate")
                    and fingerprint(values) == fingerprint(self.values_for(role)))

    def reset(self, *, revision, expected_version):
        with self.lock():
            current, role = self.selected()
            if revision != self.draft()["revision"] or current != expected_version:
                raise Conflict("人物版本或草稿已变化，请刷新")
            draft = dict(schema_version=1, revision=revision + 1, values=self.values_for(role),
                         candidate=self.personalization(role).get("candidate"), base_version=current, saved_at=timestamp())
            write_json(self.directory / "draft.json", draft)
            return draft

    def _rebase(self, draft, version, role):
        if draft.get("base_version") == version:
            return draft
        base = None
        if draft.get("base_version"):
            if not re.fullmatch(r"[a-f0-9]{64}", draft["base_version"]):
                raise ValueError("invalid draft base version")
            base = read_json(self.role_dir / "releases" / draft["base_version"] / "definition" / "role.json")
        before, latest = self.values_for(base), self.values_for(role)
        conflicts = [key for key in before if draft["values"].get(key) != before[key] and latest[key] != before[key] and latest[key] != draft["values"].get(key)]
        if conflicts:
            raise Conflict("已应用资料也修改了这些字段，请合并或重新建立草稿：" + "、".join(conflicts))
        values = {key: draft["values"].get(key) if draft["values"].get(key) != before[key] else latest[key] for key in latest}
        from appearance_tasks import fingerprint
        candidate = draft.get("candidate")
        if candidate == self.personalization(base).get("candidate"):
            candidate = self.personalization(role).get("candidate") if fingerprint(values) == fingerprint(latest) else None
        elif candidate is not None and fingerprint(values) != fingerprint(draft["values"]):
            raise Conflict("当前应用版本的外貌参数已变化，请重新确认标准素材")
        draft.update(values=values, candidate=candidate, base_version=version,
                     revision=draft["revision"] + 1, saved_at=timestamp())
        write_json(self.directory / "draft.json", draft)
        return draft

    def _preview(self, draft, version, role):
        seed, _, _ = self.seed()
        before = self.values_for(role)
        changes = [{"id": f["id"], "label": f["label"], "section": f["section"],
                    "before": before.get(f["id"]), "after": draft["values"].get(f["id"]),
                    "consumer": f["consumer"]}
                   for f in FIELDS if before.get(f["id"]) != draft["values"].get(f["id"])]
        previous_candidate = self.personalization(role).get("candidate")
        if previous_candidate != draft.get("candidate"):
            changes.append(dict(id="appearance.candidate", label="标准外貌素材版本", section="appearance",
                                before=previous_candidate, after=draft.get("candidate"), consumer="refpick, native image"))
        warnings = []
        if any(c["id"].startswith(("school.", "work.")) for c in changes):
            warnings.append("背景变化可能影响独立日程，请在日程页检查；本次应用不会改动日程。")
        from appearance_tasks import fingerprint
        if not draft.get("candidate") and fingerprint(draft["values"]) != fingerprint(before):
            warnings.append("外貌设定已变化，旧标准素材将失效；生成并批准新素材后才能继续使用人物参考。")
        gaps = [{"id": f["id"], "label": f["label"]} for f in FIELDS
                if f["target"] == "dossier" and draft["values"].get(f["id"]) in (None, "", [])]
        from personalization_runtime import style_prompt
        projections = project(draft["values"])
        return dict(revision=draft["revision"], selected_version=version, changes=changes,
                    affected=sorted({c["section"] for c in changes}), gaps=gaps, warnings=warnings,
                    candidate=draft.get("candidate"), policy_preview=style_prompt(projections),
                    preview_digest=digest({"draft": draft, "selected": version}), side_effects=False)

    def preview(self, *, revision):
        with self.lock(), self.role_lock():
            draft = self.draft()
            if revision != draft["revision"]:
                raise Conflict("草稿已变化，请重新预览")
            seed, seed_role, _ = self.seed()
            validate(draft["values"], baseline=seed, fields=fields_for(seed_role))
            version, role = self.selected()
            draft = self._rebase(draft, version, role)
            validate(draft["values"], baseline=seed, fields=fields_for(role or seed_role))
            value = self._preview(draft, version, role)
            write_json(self.directory / "preview.json", {"revision": draft["revision"], "digest": value["preview_digest"]})
            return {**value, "draft": draft}

    def apply(self, *, revision, preview_digest):
        with self.lock(), self.role_lock():
            draft = self.draft()
            version, current_role = self.selected()
            preview = read_json(self.directory / "preview.json", {})
            expected = digest({"draft": draft, "selected": version})
            if revision != draft["revision"] or preview_digest != expected or preview.get("digest") != expected:
                raise Conflict("预览已过期，请先保存并重新预览")
            baseline, role, relation_id = self.seed()
            if current_role is not None:
                role = copy.deepcopy(current_role)
            values = validate(draft["values"], baseline=baseline, fields=fields_for(role))
            projected = project(values)
            from appearance_tasks import fingerprint
            if self.is_companion and not draft.get("candidate"):
                raise ValueError("请先为当前外貌参数生成并确认标准素材")
            before = self.values_for(current_role)
            same_appearance = fingerprint(values) == fingerprint(before)
            reuse_assets = self._uses_released_candidate(current_role, draft.get("candidate"), values)
            if draft.get("candidate") and not reuse_assets:
                from appearance_tasks import approved_candidate
                approved_candidate(self, draft["candidate"], values)
            shared = copy.deepcopy(projected["dossier"])
            shared.pop("relationship_story", None)
            shared.get("identity", {}).pop("relationship_to_user", None)
            _merge(role["dossier"], shared)
            # Derived annotations belong to the fact they describe. Preserve the
            # full dossier, but do not carry a previous school's address or an
            # old age into a newly edited identity.
            if values.get("school.institution") != before.get("school.institution"):
                for key in ("institution_short", "campus", "campus_source"):
                    role["dossier"].get("school", {}).pop(key, None)
                if values.get("school.major_note") == before.get("school.major_note"):
                    role["dossier"].get("school", {}).pop("major_note", None)
            if values.get("identity.birthday") != before.get("identity.birthday") and values.get("identity.age_stated") == before.get("identity.age_stated"):
                for key in ("age_stated", "birth_year_note"):
                    role["dossier"].get("identity", {}).pop(key, None)
            role["dossier"].pop("user_ref", None)
            role["dossier"].pop("relationship_story", None)
            role["dossier"].get("identity", {}).pop("relationship_to_user", None)
            role["relationships"][relation_id].update(kind=projected["relationship"]["kind"],
                                                     story=projected["dossier"].get("relationship_story"),
                                                     description=values.get("identity.relationship_to_user"))
            personal = dict(schema_version=1, values=values, candidate=draft.get("candidate"), policy=projected["policy"], settings=projected["settings"])
            scoped = copy.deepcopy((role.get("personalization") or {}).get("relationships") or {})
            scoped[relation_id] = copy.deepcopy(personal)
            role["personalization"] = {"relationships": scoped}
            metadata = {name: read_json(self.directory / name) for name in ("service.json", "draft.json", "history.json")}
            with tempfile.TemporaryDirectory(prefix="persona-release-") as folder:
                path = Path(folder) / "role.json"
                write_json(path, role)
                empty_assets = Path(folder) / "README.md"
                empty_assets.write_text("人物素材只通过同版本批准候选发布。\n")
                files = {"definition/role.json": path, "assets/README.md": empty_assets}
                if version and same_appearance:
                    previous_assets = self.role_dir / "releases" / version
                    manifest = read_json(previous_assets / "manifest.json")
                    files.update({name: previous_assets / name for name in manifest["files"] if name.startswith("assets/")})
                if draft.get("candidate") and not reuse_assets:
                    from appearance_tasks import candidate_release_files
                    files.update(candidate_release_files(self, draft["candidate"]))
                engine_digest = hashlib.sha256(b"".join((ROOT / "src" / p).read_bytes() for p in
                    ("personalization_schema.py", "personalization_runtime.py", "config.py", "role_chat.py"))).hexdigest()
                from personalization_deploy import binding
                service_binding = binding(self)
                if service_binding:
                    engine_digest = service_binding["engine_sha256"]
                release = role_release.plan(self.persona, engine_digest, files)
                role_release.install(release, files, self.destination)
                role_release.activate(self.destination, self.persona, release["release_id"], expected_release=version)
            from personalization_deploy import reconcile
            try:
                service = reconcile(self)
                write_json(self.directory / "service.json", service)
                draft.update(base_version=release["release_id"])
                write_json(self.directory / "draft.json", draft)
                history = read_json(self.directory / "history.json", [])
                history.append(dict(version=release["release_id"], previous=version, at=timestamp(), action="apply"))
                write_json(self.directory / "history.json", history)
            except Exception:
                self._recover(version, release["release_id"], metadata)
                raise
            return self.state()

    def _restore_selection(self, previous, expected):
        if previous:
            role_release.activate(self.destination, self.persona, previous, expected_release=expected)
        else:
            with (self.role_dir / ".publish.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                active = self.role_dir / "active"
                if active.is_symlink() and active.resolve().name == expected:
                    active.unlink()

    def _recover(self, previous, expected, metadata):
        self._restore_selection(previous, expected)
        from personalization_deploy import recover_service
        failures = []
        try:
            recover_service(self, previous, expected)
        except Exception as exc:
            failures.append(str(exc))
        for name, value in metadata.items():
            try:
                if value is not None:
                    write_json(self.directory / name, value)
                else:
                    (self.directory / name).unlink(missing_ok=True)
            except OSError as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError("已恢复人物版本；服务或元数据恢复仍需处理：" + "；".join(failures))

    def rollback(self, *, version, expected_version):
        with self.lock(), self.role_lock():
            current, _ = self.selected()
            if current != expected_version:
                raise Conflict("已应用版本已变化，请刷新")
            metadata = {name: read_json(self.directory / name) for name in ("service.json", "draft.json", "history.json")}
            role_release.activate(self.destination, self.persona, version, expected_release=current)
            from personalization_deploy import reconcile
            try:
                service = reconcile(self)
                write_json(self.directory / "service.json", service)
                history = read_json(self.directory / "history.json", [])
                history.append(dict(version=version, previous=current, at=timestamp(), action="rollback"))
                write_json(self.directory / "history.json", history)
            except Exception:
                self._recover(current, version, metadata)
                raise
            return self.state()

    def state(self):
        version, role = self.selected()
        draft = self.draft()
        qq, qq_root = None, None
        if self.is_companion:
            from companion_qq import binding, session_root
            qq = binding(self)
            if qq:
                qq_root = session_root(self, qq)
        reports = []
        from personalization_deploy import report_directory
        external = report_directory(self)
        paths = list((self.directory / "loaded").glob("*.json"))
        if external is not None:
            paths.extend(external.glob("*.json"))
        for path in sorted(paths):
            report = read_json(path)
            if report.get("role_id") != self.persona or report.get("environment") != self.environment or report.get("relation_id", "partner") != self.relation:
                continue
            if self.is_companion and (not qq or not qq["enabled"] or report.get("qq_generation") != qq["generation"]):
                continue
            from personalization_status import report_status
            reports.append(report_status(self, report, external=external is not None and path.parent == external))
        seed, _, _ = self.seed()
        applied = self.values_for(role)
        from personalization_status import required_consumers, grants_for, capability_states
        status_role = role or self.seed()[1]
        grants = grants_for(self, status_role, qq=qq)
        required = required_consumers(status_role, applied, grants)
        matching = {r["consumer"] for r in reports if r["confirmed"] and r.get("version") == version}
        effective = bool(version and required) and set(required).issubset(matching)
        from personalization_view import live_state
        fields = fields_for(status_role)
        bindings = [{key: b.get(key) for key in ("channel", "peer_type", "peer_id")}
                    for b in status_role["relationships"][self.relation].get("bindings", [])
                    if b.get("environment") == self.environment]
        scope = dict(adapter=status_role.get("adapter", "unbound"), bindings=bindings)
        if self.is_companion:
            scope["runtime_directory"] = str(self.runtime_root)
            if qq_root:
                scope["qq_runtime_directory"] = str(qq_root)
        return dict(persona_id=self.persona, relation_id=self.relation, environment=self.environment, draft=draft,
                    scope=scope,
                    live_state=live_state(self, qq_root=qq_root),
                    applied=dict(version=version, values=applied, candidate=self.personalization(role).get("candidate"),
                                 has_standard_assets=bool(version and (self.role_dir / "releases" / version / "assets/canonical/body/sheet.png").is_file())),
                    runtime=dict(status="loaded" if effective else "pending" if version else "unbound",
                                 required=required, reports=reports),
                    history=read_json(self.directory / "history.json", []),
                    sections=SECTIONS, fields=fields,
                    capabilities=capability_states(status_role, applied, grants) + [
                        dict(id="voice_reply", name="语音回复", status="pending", note="正常聊天触发链尚未接通"),
                        dict(id="avatar", name="真实账号头像", status="conditional", note="按通道单独确认；Mac微信不支持自动更换"),
                        dict(id="life_order", name="点单与出行", status="disabled", note="保留原服务门禁，本次不扩展交易能力"),
                    ])


def _merge(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base
