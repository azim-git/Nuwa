from enum import Enum


class RunStatus(str, Enum):
    DRAFT                  = "draft"
    AWAITING_MASK_REVIEW   = "awaiting_mask_review"     # SAM3 one-shot done, regions pending approval
    GENERATING_PILOT       = "generating_pilot"
    AWAITING_PILOT_REVIEW  = "awaiting_pilot_review"    # a pilot candidate sits in awaiting_review
    CONSOLIDATING          = "consolidating"            # pilot→full, distil guidance + validate threshold
    RUNNING                = "running"                  # full phase, autonomous
    AWAITING_EXPORT        = "awaiting_export"
    COMPLETED              = "completed"
    ABORTED                = "aborted"
    FAILED                 = "failed"


class CandidateStatus(str, Enum):
    PENDING         = "pending"
    READY           = "ready"            # mask composited + prompt authored
    GENERATING      = "generating"       # inpainting
    EVALUATING      = "evaluating"
    AWAITING_REVIEW = "awaiting_review"  # pilot only — scored, waiting on human
    ACCEPTED        = "accepted"
    REJECTED        = "rejected"
    FAILED          = "failed"