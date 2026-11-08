#!/usr/bin/env python3
"""NULIX enrollment/check-in agent. Private keys never leave this device."""
import argparse
from datetime import datetime, timezone
import ipaddress
import tarfile
import tempfile
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "0.4.0"
UUID = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
STOP = False

class AgentError(Exception):
    pass

def private_dir(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise AgentError("State directory must be owned by this user and cannot be a symlink")
    directory.chmod(0o700)
    return directory

def write_private(file, content):
    file = Path(file)
    temporary = file.with_name(file.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(file))
    finally:
        temporary.unlink(missing_ok=True)

def read_json(file):
    file = Path(file)
    if file.stat().st_size > 32768:
        raise AgentError("Configuration is too large")
    return json.loads(file.read_text())

def endpoint(value):
    if not isinstance(value, str):
        raise AgentError("Missing HTTPS device endpoint")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise AgentError("Device endpoint must be an HTTPS origin")
    return value.rstrip("/")

def bootstrap(file):
    data = read_json(file)
    if data.get("schemaVersion") != 1 or not UUID.fullmatch(data.get("deviceId", "")) or not re.fullmatch(r"nulix_enroll_[a-zA-Z0-9_-]{43}", data.get("token", "")):
        raise AgentError("Invalid enrollment configuration")
    data["endpoint"] = endpoint(data.get("endpoint"))
    ca = data.get("caCertificate")
    if ca is not None and (not isinstance(ca, str) or len(ca) > 16000 or "BEGIN CERTIFICATE" not in ca):
        raise AgentError("Invalid platform trust certificate")
    return data

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def tls_context(data, identity=None):
    context = ssl.create_default_context(cadata=data.get("caCertificate") or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if identity:
        context.load_cert_chain(str(identity / "client.crt"), str(identity / "client.key"))
    return context

def post(data, route, body, identity=None):
    request = urllib.request.Request(data["endpoint"] + route, data=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls_context(data, identity)), NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(32769)
            if len(raw) > 32768:
                raise AgentError("Platform response is too large")
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AgentError("Access rejected. The enrollment may be expired, used or revoked; generate a new configuration.") from None
        if error.code == 409:
            raise AgentError("Enrollment is in progress or bound to a different key. Retry this configuration; regenerate only if the device key was lost.") from None
        raise AgentError("Platform request failed (HTTP %s); retrying keeps the same device key." % error.code) from None
    except (urllib.error.URLError, ssl.SSLError, ValueError):
        raise AgentError("HTTPS connection failed. Check endpoint reachability, trust certificate and device clock.") from None

def command(args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode:
        raise AgentError("OpenSSL could not prepare the device key or certificate")
    return result.stdout.decode().strip()

def active(state):
    file = state / "active.json"
    if not file.exists():
        return None
    data = read_json(file)
    if not UUID.fullmatch(data.get("deviceId", "")) or not SHA.fullmatch(data.get("fingerprint", "")):
        raise AgentError("Invalid stored device identity")
    data["endpoint"] = endpoint(data.get("endpoint"))
    directory = state / "identities" / data["fingerprint"]
    for file in (directory / "client.key", directory / "client.crt"):
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise AgentError("Stored device credentials must be private regular files")
    return data, directory

def enroll(state, file):
    data = bootstrap(file)
    current = active(state)
    token_hash = hashlib.sha256(data["token"].encode()).hexdigest()
    if current and current[0]["deviceId"] != data["deviceId"]:
        raise AgentError("This installation belongs to another device; use a fresh state directory to reprovision it")
    if current and current[0].get("enrollmentHash") == token_hash:
        Path(file).unlink(missing_ok=True)
        return current
    pending = private_dir(state / "pending")
    marker = pending / "request.json"
    if marker.exists() and read_json(marker).get("tokenHash") != token_hash:
        for child in pending.iterdir():
            if child.is_symlink() or not child.is_file():
                raise AgentError("Unexpected file in pending identity directory")
            child.unlink()
    if not (pending / "client.key").exists():
        command(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(pending / "client.key")])
        (pending / "client.key").chmod(0o600)
    if not (pending / "client.csr").exists():
        command(["openssl", "req", "-new", "-sha256", "-key", str(pending / "client.key"), "-subj", "/CN=" + data["deviceId"], "-config", "/dev/null", "-out", str(pending / "client.csr")])
    write_private(marker, json.dumps({"tokenHash": token_hash, "deviceId": data["deviceId"]}))
    result = post(data, "/enroll", {"deviceId": data["deviceId"], "token": data["token"], "csr": (pending / "client.csr").read_text()})
    certificate = result.get("certificate", "")
    if result.get("deviceId") != data["deviceId"] or not isinstance(certificate, str) or len(certificate) > 16000:
        raise AgentError("Platform returned an unexpected device identity")
    try:
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    except ValueError:
        raise AgentError("Platform returned an invalid certificate") from None
    if fingerprint != result.get("fingerprint"):
        raise AgentError("Certificate fingerprint does not match")
    identities = private_dir(state / "identities")
    identity = private_dir(identities / fingerprint)
    write_private(identity / "client.key", (pending / "client.key").read_text())
    write_private(identity / "client.crt", certificate)
    tls_context(data, identity)  # Verify certificate and private key match before activation.
    saved = {key: data.get(key) for key in ("deviceId", "endpoint", "caCertificate")}
    saved.update({"fingerprint": fingerprint, "expiresAt": result.get("expiresAt"), "enrollmentHash": token_hash})
    write_private(state / "active.json", json.dumps(saved))
    try:
        Path(file).unlink()
    except OSError:
        print("Enrollment complete; the consumed boot configuration could not be removed.", flush=True)
    for child in pending.iterdir():
        child.unlink()
    for old in identities.iterdir():
        if old.name != fingerprint and SHA.fullmatch(old.name) and old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)
    print("Device enrolled. Private key remains on this device.", flush=True)
    return saved, identity

def current_commit():
    try:
        result = subprocess.run(["ostree", "admin", "status"], capture_output=True, text=True, timeout=10, env={**os.environ, "LC_ALL": "C"})
        if result.returncode == 0:
            match = re.search(r"^\s*\*\s+\S+\s+([a-f0-9]{64})\.\d+", result.stdout, re.MULTILINE)
            return match.group(1) if match else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None

def read_optional(file, limit=200):
    try:
        return Path(file).read_text()[:limit].replace("\x00", "").strip() or None
    except OSError:
        return None

def output(args, timeout=10):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    if result.returncode or len(result.stdout) > 262144:
        raise AgentError("Device command unavailable")
    return result.stdout

def telemetry():
    addresses = []
    try:
        for interface in json.loads(output(["ip", "-j", "address", "show"])):
            for item in interface.get("addr_info", []):
                address = ipaddress.ip_address(item.get("local", ""))
                if not address.is_loopback and not address.is_link_local and not address.is_unspecified:
                    addresses.append({"interface": interface.get("ifname", "")[:40], "address": str(address)})
    except (OSError, ValueError, AgentError, subprocess.TimeoutExpired):
        pass
    docker = {"status": "not-installed", "containers": [], "truncated": False}
    if shutil.which("docker"):
        docker["status"] = "unavailable"
        try:
            # Never collect container commands, environment variables, labels or logs.
            lines = output(["docker", "ps", "--no-trunc", "--format", "{{json .}}"], 15).splitlines()
            for line in lines[:12]:
                item = json.loads(line)
                project = re.search(r"(?:^|,)com\.docker\.compose\.project=nulix-([a-f0-9]{32})(?:,|$)", item.get("Labels", ""))
                docker["containers"].append({"id": item["ID"][:64], "name": item.get("Names", "")[:100], "image": item.get("Image", "")[:200], "state": item.get("State", "running")[:30], "status": item.get("Status", "")[:120], **({"applicationId": project.group(1)} if project else {})})
            docker.update(status="available", truncated=len(lines) > 12)
        except (OSError, ValueError, KeyError, AgentError, subprocess.TimeoutExpired):
            docker["containers"] = []
    return {"addresses": addresses[:16], "docker": docker, "agentCapabilities": {"deploy": bool(shutil.which("ostree")) and Path("/sysroot/ostree/repo").is_dir(), "applications": compose_available(), "inspection": True}}

def update_state(state, deployment, status, error=None):
    value = {"id": deployment["id"], "commit": deployment["commit"], "status": status, "error": error}
    write_private(state / "deployment.json", json.dumps(value))
    return value

def deployment_report(state):
    file = state / "deployment.json"
    if not file.exists():
        return None
    report = read_json(file)
    if report.get("status") in ("downloading", "verifying", "staging"):
        with open(state / "update.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report = update_state(state, report, "failed", "interrupted")
            except BlockingIOError:
                pass
    return report

def start_deployment(state, deployment):
    if not isinstance(deployment, dict) or not UUID.fullmatch(deployment.get("id", "")) or not SHA.fullmatch(deployment.get("commit", "")) or not SHA.fullmatch(deployment.get("sha256", "")) or not isinstance(deployment.get("size"), int) or not 0 < deployment["size"] <= 5_000_000_000 or not isinstance(deployment.get("reboot"), bool):
        raise AgentError("Invalid deployment instruction")
    previous = deployment_report(state)
    if previous and previous.get("id") == deployment["id"]:
        return
    if previous and previous.get("status") not in ("failed", "staged", "rebooting"):
        raise AgentError("Another update is active")
    parsed = urllib.parse.urlsplit(deployment.get("url", ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AgentError("Invalid update download URL")
    file = state / "deployment-command.json"
    write_private(file, json.dumps(deployment))
    with open(state / "update.log", "ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "apply", "--state-dir", str(state), "--deployment-file", str(file)], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)

def extract_repository(archive, destination, max_bytes):
    total, count = 0, 0
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            count += 1
            if count > 300000:
                raise AgentError("repository")
            name = member.name.removeprefix("./")
            parts = Path(name).parts
            if not parts or parts[0] != "ostree-repo" or ".." in parts or member.name.startswith("/"):
                raise AgentError("repository")
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise AgentError("repository")
            total += member.size
            if member.size < 0 or total > max_bytes:
                raise AgentError("space")
            if member.isdir():
                continue
            # Import only immutable loose objects; never trust archived config/hooks/refs.
            if len(parts) != 4 or parts[1] != "objects" or not re.fullmatch(r"[a-f0-9]{2}", parts[2]) or not re.fullmatch(r"[a-f0-9]{62}\.(filez|commit|dirtree|dirmeta)", parts[3]):
                continue
            target = destination.joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, open(target, "xb") as result:
                shutil.copyfileobj(source, result, 1024 * 1024)

def update_command(args, timeout=1800):
    # Bound command output and keep it local; no shell or remotely supplied arguments.
    with tempfile.TemporaryFile() as log:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=timeout)
        if result.returncode:
            raise AgentError("repository" if "deploy" not in args else "stage")

def ensure_update_agent(repo, commit):
    try:
        source = output(["ostree", "--repo=" + str(repo), "cat", commit, "/usr/libexec/nulix-agent.py"], 30)
        version = re.search(r"^VERSION\s*=\s*['\"](\d+)\.(\d+)\.(\d+)['\"]", source, re.MULTILINE)
        if not version or tuple(map(int, version.groups())) < (0, 2, 1):
            raise AgentError("agent-version")
    except (OSError, AgentError, subprocess.TimeoutExpired):
        raise AgentError("agent-version") from None

def apply_deployment(state, file):
    deployment = read_json(file)
    with open(state / "update.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        update_state(state, deployment, "downloading")
        phase = "preflight"
        try:
            if os.geteuid() != 0:
                raise AgentError("permission")
            if not current_commit():
                raise AgentError("not-ostree")
            free = shutil.disk_usage(state).free
            if free < deployment["size"] * 2 + 256 * 1024**2:
                raise AgentError("space")
            with tempfile.TemporaryDirectory(prefix="update-", dir=state) as temporary:
                archive = Path(temporary) / "repo.tar.gz"
                phase = "download"
                url = urllib.parse.urlsplit(deployment["url"])
                if url.scheme != "https" or url.username or url.password:
                    raise AgentError("download")
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
                digest, size, started = hashlib.sha256(), 0, time.monotonic()
                with opener.open(deployment["url"], timeout=30) as source, open(archive, "xb") as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > deployment["size"] or time.monotonic() - started > 2700:
                            raise AgentError("download")
                        digest.update(chunk)
                        target.write(chunk)
                phase = "checksum"
                update_state(state, deployment, "verifying")
                if size != deployment["size"] or digest.hexdigest() != deployment["sha256"]:
                    raise AgentError("checksum")
                phase = "repository"
                repo = Path(temporary) / "repository"
                update_command(["ostree", "--repo=" + str(repo), "init", "--mode=archive-z2"])
                extract_repository(archive, repo, min(16 * 1024**3, max(0, shutil.disk_usage(state).free - 256 * 1024**2)))
                update_command(["ostree", "--repo=" + str(repo), "show", deployment["commit"]], 30)
                update_command(["ostree", "--repo=" + str(repo), "refs", "--create=nulix-update", deployment["commit"]], 30)
                update_command(["ostree", "--repo=" + str(repo), "fsck", "--quiet"])
                ensure_update_agent(repo, deployment["commit"])
                update_command(["ostree", "--repo=/sysroot/ostree/repo", "pull-local", "--untrusted", str(repo), deployment["commit"]])
                phase = "stage"
                update_state(state, deployment, "staging")
                update_command(["ostree", "admin", "--sysroot=/sysroot", "deploy", "--os=nulix", "--retain-rollback", deployment["commit"]])
                update_state(state, deployment, "staged")
            if deployment["reboot"]:
                phase = "reboot"
                update_state(state, deployment, "rebooting")
                # Persist identity and deployment state before requesting a normal reboot.
                os.sync()
                update_command(["/sbin/reboot"], 30)
        except (OSError, ValueError, AgentError, subprocess.TimeoutExpired, tarfile.TarError, urllib.error.URLError) as error:
            failure = phase
            if isinstance(error, AgentError) and str(error) in ("permission", "not-ostree", "space", "checksum", "repository", "stage", "reboot", "download", "agent-version"):
                failure = str(error)
            elif phase == "download":
                reason = getattr(error, "reason", error)
                if isinstance(error, urllib.error.HTTPError):
                    failure = "download-access" if error.code in (401, 403) else "download-missing" if error.code == 404 else "download-http"
                elif isinstance(reason, ssl.SSLError):
                    failure = "download-tls"
                elif isinstance(reason, TimeoutError):
                    failure = "download-timeout"
                elif isinstance(error, urllib.error.URLError):
                    failure = "download-network"
            update_state(state, deployment, "failed", failure)
            print("Update failed: " + failure + "; the running deployment was retained.", flush=True)
        finally:
            Path(file).unlink(missing_ok=True)

APP_PHASES = ("validating", "pulling", "starting", "stopping", "confirming")
COMPOSE_READY = None

def compose_available():
    global COMPOSE_READY
    if COMPOSE_READY is None:
        try:
            output(["docker", "compose", "version", "--short"], 5)
            COMPOSE_READY = True
        except (OSError, AgentError, subprocess.TimeoutExpired):
            COMPOSE_READY = False
    return COMPOSE_READY

def app_validate(model):
    def keys(value, allowed):
        if not isinstance(value, dict) or any(key not in allowed for key in value):
            raise AgentError("policy")
    def text(value):
        return isinstance(value, str) and len(value) <= 1000 and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)
    def explicit(value):
        return text(value) and "$" not in value.replace("$$", "")
    identifier = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")
    keys(model, ("services", "volumes"))
    if len(json.dumps(model, separators=(",", ":"), ensure_ascii=False).encode()) > 16384 or not isinstance(model.get("services"), dict) or not 1 <= len(model["services"]) <= 5:
        raise AgentError("policy")
    volumes = model.get("volumes", {})
    if not isinstance(volumes, dict) or len(volumes) > 10 or any(not identifier.fullmatch(key) or value != {} for key, value in volumes.items()):
        raise AgentError("policy")
    ports = set()
    for name, value in model["services"].items():
        if not identifier.fullmatch(name):
            raise AgentError("policy")
        keys(value, ("image", "restart", "ports", "volumes", "environment", "depends_on", "command", "healthcheck"))
        if not isinstance(value.get("image"), str) or len(value["image"]) > 200 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:-]*(?:@sha256:[a-f0-9]{64})?", value["image"]) or "://" in value["image"] or value.get("restart") not in ("no", "always", "unless-stopped", "on-failure"):
            raise AgentError("policy")
        if not isinstance(value.get("ports", []), list) or len(value.get("ports", [])) > 8:
            raise AgentError("policy")
        for port in value.get("ports", []):
            match = re.fullmatch(r"(\d{4,5}):(\d{1,5})/(tcp|udp)", port) if isinstance(port, str) else None
            if not match or not 1024 <= int(match[1]) <= 65535 or int(match[1]) in (2375, 2376) or not 1 <= int(match[2]) <= 65535 or (match[1], match[3]) in ports:
                raise AgentError("policy")
            ports.add((match[1], match[3]))
        if not isinstance(value.get("volumes", []), list) or len(value.get("volumes", [])) > 10:
            raise AgentError("policy")
        for mount in value.get("volumes", []):
            parts = mount.split(":") if isinstance(mount, str) else []
            if not 2 <= len(parts) <= 3 or parts[0] not in volumes or not re.fullmatch(r"/[a-zA-Z0-9_./-]+", parts[1]) or ".." in parts[1].split("/") or len(parts) == 3 and parts[2] not in ("ro", "rw"):
                raise AgentError("policy")
        environment = value.get("environment", {})
        if not isinstance(environment, dict) or len(environment) > 30 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", key) or not explicit(item) for key, item in environment.items()):
            raise AgentError("policy")
        dependencies = value.get("depends_on", [])
        if not isinstance(dependencies, list) or len(dependencies) > 5 or any(key == name or key not in model["services"] for key in dependencies):
            raise AgentError("policy")
        if "command" in value and (not isinstance(value["command"], list) or len(value["command"]) > 30 or not all(explicit(item) for item in value["command"])):
            raise AgentError("policy")
        if "healthcheck" in value:
            check = value["healthcheck"]
            keys(check, ("test", "interval", "timeout", "retries", "start_period"))
            test = check.get("test")
            if not isinstance(test, list) or not 2 <= len(test) <= 20 or test[0] not in ("CMD", "CMD-SHELL") or not all(explicit(item) for item in test):
                raise AgentError("policy")
            for key in ("interval", "timeout", "start_period"):
                if key in check and (not isinstance(check[key], str) or not re.fullmatch(r"\d{1,3}(ms|s|m)", check[key])):
                    raise AgentError("policy")
            if "retries" in check and (type(check["retries"]) is not int or not 1 <= check["retries"] <= 10):
                raise AgentError("policy")
    visiting, visited = set(), set()
    def visit(name):
        if name in visiting:
            raise AgentError("policy")
        if name in visited:
            return
        visiting.add(name)
        for dependency in model["services"][name].get("depends_on", []):
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
    for name in model["services"]:
        visit(name)
    return model

def app_environment():
    # Do not inherit Docker endpoints, Compose files, interpolation variables or .env files.
    return {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C", "LC_ALL": "C", "COMPOSE_DISABLE_ENV_FILE": "1"}

def app_base(directory, application_id):
    return ["docker", "compose", "--env-file", "/dev/null", "--project-directory", str(directory), "--project-name", "nulix-" + application_id.replace("-", ""), "--file", str(directory / "compose.json")]

def app_command(args, phase, timeout=180):
    with tempfile.TemporaryFile() as log:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=app_environment(), start_new_session=True)
        try:
            result = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raise AgentError("timeout") from None
        if result:
            raise AgentError(phase)

def app_observation(directory, application_id, manifest):
    expected = list(manifest["services"])
    try:
        result = subprocess.run(app_base(directory, application_id) + ["ps", "--all", "--format", "json"], capture_output=True, text=True, timeout=8, env=app_environment())
        if result.returncode or len(result.stdout) > 131072:
            raise AgentError("runtime")
        raw = result.stdout.strip()
        containers = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines() if line]
        if not isinstance(containers, list):
            containers = [containers]
        services = []
        for name in expected:
            row = next((row for row in containers if row.get("Service") == name), {})
            state = row.get("State", "missing")
            if state not in ("running", "exited", "created", "restarting", "paused", "dead", "missing"):
                state = "unknown"
            health = row.get("Health", "")
            services.append({"name": name, "state": state, "health": health if health in ("healthy", "unhealthy", "starting") else ""})
        if all(row["state"] == "running" and row["health"] not in ("unhealthy", "starting") for row in services):
            status = "running"
        elif all(row["state"] in ("exited", "created", "missing") for row in services):
            status = "stopped" if manifest.get("installed") else "unavailable"
        else:
            status = "partial"
        return {"id": application_id, "state": status, "services": services}
    except (OSError, ValueError, AgentError, subprocess.TimeoutExpired):
        return {"id": application_id, "state": "unavailable", "services": [{"name": name, "state": "unknown", "health": ""} for name in expected]}

def application_snapshot(state):
    root = state / "applications"
    reports = []
    if not root.is_dir() or root.is_symlink():
        return reports
    for directory in sorted(root.iterdir())[:5]:
        if directory.is_symlink() or not directory.is_dir() or not UUID.fullmatch(directory.name):
            continue
        try:
            manifest = read_json(directory / "manifest.json")
            if not isinstance(manifest.get("services"), list) or not 1 <= len(manifest["services"]) <= 5 or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,49}", name) for name in manifest["services"]):
                continue
            reports.append(app_observation(directory, directory.name, manifest))
        except (OSError, ValueError):
            pass
    return reports

def application_command_report(state):
    file = state / "application-operation.json"
    if not file.exists():
        return None
    report = read_json(file)
    if report.get("status") in APP_PHASES:
        with open(state / "update.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report.update(status="failed", error="interrupted")
                write_private(file, json.dumps(report))
            except BlockingIOError:
                pass
    return {key: report.get(key) for key in ("id", "status", "error")}

def app_phase(state, command, status, error=None):
    write_private(state / "application-operation.json", json.dumps({"id": command["id"], "status": status, "error": error}))

def start_application_command(state, command):
    if not isinstance(command, dict) or not UUID.fullmatch(command.get("id", "")) or not UUID.fullmatch(command.get("applicationId", "")) or command.get("action") not in ("deploy", "start", "stop"):
        raise AgentError("Invalid application instruction")
    previous = application_command_report(state)
    if previous and previous.get("id") == command["id"]:
        return
    if previous and previous.get("status") in APP_PHASES:
        raise AgentError("An application operation is already active")
    file = state / "application-command.json"
    write_private(file, json.dumps(command, ensure_ascii=False))
    with open(state / "application-worker.log", "ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "apps-apply", "--state-dir", str(state), "--application-file", str(file)], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)

def apply_application_command(state, file):
    command = read_json(file)
    if not UUID.fullmatch(command.get("id", "")) or not UUID.fullmatch(command.get("applicationId", "")) or command.get("action") not in ("deploy", "start", "stop"):
        raise AgentError("Invalid application instruction")
    with open(state / "update.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            app_phase(state, command, "failed", "conflict")
            return
        phase = "policy"
        app_phase(state, command, "validating")
        try:
            expires = datetime.fromisoformat(command["expiresAt"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= expires or os.geteuid() != 0:
                raise AgentError("policy")
            compose = app_validate(command["compose"])
            if not compose_available():
                raise AgentError("runtime")
            root = private_dir(state / "applications")
            directory = private_dir(root / command["applicationId"])
            compose_file = directory / "compose.json"
            manifest_file = directory / "manifest.json"
            manifest = read_json(manifest_file) if manifest_file.exists() else {"services": list(compose["services"]), "installed": False}
            if compose_file.exists() and read_json(compose_file) != compose:
                raise AgentError("policy")
            if command["action"] != "deploy" and not compose_file.exists():
                raise AgentError("compose")
            write_private(compose_file, json.dumps(compose, ensure_ascii=False))
            write_private(manifest_file, json.dumps(manifest))
            base = app_base(directory, command["applicationId"])
            phase = "compose"
            app_command(base + ["config", "--quiet"], phase, 30)
            if command["action"] == "deploy":
                phase = "pull"
                app_phase(state, command, "pulling")
                app_command(base + ["pull"], phase, 600)
                phase = "start"
                app_phase(state, command, "starting")
                app_command(base + ["up", "--detach", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "120"], phase, 180)
                manifest["installed"] = True
                write_private(manifest_file, json.dumps(manifest))
            elif command["action"] == "start":
                phase = "start"
                app_phase(state, command, "starting")
                app_command(base + ["start"], phase, 60)
            else:
                phase = "stop"
                app_phase(state, command, "stopping")
                app_command(base + ["stop", "--timeout", "30"], phase, 60)
            app_phase(state, command, "confirming")
            wanted = "stopped" if command["action"] == "stop" else "running"
            deadline = time.monotonic() + 120
            while True:
                observation = app_observation(directory, command["applicationId"], manifest)
                if observation["state"] == wanted:
                    break
                if time.monotonic() >= deadline:
                    raise AgentError("stop" if wanted == "stopped" else "start")
                time.sleep(2)
            app_phase(state, command, "succeeded")
        except (OSError, ValueError, KeyError, TypeError, AgentError, subprocess.TimeoutExpired) as error:
            failure = str(error) if isinstance(error, AgentError) and str(error) in ("policy", "compose", "pull", "start", "stop", "runtime", "timeout") else phase
            app_phase(state, command, "failed", failure)
            print("Application action failed: " + failure, flush=True)
        finally:
            Path(file).unlink(missing_ok=True)

def inventory_components():
    image_file = Path('/usr/share/nulix/sbom.spdx.json')
    if image_file.is_file() and image_file.stat().st_size <= 2 * 1024 * 1024:
        value = json.loads(image_file.read_text())
        packages = value.get('packages', [])
        components = [{'name': p['name'][:150], 'version': p.get('versionInfo', 'Unknown')[:150], 'license': p.get('licenseDeclared', 'NOASSERTION')[:150], 'kind': 'kernel' if p.get('primaryPackagePurpose') == 'OPERATING-SYSTEM' and p['name'] == 'linux' else 'agent' if p['name'] == 'nulix-agent' else 'os'} for p in packages]
        source = 'image-sbom'
    else:
        components = []
        for record in Path('/lib/apk/db/installed').read_text().split('\n\n'):
            fields = dict(line.split(':', 1) for line in record.splitlines() if len(line) > 1 and line[1] == ':')
            if fields.get('P') and fields.get('V'):
                components.append({'name': fields['P'][:150], 'version': fields['V'][:150], 'license': fields.get('L', 'Unknown')[:150], 'kind': 'os'})
        components.append({'name': 'linux', 'version': os.uname().release[:150], 'license': 'GPL-2.0-only', 'kind': 'kernel'})
        source = 'device-inventory'
    if not 1 <= len(components) <= 3000:
        raise AgentError('Inventory exceeds supported size')
    return sorted(components, key=lambda c: (c['kind'], c['name'])), source

def redact_log(line):
    line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line)
    line = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', line)
    line = re.sub(r'nulix_[A-Za-z0-9_-]{20,}', '[REDACTED]', line)
    line = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+', r'\1[REDACTED]', line)
    line = re.sub(r'(?i)((?:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*)\S+', r'\1[REDACTED]', line)
    return line.encode('utf-8')[:300].decode('utf-8', errors='ignore')

def bounded_log_command(args):
    with tempfile.TemporaryFile() as output_file:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output_file, stderr=subprocess.DEVNULL, env=app_environment(), start_new_session=True)
        try:
            result = child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL); child.wait()
            raise AgentError('Log source unavailable') from None
        if result:
            raise AgentError('Log source unavailable')
        length = output_file.tell()
        output_file.seek(max(0, length - 16000))
        lines = output_file.read(16000).decode('utf-8', errors='replace').splitlines()
        return lines[-30:], length > 16000 or len(lines) > 30

def read_log_batch(state, instruction, cache):
    source = instruction.get('source')
    if source not in ('system', 'kernel', 'agent', 'applications'):
        raise AgentError('Invalid log source')
    truncated = False
    if source in ('system', 'agent'):
        path = Path('/var/log/messages' if source == 'system' else '/var/log/nulix-agent.log')
        with path.open('rb') as stream:
            info = os.fstat(stream.fileno())
            position = cache.get('position', 0) if cache.get('inode') == info.st_ino else 0
            if position > info.st_size: position = 0
            start = max(position, info.st_size - 12000)
            truncated = bool(position and start > position)
            stream.seek(start)
            raw = stream.read(12000)
            cache.update(inode=info.st_ino, position=stream.tell())
        if start > position: raw = raw.split(b'\n', 1)[-1]
        lines = raw.decode('utf-8', errors='replace').splitlines()
        truncated = truncated or len(lines) > 30
        lines = lines[-30:]
    elif source == 'kernel':
        lines, truncated = bounded_log_command(['dmesg'])
        hashes = [hashlib.sha256(line.encode()).hexdigest() for line in lines]
        seen = set(cache.get('seen', [])); lines = [line for line, digest in zip(lines, hashes) if digest not in seen]
        cache['seen'] = hashes
    else:
        app_id = instruction.get('applicationId', '')
        if not UUID.fullmatch(app_id): raise AgentError('Invalid application logs')
        directory = state / 'applications' / app_id
        if directory.is_symlink() or not (directory / 'manifest.json').is_file(): raise AgentError('Unknown managed application')
        args = app_base(directory, app_id) + ['logs', '--no-color', '--timestamps', '--tail', '30']
        if cache.get('since'): args += ['--since', cache['since']]
        since = datetime.now(timezone.utc).isoformat()
        lines, truncated = bounded_log_command(args)
        hashes = [hashlib.sha256(line.encode()).hexdigest() for line in lines]
        seen = set(cache.get('seen', [])); lines = [line for line, digest in zip(lines, hashes) if digest not in seen]
        cache.update(seen=hashes, since=since)
    result = []
    for line in lines:
        if '-----BEGIN ' in line and 'PRIVATE KEY' in line: cache['privateKey'] = True
        if cache.get('privateKey'):
            if '-----END ' in line and 'PRIVATE KEY' in line: cache['privateKey'] = False
            continue
        message = redact_log(line)
        if not message: continue
        level = 'error' if re.search(r'\b(error|failed|fatal|panic)\b', message, re.I) else 'warning' if re.search(r'\b(warn(?:ing)?|timeout)\b', message, re.I) else 'info'
        result.append({'time': datetime.now(timezone.utc).isoformat(), 'level': level, 'message': message})
    return result, truncated

def send_inspection(state, data, directory, instruction):
    report = {'deviceId': data['deviceId']}
    requested = instruction.get('inventory')
    if requested and SHA.fullmatch(requested.get('commit', '')) and requested['commit'] == current_commit():
        components, source = inventory_components()
        offset = requested.get('offset', 0)
        if isinstance(offset, int) and 0 <= offset < len(components):
            chunk = components[offset:offset + 40]
            while len(json.dumps(chunk, ensure_ascii=False).encode()) > 14000 and len(chunk) > 1: chunk.pop()
            report['inventory'] = {'commit': requested['commit'], 'offset': offset, 'total': len(components), 'source': source, 'components': chunk}
    session = instruction.get('log')
    if session and UUID.fullmatch(session.get('id', '')) and isinstance(session.get('sequence'), int) and session['sequence'] >= 0:
        file = state / 'inspection-cursor.json'
        cache = read_json(file) if file.exists() else {}
        boot = read_optional('/proc/sys/kernel/random/boot_id', 36) or ''
        if cache.get('id') != session['id'] or cache.get('bootId') != boot:
            cache = {'id': session['id'], 'bootId': boot}
        pending = cache.get('pending')
        if not pending or pending.get('sequence') != session['sequence'] + 1:
            try:
                lines, truncated = read_log_batch(state, session, cache)
                status = 'live'
            except (OSError, ValueError, AgentError, subprocess.TimeoutExpired):
                lines, truncated, status = [], False, 'unavailable'
            pending = {'id': session['id'], 'sequence': session['sequence'] + 1, 'bootId': boot, 'state': status, 'lines': lines, 'truncated': truncated}
            cache['pending'] = pending
            write_private(file, json.dumps(cache, ensure_ascii=False))
        report['log'] = pending
    if len(report) > 1:
        post(data, '/inspect', report, directory)

def checkin(state):
    identity = active(state)
    if not identity:
        raise AgentError("Device is not enrolled")
    data, directory = identity
    report = {"deviceId": data["deviceId"], "agentVersion": VERSION, "currentCommit": current_commit(), "bootId": read_optional("/proc/sys/kernel/random/boot_id", 36), "machine": read_optional("/proc/device-tree/model"), "telemetry": telemetry(), "deployment": deployment_report(state), "applications": application_snapshot(state), "applicationCommand": application_command_report(state)}
    response = post(data, "/checkin", report, directory)
    if response.get("accepted") is not True or response.get("deviceId") != data["deviceId"]:
        raise AgentError("Check-in was not accepted")
    if response.get("deployment"):
        start_deployment(state, response["deployment"])
    elif response.get("application"):
        start_application_command(state, response["application"])
    if response.get("inspection"):
        try:
            send_inspection(state, data, directory, response["inspection"])
        except (OSError, ValueError, KeyError, TypeError, AgentError, subprocess.TimeoutExpired):
            print("Inspection report deferred; device check-ins continue.", flush=True)
    print("Authenticated device check-in accepted.", flush=True)
    return response

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enroll", "checkin", "run", "apply", "apps-apply"])
    parser.add_argument("--state-dir", default="/var/lib/nulix")
    parser.add_argument("--config", help="One-device enrollment JSON; removed after successful enrollment")
    parser.add_argument("--bootstrap", default="/mnt/boot/nulix-enrollment.json")
    parser.add_argument("--deployment-file", help=argparse.SUPPRESS)
    parser.add_argument("--application-file", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    state = private_dir(args.state_dir)
    os.umask(0o077)
    if args.action == "apps-apply":
        if not args.application_file:
            raise AgentError("Missing application command")
        apply_application_command(state, args.application_file)
        return 0
    if args.action == "apply":
        if not args.deployment_file:
            raise AgentError("Missing deployment command")
        apply_deployment(state, args.deployment_file)
        return 0
    lock = open(state / "agent.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise AgentError("Another agent is already using this identity") from None
    if args.action == "enroll":
        if not args.config:
            raise AgentError("Provide --config with the downloaded enrollment JSON")
        enroll(state, args.config)
    elif args.action == "checkin":
        checkin(state)
    else:
        failures = 0
        global STOP
        def stop(_signal, _frame):
            global STOP
            STOP = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not STOP:
            try:
                file = Path(args.config or args.bootstrap)
                if file.exists():
                    enroll(state, file)
                if not active(state):
                    raise AgentError("Waiting for this device's enrollment configuration on the boot partition")
                response = checkin(state)
                failures = 0
                delay = max(5, min(60, int(response.get("nextCheckinSeconds", 60))))
            except (AgentError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                failures += 1
                print("nulix-agent: " + str(error), file=sys.stderr, flush=True)
                delay = min(300, 5 * 2 ** min(failures, 6)) + random.randint(0, 5)
            for _ in range(delay):
                if STOP:
                    break
                time.sleep(1)
    lock.close()
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AgentError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("nulix-agent: " + str(error), file=sys.stderr)
        sys.exit(1)
