"""Exercise standalone build gates without running PyInstaller or opening Tk."""
from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import build_macos_standalone as standalone
from app_entry import StartupFailure, owned_fd


class TestStandaloneBuilder(unittest.TestCase):
    def test_pinned_toolchain_and_hashes(self):
        versions = standalone.build_requirements()
        self.assertEqual(versions, {
            'altgraph': '0.17.5', 'macholib': '1.16.4', 'packaging': '26.3',
            'pyinstaller': '6.22.3', 'pyinstaller-hooks-contrib': '2026.10',
            'setuptools': '84.2.1',
        })

    def test_unsupported_build_host_fails_before_build(self):
        with (patch.object(standalone.sys, 'platform', 'linux'),
              patch.object(standalone.subprocess, 'run') as build):
            with self.assertRaisesRegex(ValueError, 'ARM64'):
                standalone.build_standalone('/not-used.app')
        build.assert_not_called()

    def test_builder_rejects_output_inside_checkout(self):
        with patch.object(standalone, 'validate_build_environment'):
            with self.assertRaisesRegex(ValueError, 'outside the source checkout'):
                standalone.build_standalone(standalone.ROOT / 'Unused.app')

    def test_invalid_bundle_symlink_and_prohibited_files_fail_before_tooling(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            bundle = root / 'App.app'
            bundle.mkdir()
            outside = root / 'external'
            outside.touch()
            link = bundle / 'library'
            link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'External bundle symlink'):
                standalone.inspect_bundle(bundle, ())
            link.unlink()
            (bundle / 'checklist_data.json').touch()
            with self.assertRaisesRegex(ValueError, 'Prohibited artifact'):
                standalone.inspect_bundle(bundle, ())

    def test_private_build_path_and_nested_absolute_code_filename_rejected(self):
        with TemporaryDirectory() as temp:
            bundle = Path(temp).resolve()
            (bundle / 'payload').write_bytes(b'before /private/synthetic/build after')
            with self.assertRaisesRegex(ValueError, 'Private build path'):
                standalone.inspect_bundle(bundle, ('/private/synthetic/build',))
        inner = compile('None', '/private/synthetic/build/program.py', 'exec')
        outer = compile('None', 'program.py', 'exec').replace(co_consts=(inner,))
        with self.assertRaisesRegex(ValueError, 'absolute code filename'):
            standalone._check_code(outer)

    def test_relative_rpath_resolves_to_bundled_binary_only(self):
        with TemporaryDirectory() as temp:
            bundle = Path(temp).resolve() / 'App.app'
            executable = bundle / 'Contents/MacOS/Launcher'
            dylib = bundle / 'Contents/Frameworks/test.dylib'
            dylib.parent.mkdir(parents=True)
            executable.parent.mkdir()
            commands = {
                executable: (['@rpath/test.dylib', '/usr/lib/libSystem.B.dylib'],
                             ['@executable_path/../Frameworks']),
                dylib: ([], []),
            }
            with patch.object(standalone, 'load_commands', side_effect=commands.__getitem__):
                standalone.inspect_dependencies(bundle, executable, commands)
                commands[dylib] = (['/Library/Frameworks/private.dylib'], [])
                with self.assertRaisesRegex(ValueError, 'Unexpected bundle load path'):
                    standalone.inspect_dependencies(bundle, executable, commands)
                commands[dylib] = ([], ['@loader_path/../../..'])
                with self.assertRaisesRegex(ValueError, 'External or missing rpath'):
                    standalone.inspect_dependencies(bundle, executable, commands)

    def test_dependency_audit_rejects_actual_data_volume_alias(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            executable = root / 'Launcher'
            external = root / 'foreign'
            external.touch()
            alias = Path('/System/Volumes/Data') / external.relative_to('/')
            if not alias.exists():
                self.skipTest('macOS Data volume alias unavailable')
            self.assertTrue(alias.samefile(external))
            with patch.object(standalone, 'load_commands', return_value=([str(alias)], [])):
                with self.assertRaisesRegex(ValueError, 'Unexpected bundle load path'):
                    standalone.inspect_dependencies(root, executable, {executable})

    def test_exact_output_path_rejects_every_existing_entry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / 'Unusual name Ω.app'
            unrelated = root / 'Unrelated.app'
            output.symlink_to(unrelated)
            with self.assertRaises(FileExistsError):
                standalone._output_path(output)
            self.assertFalse(unrelated.exists())
            unrelated.mkdir()
            with self.assertRaises(FileExistsError):
                standalone._output_path(output)
            output.unlink()
            output.touch()
            with self.assertRaises(FileExistsError):
                standalone._output_path(output)
            output.unlink()
            output.mkdir()
            with self.assertRaises(FileExistsError):
                standalone._output_path(output)
            output.rmdir()
            self.assertEqual(standalone._output_path(output), output)

    def test_rejects_symlinked_output_parent(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            parent = root / 'Alias'
            physical = root / 'Ordinary'
            physical.mkdir()
            parent.symlink_to(physical, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'ordinary output parent'):
                standalone._output_path(parent / 'Test.app')

    def test_parent_replacement_does_not_publish_or_clean_unrelated_entries(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            parent = root / 'Destination'
            parent.mkdir()
            moved = root / 'Moved'
            work = []
            def replace_parent(*args, cwd, **kwargs):
                work.append(cwd)
                parent.rename(moved)
                parent.mkdir()
                (parent / 'unrelated').touch()
            with (patch.object(standalone, 'validate_build_environment'),
                  patch.object(standalone, 'copy_notices'),
                  patch.object(standalone, 'inspect_bundle', return_value=set()),
                  patch.object(standalone.subprocess, 'run', side_effect=replace_parent)):
                with self.assertRaises(StartupFailure):
                    standalone.build_standalone(parent / 'Test.app')
            self.assertEqual({p.name for p in parent.iterdir()}, {'unrelated'})
            self.assertFalse(list(moved.iterdir()))
            self.assertTrue(work)
            self.assertFalse(work[0].exists())

    def test_failed_build_cleans_scratch_and_can_retry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / 'Test Ω.app'
            work = []
            def build(*args, cwd, **kwargs):
                work.append(cwd)
                if len(work) == 1:
                    raise OSError('synthetic build failure')
                bundle = cwd / 'dist' / (standalone.NAME + '.app')
                bundle.mkdir(parents=True)
                (bundle / 'fixture').touch()
            with (patch.object(standalone, 'validate_build_environment'),
                  patch.object(standalone, 'copy_notices'),
                  patch.object(standalone, 'inspect_bundle', return_value=set()),
                  patch.object(standalone.subprocess, 'run', side_effect=build)):
                with self.assertRaisesRegex(OSError, 'synthetic build failure'):
                    standalone.build_standalone(output)
                self.assertFalse(output.exists())
                self.assertFalse(work[0].exists())
                self.assertEqual(standalone.build_standalone(output), output)
            self.assertTrue((output / 'fixture').is_file())
            self.assertFalse(work[1].exists())

    def test_scratch_substitution_preserves_both_directories_and_retry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            moved = root / 'Original scratch'
            with self.assertRaisesRegex(standalone.BuildBoundaryError, str(moved)):
                with standalone._scratch_directory(root) as (work, _):
                    (work / 'owned').touch()
                    work.rename(moved)
                    work.mkdir()
                    (work / 'unrelated').write_text('synthetic sentinel')
                    raise OSError('synthetic failure')
            self.assertTrue((moved / 'owned').is_file())
            self.assertEqual((work / 'unrelated').read_text(), 'synthetic sentinel')
            with standalone._scratch_directory(root) as (retry, check):
                check()
                (retry / 'scratch').touch()
            self.assertFalse(retry.exists())

    def test_scratch_cancellation_cleans_unchanged_owned_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaises(KeyboardInterrupt):
                with standalone._scratch_directory(root) as (work, _):
                    (work / 'nested').mkdir()
                    (work / 'nested/data').touch()
                    raise KeyboardInterrupt
            self.assertFalse(work.exists())

    def test_rename_races_and_cancellation_roll_back_through_held_parent(self):
        for when in ('before', 'after', 'cancel'):
            with self.subTest(when=when), TemporaryDirectory() as temp, ExitStack() as stack:
                root = Path(temp).resolve()
                parent = root / 'Parent'
                parent.mkdir()
                moved = root / 'Moved'
                source = root / 'Staged.app'
                source.mkdir()
                (source / 'owned').touch()
                expected = source.stat()
                descriptor = owned_fd(stack, parent, standalone.DIRECTORY_FLAGS)
                output = parent / 'Output.app'
                def replace_parent():
                    parent.rename(moved)
                    parent.mkdir()
                    output.mkdir()
                    (output / 'competitor').touch()
                def revalidate():
                    if not os.path.samestat(parent.lstat(), os.fstat(descriptor)):
                        raise StartupFailure('runtime')
                rename = standalone._rename_exclusive
                moves = []
                def interrupted(*args):
                    moves.append(args)
                    if len(moves) == 1 and when == 'before':
                        replace_parent()
                    rename(*args)
                    if len(moves) == 1 and when == 'after':
                        replace_parent()
                    if len(moves) == 1 and when == 'cancel':
                        raise KeyboardInterrupt
                with patch.object(standalone, '_rename_exclusive', side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt if when == 'cancel' else StartupFailure):
                        standalone._publish_exclusive(source, output, descriptor, revalidate, expected)
                self.assertTrue((source / 'owned').is_file())
                self.assertEqual(len(moves), 2)
                self.assertEqual(moves[1][0], descriptor)
                if when == 'cancel':
                    self.assertFalse(output.exists())
                    standalone._publish_exclusive(source, output, descriptor, revalidate, expected)
                    self.assertTrue((output / 'owned').is_file())
                else:
                    self.assertTrue((output / 'competitor').is_file())
                    self.assertFalse(list(moved.iterdir()))
                    (output / 'competitor').unlink()
                    output.rmdir()
                    retry = owned_fd(stack, parent, standalone.DIRECTORY_FLAGS)
                    standalone._publish_exclusive(source, output, retry, lambda: None, expected)
                    self.assertTrue((output / 'owned').is_file())

    def test_exclusive_rename_preserves_competitor(self):
        with TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp).resolve()
            source, output = root / 'Stage.app', root / 'Output.app'
            source.mkdir()
            output.mkdir()
            (output / 'competitor').touch()
            descriptor = owned_fd(stack, root, standalone.DIRECTORY_FLAGS)
            with self.assertRaises(FileExistsError):
                standalone._publish_exclusive(source, output, descriptor, lambda: None, source.stat())
            self.assertTrue(source.is_dir())
            self.assertTrue((output / 'competitor').is_file())

    def test_unprovable_rollback_retains_both_original_and_competitor(self):
        with TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp).resolve()
            out = root / 'Output.app'
            descriptor = owned_fd(stack, root, standalone.DIRECTORY_FLAGS)
            with self.assertRaisesRegex(standalone.RetainBuildArtifacts, 'Cannot safely roll back'):
                with standalone._scratch_directory(root) as (work, _):
                    source = work / 'dist' / 'Stage.app'
                    source.mkdir(parents=True)
                    (source / 'original').touch()
                    rename = standalone._rename_exclusive
                    def interrupt(*args):
                        rename(*args)
                        source.mkdir()
                        (source / 'competitor').touch()
                        raise KeyboardInterrupt
                    with patch.object(standalone, '_rename_exclusive', side_effect=interrupt):
                        standalone._publish_exclusive(source, out, descriptor, lambda: None, source.stat())
            self.assertTrue((out / 'original').is_file())
            self.assertTrue((source / 'competitor').is_file())

    def test_failure_preserves_substituted_staging_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            retained = []
            def synthetic_build(*args, cwd, **kwargs):
                stage = cwd / 'dist' / (standalone.NAME + '.app')
                stage.mkdir(parents=True)
                (stage / 'original').touch()
            def substitute(bundle, _):
                bundle.parent.rename(bundle.parent.with_name('Old dist'))
                bundle.parent.mkdir()
                (bundle.parent / 'unrelated').touch()
                retained.append(bundle.parent.parent)
                raise OSError('synthetic inspection failure')
            scratch = standalone._scratch_directory
            with (patch.object(standalone, 'validate_build_environment'),
                  patch.object(standalone, 'copy_notices'),
                  patch.object(standalone.subprocess, 'run', side_effect=synthetic_build),
                  patch.object(standalone, 'inspect_bundle', side_effect=substitute),
                  patch.object(standalone, '_scratch_directory', side_effect=lambda: scratch(root))):
                with self.assertRaisesRegex(standalone.RetainBuildArtifacts, 'Staging identity changed'):
                    standalone.build_standalone(root / 'Out.app')
            self.assertTrue((retained[0] / 'dist/unrelated').is_file())
            self.assertTrue((retained[0] / 'Old dist' / (standalone.NAME + '.app') / 'original').is_file())
