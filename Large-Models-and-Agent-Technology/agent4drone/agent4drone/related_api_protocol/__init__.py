"""Related APIs v1. Dependency-free protocol, vendored identically by both apps."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import quote, unquote

VERSION = 1
CATALOG = json.loads(Path(__file__).with_name('catalog.json').read_text())
PLACEHOLDER = re.compile(r'\{[A-Za-z_][^{}]*\}')


def parse_plan_text(text):
    text = text.strip()
    if text.startswith('"related_apis"'):
        text = '{' + text.rstrip(',') + '}'
    try:
        value = json.loads(text)
    except json.JSONDecodeError as original:
        try:
            value = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            raise ValueError(f'Invalid JSON at line {original.lineno}, column {original.colno}: {original.msg}') from original
    if isinstance(value, dict):
        value = value.get('related_apis', [value])
    if not isinstance(value, list) or not value:
        raise ValueError('Expected a non-empty related_apis array')
    return value


def _validate_values(value):
    if isinstance(value, str) and PLACEHOLDER.search(value):
        raise ValueError('Unresolved template placeholder: ' + value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite number')
    if isinstance(value, dict):
        for child in value.values():
            _validate_values(child)
    elif isinstance(value, list):
        for child in value:
            _validate_values(child)


def normalize_step(step):
    if not isinstance(step, dict):
        raise ValueError('Operation must be an object')
    endpoint = step.get('endpoint')
    if step.get('method', 'POST').upper() != 'POST' or endpoint not in CATALOG:
        raise ValueError('Unsupported related_apis command: ' + str(endpoint))
    if any(key in step for key in ('path', 'query_params', 'request_body', 'payload')):
        raise ValueError('Ambiguous entry: candidate parameters mixed with request history fields')
    params = copy.deepcopy(step.get('parameters'))
    if not isinstance(params, dict):
        raise ValueError('parameters must be an object')
    _validate_values(params)
    if not isinstance(params.get('id'), (str, int)) or isinstance(params.get('id'), bool) or not str(params['id']).strip():
        raise ValueError('Missing drone id')
    spec = CATALOG[endpoint]
    missing = set(spec['required']) - params.keys()
    unknown = params.keys() - set(spec['parameters'])
    if missing or unknown:
        raise ValueError(f'Missing parameters: {sorted(missing)}; unknown parameters: {sorted(unknown)}')
    for key in ('x', 'y', 'z', 'altitude', 'heading', 'distance', 'duration', 'charge_amount', 'dx', 'dy', 'dz', 'azimuth', 'elevation'):
        if key in params and (isinstance(params[key], bool) or not isinstance(params[key], (int, float))):
            raise ValueError(key + ' must be numeric')
    if endpoint.endswith('/move_towards'):
        directions = sum(('heading' in params, 'dx' in params or 'dy' in params, 'azimuth' in params))
        if directions > 1 or ('dx' in params) != ('dy' in params):
            raise ValueError('Specify only one direction method; dx and dy must be provided together')
    if endpoint.endswith('/command'):
        command = params.get('command')
        nested = params.get('parameters', {})
        if not isinstance(command, str) or not isinstance(nested, dict) or 'id' in nested:
            raise ValueError('Invalid generic command parameters')
        normalize_step({'endpoint': '/drones/{id}/command/' + command, 'parameters': {'id': params['id'], **nested}})
    if endpoint.endswith('/move_along_path') and (not isinstance(params['waypoints'], list) or not params['waypoints']):
        raise ValueError('waypoints must be a non-empty array')
    if endpoint.endswith('/move_along_path'):
        for point in params['waypoints']:
            if not isinstance(point, dict) or not {'x', 'y'} <= point.keys():
                raise ValueError('Each waypoint requires x and y')
            for axis in ('x', 'y', 'z'):
                if axis in point and (isinstance(point[axis], bool) or not isinstance(point[axis], (int, float))):
                    raise ValueError('Waypoint coordinates must be numeric')
    return {'method': 'POST', 'endpoint': endpoint, 'parameters': params}


def normalize_plan(entries):
    result = []
    for index, entry in enumerate(entries, 1):
        try:
            result.append(normalize_step(entry))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f'Step {index}: {exc}') from exc
    return result


def resolve_step(step):
    step = normalize_step(step)
    params = step['parameters']
    path = step['endpoint'].replace('{id}', quote(str(params.pop('id')), safe=''))
    body = CATALOG[step['endpoint']]['transport'] == 'json'
    return {'method': 'POST', 'endpoint': path, 'payload': params if body else None, 'params': {} if body else params}


def normalize_request(entry):
    if not isinstance(entry, dict):
        raise ValueError('Operation must be an object')
    if 'parameters' in entry:
        return resolve_step(entry)
    path = entry.get('path') or entry.get('endpoint')
    if not path or not entry.get('method'):
        raise ValueError('History entry must include method and path')
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or PLACEHOLDER.search(path):
        raise ValueError('Invalid or unresolved request path')
    return {'method': str(entry['method']).upper(), 'endpoint': path,
            'payload': entry.get('request_body', entry.get('payload')),
            'params': entry.get('query_params') or {}}


def requests_to_plan(records):
    plan, issues, indices = [], [], []
    observations = {}
    for record in records:
        response = record.get('response', record.get('response_body'))
        values = response if isinstance(response, list) else [response]
        for value in values:
            if isinstance(value, dict) and value.get('command_id'):
                observations.setdefault(str(value['command_id']), []).append((record.get('index', 0), value))
    for record in sorted(records, key=lambda r: r.get('index', 0)):
        index = record.get('index')
        method = str(record.get('method', '')).upper()
        if method in ('GET', 'HEAD', 'OPTIONS') or record.get('endpoint') in ('/obstacles/collision/check', '/obstacles/collision/path'):
            continue
        path = record.get('endpoint') or record.get('path', '')
        match = re.fullmatch(r'/drones/([^/]+)/command(/[^/]+)?', path)
        try:
            if method != 'POST' or not match:
                raise ValueError('Unsupported write operation')
            params = {'id': unquote(match[1])}
            if record.get('data') is not None:
                raise ValueError('Raw/form request body cannot be converted')
            for part in (record.get('params') if record.get('params') is not None else record.get('query_params'), record.get('json', record.get('request_body', record.get('payload')))):
                if part is None:
                    continue
                if not isinstance(part, dict):
                    raise ValueError('Parameters must be objects')
                for key, val in part.items():
                    if key in params and params[key] != val:
                        raise ValueError('Conflicting parameter: ' + key)
                    params[key] = val
            step = normalize_step({'endpoint': '/drones/{id}/command' + (match[2] or ''), 'parameters': params})
            response = record.get('response', record.get('response_body'))
            state = response.get('status') if isinstance(response, dict) else None
            command_id = response.get('command_id') if isinstance(response, dict) else None
            if command_id and state in ('queued', 'pending', 'running', 'executing', 'accepted'):
                later = [(i, r) for i, r in observations.get(str(command_id), []) if i > (index or 0) and r.get('status') in ('completed', 'success', 'failed', 'error', 'cancelled', 'partial_success')]
                if later:
                    terminal_index, final = min(later, key=lambda pair: pair[0])
                    state = final.get('status')
                    if any((index or 0) < r.get('index', 0) < terminal_index and str(r.get('method', '')).upper() == 'POST' and '/command' in str(r.get('endpoint', '')) for r in records):
                        issues.append({'index': index, 'reason': 'Overlapping commands; sequential replay changes timing'})
            if record.get('success') is False or record.get('http_success') is False or record.get('error') or isinstance(response, dict) and (response.get('success') is False or response.get('partial_success')) or state in ('failed', 'error', 'partial_success', 'cancelled'):
                raise ValueError('Failed/partial request excluded from the plan; review required')
            if not isinstance(response, dict) or state not in ('completed', 'success', 'succeeded', 'done'):
                raise ValueError('Final command completion not established; review required')
            body = record.get('json', record.get('request_body', record.get('payload')))
            query = record.get('params') if record.get('params') is not None else record.get('query_params')
            if CATALOG[step['endpoint']]['transport'] == 'query' and body is not None or CATALOG[step['endpoint']]['transport'] == 'json' and query:
                issues.append({'index': index, 'reason': 'Original parameter transport differs from API catalog; review required'})
            plan.append(step)
            indices.append(index)
        except (ValueError, TypeError) as exc:
            issues.append({'index': index, 'path': path, 'reason': str(exc)})
    return plan, issues, indices


def prepare_related_apis_replacement(task, candidate, metadata):
    """Pure preview only. Never mutates or saves a task."""
    plan = normalize_plan(candidate)
    if not plan:
        raise ValueError('Empty candidate')
    identity = metadata.get('experiment', {})
    if task.get('id') and identity.get('task_id') != task['id']:
        raise ValueError('Candidate belongs to another task')
    info = metadata.get('related_apis', metadata)
    if info.get('conversion_status') != 'complete':
        raise ValueError('Candidate has unresolved conversion issues')
    commands = [s['parameters'].get('command') if s['endpoint'].endswith('/command') else s['endpoint'].rsplit('/', 1)[-1] for s in plan]
    return {'before': copy.deepcopy(task.get('related_apis', [])),
            'updates': {'related_apis': plan, 'commands': commands},
            'expected_task_hash': hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
