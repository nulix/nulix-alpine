#!/usr/bin/env python3
"""Resolve exact recipe-matching GHCR components without requiring a merged pin PR.

Also distributed as scripts/resolve-machine-assets.py in nulix-alpine and installed
beside the hosted runner. Keep those deployment copies identical.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess

COMPONENTS = ('linux-kernel', 'u-boot', 'firmware', 'ostree-initramfs')
MACHINE = 'raspberry-pi-3'


def fingerprint(root):
    # Keep compatible with the publisher's input hash, including older foundation
    # revisions that predate the publishing script itself.
    plan = json.loads(subprocess.run(['docker', 'buildx', 'bake', '-f', 'docker-bake.hcl',
        '-f', 'machines/' + MACHINE + '.hcl', '--print', *COMPONENTS], cwd=root,
        check=True, text=True, stdout=subprocess.PIPE, timeout=60).stdout)['target']
    recipes = {name: {key: value for key, value in plan[name].items()
                     if key in ('context', 'dockerfile', 'args', 'contexts', 'platforms')}
               for name in ('nulix-builder', *COMPONENTS)}
    files = {}
    for directory in ('builder', *(f'recipes/{name}' for name in COMPONENTS)):
        for file in sorted((root / directory).rglob('*')):
            if file.is_symlink():
                raise ValueError('Machine recipes must not contain symlinks')
            if file.is_file():
                files[file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps({'recipes': recipes, 'files': files}, sort_keys=True).encode()).hexdigest()


def inspect(image):
    try:
        result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', image,
                                 '--format', '{{json .Manifest}}'],
                                capture_output=True, text=True, timeout=40)
        if result.returncode:
            error = result.stderr.lower()
            reason = 'access denied' if any(word in error for word in ('unauthorized', 'denied', 'forbidden')) else 'not published' if any(word in error for word in ('not found', 'manifest unknown', 'name unknown')) else 'registry unavailable'
            return None, reason
        digest = json.loads(result.stdout).get('digest', '')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise ValueError('Registry returned an invalid component digest')
        return image.rsplit(':', 1)[0] + '@' + digest, None
    except (subprocess.TimeoutExpired, OSError):
        return None, 'registry unavailable'


def render(components):
    lines = ['# Resolved for this build; references are immutable GHCR digests.',
             'group "default" { targets = ["ostree-rootfs", "ostree-sd-image"] }']
    for target, names in [('ostree-rootfs', ('linux-kernel', 'ostree-initramfs')),
                          ('ostree-sd-image', ('linux-kernel', 'u-boot', 'firmware'))]:
        lines += ['', f'target "{target}" {{', '  contexts = {']
        lines += [f'    {name} = "docker-image://{components[name]}"' for name in names if name in components]
        lines += ['  }', '}']
    return '\n'.join(lines) + '\n'


def prepare(root, repository='nulix/nulix-alpine', probe=inspect):
    root = Path(root).resolve()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*', repository):
        raise ValueError('Invalid foundation registry repository')
    tool = root / 'scripts/machine-assets.py'
    pins = [root / name for name in ('machine-assets.lock.json', 'hosted-machine-assets.hcl')]
    if any(file.exists() or file.is_symlink() for file in pins):
        if not all(file.is_file() and not file.is_symlink() for file in (*pins, tool)):
            raise ValueError('Incomplete or invalid committed machine asset pins')
        subprocess.run(['python3', str(tool), 'check'], cwd=root, check=True,
                       stdout=subprocess.PIPE, text=True, timeout=120)
        return {'overlay': pins[1].name, 'reused': list(COMPONENTS), 'compiled': [], 'mode': 'committed'}
    if not (root / ('machines/' + MACHINE + '.hcl')).is_file():
        return {'overlay': None, 'reused': [], 'compiled': list(COMPONENTS), 'mode': 'source',
                'reason': 'Foundation does not provide the supported machine profile'}
    inputs = fingerprint(root)
    images = [f'ghcr.io/{repository}/{MACHINE}-{name}:{inputs}' for name in COMPONENTS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(probe, images))
    components = {name: ref for name, (ref, _) in zip(COMPONENTS, results) if ref}
    # Partial availability is useful: only unresolved contexts retain source targets.
    report = {'overlay': '.nulix/machine-assets.hcl', 'inputsSha256': inputs,
              'components': components, 'mode': 'discovered', 'reused': list(components),
              'compiled': [name for name in COMPONENTS if name not in components],
              'fallbacks': {name: reason for name, (_, reason) in zip(COMPONENTS, results) if reason}}
    directory = root / '.nulix'
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise ValueError('.nulix must be a real directory')
    directory.mkdir(mode=0o700, exist_ok=True)
    for name, contents in [('machine-assets.hcl', render(components)),
                           ('resolved-machine-assets.json', json.dumps(report, indent=2) + '\n')]:
        file = directory / name
        if file.is_symlink():
            raise ValueError('Resolved machine asset output must not be a symlink')
        file.write_text(contents)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--repository', default='nulix/nulix-alpine')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.project, args.repository)))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Machine asset discovery failed: ' + str(error) + '\n')
