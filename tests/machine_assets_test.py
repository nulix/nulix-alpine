import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('assets', Path(__file__).resolve().parents[1] / 'scripts/machine-assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class MachineAssetsTests(unittest.TestCase):
    def lock(self):
        return {'schemaVersion': 1, 'machine': assets.MACHINE, 'inputsSha256': 'a' * 64,
                'components': {name: 'ghcr.io/nulix/nulix-alpine/' + name + '@sha256:' + 'b' * 64
                               for name in assets.COMPONENTS}}

    def test_overlay_selects_only_image_and_rootfs_and_pins_all_contexts(self):
        overlay = assets.overlay(self.lock())
        self.assertIn('targets = ["ostree-rootfs", "ostree-sd-image"]', overlay)
        self.assertNotIn('target:linux-kernel', overlay)
        self.assertEqual(overlay.count('docker-image://'), 5)
        self.assertIn('ostree-initramfs = "docker-image://ghcr.io/', overlay)

    def test_rejects_mutable_tags_missing_components_and_wrong_machine(self):
        for change in ('tag', 'missing', 'machine', 'registry'):
            lock = copy.deepcopy(self.lock())
            if change == 'tag': lock['components']['linux-kernel'] = 'ghcr.io/nulix/kernel:latest'
            if change == 'missing': del lock['components']['u-boot']
            if change == 'machine': lock['machine'] = 'other-machine'
            if change == 'registry': lock['components']['firmware'] = 'example.com/firmware@sha256:' + 'b' * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                assets.overlay(lock)

    def test_stale_inputs_and_tampered_overlay_fail_closed(self):
        plan = {'group': {'default': {'targets': ['ostree-rootfs', 'ostree-sd-image']}}, 'target': {'ostree-rootfs': {}, 'ostree-sd-image': {}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'run', return_value=json.dumps(plan)):
            lock = self.lock()
            (assets.ROOT / assets.LOCK).write_text(json.dumps(lock))
            (assets.ROOT / assets.OVERLAY).write_text(assets.overlay(lock))
            assets.check()
            (assets.ROOT / assets.OVERLAY).write_text('group "default" { targets = ["linux-kernel"] }')
            with self.assertRaises(ValueError): assets.check()
            (assets.ROOT / assets.OVERLAY).write_text(assets.overlay(lock))
            lock['inputsSha256'] = 'c' * 64
            (assets.ROOT / assets.LOCK).write_text(json.dumps(lock))
            with self.assertRaises(ValueError): assets.check()

    def test_component_recipe_changes_invalidate_but_rootfs_changes_do_not(self):
        plan = {'target': {name: {'context': name} for name in ('nulix-builder', *assets.COMPONENTS)}}
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'run', return_value=json.dumps(plan)):
            root = assets.ROOT
            (root / 'recipes/linux-kernel').mkdir(parents=True)
            kernel = root / 'recipes/linux-kernel/Dockerfile'
            kernel.write_text('kernel v1')
            before = assets.fingerprint()
            component_before = {name: assets.fingerprint(name) for name in assets.COMPONENTS}
            (root / 'recipes/ostree-rootfs').mkdir()
            (root / 'recipes/ostree-rootfs/Dockerfile').write_text('new agent')
            self.assertEqual(before, assets.fingerprint())
            kernel.write_text('kernel v2')
            self.assertNotEqual(before, assets.fingerprint())
            self.assertNotEqual(component_before['linux-kernel'], assets.fingerprint('linux-kernel'))
            for name in ('u-boot', 'firmware', 'ostree-initramfs'):
                self.assertEqual(component_before[name], assets.fingerprint(name))
            (root / 'builder').mkdir();(root / 'builder/Dockerfile').write_text('new compiler')
            self.assertNotEqual(component_before['u-boot'], assets.fingerprint('u-boot'))
            self.assertEqual(component_before['ostree-initramfs'], assets.fingerprint('ostree-initramfs'))

    def test_failed_publication_preserves_previous_pins(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'published_digest', return_value=None):
            (assets.ROOT / assets.LOCK).write_text('previous lock')
            (assets.ROOT / assets.OVERLAY).write_text('previous overlay')
            def publish_command(args, capture=False):
                if args[0] == 'git': return 'c' * 40
                raise subprocess.CalledProcessError(1, args)
            with patch.object(assets, 'run', side_effect=publish_command), self.assertRaises(subprocess.CalledProcessError):
                assets.publish('nulix/nulix-alpine')
            self.assertEqual((assets.ROOT / assets.LOCK).read_text(), 'previous lock')
            self.assertEqual((assets.ROOT / assets.OVERLAY).read_text(), 'previous overlay')

    def test_publication_uses_reported_digests_not_tags(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'published_digest', return_value=None), patch.object(assets, 'check') as checked:
            def publish_command(args, capture=False):
                if args[0] == 'git': return 'c' * 40
                if 'imagetools' in args: return ''
                metadata = Path(args[args.index('--metadata-file') + 1])
                metadata.write_text(json.dumps({metadata.stem: {'containerimage.digest': 'sha256:' + 'd' * 64}}))
                self.assertIn(metadata.stem + '.output=type=image,push=true', args)
            with patch.object(assets, 'run', side_effect=publish_command): assets.publish('Nulix/Nulix-Alpine')
            lock = json.loads((assets.ROOT / assets.LOCK).read_text())
            self.assertEqual(len(lock['components']), 4)
            self.assertTrue(all(ref.endswith('@sha256:' + 'd' * 64) for ref in lock['components'].values()))
            checked.assert_called_once()

    def test_unchanged_publication_never_builds_retags_or_changes_pin_provenance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'published_digest', return_value='sha256:' + 'b' * 64), patch.object(assets, 'check'):
            lock = {'schemaVersion': 1, 'machine': assets.MACHINE, 'sourceCommit': 'e' * 40,
                    'inputsSha256': 'a' * 64,
                    'componentInputs': {name: 'a' * 64 for name in assets.COMPONENTS},
                    'components': {name: 'ghcr.io/nulix/nulix-alpine/raspberry-pi-3-' + name + '@sha256:' + 'b' * 64 for name in assets.COMPONENTS}}
            (assets.ROOT / assets.LOCK).write_text(json.dumps(lock, indent=2) + '\n')
            before = (assets.ROOT / assets.LOCK).read_text()
            with patch.object(assets, 'run', return_value='c' * 40) as command:
                assets.publish('nulix/nulix-alpine')
                command.assert_called_once_with(['git', 'rev-parse', 'HEAD'], capture=True)
            self.assertEqual((assets.ROOT / assets.LOCK).read_text(), before)

    def test_existing_aggregate_images_are_adopted_without_recompilation(self):
        def lookup(image):
            return None if ':input-' in image else 'sha256:' + 'b' * 64
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'published_digest', side_effect=lookup), patch.object(assets, 'check'), patch.object(assets, 'run', return_value='c' * 40) as command:
            assets.publish('nulix/nulix-alpine')
            docker_commands = [call.args[0] for call in command.call_args_list if call.args[0][0] == 'docker']
            self.assertEqual(len(docker_commands), 4)
            self.assertTrue(all('imagetools' in args and 'create' in args and '--prefer-index=false' in args for args in docker_commands))

    def test_only_missing_kernel_is_built(self):
        def lookup(image):
            return 'sha256:' + 'b' * 64 if ':input-' in image and '-linux-kernel:' not in image else None
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'published_digest', side_effect=lookup), patch.object(assets, 'check'):
            built = []
            def command(args, capture=False):
                if args[0] == 'git': return 'c' * 40
                if 'imagetools' in args: return ''
                metadata = Path(args[args.index('--metadata-file') + 1])
                built.append(metadata.stem)
                metadata.write_text(json.dumps({metadata.stem: {'containerimage.digest': 'sha256:' + 'd' * 64}}))
            with patch.object(assets, 'run', side_effect=command):assets.publish('nulix/nulix-alpine')
            self.assertEqual(built, ['linux-kernel'])

    def test_registry_error_does_not_trigger_compilation(self):
        for error in ('unauthorized', 'unexpected status 502'):
            result = subprocess.CompletedProcess([], 1, '', error)
            with self.subTest(error=error), patch.object(assets.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                assets.published_digest('ghcr.io/nulix/kernel:input-hash')


if __name__ == '__main__':
    unittest.main()
