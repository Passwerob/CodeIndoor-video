"""Complete, dependency-aware diagnostics for one planning candidate."""
from __future__ import annotations

import json
import hashlib


class PlanningValidationError(ValueError):
    def __init__(self, report):
        self.measurements = report
        super().__init__(report['stage']+' preflight failed: '+'; '.join(
            f"[{item.get('scenario','baseline')}][{item['code']}] {item['evidence']}" for item in report['issues']))


class Diagnostics:
    def __init__(self, stage):
        self.stage = stage
        self.issues = []
        self.blocked_checks = []

    def add(self, code, evidence, *, paths=(), component_ids=(), scenario='baseline',
            measured=None, expected=None):
        item = {'code': code, 'evidence': evidence, 'paths': list(paths),
                'component_ids': list(component_ids), 'scenario': scenario}
        if measured is not None:
            item['measured'] = measured
        if expected is not None:
            item['expected'] = expected
        if item not in self.issues:
            self.issues.append(item)

    def block(self, check, reason, *, paths=(), scenario='baseline'):
        item = {'check': check, 'reason': reason, 'paths': list(paths), 'scenario': scenario}
        if item not in self.blocked_checks:
            self.blocked_checks.append(item)

    def report(self):
        for item in self.issues:
            identity={key:item[key] for key in ('code','paths','component_ids','scenario')}
            # Several independent constraints may share a controlling object.
            identity['evidence']=item['evidence']
            item['id']=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:16]
        return {'stage': self.stage, 'status': 'needs_revision' if self.issues else 'passed',
                'issues': self.issues, 'blocked_checks': self.blocked_checks,
                'coverage': 'All independent checks in this stage; blocked checks require corrected prerequisites.'}

    def raise_if_any(self):
        if self.issues:
            raise PlanningValidationError(self.report())


def failure_report(failure, stage):
    """Keep semantic measurements and all Pydantic field errors without truncation."""
    measurements = getattr(failure, 'measurements', {})
    if isinstance(measurements, dict) and 'issues' in measurements:
        return measurements
    report = Diagnostics(stage)
    if hasattr(failure, 'errors'):
        schema_errors=False
        for error in failure.errors(include_url=False, include_input=False):
            path = ''.join('/' + str(item).replace('~', '~0').replace('/', '~1')
                           for item in error.get('loc', ()))
            nested=getattr(error.get('ctx',{}).get('error'),'measurements',None)
            if isinstance(nested,dict) and 'issues' in nested:
                for item in nested['issues']:
                    item={**item,'paths':[path+p for p in item.get('paths',[])]}
                    report.issues.append(item)
                report.blocked_checks.extend(nested.get('blocked_checks',[]))
            else:
                report.add(error['type'], error['msg'], paths=[path])
                schema_errors=True
        if schema_errors:
            report.block('semantic_and_geometry_checks', 'Schema errors prevent constructing the typed candidate.')
    else:
        report.add('validation', str(failure))
    result = report.report()
    if measurements:
        result['measurement_evidence'] = measurements
    # Validate that the report is suitable for the model transport and artifacts.
    json.dumps(result)
    return result


def diagnostic_families(report):
    """Return stable issue scopes, excluding measurements that can drift between retries."""
    families=set()
    def stable_values(value):
        if value is None:return []
        if isinstance(value,str):return [value]
        return sorted(set(map(str,value)))
    for item in report.get('issues',report.get('findings',[])):
        measured=item.get('measured') if isinstance(item.get('measured'),dict) else {}
        family={
            'kind':'issue',
            'code':item.get('code'),
            'constraint_id':item.get('constraint_id'),
            'component_ids':stable_values(item.get('component_ids')),
            'part_ids':stable_values(item.get('part_ids',measured.get('part_ids'))),
            'paths':stable_values(item.get('paths')),
            'repair_target':item.get('repair_target',measured.get('repair_target')),
            'scenario':item.get('scenario','baseline'),
            'severity':item.get('severity'),
        }
        if item.get('code')=='validation':
            family['validation_evidence']=item.get('evidence')
        families.add(json.dumps(family,sort_keys=True,ensure_ascii=False,default=str))
    for item in report.get('blocked_checks',[]):
        family={'kind':'blocked_check','check':item.get('check'),
                'paths':stable_values(item.get('paths')),
                'scenario':item.get('scenario','baseline')}
        families.add(json.dumps(family,sort_keys=True,ensure_ascii=False,default=str))
    return families


def resolved_diagnostic_families(previous, current):
    """Identify complete issue families that disappeared after a candidate repair."""
    before=diagnostic_families(previous) if isinstance(previous,dict) else set(previous)
    after=diagnostic_families(current) if isinstance(current,dict) else set(current)
    return before-after


