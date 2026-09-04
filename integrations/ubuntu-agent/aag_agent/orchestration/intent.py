"""Deterministic bilingual intent and domain routing for Operational V1.

The classifier selects only project-owned read-only workflow categories. It
never selects an executable, collector, operation, service target, path,
approval, predicate, or command. Natural-language text is data, not runtime
authority.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


MAX_REQUEST_BYTES = 4096


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    playbook_id: str | None
    entity: str | None
    matched_rules: tuple[str, ...]
    clarification_required: bool = False
    domains: tuple[str, ...] = ()
    negated_actions: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "schema": "aag-orchestration-intent-v2",
            "intent": self.intent,
            "playbook_id": self.playbook_id,
            "entity": self.entity,
            "domains": list(self.domains),
            "matched_rules": list(self.matched_rules),
            "negated_actions": list(self.negated_actions),
            "clarification_required": self.clarification_required,
            "classifier": "deterministic-bilingual-rules-v2",
            "classifier_is_authority": False,
        }


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    for mark in ("\u05be", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"):
        value = value.replace(mark, "-")
    return re.sub(r"\s+", " ", value).strip()


def _active_text(value: str) -> str:
    """Remove quoted/code payloads before selecting operational intent."""
    value = re.sub(r"```.*?```", " ", value, flags=re.DOTALL)
    value = re.sub(r"`[^`]*`", " ", value)
    value = re.sub(r'"[^"\n]*"', " ", value)
    value = re.sub(r"'[^'\n]*'", " ", value)
    value = re.sub(r"“[^”\n]*”|„[^”\n]*”|׳[^׳\n]*׳", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _phrase_pattern(term: str) -> re.Pattern[str]:
    pieces = [re.escape(item) for item in re.split(r"[\s_-]+", term) if item]
    body = r"[\s_-]+".join(pieces)
    return re.compile(r"(?<![\w])" + body + r"(?![\w])", re.UNICODE)


def _matches(text: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if _phrase_pattern(term).search(text)]


BRIDGE = (
    "aag-ubuntu-agent-bridge.service", "host bridge", "agent bridge", "bridge",
    "גשר", "הגשר", "בגשר", "גשר הסוכן", "שירות הגשר",
)
PERFORMANCE = (
    "slow", "slowness", "performance", "high cpu", "memory pressure",
    "computer is sluggish", "sustained load", "איטי", "האיטי", "איטיות", "האיטיות", "ביצועים", "הביצועים",
    "מעבד", "המעבד", "זיכרון", "הזיכרון", "לחץ זיכרון", "המחשב נתקע", "עומס",
)
STORAGE = (
    "disk", "storage", "filesystem", "root filesystem", "disk space",
    "storage space", "full disk", "אחסון", "האחסון", "דיסק", "הדיסק", "לדיסק", "מערכת הקבצים",
    "מקום פנוי", "דיסק מלא", "הדיסק מלא",
)
STORAGE_CONSUMERS = (
    "what is consuming disk space", "what consumes disk space", "what takes the most space",
    "largest files", "largest directories", "disk consumers", "storage consumers",
    "מה תופס לי את המקום", "מה תופס לי הכי הרבה מקום", "מה תופס הכי הרבה מקום", "מה תופס הכי הרבה",
    "הקבצים הגדולים", "התיקיות הגדולות", "צרכני האחסון",
)
STORAGE_PROTECTION = (
    "can i delete the large files", "can we delete the large files", "protected storage",
    "אפשר למחוק את הקבצים הגדולים", "האם אפשר למחוק", "מוגן ממחיקה",
)
SUSTAINED_PERFORMANCE = (
    "repeated samples", "multiple samples", "sustained performance", "recurring load",
    "one time spike", "over several measurements", "כמה מדידות", "עומס שחוזר",
    "איטיות רגעית", "בדגימות חוזרות", "מדידות חוזרות", "בכמה מדידות",
)
HISTORY = (
    "previously", "previous", "history", "historical", "last time", "prior attempt",
    "what failed", "what did we try", "rejected approach", "בעבר", "היסטוריה",
    "בפעם הקודמת", "ניסינו", "נכשל בעבר", "דחינו", "מה קרה בעבר",
)
CURRENT = (
    "current", "currently", "right now", "now", "today", "is it happening now",
    "current state", "כעת", "עכשיו", "כרגע", "הנוכחי", "הנוכחית",
    "המצב כעת", "המצב עכשיו", "קורה עכשיו", "קיימת עכשיו",
)
PLAN = (
    "repair plan", "remediation plan", "evidence based remediation plan",
    "evidence-based remediation plan", "make a repair plan", "prepare a repair plan",
    "תוכנית תיקון", "תכנית תיקון", "תוכנית טיפול", "תכנית טיפול",
)
REPAIR_ACTION = (
    "fix", "repair", "remediate", "resolve it", "resolve this", "correct this",
    "תקן", "תתקן", "לתקן", "תפתור", "תטפל בזה", "טפל בזה",
)
DEICTIC_REPAIR = (
    "fix it", "fix this", "repair this", "resolve it", "resolve this", "correct this",
    "תקן את זה", "תתקן את זה", "תפתור את זה", "תטפל בזה", "טפל בזה",
)
NEGATED_REPAIR = (
    "don't fix", "do not fix", "don't repair", "do not repair", "do not remediate",
    "i don't want a repair", "i do not want a repair", "אל תתקן", "אל תפתור",
    "אל תפעיל תיקון", "אל תעשה repair", "אני לא רוצה תיקון", "בלי תיקון",
)
NEGATED_INVESTIGATION = (
    "don't investigate", "do not investigate", "don't check", "do not check",
    "אל תחקור", "אל תבדוק", "לא לבדוק", "בלי בדיקה",
)
NEGATED_HISTORY = (
    "do not use history", "don't use history", "without history", "אל תשתמש בהיסטוריה",
    "בלי היסטוריה",
)
READ_ONLY = (
    "do not change anything", "don't change anything", "do not delete", "don't delete",
    "just check", "only check", "just explain", "only explain", "change nothing",
    "אל תשנה כלום", "אל תמחק", "רק תבדוק", "רק תסביר", "בלי לבצע",
)
CONTINUATION = (
    "continue the task", "resume the task", "continue investigation", "resume investigation",
    "what have you checked", "what is still unknown", "המשך את המשימה",
    "תמשיך את המשימה", "המשך בחקירה", "חדש את המשימה", "מה כבר בדקת",
    "מה עדיין לא ידוע",
)
CURRENT_PID = (
    "current pid", "current bridge pid", "bridge pid", "pid now",
    "pid הנוכחי", "ה-pid הנוכחי", "מזהה התהליך הנוכחי",
)
MAINTENANCE = (
    "maintenance intelligence", "maintenance state", "maintenance intelligence v1",
    "מצב התחזוקה", "תחזוקה", "התחזוקה", "maintenance",
)
SELF_HEALTH = (
    "is the aag agent healthy", "is the agent healthy", "agent self health", "aag health",
    "הסוכן עצמו תקין", "האם הסוכן תקין", "בריאות הסוכן", "מצב הסוכן עצמו",
)
RELEASE = (
    "current aag release", "current agent version", "agent version", "release version",
    "הגרסה הנוכחית של הסוכן", "גרסת הסוכן", "גרסת aag",
)
SECURITY_TEXT = (
    "ignore policy", "ignore previous instructions", "already approved", "run sudo",
    "operation_id=", "service=", "path=/", "pretend this was approved",
    "תתעלם מהמדיניות", "תתעלם מהכללים", "כבר אישרתי", "הפעל sudo",
    "המסמך אומר למחוק", "המסמך אומר להריץ",
)


def _domains(text: str) -> tuple[list[str], dict[str, list[str]]]:
    matches = {
        "bridge": _matches(text, BRIDGE),
        "performance": _matches(text, PERFORMANCE),
        "storage": _matches(text, STORAGE) + _matches(text, STORAGE_CONSUMERS),
    }
    return [name for name, found in matches.items() if found], matches


def classify(request: str) -> IntentDecision:
    if not isinstance(request, str):
        raise ValueError("invalid_orchestration_request")
    try:
        encoded = request.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_orchestration_request") from exc
    if not request.strip() or len(encoded) > MAX_REQUEST_BYTES or "\x00" in request:
        raise ValueError("invalid_orchestration_request")
    if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in request) or "\x7f" in request:
        raise ValueError("invalid_orchestration_request")

    text = _normalize(request)
    active = _active_text(text)
    security = _matches(active, SECURITY_TEXT)
    continuation = _matches(active, CONTINUATION)
    if continuation:
        return IntentDecision("TASK_CONTINUATION", None, None, tuple(continuation + security))

    domains, domain_rules = _domains(active)
    history = _matches(active, HISTORY)
    if _matches(active, NEGATED_HISTORY):
        history = []
    current = _matches(active, CURRENT)
    negated_repair = _matches(active, NEGATED_REPAIR)
    negated_investigation = _matches(active, NEGATED_INVESTIGATION)
    read_only = _matches(active, READ_ONLY)
    plan = _matches(active, PLAN)
    repair = _matches(active, REPAIR_ACTION)
    deictic = _matches(active, DEICTIC_REPAIR)
    negated = tuple(dict.fromkeys(negated_repair + negated_investigation))
    effective_repair = [] if negated_repair else repair

    if len(domains) > 1:
        rules = [item for name in domains for item in domain_rules[name]]
        intent = "REMEDIATION_CLARIFICATION" if effective_repair or plan else "INVESTIGATION_CLARIFICATION"
        return IntentDecision(intent, None, None, tuple(rules + effective_repair + plan + security), True, tuple(domains), negated)

    bridge = domain_rules["bridge"]
    performance = domain_rules["performance"]
    storage = domain_rules["storage"]
    domain = domains[0] if domains else None

    if history and current:
        entity = {"bridge": "entity:bridge", "performance": "entity:aag-agent", "storage": "entity:root-filesystem"}.get(domain)
        return IntentDecision(
            "MIXED_CURRENT_HISTORY", None, entity,
            tuple(history + current + bridge + performance + storage + security),
            False, tuple(domains), negated,
        )

    self_health = _matches(active, SELF_HEALTH)
    if self_health:
        return IntentDecision("AGENT_SELF_HEALTH", None, "entity:aag-agent", tuple(self_health + security), domains=("agent",), negated_actions=negated)
    release = _matches(active, RELEASE)
    if release:
        return IntentDecision("CURRENT_RELEASE", None, "entity:aag-agent", tuple(release), domains=("agent",), negated_actions=negated)

    pid = _matches(active, CURRENT_PID)
    if bridge and pid:
        return IntentDecision("CURRENT_BRIDGE_CONTEXT", None, "entity:bridge", tuple(bridge + pid + security), domains=("bridge",), negated_actions=negated)

    if history:
        return IntentDecision("HISTORICAL_CONTEXT", None, None, tuple(history + security), domains=tuple(domains), negated_actions=negated)

    if negated_investigation:
        return IntentDecision(
            "CONTEXT_QUERY", None, None,
            tuple(negated_investigation + read_only + bridge + performance + storage + security),
            False, tuple(domains), negated,
        )

    protection = _matches(active, STORAGE_PROTECTION)
    if protection:
        return IntentDecision("STORAGE_PROTECTION_CONTEXT", None, "entity:data-mount", tuple(protection + storage + read_only), domains=("storage",), negated_actions=negated)
    consumers = _matches(active, STORAGE_CONSUMERS)
    if consumers:
        return IntentDecision("STORAGE_CONSUMERS", None, "entity:data-mount", tuple(consumers + read_only), domains=("storage",), negated_actions=negated)
    sustained = _matches(active, SUSTAINED_PERFORMANCE)
    if sustained:
        return IntentDecision("SUSTAINED_PERFORMANCE", None, "entity:aag-agent", tuple(sustained + performance + read_only), domains=("performance",), negated_actions=negated)

    if deictic and not domain and not plan and not negated_repair:
        return IntentDecision("DEICTIC_REMEDIATION", None, None, tuple(deictic + security), True)

    if plan or effective_repair:
        candidates = {
            "bridge": ("bridge.readiness_investigation", "entity:bridge"),
            "storage": ("storage.root_pressure_investigation", "entity:root-filesystem"),
            "performance": ("system.performance_investigation", "entity:aag-agent"),
        }
        if domain in candidates:
            playbook, entity = candidates[domain]
            rules = plan + effective_repair + domain_rules[domain] + read_only + security
            return IntentDecision("REMEDIATION_PROPOSAL", playbook, entity, tuple(rules), False, (domain,), negated)
        return IntentDecision(
            "REMEDIATION_CLARIFICATION", None, None,
            tuple(plan + effective_repair + read_only + security), True, (), negated,
        )

    maintenance = _matches(active, MAINTENANCE)
    if maintenance:
        return IntentDecision("MAINTENANCE_CONTEXT", None, "entity:maintenance-v1", tuple(maintenance), domains=("maintenance",), negated_actions=negated)
    if bridge:
        return IntentDecision("BRIDGE_INVESTIGATION", "bridge.readiness_investigation", "entity:bridge", tuple(bridge + read_only + security), domains=("bridge",), negated_actions=negated)
    if performance:
        return IntentDecision("PERFORMANCE_INVESTIGATION", "system.performance_investigation", "entity:aag-agent", tuple(performance + read_only + security), domains=("performance",), negated_actions=negated)
    if storage:
        return IntentDecision("STORAGE_INVESTIGATION", "storage.root_pressure_investigation", "entity:root-filesystem", tuple(storage + read_only + security), domains=("storage",), negated_actions=negated)
    return IntentDecision("CONTEXT_QUERY", None, None, tuple(security), negated_actions=negated)
