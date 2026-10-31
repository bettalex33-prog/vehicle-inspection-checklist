"""Session contracts with injected captures/projections only; no runtime IO."""
from copy import deepcopy
from datetime import date, datetime
from dataclasses import FrozenInstanceError, replace as replace_record
import inspect
import sys
import unittest
from unittest.mock import patch

from dashboard_controller import DashboardController, DashboardControllerError

DAY = date(2026, 10, 31)
PID, OTHER = 'stable-1', 'stable-2'


class Capture:
    def __init__(self):
        self.checks = 0
        self.failure = None
        self.read_failure = None
        self.data = dict(stores={
            'profiles': dict(availability='Available', data={'profiles': [
                dict(profile_id=PID, vehicle_name='Same'),
                dict(profile_id=OTHER, vehicle_name='Same')]}),
            'checklist': dict(availability='Available', data={'shared': {'complete': True}}),
            'history': dict(availability='Available', data={'sentinel': 'history'}),
            'maintenance': dict(availability='Available', data={'sentinel': 'schedule'})},
            insights=dict(availability='Available', data=dict(input_error=None,
                export_available=True, sentinel=['precomputed facts'])))

    @property
    def model(self):
        if self.read_failure:
            raise ValueError(self.read_failure)
        return deepcopy(self.data)

    def revalidate(self):
        self.checks += 1
        if self.failure:
            raise ValueError(self.failure)


class Factory:
    def __init__(self):
        self.capture = Capture()
        self.calls = []
        self.hook = None

    def __call__(self, **inputs):
        self.calls.append(deepcopy(inputs))
        if self.hook:
            self.hook()
        return self.capture


def project(facts, checklist, *, scope_profile_id):
    return dict(scope_profile_id=scope_profile_id, facts=facts,
                shared_inspection_progress=checklist)


