"""Schema-3 log storage; no execution locking, cancellation or restart recovery."""
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
import json
import os
import re
import threading
import uuid

from logging_config import LOG_DIR, SERVICE_INSTANCE_ID
from related_api_exporter import export_run

ACTIVE_RUN = ContextVar('active_experiment_run', default=None)
TOOL_CALL_ID = ContextVar('active_tool_call_id', default=None)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(re.escape(prefix) + r'[A-Za-z0-9][A-Za-z0-9_.-]{0,123}', value):
        raise ValueError('Invalid experiment/run identity')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    os.replace(temp, path)


class RunStore:
    def __init__(self, root=None, service_instance_id=None):
        self.root = Path(root or LOG_DIR / 'batches')
        self.service_instance_id = service_instance_id or SERVICE_INSTANCE_ID
        self.lock = threading.RLock()

    @property
    def default_batch_id(self):
        return 'eid-' + str(uuid.uuid5(uuid.NAMESPACE_URL, self.service_instance_id))

    def batch_path(self, experiment_id):
        safe_id(experiment_id, 'eid-')
        matches = [path.parent for path in self.root.glob('*/batch.json')
                   if json.loads(path.read_text(encoding='utf-8'))['experiment_id'] == experiment_id]
        if len(matches) > 1:
            raise ValueError('Duplicate experiment_id in batch directories')
        if matches:
            return matches[0]
        label = 'default' if experiment_id == self.default_batch_id else experiment_id
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        directory = self.root / f'{timestamp}-{label}'
        while directory.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            directory = self.root / f'{timestamp}-{label}'
        return directory

    def manifests(self):
        return self.root.glob('*/runs/rid-*/manifest.json')

    def path(self, run_id):
        # The directory tree is the rebuildable index; no absolute paths are persisted.
        matches = list(self.root.glob('*/runs/' + safe_id(run_id, 'rid-') + '/manifest.json'))
        if len(matches) > 1:
            raise ValueError('Duplicate run_id in batch directories')
        if not matches:
            raise KeyError(run_id)
        return matches[0].parent

    def read(self, run_id):
        try:
            safe_id(run_id, 'rid-')
        except ValueError:
            return None
        try:
            return json.loads((self.path(run_id) / 'manifest.json').read_text(encoding='utf-8'))
        except KeyError:
            return None

    def ensure_batch(self, experiment_id, note=None, **metadata):
        with self.lock:
            path = self.batch_path(experiment_id) / 'batch.json'
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                if note is not None and data.get('note') != note:
                    raise ValueError('Experiment note differs from frozen batch note')
                return data
            data = {'schema_version': 3, 'experiment_id': experiment_id, 'note': note,
                    'created_at': now(), **metadata}
            atomic_json(path, data)
            return data

    def save(self, run_id, **updates):
        with self.lock:
            data = self.read(run_id)
            created = data is None
            if created:
                experiment = dict(updates.get('experiment') or {})
                batch_id = experiment.setdefault('experiment_id', self.default_batch_id)
                experiment['run_id'] = run_id
                self.ensure_batch(batch_id, source='agent_default')
                directory = self.batch_path(batch_id) / 'runs' / safe_id(run_id, 'rid-')
                data = {'schema_version': 3, 'run_id': run_id, 'experiment': experiment,
                        'service_instance_id': self.service_instance_id, 'batch_file': '../../batch.json'}
                updates['experiment'] = experiment
            else:
                directory = self.path(run_id)
                supplied_batch = (updates.get('experiment') or {}).get('experiment_id')
                if supplied_batch and supplied_batch != data['experiment']['experiment_id']:
                    raise ValueError('Cannot change run batch identity')
            data.update(updates)
            if data.get('execution_finished_at') or data.get('execution_status') in ('interrupted', 'aborted_before_start'):
                try:
                    exported = export_run(directory)
                    if data.get('execution_status') in ('interrupted', 'cancelled', 'failed') or (data.get('result') or {}).get('recording_status') == 'partial':
                        exported['related_apis']['conversion_status'] = 'conversion_required'
                        exported['related_apis']['issues'].append({'reason': 'Run interrupted/failed or recording incomplete; review required'})

                    data.update(exported)
                    data.pop('related_apis_error', None)
                except (ValueError, TypeError, OSError) as exc:
                    data.update(related_apis={'conversion_status': 'error'}, related_apis_error=str(exc))
            atomic_json(directory / 'manifest.json', data)
            if created:
                self._append(directory.parent.parent / 'runs.jsonl', {
                    'run_id': run_id, 'session_id': data['experiment'].get('session_id'),
                    'task_id': data['experiment'].get('task_id'),
                    'manifest': 'runs/' + run_id + '/manifest.json'})
            return data


    @staticmethod
    def _append(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False, default=str) + '\n')
            stream.flush()

    def append(self, run_id, filename, data):
        with self.lock:
            self._append(self.path(run_id) / filename, data)

    def event(self, run_id, event, **details):
        self.append(run_id, 'events.jsonl', {'at': now(), 'event': event, **details})

