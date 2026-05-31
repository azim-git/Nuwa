from enum import Enum


class RunStatus(str, Enum):
    DRAFT                      = "draft"
    GENERATING_PILOT           = "generating_pilot"
    AWAITING_PILOT_REVIEW      = "awaiting_pilot_review"
    RUNNING                    = "running"
    AWAITING_MASK_REVIEW       = "awaiting_mask_review"
    AWAITING_CANDIDATE_REVIEW  = "awaiting_candidate_review"
    AWAITING_EXPORT            = "awaiting_export"
    COMPLETED                  = "completed"
    ABORTED                    = "aborted"
    FAILED                     = "failed"


class CandidateStatus(str, Enum):
    PENDING       = "pending"
    ANALYZING     = "analyzing"
    AWAITING_MASK = "awaiting_mask"
    READY         = "ready"
    GENERATING    = "generating"
    EVALUATING    = "evaluating"
    ESCALATED     = "escalated"
    ACCEPTED      = "accepted"
    REJECTED      = "rejected"
    FAILED        = "failed"