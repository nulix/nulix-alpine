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

    def test_registry_error_does_not_trigger_compilation(self):
        for error in ('unauthorized', 'unexpected status 502'):
            result = subprocess.CompletedProcess([], 1, '', error)
            with self.subTest(error=error), patch.object(assets.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                assets.published_digest('ghcr.io/nulix/kernel:input-hash')



class VersionedPublicationTests(unittest.TestCase):
    def versions(self):
        return {'rootfs-base': {'version':'3.22.2','line':'3.22'},
                'linux-kernel': {'version':'6.12.62','line':'6.12','sourceRevision':'1'*40},
                'u-boot': {'version':'2025.10','line':'2025.10','sourceRevision':'2'*40},
                'firmware': {'version':'2025.12.17-abcdef123456','line':'2025.12','sourceRevision':'3'*40},
                'ostree-initramfs': {'version':'1.0.0','line':'1.0'}}

    def fake_registry(self, missing=()):
        registry={f'ghcr.io/nulix/nulix-alpine/raspberry-pi-3-{c}:input-'+('a'*64):'sha256:'+str(i+1)*64
                  for i,c in enumerate(assets.COMPONENTS) if c not in missing}
        builds=[];repackages=[];writes=[]
        def lookup(image, **options): return registry.get(image)
        def command(args,capture=False):
            if args[0]=='git': return '' if args[1]=='status' else 'b'*40
            if 'imagetools' in args:
                tag=args[args.index('--tag')+1];registry[tag]=args[-1].split('@')[1];writes.append(tag);return ''
            metadata=Path(args[args.index('--metadata-file')+1]);component=metadata.stem
            tag=args[args.index('--tag')+1] if '--tag' in args else next(a.split('.tags=',1)[1] for a in args if '.tags=' in a)
            digest='sha256:'+str(assets.PUBLISHED_COMPONENTS.index(component)+5)*64
            if 'bake' in args:
                builds.append(component);metadata.write_text(json.dumps({component:{'containerimage.digest':digest}}))
            else:
                dockerfile=(Path(args[-1])/'Dockerfile').read_text();self.assertNotIn('RUN ',dockerfile);self.assertIn('FROM ghcr.io/',dockerfile)
                self.assertIn('io.nulix.machine',dockerfile);self.assertIn('org.opencontainers.image.version',dockerfile)
                repackages.append(component);metadata.write_text(json.dumps({'containerimage.digest':digest}))
            registry[tag]=digest
            return ''
        return registry,builds,repackages,writes,lookup,command

    def test_machine_paths_full_versions_and_dev_channels(self):
        info=self.versions()['linux-kernel']
        stable=assets.release_descriptor('nulix/nulix-alpine','linux-kernel',info,'a'*64)
        dev=assets.release_descriptor('nulix/nulix-alpine','linux-kernel',info,'a'*64,'dev')
        self.assertEqual(stable['image'],'ghcr.io/nulix/nulix-alpine/raspberry-pi-3/linux-kernel/6.12')
        self.assertTrue(dev['image'].endswith('/linux-kernel/6.12-dev'))
        self.assertIn(':6.12.62-',stable['versionTag']);self.assertNotEqual(stable['tag'],dev['tag'])

    def test_first_migration_repackages_hardware_layers_and_only_builds_rootfs_base(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'release_versions',return_value=self.versions()),patch.object(assets,'fingerprint',return_value='a'*64),patch.object(assets,'check'):
            registry,built,repacked,writes,lookup,command=self.fake_registry()
            with patch.object(assets,'published_digest',side_effect=lookup),patch.object(assets,'run',side_effect=command):assets.publish('nulix/nulix-alpine')
            self.assertEqual(built,['rootfs-base']);self.assertEqual(set(repacked),set(assets.COMPONENTS))
            lock=json.loads((assets.ROOT/assets.LOCK).read_text());self.assertEqual(lock['schemaVersion'],2);self.assertEqual(len(lock['components']),5)
            self.assertIn('/raspberry-pi-3/rootfs-base/3.22@',lock['components']['rootfs-base'])
            self.assertEqual(assets.overlay(lock).count('docker-image://'),6)
            self.assertTrue(all('/raspberry-pi-3/' in ref for ref in lock['components'].values()))

    def test_unchanged_publications_do_not_rebuild_repackage_retag_or_change_source_pin(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'release_versions',return_value=self.versions()),patch.object(assets,'fingerprint',return_value='a'*64),patch.object(assets,'check'):
            registry,built,repacked,writes,lookup,command=self.fake_registry()
            with patch.object(assets,'published_digest',side_effect=lookup),patch.object(assets,'run',side_effect=command):
                assets.publish('nulix/nulix-alpine');before=(assets.ROOT/assets.LOCK).read_text()
                built.clear();repacked.clear();writes.clear();assets.publish('nulix/nulix-alpine')
            self.assertEqual(built,[]);self.assertEqual(repacked,[]);self.assertEqual(writes,[])
            self.assertEqual((assets.ROOT/assets.LOCK).read_text(),before)

    def test_missing_kernel_source_is_the_only_hardware_compiled(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'release_versions',return_value=self.versions()),patch.object(assets,'fingerprint',return_value='a'*64),patch.object(assets,'check'):
            registry,built,repacked,writes,lookup,command=self.fake_registry(missing=('linux-kernel',))
            with patch.object(assets,'published_digest',side_effect=lookup),patch.object(assets,'run',side_effect=command):assets.publish('nulix/nulix-alpine')
            self.assertEqual(built,['rootfs-base','linux-kernel']);self.assertEqual(set(repacked),{'u-boot','firmware','ostree-initramfs'})

    def test_failure_does_not_replace_existing_locks_and_immutable_tags_cannot_move(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'release_versions',return_value=self.versions()),patch.object(assets,'fingerprint',return_value='a'*64),patch.object(assets,'published_digest',return_value=None):
            (assets.ROOT/assets.LOCK).write_text('previous pins');(assets.ROOT/assets.OVERLAY).write_text('previous overlay')
            def fail(args,capture=False):
                if args[0]=='git':return '' if args[1]=='status' else 'a'*40
                raise subprocess.CalledProcessError(1,args)
            with patch.object(assets,'run',side_effect=fail),self.assertRaises(subprocess.CalledProcessError):assets.publish('nulix/nulix-alpine')
            self.assertEqual((assets.ROOT/assets.LOCK).read_text(),'previous pins')
            self.assertEqual((assets.ROOT/assets.OVERLAY).read_text(),'previous overlay')
        with patch.object(assets,'published_digest',return_value='sha256:'+'a'*64),self.assertRaises(ValueError):
            assets.add_tag('immutable-tag','source@sha256:'+'b'*64,'sha256:'+'b'*64)


    def test_versions_validate_the_machine_architecture_and_pinned_sources(self):
        versions=self.versions()
        plan={'rootfs-base':{'args':{'ALPINE_VERSION':'3.22.2'}},'linux-kernel':{'args':{'MACHINE_ARCH':'arm64','KERNEL_REV':'1'*40}},'u-boot':{'args':{'UBOOT_REV':'2'*40}},'firmware':{'args':{'MACHINE_NAME':assets.MACHINE,'FIRMWARE_REV':'3'*40}},'ostree-initramfs':{}}
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'run',return_value=json.dumps({'target':plan})):
            folder=assets.ROOT/'machines';folder.mkdir();file=folder/(assets.MACHINE+'.components.json')
            file.write_text(json.dumps({'schemaVersion':1,'machine':assets.MACHINE,'components':versions}))
            self.assertEqual(assets.release_versions(),versions)
            versions['linux-kernel']['sourceRevision']='9'*40;file.write_text(json.dumps({'schemaVersion':1,'machine':assets.MACHINE,'components':versions}))
            with self.assertRaisesRegex(ValueError,'pinned source'):assets.release_versions()

    def test_dirty_source_never_reaches_registry_publication(self):
        with patch.object(assets,'run',side_effect=['b'*40,' M recipes/linux-kernel/Dockerfile\n']),patch.object(assets,'published_digest') as registry:
            with self.assertRaisesRegex(ValueError,'Commit component source'):assets.publish('nulix/nulix-alpine')
            registry.assert_not_called()

    def test_promotion_from_dev_reuses_all_five_images_without_source_compilation(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'release_versions',return_value=self.versions()),patch.object(assets,'fingerprint',return_value='a'*64),patch.object(assets,'check'):
            registry,built,repacked,writes,lookup,command=self.fake_registry(missing=assets.COMPONENTS)
            for name,info in self.versions().items():registry[assets.release_descriptor('nulix/nulix-alpine',name,info,'a'*64,'dev')['tag']]='sha256:'+'4'*64
            with patch.object(assets,'published_digest',side_effect=lookup),patch.object(assets,'run',side_effect=command):assets.publish('nulix/nulix-alpine')
            self.assertEqual(built,[]);self.assertEqual(set(repacked),set(assets.PUBLISHED_COMPONENTS))

    def test_failed_final_verification_restores_previous_lock_files(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'ROOT',Path(directory)),patch.object(assets,'release_versions',return_value=self.versions()),patch.object(assets,'fingerprint',return_value='a'*64),patch.object(assets,'check',side_effect=ValueError('verification failed')):
            registry,built,repacked,writes,lookup,command=self.fake_registry()
            (assets.ROOT/assets.LOCK).write_text('{}');(assets.ROOT/assets.OVERLAY).write_text('previous overlay')
            with patch.object(assets,'published_digest',side_effect=lookup),patch.object(assets,'run',side_effect=command),self.assertRaisesRegex(ValueError,'verification failed'):assets.publish('nulix/nulix-alpine')
            self.assertEqual((assets.ROOT/assets.LOCK).read_text(),'{}');self.assertEqual((assets.ROOT/assets.OVERLAY).read_text(),'previous overlay')

if __name__ == '__main__':
    unittest.main()