STORE = RunStore()


def record_request(record):
    context = ACTIVE_RUN.get()
    if context:
        STORE.append(context['run_id'], 'requests.jsonl', {**context['ids'], 'tool_call_id': TOOL_CALL_ID.get(), **record})

def guarded_record(action, context=None):
    """Logging failures never change request, model or job execution."""
    try:
        return action()
    except Exception as exc:
        context = context if context is not None else ACTIVE_RUN.get()
        if context is not None:
            context.setdefault('recording_errors', []).append(type(exc).__name__)
        import logging
        logging.getLogger(__name__).error('Experiment recording failed error_type=%s', type(exc).__name__)
        return None


def create_run(command, experiment=None, job_id=None, execution_status='running'):
    experiment = dict(experiment or {})
    note = experiment.pop('note', None)
    with STORE.lock:
        requested = experiment.get('run_id')
        run_id = requested or 'rid-' + str(uuid.uuid4())
        # Logging must not deduplicate or suppress an existing execution.
        if STORE.read(run_id):
            run_id = 'rid-' + str(uuid.uuid4())
        experiment['run_id'] = run_id
        batch_id = experiment.setdefault('experiment_id', STORE.default_batch_id)
        # Notes are frozen once per batch, but never gate execution.
        batch_exists = (STORE.batch_path(batch_id) / 'batch.json').exists()
        STORE.ensure_batch(batch_id, note=None if batch_exists else note,
                           source='client' if batch_id != STORE.default_batch_id else 'agent_default',
                           previous_experiment_id=experiment.get('previous_experiment_id'), start_reason=experiment.get('start_reason'))
        data = STORE.save(run_id, experiment=experiment, command_id=str(uuid.uuid4()),
                          job_id=job_id, command=command, created_at=now(),
                          execution_status=execution_status, execution_finished_at=None,
                          requested_run_id=requested if requested != run_id else None)
        context = {'run_id': run_id, 'command_id': data['command_id'], 'experiment': experiment,
                   'ids': {k: data.get(k) for k in ('run_id', 'job_id', 'command_id')}}
        context['ids'].update({k: experiment.get(k) for k in ('experiment_id', 'session_id', 'task_id')})
        context['ids']['service_instance_id'] = STORE.service_instance_id
        (STORE.path(run_id) / 'requests.jsonl').touch(exist_ok=True)
        STORE.event(run_id, 'accepted')
        return context


def start_run(context):
    if context:
        guarded_record(lambda: STORE.save(context['run_id'], execution_status='running', started_at=now()), context)
        guarded_record(lambda: STORE.event(context['run_id'], 'started'), context)


def finish_run(context, result=None, execution_status='completed', error=None):
    if not context:
        return
    from serialization_utils import make_json_safe
    updates = {'execution_status': execution_status, 'execution_finished_at': now(),
               'recording_status': 'partial' if context.get('recording_errors') else 'complete'}
    if result is not None:
        updates['result'] = make_json_safe(result)
    if error is not None:
        updates['error'] = str(error)
    guarded_record(lambda: STORE.save(context['run_id'], **updates), context)
    guarded_record(lambda: STORE.event(context['run_id'], 'execution_exited'), context)


def result_artifacts(result, context):
    if not context:
        return result
    artifacts = {'manifest': f"/agent/runs/{context['run_id']}",
                 'request_history': f"/agent/runs/{context['run_id']}/artifacts/request_history",
                 'events': f"/agent/runs/{context['run_id']}/artifacts/events",
                 'related_apis': f"/agent/runs/{context['run_id']}/artifacts/related_apis"}
    data = guarded_record(lambda: STORE.read(context['run_id']), context) or {}
    for field, artifact in [('llm_calls_file', 'llm_calls'), ('toolchain_file', 'toolchain')]:
        if data.get(field):
            artifacts[artifact] = f"/agent/runs/{context['run_id']}/artifacts/{artifact}"
    return {**result, 'run_id': context['run_id'], 'command_id': context.get('command_id') or data.get('command_id'),
            'experiment': context.get('experiment') or data.get('experiment'), 'artifacts': artifacts,
            'recording_status': 'partial' if context.get('recording_errors') else 'complete'}
