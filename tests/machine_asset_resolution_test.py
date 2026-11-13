import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('resolver',Path(__file__).resolve().parents[1]/'scripts/resolve-machine-assets.py')
resolver=importlib.util.module_from_spec(spec);spec.loader.exec_module(resolver)


class AssetResolutionTests(unittest.TestCase):
    def fixture(self, root):
        tool=root/'scripts/machine-assets.py';tool.parent.mkdir()
        tool.write_text('MACHINE="raspberry-pi-3"\ndef fingerprint(): return "'+'a'*64+'"\n')
        (root/'machines').mkdir();(root/'machines/raspberry-pi-3.hcl').write_text('')

    def test_exact_fingerprint_is_resolved_to_immutable_digest_for_each_component(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(resolver,'fingerprint',return_value='a'*64):
            root=Path(temp);self.fixture(root);seen=[]
            def probe(image):
                seen.append(image)
                return image.rsplit(':',1)[0]+'@sha256:'+'b'*64,None
            report=resolver.prepare(root,probe=probe)
            self.assertEqual(len(seen),4)
            self.assertTrue(all(ref.endswith(':'+'a'*64) for ref in seen))
            self.assertEqual(report['compiled'],[])
            self.assertEqual(len(report['reused']),4)
            overlay=(root/report['overlay']).read_text()
            self.assertNotIn(':latest',overlay)
            self.assertEqual(overlay.count('@sha256:'),5)
            self.assertEqual(json.loads((root/'.nulix/resolved-machine-assets.json').read_text()),report)

    def test_missing_components_keep_source_contexts_while_available_ones_are_reused(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(resolver,'fingerprint',return_value='a'*64):
            root=Path(temp);self.fixture(root)
            def probe(image):
                return (image.rsplit(':',1)[0]+'@sha256:'+'b'*64,None) if '-linux-kernel:' in image else (None,'not published')
            report=resolver.prepare(root,probe=probe)
            self.assertEqual(report['reused'],['linux-kernel'])
            self.assertEqual(report['compiled'],['u-boot','firmware','ostree-initramfs'])
            overlay=(root/report['overlay']).read_text()
            self.assertNotIn('u-boot =',overlay)
            self.assertIn('linux-kernel = "docker-image://',overlay)

    def test_incomplete_committed_pins_do_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            (root/'machine-assets.lock.json').write_text('{}')
            with self.assertRaises(ValueError):resolver.prepare(root,probe=lambda _:self.fail('must not probe'))

    def test_registry_access_error_is_reported_without_exposing_output(self):
        result=subprocess.CompletedProcess([],1,'','unauthorized secret-placeholder')
        with patch.object(resolver.subprocess,'run',return_value=result):
            self.assertEqual(resolver.inspect('ghcr.io/nulix/test:hash'),(None,'access denied'))
        result=subprocess.CompletedProcess([],0,json.dumps({'digest':'sha256:'+'a'*64}), '')
        with patch.object(resolver.subprocess,'run',return_value=result):
            self.assertEqual(resolver.inspect('ghcr.io/nulix/test:hash'),('ghcr.io/nulix/test@sha256:'+'a'*64,None))

    def test_legacy_foundations_without_fingerprints_keep_source_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            report=resolver.prepare(temp,probe=lambda _:self.fail('must not probe'))
            self.assertIsNone(report['overlay'])
            self.assertEqual(report['compiled'],list(resolver.COMPONENTS))


    def test_new_recipes_resolve_all_five_machine_scoped_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root);(root/'machines/raspberry-pi-3.components.json').write_text('{}')
            names=('rootfs-base',*resolver.COMPONENTS)
            publisher={'PUBLISHED_COMPONENTS':names,'release_versions':lambda:{name:{'line':'6.12'} for name in names},'fingerprint':lambda component=None:'a'*64,'release_descriptor':lambda repo,name,info,source:{'tag':'ghcr.io/'+repo+'/raspberry-pi-3/'+name+'/6.12:input-'+source}}
            with patch.object(resolver.runpy,'run_path',return_value=publisher):
                seen=[]
                def probe(image):seen.append(image);return image.rsplit(':',1)[0]+'@sha256:'+'b'*64,None
                report=resolver.prepare(root,probe=probe)
            self.assertEqual(len(seen),5);self.assertEqual(report['compiled'],[])
            self.assertIn('rootfs-base =',(root/report['overlay']).read_text())

    def test_namespace_migration_reuses_exact_legacy_hardware_before_source_compilation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root);(root/'machines/raspberry-pi-3.components.json').write_text('{}')
            names=('rootfs-base',*resolver.COMPONENTS)
            publisher={'PUBLISHED_COMPONENTS':names,'release_versions':lambda:{name:{} for name in names},'fingerprint':lambda component=None:'a'*64,'release_descriptor':lambda repo,name,info,source:{'tag':'ghcr.io/'+repo+'/raspberry-pi-3/'+name+'/6.12:input-'+source}}
            def probe(image):
                return (image.rsplit(':',1)[0]+'@sha256:'+'b'*64,None) if '/raspberry-pi-3-' in image else (None,'not published')
            with patch.object(resolver.runpy,'run_path',return_value=publisher):report=resolver.prepare(root,probe=probe)
            self.assertEqual(report['compiled'],['rootfs-base'])
            self.assertEqual(len(report['reused']),4)
if __name__=='__main__':unittest.main()
