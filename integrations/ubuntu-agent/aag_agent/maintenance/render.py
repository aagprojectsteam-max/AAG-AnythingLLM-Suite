"""Concise Hebrew presentation separated from low-level collection."""

from __future__ import annotations

from typing import Any


def render_hebrew(result: dict[str, Any]) -> dict[str, Any]:
    tool = result.get("tool", "maintenance")
    completeness = result.get("completeness", {})
    status = completeness.get("status", "failed")
    findings = result.get("findings", [])
    recommendations = result.get("recommendations", [])
    unknown = result.get("result", {}).get("unknown_areas", [])
    if tool == "system.health":
        overall = result.get("result", {}).get("overall_status", "unknown")
        headline = f"מצב המחשב: {overall}. כיסוי הבדיקה: {result.get('result', {}).get('coverage_percent', 0)}%."
    elif tool == "performance.snapshot":
        inferences = result.get("inferences", [])
        headline = inferences[0]["summary"] if inferences else "לא נאספו מספיק נתונים לזיהוי צוואר בקבוק."
    elif tool == "maintenance.plan":
        items = result.get("result", {}).get("items", [])
        estimate = result.get("result", {}).get("estimated_reclaimable_bytes", 0)
        headline = f"הוכנה תוכנית תחזוקה יבשה עם {len(items)} פריטים; אומדן שמרני: {estimate} בתים. לא בוצעה מחיקה."
    elif tool.startswith("storage."):
        headline = f"בדיקת האחסון הסתיימה במצב {status}."
    else:
        headline = f"בדיקת התחזוקה הסתיימה במצב {status}."
    if status != "complete":
        headline += " הכיסוי חלקי ואין להסיק שמצב שלא נבדק הוא תקין."
    return {
        "language": "he",
        "summary": headline,
        "sections": {
            "עובדה שנמדדה": [item.get("value") for item in result.get("observations", [])[:5]],
            "מסקנה": [item.get("summary") for item in result.get("inferences", [])[:5]],
            "רמת ביטחון": [item.get("confidence") for item in result.get("inferences", [])[:5]],
            "ממצא": [item.get("summary") for item in findings[:10]],
            "המלצה": [item.get("summary") for item in recommendations[:10]],
            "סיכון": [item.get("risk") for item in recommendations[:10]],
            "מה לא נבדק": list(unknown) + list(completeness.get("limits_reached", [])),
        },
        "mutations": "לא בוצעו שינויים או פעולות ניקוי.",
    }

