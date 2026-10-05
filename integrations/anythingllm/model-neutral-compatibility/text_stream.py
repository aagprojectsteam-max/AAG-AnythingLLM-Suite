"""Incremental ordinary text relay; tool responses use whole-response validation."""
from copy import deepcopy
import json

from compatibility import (
    CompatibilityError, GENERIC_INTERNAL_RE, RAW_CONTROL_RE, RAW_UNUSED_RE,
    stable_json, validate_ordinary_response,
)

MAX_EVENT_BYTES = 2_100_000


def encode_event(value):
    return ('data: ' + stable_json(value) + '\n\n').encode('utf-8')


def read_events(response):
    parts = []
    size = 0
    while True:
        line = response.readline(MAX_EVENT_BYTES + 1)
        if not line:
            raise CompatibilityError('STREAM_INCOMPLETE', 'The inference stream ended without a completion marker.')
        size += len(line)
        if size > MAX_EVENT_BYTES:
            raise CompatibilityError('STREAM_EVENT_TOO_LARGE', 'An inference stream event exceeded the size limit.')
        line = line.rstrip(b'\r\n')
        if not line:
            if not parts:
                size = 0
                continue
            data = b'\n'.join(parts)
            parts = []
            size = 0
            if data == b'[DONE]':
                return
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError) as error:
                raise CompatibilityError('STREAM_JSON_INVALID', 'The inference stream contains malformed JSON.') from error
            if not isinstance(value, dict):
                raise CompatibilityError('STREAM_JSON_INVALID', 'The inference stream event is not an object.')
            if value.get('error'):
                raise CompatibilityError('UPSTREAM_STREAM_ERROR', 'The inference runtime reported a stream error.')
            yield value
        elif line.startswith(b'data:'):
            parts.append(line[5:].removeprefix(b' '))


def safe_prefix_end(text):
    try:
        size = len(text.encode('utf-8'))
    except UnicodeError as error:
        raise CompatibilityError('STREAM_TEXT_INVALID', 'The inference stream contains invalid Unicode.') from error
    if size > 2_000_000:
        raise CompatibilityError('OUTPUT_TOO_LARGE', 'The model output exceeded the size limit.')
    for pattern, code in [(RAW_UNUSED_RE, 'RAW_UNUSED_TOKEN'), (RAW_CONTROL_RE, 'TOOL_CONTROL_TOKEN_LEAK'), (GENERIC_INTERNAL_RE, 'INTERNAL_SPECIAL_TOKEN_LEAK')]:
        if pattern.search(text):
            raise CompatibilityError(code, 'The model emitted an internal control token.')
    if any(ord(char) < 9 for char in text):
        raise CompatibilityError('BINARY_CONTROL_OUTPUT', 'The model emitted a binary control character.')
    # Do not expose an internal token split across upstream chunks.
    opening = text.rfind('<')
    closing = text.rfind('>')
    return opening if opening > closing else len(text)


def validated_text_events(events):
    texts = {'content': '', 'reasoning_content': '', 'reasoning': ''}
    sent = dict.fromkeys(texts, 0)
    base = None
    role_sent = False
    finish = None
    usage = None
    timings = None
    for event in events:
        if base is None:
            base = {key: event[key] for key in ('id', 'object', 'created', 'model') if key in event}
        if event.get('usage') is not None:
            usage = event['usage']
        if event.get('timings') is not None:
            timings = event['timings']
        choices = event.get('choices')
        if not isinstance(choices, list):
            raise CompatibilityError('STREAM_CHOICE_INVALID', 'The inference stream has no choice list.')
        if not choices:
            continue
        if len(choices) != 1 or not isinstance(choices[0], dict) or choices[0].get('index') != 0:
            raise CompatibilityError('STREAM_CHOICE_INVALID', 'The text relay requires one completion choice.')
        choice = choices[0]
        delta = choice.get('delta')
        if not isinstance(delta, dict):
            raise CompatibilityError('STREAM_DELTA_INVALID', 'The inference stream has an invalid text delta.')
        if delta.get('tool_calls') or delta.get('function_call'):
            raise CompatibilityError('UNEXPECTED_STREAM_TOOL', 'Tool calls require complete schema validation.')
        if finish is not None:
            raise CompatibilityError('STREAM_AFTER_FINISH', 'The inference stream continued after completion.')
        if delta.get('role') not in (None, 'assistant'):
            raise CompatibilityError('STREAM_ROLE_INVALID', 'The inference stream has an invalid role.')
        outgoing = {}
        for key in texts:
            value = delta.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise CompatibilityError('STREAM_DELTA_INVALID', 'The inference stream contains non-text content.')
                texts[key] += value
                end = safe_prefix_end(texts[key])
                if any(char.isalnum() for char in texts[key][:end]):
                    if end > sent[key]:
                        outgoing[key] = texts[key][sent[key]:end]
                        sent[key] = end
        if outgoing:
            if not role_sent:
                outgoing['role'] = 'assistant'
                role_sent = True
            yield {**base, 'choices': [{'index': 0, 'delta': outgoing, 'finish_reason': None}]}
        if choice.get('finish_reason') is not None:
            if choice['finish_reason'] not in ('stop', 'length', 'content_filter'):
                raise CompatibilityError('STREAM_FINISH_INVALID', 'The text completion has an invalid finish reason.')
            finish = deepcopy(event)
    if finish is None:
        raise CompatibilityError('STREAM_INCOMPLETE', 'The inference stream did not finish its completion.')
    validate_ordinary_response({'choices': [{'message': texts}]})
    tail = {key: text[sent[key]:] for key, text in texts.items() if len(text) > sent[key]}
    if tail:
        yield {**base, 'choices': [{'index': 0, 'delta': tail, 'finish_reason': None}]}
    finish['choices'][0]['delta'] = {}
    # AnythingLLM stops reading at finish_reason, before a separate usage event.
    if usage is not None:
        finish['usage'] = usage
    if timings is not None:
        finish['timings'] = timings
    yield finish
    if usage is not None:
        yield {**base, 'choices': [], 'usage': usage, **({'timings': timings} if timings is not None else {})}
