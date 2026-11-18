import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('builder_image',Path(__file__).resolve().parents[1]/'scripts/builder-image.py')
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)

class BuilderImageTests(unittest.TestCase):
    def fixture(self,root):
        (root/'builder').mkdir();(root/'builder/Dockerfile').write_text('FROM ubuntu:24.04\nRUN echo tools\n')
        return {'group':{'default':{'targets':['ostree-rootfs','ostree-sd-image']}},'target':{'nulix-builder':{'context':'./builder'}}}

    def test_rootfs_edits_do_not_invalidate_tooling_but_builder_edits_do(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root)
            with patch.object(tool,'command',return_value=json.dumps(plan)):
                before=tool.fingerprint(root)
                (root/'recipes').mkdir();(root/'recipes/agent.py').write_text('agent v2')
                self.assertEqual(before,tool.fingerprint(root))
                (root/'builder/Dockerfile').write_text('FROM ubuntu:24.04\nRUN echo updated')
                self.assertNotEqual(before,tool.fingerprint(root))

    def test_exact_published_tooling_prunes_source_builder_and_preserves_selected_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root)
            with patch.object(tool,'command',return_value=json.dumps(plan)),patch.object(tool,'inspect',return_value='sha256:'+'a'*64):
                result=tool.resolve(root,overlays=['.nulix/selected-components.hcl'],require=True)
            overlay=(root/result['overlay']).read_text()
            self.assertIn('docker-image://ghcr.io/nulix/nulix-alpine/build-tools/arm64@sha256:',overlay)
            self.assertIn('targets = ["ostree-rootfs", "ostree-sd-image"]',overlay)
            self.assertNotIn('target:nulix-builder',overlay)
            self.assertEqual(overlay.count('contexts = { builder'),5)

    def test_missing_publication_cannot_trigger_a_hosted_source_tooling_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root)
            with patch.object(tool,'command',return_value=json.dumps(plan)),patch.object(tool,'inspect',return_value=None):
                with self.assertRaisesRegex(ValueError,'Publish the matching'):tool.resolve(root,require=True)
                self.assertEqual(tool.resolve(root)['mode'],'source')

    def test_stale_or_cross_repository_lock_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root)
            with patch.object(tool,'command',return_value=json.dumps(plan)):
                inputs=tool.fingerprint(root)
                for ref,hashvalue in [('ghcr.io/attacker/tools@sha256:'+'a'*64,inputs),('ghcr.io/nulix/nulix-alpine/build-tools/arm64@sha256:'+'a'*64,'b'*64)]:
                    (root/'builder-image.lock.json').write_text(json.dumps({'schemaVersion':1,'inputsSha256':hashvalue,'ref':ref}))
                    with self.assertRaisesRegex(ValueError,'does not match'):tool.resolve(root)
                (root/'builder-image.lock.json').unlink();(root/'.nulix').symlink_to(root,target_is_directory=True)
                with patch.object(tool,'inspect',return_value='sha256:'+'a'*64),self.assertRaisesRegex(ValueError,'symlink'):tool.resolve(root)

    def test_an_existing_tooling_image_is_never_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(tool,'fingerprint',return_value='a'*64),patch.object(tool,'inspect',return_value='sha256:'+'b'*64):
            calls=[]
            def run(args,root,capture=True):
                calls.append(args)
                if args[:2]==['git','rev-parse']:return 'c'*40
                return ''
            with patch.object(tool,'command',side_effect=run):tool.publish(directory)
            self.assertTrue(all(args[0]=='git' for args in calls))

if __name__=='__main__':unittest.main()
