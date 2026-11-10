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
            (root / 'recipes/ostree-rootfs').mkdir()
            (root / 'recipes/ostree-rootfs/Dockerfile').write_text('new agent')
            self.assertEqual(before, assets.fingerprint())
            kernel.write_text('kernel v2')
            self.assertNotEqual(before, assets.fingerprint())

    def test_failed_publication_preserves_previous_pins(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64):
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
        with tempfile.TemporaryDirectory() as directory, patch.object(assets, 'ROOT', Path(directory)), patch.object(assets, 'fingerprint', return_value='a' * 64), patch.object(assets, 'check') as checked:
            def publish_command(args, capture=False):
                if args[0] == 'git': return 'c' * 40
                metadata = Path(args[args.index('--metadata-file') + 1])
                metadata.write_text(json.dumps({metadata.stem: {'containerimage.digest': 'sha256:' + 'd' * 64}}))
                self.assertIn(metadata.stem + '.output=type=image,push=true', args)
            with patch.object(assets, 'run', side_effect=publish_command): assets.publish('Nulix/Nulix-Alpine')
            lock = json.loads((assets.ROOT / assets.LOCK).read_text())
            self.assertEqual(len(lock['components']), 4)
            self.assertTrue(all(ref.endswith('@sha256:' + 'd' * 64) for ref in lock['components'].values()))
            checked.assert_called_once()


if __name__ == '__main__':
    unittest.main()