class TestDashboardController(unittest.TestCase):
    def setUp(self):
        self.factory = Factory()
        self.controller = DashboardController(self.factory, as_of=DAY, projector=project)

    def reject_action(self):
        calls = []
        with self.assertRaises(DashboardControllerError):
            self.controller.perform_action(calls.append)
        self.assertEqual(calls, [])

    def test_initial_empty_state(self):
        state = self.controller.state
        self.assertEqual(state['status'], 'empty')
        self.assertEqual(state['revision'], 0)
        self.assertEqual(state['publication_generation'], 0)
        self.assertIsNone(state['presentation_generation'])
        self.assertIsNone(state['last_successful_generation'])
        self.assertIsNone(state['presentation'])
        self.assertIsNone(state['last_successful_presentation'])
        self.assertFalse(state['stale'])
        self.assertEqual(self.factory.calls, [])
        self.reject_action()

    def test_successful_all_vehicle_refresh(self):
        state = self.controller.refresh()
        self.assertEqual(state['status'], 'ready')
        self.assertIsNone(state['presentation']['scope_profile_id'])
        self.assertTrue(state['action_ready'])
        self.assertEqual(self.factory.capture.checks, 1)

    def test_stable_ids_and_duplicate_names(self):
        for pid in (PID, OTHER):
            self.controller.select_scope(pid)
            self.assertEqual(self.controller.refresh()['presentation']['scope_profile_id'], pid)

    def test_unknown_or_name_selection_never_falls_back(self):
        for pid in ('Same', 'unknown'):
            self.controller.select_scope(pid)
            with self.assertRaisesRegex(DashboardControllerError, 'unavailable'):
                self.controller.refresh()
            self.assertEqual(self.controller.state['selected_profile_id'], pid)
            self.assertIsNone(self.controller.state['presentation'])
            self.reject_action()

    def test_invalid_selection_type(self):
        for value in (1, True, [], ''):
            with self.assertRaises(DashboardControllerError):
                self.controller.select_scope(value)
            self.reject_action()

    def test_scope_change_clears_mileage_and_preserves_shared_checklist(self):
        original = deepcopy(self.factory.capture.data)
        self.controller.select_scope(PID)
        self.controller.set_explicit_mileage(123)
        first = self.controller.refresh()['presentation']
        self.controller.select_scope(OTHER)
        self.assertIsNone(self.controller.state['explicit_current_mileage'])
        second = self.controller.refresh()['presentation']
        self.assertEqual(first['shared_inspection_progress'], second['shared_inspection_progress'])
        self.assertEqual(self.factory.capture.data, original)

    def test_same_scope_preserves_mileage_and_ready_state(self):
        self.controller.select_scope(PID)
        self.controller.set_explicit_mileage(0)
        self.controller.refresh()
        self.controller.select_scope(PID)
        self.assertEqual(self.controller.state['explicit_current_mileage'], 0)
        self.assertTrue(self.controller.state['action_ready'])

    def test_invalid_explicit_mileage(self):
        self.controller.select_scope(PID)
        for value in (-1, True, '123', 'bad', 1.5, [], {}):
            with self.assertRaises(DashboardControllerError):
                self.controller.set_explicit_mileage(value)
            with self.assertRaises(DashboardControllerError):
                self.controller.refresh()
            self.reject_action()
        self.controller.set_explicit_mileage(None)
        self.controller.refresh()

    def test_mileage_requires_one_profile(self):
        with self.assertRaises(DashboardControllerError):
            self.controller.set_explicit_mileage(0)

    def test_domain_input_error_is_not_recalculated(self):
        self.factory.capture.data['insights']['data']['input_error'] = 'Below history'
        with self.assertRaisesRegex(DashboardControllerError, 'Below history'):
            self.controller.refresh()
        self.reject_action()

    def test_exact_injected_inputs(self):
        self.controller.select_scope(OTHER)
        self.controller.set_explicit_mileage(456)
        self.controller.set_cost_bounds('2026-01-01', '2026-10-31')
        self.controller.refresh()
        self.assertEqual(self.factory.calls[-1], dict(as_of=DAY,
            selected_profile_id=OTHER, explicit_current_mileage=456,
            cost_start_date='2026-01-01', cost_end_date='2026-10-31'))

    def test_invalid_evaluation_dates(self):
        for value in (None, '2026-10-31', datetime(2026, 10, 31), True):
            with self.assertRaises(DashboardControllerError):
                DashboardController(self.factory, as_of=value, projector=project)
            with self.assertRaises(DashboardControllerError):
                self.controller.set_evaluation_date(value)
            self.reject_action()
        self.controller.set_evaluation_date(DAY)
        self.controller.refresh()

    def test_valid_and_invalid_cost_bounds(self):
        for bounds in ((None, None), ('2026-01-01', None),
                       (None, '2026-10-31'), ('2026-01-01', '2026-01-01')):
            self.controller.set_cost_bounds(*bounds)
            self.controller.refresh()
        for bounds in (('', None), ('bad', None), (DAY, None),
                       ('2026-02-30', None), ('20261031', None),
                       ('2026-10-31', '2026-01-01')):
            with self.assertRaises(DashboardControllerError):
                self.controller.set_cost_bounds(*bounds)
            self.reject_action()

    def test_failed_refresh_preserves_only_stale_last_success(self):
        success = self.controller.refresh()
        previous = success['presentation']
        self.factory.capture.read_failure = 'read failed'
        with self.assertRaisesRegex(DashboardControllerError, 'read failed'):
            self.controller.refresh()
        state = self.controller.state
        self.assertEqual(state['last_successful_presentation'], previous)
        self.assertIsNone(state['presentation'])
        self.assertTrue(state['stale'])
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['publication_generation'], success['publication_generation'])
        self.assertGreater(state['revision'], success['revision'])
        self.assertIsNone(state['presentation_generation'])
        self.reject_action()

    def test_no_partial_publication(self):
        previous = self.controller.refresh()['presentation']
        def broken(facts, checklist, **kwargs):
            facts['sentinel'].append('partial')
            self.assertIsNone(self.controller.state['presentation'])
            self.assertEqual(self.controller.state['last_successful_presentation'], previous)
            raise ValueError('projection failed')
        self.controller._projector = broken
        with self.assertRaises(DashboardControllerError):
            self.controller.refresh()
        self.assertEqual(self.controller.state['last_successful_presentation'], previous)

    def test_changed_or_replaced_sources_rejected_before_action(self):
        for reason in ('source changed', 'source replaced'):
            self.factory.capture.failure = None
            self.controller.refresh()
            self.factory.capture.failure = reason
            self.reject_action()
            self.assertTrue(self.controller.state['stale'])
            self.assertIn(reason, self.controller.state['error'])

    def test_loading_rejects_actions_and_nested_refresh(self):
        self.controller.refresh()
        def during_capture():
            self.assertTrue(self.controller.state['loading'])
            self.reject_action()
            with self.assertRaises(DashboardControllerError):
                self.controller.refresh()
        self.factory.hook = during_capture
        self.controller.refresh()

    def test_dirty_rejects_action(self):
        self.controller.refresh()
        self.controller.set_evaluation_date(date(2026, 10, 30))
        self.assertEqual(self.controller.state['status'], 'dirty')
        self.reject_action()

    def test_immediate_revalidation_then_consumer(self):
        self.controller.refresh()
        before = self.factory.capture.checks
        def consume(presentation):
            self.assertEqual(self.factory.capture.checks, before + 1)
            presentation['facts']['sentinel'].append('consumer mutation')
            return 42
        self.assertEqual(self.controller.perform_action(consume), 42)
        self.assertEqual(self.controller.state['presentation']['facts']['sentinel'], ['precomputed facts'])

    def test_write_restore_reset_invalidation_idempotent_without_reads(self):
        self.controller.select_scope(PID)
        self.controller.set_explicit_mileage(100)
        self.controller.refresh()
        checks = self.factory.capture.checks
        self.controller.invalidate()
        state = self.controller.state
        self.assertIsNone(state['explicit_current_mileage'])
        self.reject_action()
        self.controller.invalidate()
        repeated = self.controller.state
        self.assertGreater(repeated['revision'], state['revision'])
        self.assertEqual(repeated['publication_generation'], state['publication_generation'])
        repeated['revision'] = state['revision']
        self.assertEqual(repeated, state)
        self.assertEqual(self.factory.capture.checks, checks)
        self.assertEqual(len(self.factory.calls), 1)

    def test_repeated_refresh_and_recovery(self):
        first = self.controller.refresh()
        self.assertEqual(self.controller.refresh()['presentation'], first['presentation'])
        self.controller.invalidate()
        self.assertEqual(self.controller.refresh()['presentation'], first['presentation'])
        self.factory.capture.failure = 'failed'
        with self.assertRaises(DashboardControllerError):
            self.controller.refresh()
        self.factory.capture.failure = None
        self.assertEqual(self.controller.refresh()['presentation'], first['presentation'])

    def test_public_state_alias_protection(self):
        state = self.controller.refresh()
        expected = self.controller.state
        state['presentation']['facts']['sentinel'].clear()
        state['last_successful_presentation']['shared_inspection_progress'].clear()
        self.assertEqual(self.controller.state, expected)
        self.assertEqual(expected['last_successful_presentation']['facts']['sentinel'], ['precomputed facts'])

    def test_missing_invalid_sources_and_capture(self):
        for kind in ('checklist', 'profiles', 'history', 'maintenance'):
            for status in ('Missing', 'Invalid'):
                capture = Capture()
                capture.data['stores'][kind]['availability'] = status
                self.factory.capture = capture
                with self.assertRaises(DashboardControllerError):
                    self.controller.refresh()
                self.reject_action()
        self.factory.capture = None
        with self.assertRaises(DashboardControllerError):
            self.controller.refresh()
        self.reject_action()

    def test_invalid_input_alias_protection(self):
        value = []
        with self.assertRaises(DashboardControllerError):
            self.controller.set_explicit_mileage(value)
        value.append('outside mutation')
        self.assertEqual(self.controller.state['explicit_current_mileage'], [])

    def test_invalidation_during_action_revalidation(self):
        self.controller.refresh()
        self.factory.capture.revalidate = self.controller.invalidate
        self.reject_action()

    def test_invalid_insights(self):
        self.factory.capture.data['insights']['availability'] = 'Unavailable'
        with self.assertRaises(DashboardControllerError):
            self.controller.refresh()
        self.reject_action()

    def test_final_revalidation_prevents_publication(self):
        self.factory.capture.failure = 'changed during projection'
        with self.assertRaises(DashboardControllerError):
            self.controller.refresh()
        self.assertIsNone(self.controller.state['last_successful_presentation'])

    def test_reentrant_invalidation_cancels_refresh(self):
        self.factory.hook = self.controller.invalidate
        with self.assertRaisesRegex(DashboardControllerError, 'Session changed'):
            self.controller.refresh()
        self.reject_action()

    def test_projection_uses_only_precomputed_facts_and_shared_checklist(self):
        original = deepcopy(self.factory.capture.data)
        state = self.controller.refresh()
        self.assertEqual(state['presentation']['facts'], original['insights']['data'])
        self.assertEqual(self.factory.capture.data, original)


    def test_refresh_during_revalidation_rejects_outer_action_even_if_caught(self):
        self.controller.refresh()
        original = self.factory.capture
        replacement = Capture()
        replacement.data['insights']['data']['sentinel'] = ['replacement']
        def revalidate():
            self.factory.capture = replacement
            with self.assertRaisesRegex(DashboardControllerError, 'during an action'):
                self.controller.refresh()
            self.assertIs(self.controller._session.publication.capture, original)
        original.revalidate = revalidate
        self.reject_action()
        self.assertIsNone(self.controller.state['presentation'])
        self.assertFalse(self.controller.state['action_in_progress'])
        self.controller.refresh()
        self.assertEqual(self.controller.perform_action(lambda p: p['facts']['sentinel']),
                         ['replacement'])

    def test_selection_change_during_revalidation_rejects_action(self):
        self.controller.refresh()
        self.factory.capture.revalidate = lambda: self.controller.select_scope(OTHER)
        self.reject_action()
        self.assertEqual(self.controller.state['selected_profile_id'], OTHER)
        self.assertTrue(self.controller.state['stale'])
        self.assertFalse(self.controller.state['action_in_progress'])

    def test_nested_action_during_revalidation_rejects_outer_action(self):
        self.controller.refresh()
        nested_calls = []
        def revalidate():
            with self.assertRaisesRegex(DashboardControllerError, 'Nested actions'):
                self.controller.perform_action(nested_calls.append)
        self.factory.capture.revalidate = revalidate
        self.reject_action()
        self.assertEqual(nested_calls, [])
        self.assertFalse(self.controller.state['action_in_progress'])
        self.factory.capture = Capture()
        self.controller.refresh()
        self.assertEqual(self.controller.perform_action(lambda p: 'recovered'), 'recovered')

    def test_nested_action_from_callback_is_rejected_without_clearing_outer_guard(self):
        self.controller.refresh()
        nested_calls = []
        def callback(presentation):
            with self.assertRaisesRegex(DashboardControllerError, 'Nested actions'):
                self.controller.perform_action(nested_calls.append)
            self.assertTrue(self.controller.state['action_in_progress'])
            self.assertFalse(self.controller.state['action_ready'])
            return 'outer success'
        self.assertEqual(self.controller.perform_action(callback), 'outer success')
        self.assertEqual(nested_calls, [])
        self.assertFalse(self.controller.state['action_in_progress'])
        self.assertTrue(self.controller.state['action_ready'])

    def test_refresh_from_callback_is_rejected_without_mutating_generation(self):
        self.controller.refresh()
        revision = self.controller._session.revision
        capture, presentation = self.controller._session.publication.capture, self.controller._session.publication.presentation
        calls = len(self.factory.calls)
        def callback(owned):
            with self.assertRaisesRegex(DashboardControllerError, 'during an action'):
                self.controller.refresh()
            self.assertTrue(self.controller.state['action_in_progress'])
        self.controller.perform_action(callback)
        self.assertEqual(self.controller._session.revision, revision)
        self.assertIs(self.controller._session.publication.capture, capture)
        self.assertIs(self.controller._session.publication.presentation, presentation)
        self.assertEqual(len(self.factory.calls), calls)
        self.assertTrue(self.controller.state['action_ready'])

    def test_successful_refresh_advances_generation_even_for_identical_output(self):
        revision = self.controller.state['revision']
        generation = self.controller.state['publication_generation']
        first = self.controller.refresh()
        self.assertEqual(self.controller.state['revision'], revision + 1)
        self.assertEqual(first['publication_generation'], generation + 1)
        self.assertEqual(self.controller.refresh()['presentation'], first['presentation'])
        self.assertEqual(self.controller.state['revision'], revision + 2)
        self.assertEqual(self.controller.state['publication_generation'], generation + 2)

    def test_action_pairs_exact_capture_and_presentation_generation(self):
        for marker in ('first', 'second'):
            capture = Capture()
            capture.data['insights']['data']['sentinel'] = [marker]
            self.factory.capture = capture
            self.controller.refresh()
            revision = self.controller.state['revision']
            generation = self.controller.state['publication_generation']
            presentation = self.controller._session.publication.presentation
            events = []
            def revalidate():
                self.assertIs(self.controller._session.publication.capture, capture)
                self.assertIs(self.controller._session.publication.presentation, presentation)
                self.assertEqual(self.controller.state['revision'], revision)
                self.assertEqual(self.controller.state['publication_generation'], generation)
                self.assertEqual(self.controller.state['presentation_generation'], generation)
                events.append('revalidate')
            capture.revalidate = revalidate
            def callback(owned):
                self.assertEqual(events, ['revalidate'])
                self.assertEqual(owned['facts']['sentinel'], [marker])
                self.assertEqual(self.controller.state['publication_generation'], generation)
                self.assertEqual(self.controller.state['presentation_generation'], generation)
                self.assertIsNot(owned, presentation)
                events.append('callback')
            self.controller.perform_action(callback)
            self.assertEqual(events, ['revalidate', 'callback'])

    def test_identity_changes_during_revalidation_reject_even_without_revision_change(self):
        for attribute, replacement in (('capture', Capture()), ('presentation', {})):
            with self.subTest(attribute=attribute):
                self.factory.capture = Capture()
                self.controller.refresh()
                def replace():
                    session = self.controller._session
                    publication = replace_record(session.publication, **{attribute: replacement})
                    self.controller._session = replace_record(session, publication=publication)
                self.factory.capture.revalidate = replace
                self.reject_action()
                self.assertIsNone(self.controller.state['presentation'])
                self.assertFalse(self.controller.state['action_in_progress'])

    def test_each_refresh_copy_failure_preserves_only_previous_success(self):
        # facts, checklist, projected output, retained success, public return state
        for fail_at in range(1, 6):
            for error_type in (MemoryError, RuntimeError, TypeError):
                with self.subTest(fail_at=fail_at, error_type=error_type):
                    self.factory.capture = Capture()
                    previous = self.controller.refresh()['presentation']
                    retained = self.controller._session.publication.last
                    revision = self.controller.state['revision']
                    generation = self.controller.state['publication_generation']
                    self.factory.capture.data['insights']['data']['sentinel'] = ['failed candidate']
                    copies = []
                    def copying(value):
                        copies.append(value)
                        self.assertFalse(self.controller._is_ready())
                        self.assertFalse(self.controller._is_ready())
                        self.assertIs(self.controller._session.publication.last, retained)
                        if len(copies) == fail_at:
                            raise error_type('preparation failed')
                        return deepcopy(value)
                    with patch('dashboard_controller.deepcopy', copying):
                        with self.assertRaisesRegex(DashboardControllerError, 'preparation failed'):
                            self.controller.refresh()
                    self.assertEqual(len(copies), fail_at)
                    state = self.controller.state
                    self.assertEqual(state['status'], 'error')
                    self.assertTrue(state['stale'])
                    self.assertFalse(state['loading'])
                    self.assertIsNone(state['presentation'])
                    self.assertFalse(self.controller._is_ready())
                    self.assertEqual(state['last_successful_presentation'], previous)
                    self.assertGreater(state['revision'], revision)
                    self.assertEqual(state['publication_generation'], generation)
                    self.assertIsNone(state['presentation_generation'])
                    self.assertEqual(state['last_successful_generation'], generation)
                    self.reject_action()
                    self.controller.refresh()
                    self.assertTrue(self.controller.state['action_ready'])

    def test_failed_first_refresh_copy_never_creates_last_success(self):
        with patch('dashboard_controller.deepcopy', side_effect=MemoryError('no memory')):
            with self.assertRaisesRegex(DashboardControllerError, 'no memory'):
                self.controller.refresh()
        state = self.controller.state
        self.assertIsNone(state['presentation'])
        self.assertIsNone(state['last_successful_presentation'])
        self.assertFalse(state['stale'])
        self.assertEqual(state['status'], 'error')

    def test_all_candidates_remain_unpublished_until_final_revalidation(self):
        previous = self.controller.refresh()['presentation']
        retained = self.controller._session.publication.last
        self.factory.capture.data['insights']['data']['sentinel'] = ['candidate']
        def final_check():
            self.assertFalse(self.controller._is_ready())
            self.assertFalse(self.controller._is_ready())
            self.assertIs(self.controller._session.publication.last, retained)
            self.assertEqual(self.controller.state['last_successful_presentation'], previous)
            self.assertTrue(self.controller.state['loading'])
            raise OSError('final check failed')
        self.factory.capture.revalidate = final_check
        with self.assertRaisesRegex(DashboardControllerError, 'final check failed'):
            self.controller.refresh()
        self.assertIsNone(self.controller.state['presentation'])
        self.assertTrue(self.controller.state['stale'])

    def test_every_setter_rejects_identical_invalid_values_repeatedly(self):
        cases = (
            ('set_explicit_mileage', (-1,)),
            ('set_explicit_mileage', (True,)),
            ('set_evaluation_date', ('2026-10-31',)),
            ('set_evaluation_date', (None,)),
            ('set_cost_bounds', ('bad', None)),
            ('set_cost_bounds', ('2026-10-31', '2026-01-01')),
            ('select_scope', (True,)),
            ('select_scope', (1,)),
            ('select_scope', ('',)),
            ('select_scope', ([],)),
        )
        for name, args in cases:
            with self.subTest(setter=name, args=args):
                controller = DashboardController(self.factory, as_of=DAY, projector=project)
                controller.select_scope(PID)
                controller.refresh()
                for _ in range(3):
                    with self.assertRaises(DashboardControllerError):
                        getattr(controller, name)(*args)
                    self.assertEqual(controller.state['status'], 'error')
                    self.assertFalse(controller.state['action_ready'])
                    self.assertIsNone(controller.state['presentation'])

    def test_callback_exception_preserves_ready_state_and_clears_guard(self):
        before = self.controller.refresh()
        error = RuntimeError('consumer failed')
        revision = self.controller._session.revision
        def callback(presentation):
            self.assertTrue(self.controller.state['action_in_progress'])
            raise error
        with self.assertRaises(RuntimeError) as caught:
            self.controller.perform_action(callback)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.controller.state, before)
        self.assertEqual(self.controller._session.revision, revision)
        self.assertEqual(self.controller.perform_action(lambda p: 'next action'), 'next action')

    def test_callback_invalidation_then_exception_preserves_invalidation(self):
        self.controller.select_scope(PID)
        self.controller.set_explicit_mileage(100)
        previous = self.controller.refresh()['presentation']
        error = OSError('write callback failed after invalidation')
        def callback(presentation):
            self.controller.invalidate()
            raise error
        with self.assertRaises(OSError) as caught:
            self.controller.perform_action(callback)
        self.assertIs(caught.exception, error)
        state = self.controller.state
        self.assertFalse(state['action_in_progress'])
        self.assertIsNone(state['explicit_current_mileage'])
        self.assertIsNone(state['presentation'])
        self.assertIsNone(state['error'])
        self.assertEqual(state['status'], 'dirty')
        self.assertTrue(state['stale'])
        self.assertEqual(state['last_successful_presentation'], previous)
        self.reject_action()
        self.controller.refresh()
        self.controller.perform_action(lambda p: None)

    def test_action_copy_failure_clears_guard_and_rejects_consumer(self):
        self.controller.refresh()
        calls = []
        with patch('dashboard_controller.deepcopy', side_effect=MemoryError('action copy failed')):
            with self.assertRaisesRegex(DashboardControllerError, 'action copy failed'):
                self.controller.perform_action(calls.append)
        self.assertEqual(calls, [])
        self.assertFalse(self.controller.state['action_in_progress'])
        self.assertTrue(self.controller.state['stale'])
        self.controller.refresh()
        self.controller.perform_action(calls.append)
        self.assertEqual(len(calls), 1)

    def test_revalidation_exception_types_preserve_useful_error_and_clear_guard(self):
        for error in (ValueError('changed'), OSError('replaced'), RuntimeError(), TypeError('bad capture')):
            with self.subTest(error=type(error)):
                self.factory.capture = Capture()
                self.controller.refresh()
                def fail():
                    raise error
                self.factory.capture.revalidate = fail
                self.reject_action()
                self.assertEqual(self.controller.state['error'], str(error) or type(error).__name__)
                self.assertFalse(self.controller.state['action_in_progress'])
                self.assertTrue(self.controller.state['stale'])

    def test_callback_base_exception_also_clears_guard(self):
        before = self.controller.refresh()
        def callback(presentation):
            raise KeyboardInterrupt('cancel consumer')
        with self.assertRaises(KeyboardInterrupt):
            self.controller.perform_action(callback)
        self.assertEqual(self.controller.state, before)

    def assert_cancelled_output(self, controller, before, cancellation):
        state = controller.state
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['error'], type(cancellation).__name__)
        self.assertFalse(state['loading'])
        self.assertFalse(state['action_in_progress'])
        self.assertFalse(state['action_ready'])
        self.assertTrue(state['dirty'])
        self.assertIsNone(state['presentation'])
        self.assertIsNone(state['presentation_generation'])
        self.assertFalse(controller._is_ready())
        self.assertGreater(state['revision'], before['revision'])
        self.assertEqual(state['publication_generation'], before['publication_generation'])
        self.assertEqual(state['last_successful_generation'], before['last_successful_generation'])
        self.assertEqual(state['last_successful_presentation'], before['last_successful_presentation'])
        self.assertEqual(state['stale'], before['last_successful_presentation'] is not None)
        calls = []
        with self.assertRaises(DashboardControllerError):
            controller.perform_action(calls.append)
        self.assertEqual(calls, [])

    def test_public_revision_and_generation_track_setters_independently(self):
        self.controller.refresh()
        for method, args in (
                ('select_scope', (PID,)),
                ('set_explicit_mileage', (100,)),
                ('set_evaluation_date', (date(2026, 10, 30),)),
                ('set_cost_bounds', ('2026-01-01', '2026-10-30')),
                ('select_scope', (OTHER,)),
                ('select_scope', (None,))):
            with self.subTest(method=method, args=args):
                before = self.controller.state
                getattr(self.controller, method)(*args)
                changed = self.controller.state
                self.assertEqual(changed['revision'], before['revision'] + 1)
                self.assertEqual(changed['publication_generation'], before['publication_generation'])
                self.assertIsNone(changed['presentation_generation'])
                self.assertTrue(changed['stale'])
                self.assertFalse(changed['action_ready'])
                published = self.controller.refresh()
                self.assertEqual(published['publication_generation'], before['publication_generation'] + 1)
                self.assertEqual(published['presentation_generation'], published['publication_generation'])
                self.assertEqual(published['last_successful_generation'], published['publication_generation'])
                self.assertEqual(published, self.controller.state)
                getattr(self.controller, method)(*args)
                self.assertEqual(self.controller.state, published)

    def test_invalidation_preserves_publication_generation_and_last_success_generation(self):
        self.controller.select_scope(PID)
        self.controller.set_explicit_mileage(100)
        before = self.controller.refresh()
        for _ in range(2):
            revision = self.controller.state['revision']
            self.controller.invalidate()
            state = self.controller.state
            self.assertEqual(state['revision'], revision + 1)
            self.assertEqual(state['publication_generation'], before['publication_generation'])
            self.assertEqual(state['last_successful_generation'], before['publication_generation'])
            self.assertIsNone(state['presentation_generation'])
            self.assertIsNone(state['explicit_current_mileage'])
            self.assertFalse(state['action_ready'])

    def test_revision_change_without_publication_change_rejects_action(self):
        self.controller.select_scope(PID)
        self.controller.set_explicit_mileage(100)
        for change in (self.controller.invalidate,
                       lambda: self.controller.select_scope(OTHER),
                       lambda: self.controller.set_explicit_mileage(101),
                       lambda: self.controller.set_evaluation_date(date(2026, 10, 30)),
                       lambda: self.controller.set_cost_bounds('2026-01-01', None)):
            self.factory.capture = Capture()
            before = self.controller.refresh()
            self.factory.capture.revalidate = change
            self.reject_action()
            state = self.controller.state
            self.assertEqual(state['publication_generation'], before['publication_generation'])
            self.assertGreater(state['revision'], before['revision'])
            self.assertIsNone(state['presentation_generation'])
            self.assertFalse(state['action_in_progress'])

    def test_generation_change_during_revalidation_rejects_even_with_same_identities(self):
        self.controller.refresh()
        capture, presentation = self.controller._session.publication.capture, self.controller._session.publication.presentation
        def change_generation():
            # Simulate a broken dependency to isolate the generation check.
            session = self.controller._session
            publication = replace_record(session.publication, generation=session.publication.generation + 1)
            self.controller._session = replace_record(session, publication=publication)
            self.assertIs(self.controller._session.publication.capture, capture)
            self.assertIs(self.controller._session.publication.presentation, presentation)
        self.factory.capture.revalidate = change_generation
        self.reject_action()
        self.assertIsNone(self.controller.state['presentation'])
        self.assertFalse(self.controller.state['action_in_progress'])

    def test_cancelled_refresh_capture_model_projection_and_final_revalidation(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for phase in ('capture', 'model', 'projection', 'final revalidation'):
                with self.subTest(error_type=error_type, phase=phase):
                    factory = Factory()
                    controller = DashboardController(factory, as_of=DAY, projector=project)
                    before = controller.refresh()
                    cancellation = error_type('cancel ' + phase)
                    def cancel(*args, **kwargs):
                        raise cancellation
                    if phase == 'capture':
                        factory.hook = cancel
                    elif phase == 'model':
                        class CancelledCapture(Capture):
                            @property
                            def model(self):
                                raise cancellation
                        factory.capture = CancelledCapture()
                    elif phase == 'projection':
                        controller._projector = cancel
                    else:
                        factory.capture.revalidate = cancel
                    with self.assertRaises(error_type) as caught:
                        controller.refresh()
                    self.assertIs(caught.exception, cancellation)
                    self.assert_cancelled_output(controller, before, cancellation)
                    factory.hook = None
                    factory.capture = Capture()
                    controller._projector = project
                    recovered = controller.refresh()
                    self.assertEqual(recovered['publication_generation'], before['publication_generation'] + 1)
                    controller.perform_action(lambda p: None)

    def test_cancelled_refresh_at_every_preparation_copy_preserves_generation(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for fail_at in range(1, 6):
                with self.subTest(error_type=error_type, fail_at=fail_at):
                    factory = Factory()
                    controller = DashboardController(factory, as_of=DAY, projector=project)
                    before = controller.refresh()
                    factory.capture.data['insights']['data']['sentinel'] = ['cancelled candidate']
                    cancellation = error_type('cancel copy')
                    copies = []
                    def copying(value):
                        copies.append(value)
                        self.assertFalse(controller._is_ready())
                        self.assertFalse(controller._is_ready())
                        if len(copies) == fail_at:
                            raise cancellation
                        return deepcopy(value)
                    with patch('dashboard_controller.deepcopy', copying):
                        with self.assertRaises(error_type) as caught:
                            controller.refresh()
                    self.assertIs(caught.exception, cancellation)
                    self.assertEqual(len(copies), fail_at)
                    self.assert_cancelled_output(controller, before, cancellation)
                    self.assertEqual(controller.refresh()['publication_generation'],
                                     before['publication_generation'] + 1)

    def test_cancelled_first_refresh_leaves_generation_zero(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error_type=error_type):
                factory = Factory()
                controller = DashboardController(factory, as_of=DAY, projector=project)
                before = controller.state
                cancellation = error_type('cancel initial capture')
                def cancel():
                    raise cancellation
                factory.hook = cancel
                with self.assertRaises(error_type) as caught:
                    controller.refresh()
                self.assertIs(caught.exception, cancellation)
                self.assert_cancelled_output(controller, before, cancellation)
                self.assertEqual(controller.state['publication_generation'], 0)

    def test_cancelled_action_revalidation_makes_generation_nonactionable(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error_type=error_type):
                factory = Factory()
                controller = DashboardController(factory, as_of=DAY, projector=project)
                before = controller.refresh()
                cancellation = error_type('cancel source check')
                def cancel():
                    raise cancellation
                factory.capture.revalidate = cancel
                calls = []
                with self.assertRaises(error_type) as caught:
                    controller.perform_action(calls.append)
                self.assertIs(caught.exception, cancellation)
                self.assertEqual(calls, [])
                self.assert_cancelled_output(controller, before, cancellation)
                factory.capture = Capture()
                controller.refresh()
                controller.perform_action(calls.append)
                self.assertEqual(len(calls), 1)

    def test_cancelled_action_preparation_also_cleans_up(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error_type=error_type):
                factory = Factory()
                controller = DashboardController(factory, as_of=DAY, projector=project)
                before = controller.refresh()
                cancellation = error_type('cancel payload copy')
                calls = []
                checks = factory.capture.checks
                with patch('dashboard_controller.deepcopy', side_effect=cancellation):
                    with self.assertRaises(error_type) as caught:
                        controller.perform_action(calls.append)
                self.assertIs(caught.exception, cancellation)
                self.assertEqual(calls, [])
                self.assertEqual(factory.capture.checks, checks)
                self.assert_cancelled_output(controller, before, cancellation)

    def test_callback_cancellation_propagates_without_reverting_intentional_invalidation(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for invalidate in (False, True):
                with self.subTest(error_type=error_type, invalidate=invalidate):
                    factory = Factory()
                    controller = DashboardController(factory, as_of=DAY, projector=project)
                    controller.select_scope(PID)
                    controller.set_explicit_mileage(100)
                    before = controller.refresh()
                    cancellation = error_type('cancel callback')
                    def callback(presentation):
                        self.assertTrue(controller.state['action_in_progress'])
                        if invalidate:
                            controller.invalidate()
                        raise cancellation
                    with self.assertRaises(error_type) as caught:
                        controller.perform_action(callback)
                    self.assertIs(caught.exception, cancellation)
                    state = controller.state
                    self.assertFalse(state['action_in_progress'])
                    self.assertEqual(state['publication_generation'], before['publication_generation'])
                    if invalidate:
                        self.assertEqual(state['revision'], before['revision'] + 1)
                        self.assertEqual(state['status'], 'dirty')
                        self.assertTrue(state['stale'])
                        self.assertFalse(state['action_ready'])
                        self.assertIsNone(state['error'])
                        self.assertIsNone(state['explicit_current_mileage'])
                        self.assertIsNone(state['presentation'])
                        self.assertIsNone(state['presentation_generation'])
                        self.assertEqual(state['last_successful_generation'], before['publication_generation'])
                        self.assertEqual(state['last_successful_presentation'], before['presentation'])
                        controller.refresh()
                    else:
                        self.assertEqual(state, before)
                    controller.perform_action(lambda p: None)

    def run_with_cancellation_trace(self, controller, operation, predicate, cancellation):
        """Inject exactly once at a selected line boundary; restore prior tracing."""
        code = operation.__code__
        fired = []
        old_trace = sys.gettrace()
        def trace(frame, event, arg):
            if frame.f_code is code and event == 'line' and not fired and predicate(frame):
                fired.append(frame.f_lineno)
                raise cancellation
            return trace
        try:
            sys.settrace(trace)
            with self.assertRaises(type(cancellation)) as caught:
                if code is DashboardController.perform_action.__code__:
                    operation(controller._test_consumer)
                else:
                    operation()
        finally:
            sys.settrace(old_trace)
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(len(fired), 1)

    def assert_publication_pairing(self, controller):
        state = controller.state
        publication = controller._session.publication
        self.assertEqual(state['publication_generation'], publication.generation)
        self.assertEqual(state['presentation'] is None, state['presentation_generation'] is None)
        self.assertEqual(state['last_successful_presentation'] is None,
                         state['last_successful_generation'] is None)
        if state['presentation'] is not None:
            self.assertEqual(state['presentation_generation'], publication.generation)
            self.assertEqual(state['presentation'], publication.presentation)
            self.assertIsNotNone(publication.capture)
        if state['last_successful_presentation'] is not None:
            self.assertEqual(state['last_successful_generation'], publication.last_generation)
            self.assertEqual(state['last_successful_presentation'], publication.last)
        self.assertFalse(state['status'] == 'ready' and (state['stale'] or state['error']))
        self.assertFalse(state['action_ready'] and (state['loading'] or state['dirty']
                         or state['stale'] or state['action_in_progress'] or state['error']))

    def test_internal_publication_session_and_activity_records_are_frozen(self):
        self.controller.refresh()
        session = self.controller._session
        with self.assertRaises(FrozenInstanceError):
            session.revision = 100
        with self.assertRaises(FrozenInstanceError):
            session.publication.generation = 100
        with self.assertRaises(FrozenInstanceError):
            self.controller._activity.loading = True
        self.assert_publication_pairing(self.controller)

    def test_cancellation_immediately_after_loading_activation_restores_guards(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error_type=error_type):
                factory = Factory()
                controller = DashboardController(factory, as_of=DAY, projector=project)
                before = controller.refresh()
                publication = controller._session.publication
                cancellation = error_type('after loading activation')
                def activated(frame):
                    return controller._activity.loading
                self.run_with_cancellation_trace(controller, controller.refresh, activated, cancellation)
                self.assert_cancelled_output(controller, before, cancellation)
                self.assertIs(controller._session.publication, publication)
                self.assert_publication_pairing(controller)
                self.assertEqual(controller.refresh()['publication_generation'], publication.generation + 1)
                controller.perform_action(lambda p: None)

    def test_cancellation_immediately_after_action_activation_restores_guards(self):
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(error_type=error_type):
                factory = Factory()
                controller = DashboardController(factory, as_of=DAY, projector=project)
                before = controller.refresh()
                publication = controller._session.publication
                calls = []
                controller._test_consumer = calls.append
                cancellation = error_type('after action activation')
                self.run_with_cancellation_trace(controller, controller.perform_action,
                    lambda frame: controller._activity.action, cancellation)
                self.assertEqual(calls, [])
                self.assert_cancelled_output(controller, before, cancellation)
                self.assertIs(controller._session.publication, publication)
                self.assert_publication_pairing(controller)
                controller.refresh()
                controller.perform_action(calls.append)
                self.assertEqual(len(calls), 1)

    def test_cancellation_at_every_publication_construction_commit_and_return_boundary(self):
        # Cover every executed line replacing the former individual publication
        # assignments, plus the single commit, guard restoration, and return.
        source, start = inspect.getsourcelines(DashboardController.refresh)
        lines = {start + index: text.strip() for index, text in enumerate(source)}
        first = next(number for number, text in lines.items() if text == 'last = deepcopy(presentation)')
        last = next(number for number, text in lines.items() if text.startswith('return result'))
        observed = set()
        old_trace = sys.gettrace()
        def observe(frame, event, arg):
            if frame.f_code is DashboardController.refresh.__code__ and event == 'line':
                if first <= frame.f_lineno <= last:
                    observed.add(frame.f_lineno)
            return observe
        try:
            sys.settrace(observe)
            self.controller.refresh()
        finally:
            sys.settrace(old_trace)
        commit = next(number for number, text in lines.items()
                      if text == 'self._session = candidate  # Single publication commit.')
        self.assertIn(commit, observed)
        self.assertIn(last, observed)
        self.assertTrue(any(number > commit for number in observed))
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for boundary in sorted(observed):
                with self.subTest(error_type=error_type, boundary=lines[boundary]):
                    factory = Factory()
                    controller = DashboardController(factory, as_of=DAY, projector=project)
                    before = controller.refresh()
                    publication = controller._session.publication
                    factory.capture = Capture()
                    factory.capture.data['insights']['data']['sentinel'] = ['cancelled candidate']
                    cancellation = error_type('publication boundary')
                    def at_boundary(frame):
                        self.assert_publication_pairing(controller)
                        return frame.f_lineno == boundary
                    self.run_with_cancellation_trace(controller, controller.refresh,
                                                     at_boundary, cancellation)
                    self.assert_cancelled_output(controller, before, cancellation)
                    # Exact record identity proves complete restoration: capture,
                    # presentation, retained success, and both generation labels.
                    self.assertIs(controller._session.publication, publication)
                    self.assert_publication_pairing(controller)
                    recovered = controller.refresh()
                    self.assertEqual(recovered['publication_generation'], publication.generation + 1)
                    self.assertEqual(recovered['presentation']['facts']['sentinel'], ['cancelled candidate'])
                    controller.perform_action(lambda p: None)

    def test_cancellation_before_and_after_commit_restores_exact_publication(self):
        source, start = inspect.getsourcelines(DashboardController.refresh)
        commit = next(start + i for i, text in enumerate(source)
                      if text.strip() == 'self._session = candidate  # Single publication commit.')
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for after_commit in (False, True):
                with self.subTest(error_type=error_type, after_commit=after_commit):
                    factory = Factory()
                    controller = DashboardController(factory, as_of=DAY, projector=project)
                    before = controller.refresh()
                    publication = controller._session.publication
                    factory.capture = Capture()
                    factory.capture.data['insights']['data']['sentinel'] = ['unreturned candidate']
                    cancellation = error_type('commit boundary')
                    def boundary(frame):
                        if after_commit:
                            candidate = frame.f_locals.get('candidate')
                            return candidate is not None and controller._session is candidate
                        return frame.f_lineno == commit
                    self.run_with_cancellation_trace(controller, controller.refresh, boundary, cancellation)
                    self.assert_cancelled_output(controller, before, cancellation)
                    self.assertIs(controller._session.publication, publication)
                    self.assertIs(controller._session.publication.capture, publication.capture)
                    self.assertIs(controller._session.publication.presentation, publication.presentation)
                    self.assertIs(controller._session.publication.last, publication.last)

    def test_cancellation_on_action_cleanup_preserves_callback_invalidation(self):
        source, start = inspect.getsourcelines(DashboardController.perform_action)
        cleanup = next(start + i for i, text in enumerate(source)
                       if text.strip() == 'self._activity = activity')
        for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for invalidate in (False, True):
                with self.subTest(error_type=error_type, invalidate=invalidate):
                    factory = Factory()
                    controller = DashboardController(factory, as_of=DAY, projector=project)
                    controller.select_scope(PID)
                    controller.set_explicit_mileage(100)
                    before = controller.refresh()
                    publication = controller._session.publication
                    calls = []
                    def callback(presentation):
                        calls.append(presentation)
                        if invalidate:
                            controller.invalidate()
                    controller._test_consumer = callback
                    cancellation = error_type('action cleanup boundary')
                    self.run_with_cancellation_trace(controller, controller.perform_action,
                        lambda frame: frame.f_lineno == cleanup, cancellation)
                    self.assertEqual(len(calls), 1)
                    self.assertFalse(controller.state['action_in_progress'])
                    self.assertFalse(controller.state['loading'])
                    self.assertIs(controller._session.publication, publication)
                    if invalidate:
                        self.assertTrue(controller.state['stale'])
                        self.assertFalse(controller.state['action_ready'])
                        self.assertIsNone(controller.state['explicit_current_mileage'])
                        self.assertEqual(controller.state['revision'], before['revision'] + 1)
                        controller.refresh()
                    else:
                        self.assertEqual(controller.state, before)
                    self.assert_publication_pairing(controller)
                    controller.perform_action(lambda p: None)

if __name__ == '__main__':
    unittest.main()