def validate_together(stage, checks):
    """Run independent semantic and geometric validators before requesting a repair."""
    diagnostics = Diagnostics(stage)
    for item in checks:
        name, check = item[:2]
        paths = item[2] if len(item) > 2 else ()
        try:
            check()
        except PlanningValidationError as failure:
            report = failure.measurements
            for issue in report.get('issues', []):
                diagnostics.add(issue['code'], f'[{name}] {issue["evidence"]}',
                    paths=issue.get('paths', ()), component_ids=issue.get('component_ids', ()),
                    scenario=issue.get('scenario', 'baseline'), measured=issue.get('measured'),
                    expected=issue.get('expected'))
            for blocked in report.get('blocked_checks', []):
                diagnostics.block(blocked['check'], f'[{name}] {blocked["reason"]}',
                    paths=blocked.get('paths', ()), scenario=blocked.get('scenario', 'baseline'))
        except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as failure:
            diagnostics.add(name, str(failure), paths=paths)
    if diagnostics.issues or diagnostics.blocked_checks:
        raise PlanningValidationError(diagnostics.report())


def combine_reports(stage, *reports):
    """Preserve independent failures from checks that can run in separate stages."""
    diagnostics=Diagnostics(stage);solver=None
    for report in reports:
        if not isinstance(report,dict):continue
        if isinstance(report.get('numerical_solver'),dict):solver=report['numerical_solver']
        for item in report.get('issues',[]):
            diagnostics.add(item.get('code','validation'),item.get('evidence','Validation failed.'),
                paths=item.get('paths',()),component_ids=item.get('component_ids',()),
                scenario=item.get('scenario','baseline'),measured=item.get('measured'),
                expected=item.get('expected'))
        for item in report.get('blocked_checks',[]):
            diagnostics.block(item.get('check','validation'),item.get('reason','Prerequisite failed.'),
                paths=item.get('paths',()),scenario=item.get('scenario','baseline'))
    result=diagnostics.report()
    if solver is not None:result['numerical_solver']=solver
    return result


def repair_report(report, allowed_paths):
    """Map diagnostics to writable pointers without making frozen fields editable."""
    result={**report,'issues':[dict(item) for item in report['issues']]}
    for item in result['issues']:
        selected=[]
        for path in item.get('paths',[]):
            if path in allowed_paths:matches=[path]
            else:
                ancestors=[allowed for allowed in allowed_paths if path.startswith(allowed+'/')]
                if ancestors:matches=[max(ancestors,key=len)]
                else:
                    descendants=[allowed for allowed in allowed_paths if allowed.startswith(path+'/')]
                    matches=[allowed for allowed in descendants if not any(
                        allowed.startswith(other+'/') for other in descendants)]
            selected.extend(matches)
        item['allowed_repair_paths']=list(dict.fromkeys(selected))
    result['repair_instruction']=('Address all independent actionable issues in one coherent patch. '
        'Use measured/expected values and preserve frozen constraints. A path match alone does not prove '
        'a fix: the system reruns the complete checks. Blocked checks are not passes.')
    return result


def prompt_evidence(report, *, scenario_names=()):
    """Preserve independent failures without repeating identical edit evidence.

    Full machine reports remain on disk. Numeric evidence that changes between
    edits stays distinct; only genuinely identical checks share scenario lists.
    Solver candidates/bounds already travel separately as the layout seed.
    """
    result={key:report[key] for key in ('stage','status','coverage','repair_patch_rejected') if key in report}
    for collection in ('issues','blocked_checks'):
        merged={}
        for source in report.get(collection,[]):
            item={key:value for key,value in source.items() if key not in {'id','scenario'}}
            identity=json.dumps(item,sort_keys=True,ensure_ascii=False)
            if identity not in merged:merged[identity]={**item,'scenarios':[],'source_ids':[]}
            record=merged[identity]
            for scenario in source.get('scenarios',[source.get('scenario','baseline')]):
                if scenario not in record['scenarios']:record['scenarios'].append(scenario)
            if source.get('id') and source['id'] not in record['source_ids']:record['source_ids'].append(source['id'])
        result[collection]=list(merged.values())
    solver=report.get('numerical_solver')
    if isinstance(solver,dict):
        result['numerical_solver']={key:solver[key] for key in
            ('status','backend','elapsed_seconds','limits','reason','orientation_proposals',
             'access_side_proposals','routing_failure','joint_corridor_failure',
             'independent_conflict_groups') if key in solver}
        names={'baseline',*scenario_names};constraints={}
        for label in solver.get('incompatible_constraints',[]):
            prefix,_,rest=label.partition(' ')
            scenario=prefix if prefix in names else 'all'
            text=rest if prefix in names else label
            constraints.setdefault(text,[])
            if scenario not in constraints[text]:constraints[text].append(scenario)
        result['numerical_solver']['incompatible_constraints']=[
            {'constraint':label,'scenarios':scenarios} for label,scenarios in constraints.items()]
    result['original_issue_count']=len(report.get('issues',[]))
    return result
