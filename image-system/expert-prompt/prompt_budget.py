"""Opt-in prompt budgeting. No model loads, prompt rewriting or execution.

The 512-token operating envelope is the BFL reference conditioning length,
not a claim that installed ComfyUI has a 512-token architectural maximum.
Deployment and live acceptance are recorded separately from this budget profile.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import importlib.util
import sys
import threading
from pathlib import Path
import re
from typing import Callable

LEGACY_UTF16_UNITS = 1800
TRANSPORT_UTF16_UNITS = 8000  # Existing runtime.normalizeTask transport ceiling.
TRANSPORT_UTF8_BYTES = TRANSPORT_UTF16_UNITS * 4
OPERATING_TOKENS = 512
SUPPORTED_ROUTES = frozenset({'generate', 'transform-subject'})
TEMPLATE = '<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
PRETOKENIZE = r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
PIN_FILE = Path(__file__).with_name('engine-budget-profile.json')
_TOKENIZER_IMPORT_LOCK = threading.RLock()


class BudgetError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details


def utf16_units(text: str) -> int:
    try:
        return len(text.encode('utf-16-le', 'strict')) // 2
    except UnicodeEncodeError as exc:
        raise BudgetError('PROMPT_INVALID_UNICODE', 'Unpaired Unicode surrogate.') from exc


def _text(text: str, maximum: int) -> str:
    if not isinstance(text, str) or not text.strip():
        raise BudgetError('PROMPT_REQUIRED', 'A nonempty authored prompt is required.')
    if utf16_units(text) > maximum:
        raise BudgetError('PROMPT_TRANSPORT_LIMIT', 'Prompt exceeds the unchanged transport bound.', limit=maximum)
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text):
        raise BudgetError('PROMPT_INVALID_CONTROL', 'Unsafe control character in prompt.')
    return text


class KleinTokenCounter:
    """Exact local tokenizer adapter, pinned to the installed Comfy implementation.

    Uses only the Rust tokenizers package and vocabulary data. Does not import
    torch/transformers, load weights, use accelerator memory, or access network.
    Token IDs are compared against both installed Comfy Klein tokenizer classes
    in the integration tests. Dependency or source drift is a closed failure.
    """
    def __init__(self, comfy_root: Path, profile_file: Path = PIN_FILE):
        profile = json.loads(profile_file.read_text())
        self.profile = profile
        self.comfy_root = Path(comfy_root)
        for relative, expected in profile['installed_source_sha256'].items():
            file = self.comfy_root / relative
            try:
                actual = hashlib.sha256(file.read_bytes()).hexdigest()
            except OSError as exc:
                raise BudgetError('TOKENIZER_UNAVAILABLE', 'Required local tokenizer evidence is unavailable.') from exc
            if actual != expected:
                raise BudgetError('TOKENIZER_SOURCE_DRIFT', 'Pinned encoder/tokenizer source changed.', file=relative)
        try:
            with _TOKENIZER_IMPORT_LOCK:
                if importlib.util.find_spec('tokenizers') is None:
                    # Reuse only Comfy's installed stable-ABI package, without
                    # adding its whole environment to the module search path.
                    packages = list((self.comfy_root/'venv/lib').glob('python*/site-packages/tokenizers/__init__.py'))
                    if len(packages) != 1:
                        raise ImportError('Expected one installed tokenizer package')
                    spec = importlib.util.spec_from_file_location('tokenizers', packages[0])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules['tokenizers'] = module
                    try: spec.loader.exec_module(module)
                    except BaseException:
                        sys.modules.pop('tokenizers', None)
                        raise
                from tokenizers import Tokenizer, AddedToken, Regex, models, normalizers, pre_tokenizers, decoders
        except ImportError as exc:
            raise BudgetError('TOKENIZER_UNAVAILABLE', 'Exact tokenizer dependency is unavailable; no character estimate fallback.') from exc
        base = self.comfy_root / 'comfy/text_encoders/qwen25_tokenizer'
        tokenizer = Tokenizer(models.BPE.from_file(str(base/'vocab.json'), str(base/'merges.txt')))
        tokenizer.normalizer = normalizers.NFC()
        tokenizer.pre_tokenizer = pre_tokenizers.Sequence([
            pre_tokenizers.Split(Regex(PRETOKENIZE), behavior='isolated', invert=False),
            pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False),
        ])
        tokenizer.decoder = decoders.ByteLevel()
        config = json.loads((base/'tokenizer_config.json').read_text())
        for index, spec in sorted(config['added_tokens_decoder'].items(), key=lambda row: int(row[0])):
            token = AddedToken(spec['content'], single_word=spec.get('single_word', False),
                               lstrip=spec.get('lstrip', False), rstrip=spec.get('rstrip', False),
                               normalized=spec.get('normalized', False), special=spec.get('special', False))
            tokenizer.add_tokens([token])
            if tokenizer.token_to_id(spec['content']) != int(index):
                raise BudgetError('TOKENIZER_VOCAB_DRIFT', 'Special token IDs do not match the installed encoder.')
        self.tokenizer = tokenizer
        self.special_strings = frozenset(spec['content'] for spec in config['added_tokens_decoder'].values())

    def _ids(self, text: str) -> list[int]:
        # Mirrors escape_important + unescape_important with weights disabled.
        effective = text.replace(r'\)', ')').replace(r'\(', '(')
        return self.tokenizer.encode(TEMPLATE.format(effective), add_special_tokens=False).ids

    def token_ids_for_parity(self, text: str) -> list[int]:
        return self._ids(text)

    def count(self, text: str) -> int:
        _text(text, TRANSPORT_UTF16_UNITS)
        if len(text.encode('utf-8')) > TRANSPORT_UTF8_BYTES:
            raise BudgetError('PROMPT_TRANSPORT_LIMIT', 'UTF-8 prompt transport bound exceeded.')
        if any(s in text for s in self.special_strings) or re.search(r'(?:^|\s)embedding:', text):
            raise BudgetError('PROMPT_ENCODER_CONTROL', 'Prompt contains encoder control or embedding syntax.')
        return len(self._ids(text))


@dataclass(frozen=True)
class Fragment:
    id: str
    priority: int
    text: str


def _check_fragments(fragments: tuple[Fragment, ...]) -> None:
    ids = set()
    for f in fragments:
        if not isinstance(f, Fragment) or not isinstance(f.id, str) or not f.id or f.id in ids:
            raise BudgetError('PLAN_INVALID', 'Plan requires unique named fragments.')
        if type(f.priority) is not int or not 0 <= f.priority <= 4:
            raise BudgetError('PLAN_INVALID', 'Plan priorities must be P0 through P4.')
        _text(f.text, TRANSPORT_UTF16_UNITS)
        ids.add(f.id)


def fit_plan(fragments: tuple[Fragment, ...], counter: KleinTokenCounter, *, route='generate') -> dict:
    """Budget a pre-authored plan, never truncate final prose or rewrite facts.

    P0/P1/P2 and P3 remain intact. Remove whole P4 fragments, last first, only
    on overflow. If that is insufficient, request author revision of P3.
    Any P0-P2 overflow is explicit; no lossy fallback is returned as a prompt.
    Joining here is measurement of the plan, not final prose synthesis.
    """
    if route not in SUPPORTED_ROUTES:
        raise BudgetError('EXPERT_ROUTE_UNSUPPORTED', 'This route has no verified lossless Expert prompt boundary.')
    _check_fragments(fragments)
    kept, removed = list(fragments), []
    def measure(items):
        if not items:
            return len(counter.token_ids_for_parity(''))
        text = '\n'.join(f.text for f in items)
        # Do not tokenize beyond the transport ceiling just to rank a plan.
        return None if utf16_units(text) > TRANSPORT_UTF16_UNITS else counter.count(text)
    protected = [f for f in kept if f.priority <= 2]
    protected_tokens = measure(protected)
    if protected_tokens is None or protected_tokens > OPERATING_TOKENS:
        return {'status':'PROTECTED_CONTENT_EXCEEDS_BUDGET', 'limit':OPERATING_TOKENS,
                'tokens':protected_tokens, 'preserved_ids':[f.id for f in fragments], 'removed_ids':[],
                'final_prompt':None, 'action':'Do not generate. Preserve all invariants and report the downstream operating budget.'}
    count = measure(kept)
    while count is None or count > OPERATING_TOKENS:
        index = next((i for i in range(len(kept)-1, -1, -1) if kept[i].priority == 4), None)
        if index is None:
            break
        removed.append(kept.pop(index).id)
        count = measure(kept)
    return {'status':'FIT' if count is not None and count <= OPERATING_TOKENS else 'AUTHOR_REVISION_REQUIRED',
            'limit':OPERATING_TOKENS, 'tokens':count,
            'preserved_ids':[f.id for f in kept], 'removed_ids':removed,
            'fragments':[{'id':f.id,'priority':f.priority,'text':f.text} for f in kept],
            'final_prompt':None,
            'action':'Synthesize coherent prose and validate it exactly.' if count is not None and count <= OPERATING_TOKENS else
                     'Author a more economical P3 description; retain all P0/P1/P2 facts and style anchors. No automatic compression.'}


def validate_final(prompt: str, *, counter: KleinTokenCounter, protected_fragments: tuple[Fragment, ...],
                   selected_style: str, expected_style: str, route='generate',
                   expected_sha256: str | None = None, semantic_protection: bool = False) -> dict:
    if route not in SUPPORTED_ROUTES:
        raise BudgetError('EXPERT_ROUTE_UNSUPPORTED', 'Frozen identity/upscale contracts are not an Expert prose route.')
    _text(prompt, TRANSPORT_UTF16_UNITS)
    _check_fragments(protected_fragments)
    if not protected_fragments or not any(f.priority == 0 for f in protected_fragments):
        raise BudgetError('PLAN_INVALID', 'Protected user invariants are required.')
    if selected_style != expected_style or not isinstance(selected_style, str) or not selected_style:
        raise BudgetError('EXPERT_STYLE_CHANGED', 'The resolved style must match the selected canonical style.')
    if selected_style != 'auto-unresolved' and not any(f.priority == 2 for f in protected_fragments):
        raise BudgetError('PLAN_INVALID', 'Selected style requires protected P2 recognition anchors.')
    # Exact fragments are explicit compiler obligations. This does not pretend
    # to prove all natural-language semantics or replace the existing gate.
    missing = [f.id for f in protected_fragments if f.priority <= 2 and f.text not in prompt]
    if missing and not semantic_protection:
        raise BudgetError('EXPERT_PROTECTED_CONTENT_MISSING', 'Protected plan text is missing.', fragments=missing)
    count = counter.count(prompt)
    if count > OPERATING_TOKENS:
        raise BudgetError('EXPERT_TOKEN_BUDGET_EXCEEDED', 'No prompt was truncated or submitted.', tokens=count, limit=OPERATING_TOKENS)
    digest = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise BudgetError('EXPERT_PROMPT_CHANGED', 'Prompt changed after its validation.')
    return {'prompt':prompt, 'sha256':digest, 'tokens_including_template':count,
            'operating_token_limit':OPERATING_TOKENS, 'selected_style':selected_style,
            'protected_fragment_ids':[f.id for f in protected_fragments if f.priority <= 2],
            'truncated':False, 'route':route, **({'semantic_review_required':True} if semantic_protection else {})}


def validate_mode(prompt: str, *, expert: bool, legacy_validator: Callable, **expert_options):
    """OFF delegates to the existing validator without touching prompt or options."""
    if type(expert) is not bool:
        raise BudgetError('EXPERT_MODE_INVALID', 'Expert mode must be an explicit boolean.')
    if not expert:
        return legacy_validator(prompt)
    return validate_final(prompt, **expert_options)


def model_context_budget(*, input_tokens: int, output_tokens: int, context_tokens: int,
                         output_cap: int, tokenizer_identity: str) -> dict:
    """Use provider-measured tokens, never image-encoder counts for LLM context."""
    values = [input_tokens,output_tokens,context_tokens,output_cap]
    if any(type(x) is not int or x <= 0 for x in values) or not tokenizer_identity:
        raise BudgetError('MODEL_CONTEXT_UNVERIFIED', 'Verified provider token counts and context bounds are required.')
    if output_tokens > output_cap or input_tokens + output_tokens > context_tokens:
        raise BudgetError('MODEL_CONTEXT_BUDGET_EXCEEDED', 'Context/output space is insufficient; no model request was sent.')
    return {'input_tokens':input_tokens,'output_tokens':output_tokens,'context_tokens':context_tokens,
            'output_cap':output_cap,'tokenizer_identity':tokenizer_identity,'fits':True}


def validate_completion_finish(finish_reason: str) -> None:
    """A syntactically valid fragment from a token-limited response is not complete."""
    if finish_reason not in {'stop', 'tool_calls'}:
        raise BudgetError('EXPERT_COMPLETION_INCOMPLETE',
                          'Workspace model did not finish its prompt; nothing may be submitted.',
                          finish_reason=finish_reason)
