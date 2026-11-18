#!/usr/bin/env python3
"""Publish/resolve immutable ARM64 build tooling, independently of OS components."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

DIGEST = re.compile(r'sha256:[a-f0-9]{64}$')
TARGETS = ('firmware', 'linux-kernel', 'u-boot', 'ostree-rootfs', 'ostree-sd-image')


def command(args, root, capture=True):
    return subprocess.run(args, cwd=root, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None, timeout=3600).stdout


def bake(root, overlays=()):
    args = ['docker', 'buildx', 'bake', '-f', 'docker-bake.hcl', '-f', 'machines/raspberry-pi-3.hcl']
    for overlay in overlays:
        args += ['-f', overlay]
    return args


def fingerprint(root):
    root = Path(root)
    plan = json.loads(command(bake(root) + ['--print', 'nulix-builder'], root))['target']['nulix-builder']
    if plan.get('context') not in ('./builder', 'builder') or plan.get('contexts'):
        raise ValueError('Unsupported build tooling context')
    files = {}
    for path in sorted((root / 'builder').rglob('*')):
        if path.is_symlink():
            raise ValueError('Build tooling cannot contain symlinks')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if 'builder/Dockerfile' not in files:
        raise ValueError('Build tooling Dockerfile is missing')
    inputs = {'schemaVersion': 1, 'platform': 'linux/arm64', 'files': files,
              'target': {k: plan[k] for k in ('context', 'dockerfile', 'args', 'contexts', 'platforms') if k in plan}}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def image_name(repository):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*', repository):
        raise ValueError('Invalid registry repository')
    return 'ghcr.io/' + repository + '/build-tools/arm64'


def inspect(ref):
    result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', ref, '--format', '{{json .Manifest}}'],
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        error = result.stderr.lower()
        if 'unauthorized' not in error and any(word in error for word in ('not found', 'manifest unknown', 'name unknown', '403', 'denied')):
            return None
        raise ValueError('Could not verify the prebuilt tooling image; refusing an unexpected tooling rebuild')
    digest = json.loads(result.stdout).get('digest', '')
    if not DIGEST.fullmatch(digest):
        raise ValueError('Invalid build tooling digest')
    return digest


def render(ref, targets):
    if not ref.startswith('ghcr.io/') or not DIGEST.fullmatch(ref.split('@')[-1]):
        raise ValueError('Build tooling must be pinned by digest')
    lines = ['# Prebuilt tooling; no compiler/tool installation during OS assembly.',
             'group "default" { targets = ' + json.dumps([t for t in targets if t != 'nulix-builder']) + ' }']
    for target in TARGETS:
        lines += ['', 'target "' + target + '" {', '  contexts = { builder = "docker-image://' + ref + '" }', '}']
    return '\n'.join(lines) + '\n'


def resolve(root, repository='nulix/nulix-alpine', overlays=(), require=False):
    root = Path(root).resolve()
    inputs = fingerprint(root)
    image = image_name(repository)
    lock_path = root / 'builder-image.lock.json'
    if lock_path.is_symlink():
        raise ValueError('Build tooling lock cannot be a symlink')
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        ref = lock.get('ref', '')
        if lock.get('schemaVersion') != 1 or lock.get('inputsSha256') != inputs or not re.fullmatch(re.escape(image) + r'@sha256:[a-f0-9]{64}', ref):
            raise ValueError('Build tooling lock does not match this recipe')
    else:
        digest = inspect(image + ':input-' + inputs)
        if not digest:
            if require:
                raise ValueError('Publish the matching prebuilt tooling image before starting this hosted build')
            return {'overlay': None, 'mode': 'source', 'inputsSha256': inputs}
        ref = image + '@' + digest
    plan = json.loads(command(bake(root, overlays) + ['--print'], root))
    directory = root / '.nulix'
    if directory.is_symlink():
        raise ValueError('Build state cannot be a symlink')
    directory.mkdir(mode=0o700, exist_ok=True)
    output = directory / 'builder-image.hcl'
    if output.is_symlink():
        raise ValueError('Build tooling overlay cannot be a symlink')
    output.write_text(render(ref, plan['group']['default']['targets']))
    return {'overlay': '.nulix/builder-image.hcl', 'mode': 'published', 'ref': ref, 'inputsSha256': inputs}


def publish(root, repository='nulix/nulix-alpine', builder=None):
    root = Path(root).resolve()
    # Only tooling sources are published. Unrelated OS edits do not change them.
    if command(['git', 'diff', 'HEAD', '--', 'builder', 'docker-bake.hcl', 'machines/raspberry-pi-3.hcl'], root).strip() or command(['git', 'ls-files', '--others', '--exclude-standard', 'builder'], root).strip():
        raise ValueError('Commit build tooling sources before publishing')
    inputs = fingerprint(root)
    image = image_name(repository)
    tag = image + ':input-' + inputs
    digest = inspect(tag)
    revision = command(['git', 'rev-parse', 'HEAD'], root).strip()
    if not digest:
        args = bake(root) + ['nulix-builder', '--set', 'nulix-builder.platform=linux/arm64',
                '--set', 'nulix-builder.output=type=image,push=true', '--set', 'nulix-builder.tags=' + tag,
                '--set', 'nulix-builder.labels.org.opencontainers.image.source=https://github.com/' + repository,
                '--set', 'nulix-builder.labels.org.opencontainers.image.revision=' + revision,
                '--set', 'nulix-builder.labels.io.nulix.builder.inputs=' + inputs, '--provenance=false']
        if builder:
            args += ['--builder', builder]
        command(args, root, capture=False)
        digest = inspect(tag)
        if not digest:
            raise ValueError('Published tooling image was not readable')
    lock = {'schemaVersion': 1, 'inputsSha256': inputs, 'ref': image + '@' + digest}
    (root / 'builder-image.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    print(json.dumps(lock))
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['resolve', 'publish', 'fingerprint'])
    parser.add_argument('--project', default='.')
    parser.add_argument('--repository', default='nulix/nulix-alpine')
    parser.add_argument('--bake-file', action='append', default=[])
    parser.add_argument('--require', action='store_true')
    parser.add_argument('--builder')
    args = parser.parse_args()
    if args.action == 'publish':
        publish(args.project, args.repository, args.builder)
    elif args.action == 'fingerprint':
        print(fingerprint(args.project))
    else:
        print(json.dumps(resolve(args.project, args.repository, args.bake_file, args.require)))


if __name__ == '__main__':
    main()
