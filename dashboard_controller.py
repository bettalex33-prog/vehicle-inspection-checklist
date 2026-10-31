"""Transient dashboard session coordination, without IO or domain evaluation.

Inject a capture factory accepting dashboard_sources evaluation keywords (bind
explicit paths outside this module). Captures expose model and revalidate.
Consumers run synchronously through perform_action, after fresh revalidation.
"""
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date

from dashboard_model import build_dashboard_model


class DashboardControllerError(ValueError):
    """Invalid session input, unavailable output, or rejected consumer action."""


@dataclass(frozen=True)
class _Publication:
    """One committed generation. Containers are privately owned, never exposed."""
    capture: object = None
    presentation: object = None
    generation: int = 0
    last: object = None
    last_generation: int | None = None


@dataclass(frozen=True)
class _Session:
    """One coherent public state, including its exact immutable publication."""
    inputs: dict
    publication: _Publication = field(default_factory=_Publication)
    revision: int = 0
    dirty: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _Activity:
    loading: bool = False
    action: bool = False
    interrupted: bool = False


class DashboardController:
    def __init__(self, capture_factory, *, as_of, cost_start_date=None,
                 cost_end_date=None, projector=build_dashboard_model):
        self._capture_factory = capture_factory
        self._projector = projector
        self._session = _Session(dict(as_of=as_of, cost_start_date=cost_start_date,
            cost_end_date=cost_end_date, selected_profile_id=None,
            explicit_current_mileage=None))
        self._activity = _Activity()
        self._validate_inputs()

    def _validate_inputs(self, values=None):
        values = self._session.inputs if values is None else values
        if type(values['as_of']) is not date:
            raise DashboardControllerError('Evaluation date must be an explicit date.')
        bounds = []
        for key in ('cost_start_date', 'cost_end_date'):
            value = values[key]
            try:
                if value is not None and (type(value) is not str or
                        date.fromisoformat(value).isoformat() != value):
                    raise ValueError()
                bounds.append(date.fromisoformat(value) if value is not None else None)
            except (ValueError, TypeError):
                raise DashboardControllerError('Cost bounds must be ISO calendar dates or None.') from None
        if all(bounds) and bounds[0] > bounds[1]:
            raise DashboardControllerError('Cost start date must not exceed end date.')
        pid = values['selected_profile_id']
        if pid is not None and (type(pid) is not str or not pid):
            raise DashboardControllerError('Select a stable profile ID or None for all vehicles.')
        mileage = values['explicit_current_mileage']
        if mileage is not None and (pid is None or type(mileage) is not int or mileage < 0):
            raise DashboardControllerError('Explicit mileage requires a selected profile and a nonnegative integer.')

    def _change(self, **values):
        before = self._session
        candidate = deepcopy(before.inputs)
        candidate.update(deepcopy(values))
        try:
            self._validate_inputs(candidate)
        except DashboardControllerError as error:
            self._session = replace(before, inputs=candidate, revision=before.revision + 1,
                                    dirty=True, error=str(error))
            raise
        if all(before.inputs[key] == value and type(before.inputs[key]) is type(value)
               for key, value in values.items()):
            return
        self._session = replace(before, inputs=candidate, revision=before.revision + 1,
                                dirty=True, error=None)

    def select_scope(self, profile_id):
        """None selects all vehicles. Resolve IDs against the next fresh capture."""
        values = self._session.inputs
        same_scope = (type(profile_id) is type(values['selected_profile_id'])
                      and profile_id == values['selected_profile_id'])
        self._change(selected_profile_id=profile_id, explicit_current_mileage=(
            values['explicit_current_mileage'] if same_scope else None))

    def set_explicit_mileage(self, mileage):
        self._change(explicit_current_mileage=mileage)

    def set_evaluation_date(self, as_of):
        self._change(as_of=as_of)

    def set_cost_bounds(self, start, end):
        self._change(cost_start_date=start, cost_end_date=end)

    def invalidate(self):
        """Notify a successful write, restore, or reset; performs no source reads."""
        before = self._session
        inputs = dict(before.inputs, explicit_current_mileage=None)
        self._session = replace(before, inputs=inputs, revision=before.revision + 1,
                                dirty=True, error=None)

    def _is_ready(self):
        session = self._session
        publication = session.publication
        return (not self._activity.loading and not session.dirty and session.error is None
                and publication.presentation is not None and publication.capture is not None
                and publication.generation > 0)

    def _snapshot(self, session, activity):
        publication = session.publication
        current = (not activity.loading and not session.dirty and session.error is None
                   and publication.presentation is not None)
        status = ('loading' if activity.loading else 'error' if session.error else
                  'dirty' if session.dirty else 'ready' if current else 'empty')
        return deepcopy(dict(**session.inputs, revision=session.revision,
            publication_generation=publication.generation,
            presentation_generation=publication.generation if current else None,
            last_successful_generation=publication.last_generation,
            status=status, loading=activity.loading, dirty=session.dirty,
            stale=publication.last is not None and not current, error=session.error,
            presentation=publication.presentation if current else None,
            last_successful_presentation=publication.last,
            action_in_progress=activity.action,
            action_ready=current and publication.capture is not None
                         and publication.generation > 0 and not activity.action))

    @property
    def state(self):
        """Owned output; publication generation advances only on refresh success.

        Dirty/error/loading states expose the retained publication only as stale
        context, without a current presentation or current generation label.
        action_ready is advisory; perform_action always revalidates sources.
        """
        return self._snapshot(self._session, self._activity)

    def _failed(self, base, publication, error):
        message = (str(error) or type(error).__name__) if isinstance(error, Exception) else type(error).__name__
        return replace(base, publication=publication, revision=base.revision + 1,
                       dirty=True, error=message)

    def refresh(self):
        """Prepare locally, commit once, and roll back a cancelled return.

        The commit is the single ``self._session = candidate`` assignment. All
        candidate data and the owned return value are prepared beforehand. Until
        this method returns, failure restores the EXACT previous publication
        record inside one failed-session assignment, retaining it as stale only.
        One injected cancellation is supported at each operation boundary; this
        does not promise immunity to repeated asynchronous exceptions in cleanup.
        """
        if self._activity.action:
            self._activity = replace(self._activity, interrupted=True)
            raise DashboardControllerError('Refresh is forbidden during an action.')
        if self._activity.loading:
            raise DashboardControllerError('Refresh is already in progress.')
        before, activity = self._session, self._activity
        candidate = None
        try:
            try:
                self._activity = replace(activity, loading=True)
                self._validate_inputs()
                inputs = dict(before.inputs)
                capture = self._capture_factory(**inputs)
                model = capture.model
                stores = model['stores']
                if any(stores[k]['availability'] != 'Available'
                       for k in ('checklist', 'profiles', 'history', 'maintenance')):
                    raise DashboardControllerError('Dashboard sources are missing or invalid.')
                insights = model['insights']
                if insights['availability'] != 'Available' or insights['data'] is None:
                    raise DashboardControllerError(insights.get('error') or 'Insights are unavailable.')
                facts = insights['data']
                pid = inputs['selected_profile_id']
                if pid is not None and not any(p['profile_id'] == pid for p in stores['profiles']['data']['profiles']):
                    raise DashboardControllerError('Selected profile ID is unavailable.')
                if facts['input_error'] or not facts['export_available']:
                    raise DashboardControllerError(facts['input_error'] or 'Evaluation inputs are invalid.')
                presentation = deepcopy(self._projector(deepcopy(facts),
                    deepcopy(stores['checklist']['data']), scope_profile_id=pid))
                if not isinstance(presentation, dict):
                    raise DashboardControllerError('Projection did not return a presentation.')
                last = deepcopy(presentation)
                generation = before.publication.generation + 1
                publication = _Publication(capture, presentation, generation, last, generation)
                candidate = _Session(inputs, publication, before.revision + 1)
                result = self._snapshot(candidate, activity)
                capture.revalidate()
                if self._session is not before:
                    raise DashboardControllerError('Session changed during refresh; refresh again.')
                self._session = candidate  # Single publication commit.
            finally:
                self._activity = activity
            return result  # Still inside rollback protection until return.
        except BaseException as error:
            # Preserve reentrant input changes, but never retain this candidate.
            base = before if self._session is candidate else self._session
            self._session = self._failed(base, before.publication, error)
            self._activity = activity
            if not isinstance(error, Exception):
                raise
            raise DashboardControllerError(self._session.error) from error

    def perform_action(self, consumer):
        """Revalidate the exact generation immediately before its consumer.

        Source revalidation detects changes; it is not a cross-process transaction.
        Consumer errors propagate without rolling back intentional invalidation.
        """
        if self._activity.action:
            self._activity = replace(self._activity, interrupted=True)
            raise DashboardControllerError('Nested actions are forbidden.')
        if not self._is_ready():
            raise DashboardControllerError('Action requires a successful current refresh.')
        before, activity = self._session, self._activity
        publication = before.publication
        revision, generation = before.revision, publication.generation
        capture, presentation = publication.capture, publication.presentation
        try:
            try:
                try:
                    self._activity = replace(activity, action=True)
                    self._validate_inputs()
                    owned_presentation = deepcopy(presentation)
                    capture.revalidate()
                    current = self._session.publication
                    if (self._session is not before or revision != self._session.revision
                            or generation != current.generation or capture is not current.capture
                            or presentation is not current.presentation or not self._is_ready()
                            or self._activity.interrupted):
                        raise DashboardControllerError('Session changed during revalidation.')
                except BaseException as error:
                    self._session = self._failed(self._session, before.publication, error)
                    if not isinstance(error, Exception):
                        raise
                    raise DashboardControllerError(self._session.error) from error
                return consumer(owned_presentation)
            finally:
                self._activity = activity
        except BaseException:
            # Also handles a single interruption at the finally restoration line.
            self._activity = activity
            raise
