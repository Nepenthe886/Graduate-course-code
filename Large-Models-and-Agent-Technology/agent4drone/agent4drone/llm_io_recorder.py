"""Append-only model I/O capture; never serializes model clients or credentials."""
from contextvars import ContextVar
import json
import logging
import threading
import time

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from experiment_tracking import STORE, now

ACTIVE_CALL = ContextVar('active_llm_capture', default=None)
logger = logging.getLogger(__name__)
SECRET_FIELDS = {'api_key', 'authorization', 'headers', 'extra_headers', 'http_client',
                 'http_async_client', 'client', 'async_client', 'api_secret', 'password',
                 'access_token', 'refresh_token', 'x_api_key', 'default_headers'}


def structured(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json', serialize_as_any=True)
    # No fallback to repr/str: unknown objects could contain credentials.
    return json.loads(json.dumps(value, ensure_ascii=False))


def request_data(payload):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k.lower().replace('-', '_') not in SECRET_FIELDS}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    # Preserve task text, tool schemas and arguments, even when fields have credential-like names.
    return structured({k: (v if k in ('messages', 'input', 'tools', 'functions') else clean(v))
                       for k, v in payload.items() if k.lower().replace('-', '_') not in SECRET_FIELDS})


def capture_request(payload):
    active = ACTIVE_CALL.get()
    if active:
        recorder, call_id = active
        recorder.request(call_id, payload)


class RecordedChatOpenAI(ChatOpenAI):
    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        capture_request(payload)
        return payload


class RecordedChatOllama(ChatOllama):
    def _chat_params(self, *args, **kwargs):
        payload = super()._chat_params(*args, **kwargs)
        capture_request(payload)
        return payload


class LLMIORecorder(BaseCallbackHandler):
    run_inline = True

    def __init__(self, context, provider, model):
        self.context = context
        self.run_id = context['run_id']
        self.provider, self.model = provider, model
        self.lock = threading.RLock()
        self.calls = {}
        self.errors = 0
        self.attempt = 0
        self._guard(lambda: STORE.append(self.run_id, 'llm_calls.jsonl', {
            'schema_version': 1, 'event': 'capture_started', 'at': now(),
            'run_id': self.run_id, 'boundary': 'model_adapter',
            'sdk_internal_retries_visible': False}))
        self._summary()

    def _guard(self, action):
        try:
            action()
            return True
        except Exception as exc:
            self.errors += 1
            self.context.setdefault('recording_errors', []).append('llm_io:' + type(exc).__name__)
            logger.error('Model I/O recording failed run_id=%s error_type=%s', self.run_id, type(exc).__name__)
            return False

    def _event(self, call_id, event, **data):
        state = self.calls[call_id]
        return self._guard(lambda: STORE.append(self.run_id, 'llm_calls.jsonl', {
            'schema_version': 1, 'event': event, 'at': now(),
            'experiment_id': self.context.get('ids', {}).get('experiment_id'),
            'run_id': self.run_id, 'call_id': call_id, 'call_index': state['index'],
            **data}))

    def _summary(self):
        incomplete = sum(not s['ended'] or not s['request'] or s.get('partial', False) for s in self.calls.values())
        self._guard(lambda: STORE.save(self.run_id, llm_io_recording=True,
            llm_calls_file='llm_calls.jsonl', llm_io_schema_version=1,
            llm_call_count=len(self.calls), llm_recording_errors=self.errors,
            llm_incomplete_calls=incomplete,
            llm_recording_status='partial' if self.errors or incomplete else 'complete'))

    def on_chat_model_start(self, serialized, messages, *, run_id, invocation_params=None, **kwargs):
        with self.lock:
            call_id = str(run_id)
            if call_id in self.calls:
                return
            previous_id = next(reversed(self.calls), None)
            previous = self.calls.get(previous_id, {})
            retry_of = previous_id if previous.get('partial') or previous.get('attempt', self.attempt) < self.attempt else None
            self.calls[call_id] = {'attempt': self.attempt, 'index': len(self.calls) + 1, 'start': time.monotonic(),
                'ended': False, 'request': False, 'chunk': None}
            ACTIVE_CALL.set((self, call_id))
            self._guard(lambda: self._event(call_id, 'call_started', provider=self.provider,
                model=self.model, agent_attempt=self.attempt, retry_of_call_id=retry_of,
                parent_call_id=str(kwargs['parent_run_id']) if kwargs.get('parent_run_id') else None,
                input={'messages': [[structured(m) for m in batch] for batch in messages],
                       'invocation_params': request_data(invocation_params or {})},
                input_boundary='framework_messages', request_payload_pending=True))
            self._summary()

    def request(self, call_id, payload):
        with self.lock:
            state = self.calls.get(call_id)
            if not state or state['ended']:
                return
            def save():
                if self._event(call_id, 'call_request', request=request_data(payload), input_boundary='provider_payload'):
                    state['request'] = True
            self._guard(save)

    def on_llm_new_token(self, token, *, run_id, chunk=None, **kwargs):
        with self.lock:
            state = self.calls.get(str(run_id))
            if state and not state['ended'] and chunk is not None:
                def merge():
                    state['chunk'] = chunk if state['chunk'] is None else state['chunk'] + chunk
                self._guard(merge)

    def _finish(self, call_id, event, response=None, error=None):
        with self.lock:
            state = self.calls.get(call_id)
            if not state or state['ended']:
                return
            state['ended'] = True
            state['partial'] = event != 'call_finished'
            def save():
                output = structured(response) if response is not None else None
                partial = structured(state['chunk']) if state['chunk'] is not None and event != 'call_finished' else None
                self._event(call_id, event, output=output, partial_output=partial,
                    partial=state['partial'], error_type=type(error).__name__ if error else None,
                    duration_ms=round((time.monotonic() - state['start']) * 1000, 3))
            self._guard(save)
            active = ACTIVE_CALL.get()
            if active and active == (self, call_id):
                ACTIVE_CALL.set(None)
            self._summary()

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish(str(run_id), 'call_finished', response=response)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(str(run_id), 'call_failed', error=error)

    def close(self):
        with self.lock:
            for call_id, state in self.calls.items():
                if not state['ended']:
                    self._finish(call_id, 'call_interrupted')
            self._summary()
            if self.errors or any(s.get('partial') or not s['request'] for s in self.calls.values()):
                self.context.setdefault('recording_errors', []).append('llm_io:incomplete')
