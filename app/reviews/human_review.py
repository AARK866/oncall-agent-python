from dataclasses import dataclass

from app.schemas import DiagnosisReport


HIGH_RISK_ACTION_KEYWORDS = (
    "rollback",
    "restart",
    "scale",
    "scale out",
    "扩容",
    "缩容",
    "重启",
    "回滚",
    "切流",
    "降级",
)


@dataclass(frozen=True)
class HumanReviewPlan:
    required: bool
    proposed_actions: list[str]
    risk_reasons: list[str]


def build_human_review_plan(report: DiagnosisReport) -> HumanReviewPlan:
    proposed_actions = [
        recommendation
        for recommendation in report.recommendations
        if _contains_high_risk_keyword(recommendation)
    ]
    if not proposed_actions and _contains_high_risk_keyword(report.summary):
        proposed_actions.append(report.summary)

    risk_reasons = [
        risk
        for risk in report.risks
        if _contains_high_risk_keyword(risk)
    ]
    if proposed_actions and not risk_reasons:
        risk_reasons.append("High-risk operation requires explicit human approval.")

    return HumanReviewPlan(
        required=bool(proposed_actions),
        proposed_actions=proposed_actions,
        risk_reasons=risk_reasons,
    )


def _contains_high_risk_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in HIGH_RISK_ACTION_KEYWORDS)
