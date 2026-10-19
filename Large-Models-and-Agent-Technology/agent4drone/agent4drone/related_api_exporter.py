"""Produce a flat, traceable candidate; never modify original logs or tasks."""
import hashlib
import json
import os
import uuid
from pathlib import Path
from related_api_protocol import requests_to_plan


def export_run(directory):
    directory = Path(directory)
    source = directory / 'requests.jsonl'
    records = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()] if source.exists() else []
    plan, issues, indices = requests_to_plan(records)
    events = directory / 'events.jsonl'
    if events.exists():
        started = {e.get('index') for line in events.read_text().splitlines() if line.strip() for e in [json.loads(line)] if e.get('event') == 'request_started'}
        missing = started - {r.get('index') for r in records}
        for index in sorted(missing):
            issues.append({'index': index, 'reason': 'Request started without completed log record'})
    path = directory / 'related_apis.jsonl'
    payload = ''.join(json.dumps(step, ensure_ascii=False) + '\n' for step in plan)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(payload, encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {'related_apis': {'schema_version': 1, 'file': path.name, 'source': 'agent_trace',
            'conversion_status': 'conversion_required' if issues else 'complete' if plan else 'empty',
            'recording_status': 'partial' if any('without completed' in i['reason'] for i in issues) else 'complete',
            'sha256': hashlib.sha256(payload.encode()).hexdigest(), 'source_indices': indices,
            'issues': issues, 'count': len(plan),
            'replay_validation': 'not_run'}}
