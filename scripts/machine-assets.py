#!/usr/bin/env python3
"""Publish versioned, machine-scoped OCI build inputs and immutable digest locks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MACHINE = 'raspberry-pi-3'
COMPONENTS = ('linux-kernel', 'u-boot', 'firmware', 'ostree-initramfs')
PUBLISHED_COMPONENTS = ('rootfs-base', *COMPONENTS)
LOCK = 'machine-assets.lock.json'
OVERLAY = 'hosted-machine-assets.hcl'


def run(args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def bake():
    return ['docker', 'buildx', 'bake', '-f', 'docker-bake.hcl', '-f', 'machines/' + MACHINE + '.hcl']


def fingerprint(component=None):
    # Preserve the hardware-only fingerprint for adoption of existing publications.
    selected = COMPONENTS if component is None else (component,)
    if any(name not in PUBLISHED_COMPONENTS for name in selected):
        raise ValueError('Unknown machine component')
    plan = json.loads(run(bake() + ['--print', *selected], capture=True))['target']
    uses_builder = component not in ('rootfs-base', 'ostree-initramfs')
    targets = (('nulix-builder',) if uses_builder else ()) + selected
    recipes = {name: {key: value for key, value in plan[name].items()
                     if key in ('context', 'dockerfile', 'args', 'contexts', 'platforms')}
               for name in targets}
    files = {}
    for directory in (('builder',) if uses_builder else ()) + tuple('recipes/' + name for name in selected):
        for file in sorted((ROOT / directory).rglob('*')):
            if file.is_symlink():
                raise ValueError('Component recipes must not contain symlinks')
            if file.is_file():
                files[file.relative_to(ROOT).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps({'recipes': recipes, 'files': files}, sort_keys=True).encode()).hexdigest()


def release_versions():
    value = json.loads((ROOT / 'machines' / (MACHINE + '.components.json')).read_text())
    if value.get('schemaVersion') != 1 or value.get('machine') != MACHINE or set(value.get('components', {})) != set(PUBLISHED_COMPONENTS):
        raise ValueError('Invalid component release metadata')
    plan = json.loads(run(bake() + ['--print', *PUBLISHED_COMPONENTS], capture=True))['target']
    versions = value['components']
    for component, info in versions.items():
        if not isinstance(info, dict) or not re.fullmatch(r'[0-9][a-zA-Z0-9._-]{0,70}', info.get('version', '')) or not re.fullmatch(r'[0-9][a-z0-9._-]{0,40}', info.get('line', '')):
            raise ValueError('Invalid release version/line: ' + component)
        if '.'.join(info['version'].split('.')[:2]) != info['line']:
            raise ValueError('Release version does not match its family: ' + component)
        revision_arg = {'linux-kernel': 'KERNEL_REV', 'u-boot': 'UBOOT_REV', 'firmware': 'FIRMWARE_REV'}.get(component)
        if revision_arg and (not re.fullmatch(r'[a-f0-9]{40}', info.get('sourceRevision', '')) or plan[component].get('args', {}).get(revision_arg) != info['sourceRevision']):
            raise ValueError('Update the component version metadata for the pinned source: ' + component)
    if plan['linux-kernel'].get('args', {}).get('MACHINE_ARCH') != 'arm64' or plan['firmware'].get('args', {}).get('MACHINE_NAME') != MACHINE:
        raise ValueError('Component publication requires the reviewed ARM64 Raspberry Pi 3 profile')
    if plan['rootfs-base'].get('args', {}).get('ALPINE_VERSION') != versions['rootfs-base']['version']:
        raise ValueError('Rootfs Base version must match ALPINE_VERSION in Bake')
    return versions


def release_descriptor(repository, component, info, source_hash, channel='stable'):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*', repository) or channel not in ('stable', 'dev'):
        raise ValueError('Invalid repository or release channel')
    metadata = {'schemaVersion': 2, 'machine': MACHINE, 'component': component,
                'release': info, 'sourceInputsSha256': source_hash, 'channel': channel}
    publication_hash = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    image = f'ghcr.io/{repository}/{MACHINE}/{component}/{info["line"]}' + ('-dev' if channel == 'dev' else '')
    return {'image': image, 'tag': image + ':input-' + publication_hash,
            'versionTag': image + ':' + info['version'] + '-' + publication_hash[:12],
            'publicationHash': publication_hash}


def published_digest(image, allow_missing_namespace=False):
    result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', image,
                             '--format', '{{json .Manifest}}'], cwd=ROOT,
                            capture_output=True, text=True, timeout=40)
    if result.returncode:
        error = result.stderr.lower()
        if allow_missing_namespace and ('403' in error or 'denied' in error) and 'unauthorized' not in error:
            return None
        if any(word in error for word in ('unauthorized', 'denied', 'forbidden')):
            raise ValueError('GHCR access denied; no compilation started for ' + image)
        if any(word in error for word in ('not found', 'manifest unknown', 'name unknown')):
            return None
        raise ValueError('GHCR lookup failed; refusing unnecessary compilation for ' + image)
    digest = json.loads(result.stdout).get('digest', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('GHCR returned an invalid component digest')
    return digest


def overlay(lock):
    components = lock.get('components', {})
    expected = set(PUBLISHED_COMPONENTS if lock.get('schemaVersion') == 2 else COMPONENTS)
    if lock.get('schemaVersion') not in (1, 2) or lock.get('machine') != MACHINE or set(components) != expected:
        raise ValueError('Invalid component digest lock')
    for name, ref in components.items():
        if not isinstance(ref, str) or not re.fullmatch(r'ghcr\.io/[a-z0-9][a-z0-9._/-]+@sha256:[a-f0-9]{64}', ref) or '..' in ref:
            raise ValueError('Component must use a GHCR sha256 digest: ' + name)
    lines = ['# Generated by scripts/machine-assets.py; commit with machine-assets.lock.json.',
             'group "default" {', '  targets = ["ostree-rootfs", "ostree-sd-image"]', '}']
    for target, names in [('ostree-rootfs', (('rootfs-base',) if 'rootfs-base' in components else ()) + ('linux-kernel', 'ostree-initramfs')),
                          ('ostree-sd-image', ('linux-kernel', 'u-boot', 'firmware'))]:
        lines += ['', f'target "{target}" {{', '  contexts = {']
        lines += [f'    {name} = "docker-image://{components[name]}"' for name in names]
        lines += ['  }', '}']
    return '\n'.join(lines) + '\n'


def check():
    lock = json.loads((ROOT / LOCK).read_text())
    if lock.get('inputsSha256') != fingerprint():
        raise ValueError('Hardware recipes changed. Publish new components before building.')
    if lock.get('schemaVersion') == 2:
        if lock.get('componentVersions') != release_versions() or any(lock.get('componentInputs', {}).get(c) != fingerprint(c) for c in PUBLISHED_COMPONENTS):
            raise ValueError('Component inputs or release versions changed. Publish fresh pins.')
    if (ROOT / OVERLAY).read_text() != overlay(lock):
        raise ValueError('Component overlay does not match the digest lock')
    plan = json.loads(run(bake() + ['-f', OVERLAY, '--print'], capture=True))
    forbidden = PUBLISHED_COMPONENTS if lock.get('schemaVersion') == 2 else COMPONENTS
    if set(plan['group']['default']['targets']) != {'ostree-rootfs', 'ostree-sd-image'} or any(name in plan['target'] for name in forbidden):
        raise ValueError('Pinned builds must not compile component source targets')
    print('Verified pinned components for ' + MACHINE)


def add_tag(tag, source, digest, strict=True):
    existing = published_digest(tag)
    if existing == digest:
        return
    if existing is not None:
        if strict:
            raise ValueError('Refusing to overwrite an existing immutable component tag: ' + tag)
        return
    run(['docker', 'buildx', 'imagetools', 'create', '--prefer-index=false', '--tag', tag, source])


def publish(repository, channel='stable', create_namespaces=False):
    repository = repository.lower()
    revision = run(['git', 'rev-parse', 'HEAD'], capture=True).strip()
    dirty=run(['git','status','--porcelain','--untracked-files=normal'],capture=True)
    if any(line[3:] not in (LOCK,OVERLAY) for line in dirty.splitlines() if line.strip()):
        raise ValueError('Commit component source and release metadata before publishing so OCI provenance identifies the exact source revision')
    versions = release_versions()
    inputs = fingerprint()
    source_inputs = {c: fingerprint(c) for c in PUBLISHED_COMPONENTS}
    # A common immutable tag identifies the reviewed hardware combination. Rootfs
    # Base has its own independent inputs and never forces hardware recompilation.
    cohort = hashlib.sha256(json.dumps({'inputs': inputs, 'versions': {c: versions[c] for c in COMPONENTS}}, sort_keys=True).encode()).hexdigest()
    output = ROOT / 'out' / 'machine-assets';output.mkdir(parents=True, exist_ok=True)
    components = {};reused = [];built = [];repacked = []
    for component in PUBLISHED_COMPONENTS:
        info = versions[component]
        descriptor = release_descriptor(repository, component, info, source_inputs[component], channel)
        image, tag = descriptor['image'], descriptor['tag']
        digest = published_digest(tag, allow_missing_namespace=create_namespaces)
        legacy_image = f'ghcr.io/{repository}/{MACHINE}-{component}'
        legacy_digest = None
        if digest is None and component in COMPONENTS:
            legacy_digest = published_digest(legacy_image + ':input-' + source_inputs[component])
            if legacy_digest is None:
                legacy_digest = published_digest(legacy_image + ':' + inputs)
        if digest is None and legacy_digest is None:
            # Promote exact inputs between stable/dev families without compiling.
            # An absent optional peer namespace is not an error for the target.
            peer = release_descriptor(repository, component, info, source_inputs[component], 'dev' if channel == 'stable' else 'stable')
            legacy_digest = published_digest(peer['tag'], allow_missing_namespace=True)
            if legacy_digest:
                legacy_image = peer['image']
        labels = {'org.opencontainers.image.source': 'https://github.com/' + repository,
                  'org.opencontainers.image.revision': revision, 'org.opencontainers.image.version': info['version'],
                  'io.nulix.machine': MACHINE, 'io.nulix.component': component,
                  'io.nulix.release.line': info['line'], 'io.nulix.channel': channel,
                  'io.nulix.inputs.sha256': source_inputs[component]}
        if component == 'rootfs-base':
            labels['io.nulix.alpine.version'] = info['version']
        if info.get('sourceRevision'):
            labels['io.nulix.upstream.revision'] = info['sourceRevision']
        metadata = output / (component + '.json')
        if digest:
            reused.append(component)
        elif legacy_digest:
            # Reuse the exact old layers. Only the OCI config/labels are repackaged;
            # no kernel/firmware/bootloader compiler or source target is executed.
            with tempfile.TemporaryDirectory(prefix='nulix-component-') as directory:
                dockerfile = Path(directory) / 'Dockerfile'
                dockerfile.write_text('FROM ' + legacy_image + '@' + legacy_digest + '\n' +
                                      '\n'.join('LABEL ' + key + '=' + json.dumps(value) for key, value in labels.items()) + '\n')
                run(['docker', 'buildx', 'build', '--platform', 'linux/arm64', '--provenance=false', '--tag', tag,
                     '--push', '--metadata-file', str(metadata), directory])
            digest = json.loads(metadata.read_text())['containerimage.digest']
            repacked.append(component)
        else:
            args = bake() + [component, '--set', component + '.output=type=image,push=true', '--set', component + '.tags=' + tag]
            for key, value in labels.items():
                args += ['--set', component + '.labels.' + key + '=' + value]
            args += ['--set', component + '.cache-from=type=registry,ref=' + image + ':buildcache',
                     '--set', component + '.cache-to=type=registry,ref=' + image + ':buildcache,mode=max',
                     '--provenance=false', '--metadata-file', str(metadata)]
            run(args)
            digest = json.loads(metadata.read_text())[component]['containerimage.digest']
            built.append(component)
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise ValueError('BuildKit returned an invalid component digest')
        components[component] = image + '@' + digest
        add_tag(descriptor['versionTag'], components[component], digest)
        if component in COMPONENTS:
            add_tag(image + ':' + cohort, components[component], digest)
            # Compatibility aliases are additive. Never replace existing legacy pins.
            if channel == 'stable':
                old_source = f'ghcr.io/{repository}/{MACHINE}-{component}'
                old_digest = published_digest(old_source + ':input-' + source_inputs[component])
                old_ref = old_source + '@' + old_digest if old_digest else components[component]
                add_tag(old_source + ':input-' + source_inputs[component], old_ref, old_digest or digest, strict=False)
                add_tag(old_source + ':' + inputs, old_ref, old_digest or digest, strict=False)
    previous = json.loads((ROOT / LOCK).read_text()) if (ROOT / LOCK).is_file() else {}
    if previous.get('inputsSha256') == inputs and previous.get('components') == components:
        revision = previous.get('sourceCommit', revision)
    lock = {'schemaVersion': 2, 'machine': MACHINE, 'sourceCommit': revision, 'inputsSha256': inputs,
            'componentInputs': source_inputs, 'componentVersions': versions, 'components': components}
    backups={name:(ROOT/name).read_bytes() if (ROOT/name).exists() else None for name in (LOCK,OVERLAY)}
    try:
        (ROOT / LOCK).write_text(json.dumps(lock, indent=2) + '\n')
        (ROOT / OVERLAY).write_text(overlay(lock))
        check()
    except Exception:
        for name,contents in backups.items():
            if contents is None:(ROOT/name).unlink(missing_ok=True)
            else:(ROOT/name).write_bytes(contents)
        raise
    summary = '\n'.join(label + ': ' + (', '.join(items) or 'none') for label, items in [('Reused', reused), ('Repackaged (no compilation)', repacked), ('Built', built)])
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('\n### Versioned component publication\n\n' + summary.replace('\n', '  \n') + '\n\n' + '\n'.join('- `' + ref + '`' for ref in components.values()) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('publish', 'check', 'versions'))
    parser.add_argument('--repository', default='nulix/nulix-alpine')
    parser.add_argument('--channel', choices=('stable', 'dev'), default='stable')
    parser.add_argument('--create-namespaces', action='store_true', help='Explicit first-publication bootstrap for GHCR namespaces that return 403 before creation')
    args = parser.parse_args()
    try:
        if args.command == 'publish': publish(args.repository, args.channel, args.create_namespaces)
        elif args.command == 'check': check()
        else: print(json.dumps(release_versions(), indent=2))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Component publication: ' + str(error) + '\n')
