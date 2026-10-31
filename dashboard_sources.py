"""Read-only dashboard capture with explicit paths and availability.

Parents must already exist, as required by the shared Insights source reader.
Unsafe sources, aliases, oversized reads and races raise; missing files and invalid
store contents are reported separately. No missing store is replaced with empty
data. Revalidation detects changes but is not a cross-process transaction.
"""
from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path

from dashboard_model import build_dashboard_model
from data_backup import RuntimePaths, reject_output_alias
from inspection_history import validate_checklist_snapshot, validate_history
from maintenance_insights import build_insights
from maintenance_insights_sources import read_source, decode_source
from maintenance_schedule import validate_schedule
from vehicle_profiles import validate_profiles


KINDS = ('checklist', 'history', 'profiles', 'maintenance')
VALIDATORS = (validate_checklist_snapshot, validate_history,
              validate_profiles, validate_schedule)


def _reject_aliases(paths):
    for index, path in enumerate(paths):
        reject_output_alias(path, paths[index + 1:])


@dataclass(frozen=True)
class DashboardSourcesSnapshot:
    """Immutable capture; each model access validates freshness and decodes anew.

    Tokens contain only immutable parent/file identities and original bytes.
    Call revalidate immediately before consumer actions. Display scope is supplied
    separately to dashboard_model; selected_profile_id is evaluation input only.
    """
    source_paths: tuple
    tokens: tuple
    as_of: date
    selected_profile_id: str | None = None
    explicit_current_mileage: int | str | bool | None = None
    cost_start_date: str | None = None
    cost_end_date: str | None = None

    def revalidate(self):
        _reject_aliases(self.source_paths)
        if tuple(read_source(path) for path in self.source_paths) != self.tokens:
            raise ValueError('Dashboard sources changed; capture again before continuing.')

    @property
    def model(self):
        """Return owned stores, analytical facts, and optional fleet presentation.

        Store data preserves validated schema versions. Invalid stores include a
        diagnostic and no decoded data. Insights and presentation are absent if
        any analytical store is missing/invalid; checklist status is independent.
        """
        self.revalidate()
        stores = {}
        for kind, token, validator in zip(KINDS, self.tokens, VALIDATORS):
            state = dict(availability='Missing', data=None, error=None,
                         source_digest=None)
            if token[2] is not None:
                state['source_digest'] = hashlib.sha256(token[2]).hexdigest()
                try:
                    state['data'] = decode_source(token, None, validator)
                    state['availability'] = 'Available'
                except ValueError as error:
                    state.update(availability='Invalid', error=str(error))
            stores[kind] = state
        blocked = [kind for kind in KINDS[1:]
                   if stores[kind]['availability'] != 'Available']
        insights = dict(availability='Unavailable', unavailable_reason='analytical_sources_unavailable',
                        blocked_sources=blocked, error=None, data=None)
        presentation = None
        if not blocked:
            try:
                facts = build_insights(
                    stores['maintenance']['data'], stores['profiles']['data'],
                    stores['history']['data'], as_of=self.as_of,
                    selected_profile_id=self.selected_profile_id,
                    explicit_current_mileage=self.explicit_current_mileage,
                    cost_start_date=self.cost_start_date, cost_end_date=self.cost_end_date)
            except ValueError as error:
                insights.update(unavailable_reason='insights_validation_failed', error=str(error))
            else:
                facts['sources'] = [dict(source_kind=source_kind, availability=stores[kind]['availability'],
                                         source_digest=stores[kind]['source_digest'])
                                    for kind, source_kind in (('maintenance', 'maintenance'),
                                                              ('profiles', 'profiles'),
                                                              ('history', 'inspections'))]
                insights.update(availability='Available', unavailable_reason=None, data=facts)
                presentation = build_dashboard_model(facts, stores['checklist']['data'])
        self.revalidate()
        return dict(stores=stores, insights=insights, presentation=presentation)


def capture_dashboard_sources(paths=None, *, checklist_path=None, history_path=None,
                              profiles_path=None, maintenance_path=None, as_of,
                              selected_profile_id=None, explicit_current_mileage=None,
                              cost_start_date=None, cost_end_date=None):
    """Capture RuntimePaths OR all four explicit paths (never discover defaults).

    All evaluation inputs are immutable scalars, matching the existing Insights
    API. Invalid mileage remains an existing domain input_error when insights can
    be built; capture never infers mileage or changes domain validation rules.
    """
    explicit = (checklist_path, history_path, profiles_path, maintenance_path)
    if paths is not None:
        if not isinstance(paths, RuntimePaths) or any(p is not None for p in explicit):
            raise ValueError('Supply RuntimePaths or four explicit paths, not both.')
        # Require maintenance explicitly: RuntimePaths.mapping has a fallback
        # filename, which this layer intentionally does not use.
        explicit = (paths.checklist, paths.history, paths.profiles, paths.maintenance)
    if any(p is None for p in explicit):
        raise ValueError('All four source paths must be supplied explicitly.')
    if type(as_of) is not date:
        raise ValueError('Evaluation date must be an explicit date.')
    for value in (selected_profile_id, cost_start_date, cost_end_date):
        if value is not None and type(value) is not str:
            raise ValueError('Profile ID and cost bounds must be immutable text or None.')
    if explicit_current_mileage is not None and type(explicit_current_mileage) not in (int, str, bool):
        raise ValueError('Explicit mileage must be scalar integer or form text.')
    source_paths = tuple(Path(p).absolute() for p in explicit)
    _reject_aliases(source_paths)
    snapshot = DashboardSourcesSnapshot(source_paths, tuple(read_source(p) for p in source_paths),
        as_of, selected_profile_id, explicit_current_mileage, cost_start_date, cost_end_date)
    snapshot.model
    return snapshot
