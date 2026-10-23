"""Typed failure classification. Every failed reconstruction maps to one DiagnosticCode with advice."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DiagnosticCode(StrEnum):
    TOO_FEW_IMAGES = "TOO_FEW_IMAGES"
    TOO_MANY_IMAGES = "TOO_MANY_IMAGES"
    UNREADABLE_IMAGES = "UNREADABLE_IMAGES"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    DUPLICATE_IMAGES = "DUPLICATE_IMAGES"
    NO_FEATURES = "NO_FEATURES"
    INSUFFICIENT_MATCHES = "INSUFFICIENT_MATCHES"
    NO_INITIAL_PAIR = "NO_INITIAL_PAIR"
    SFM_NO_MODEL = "SFM_NO_MODEL"
    LOW_REGISTRATION = "LOW_REGISTRATION"
    FRAGMENTED_MODEL = "FRAGMENTED_MODEL"
    DENSE_UNAVAILABLE = "DENSE_UNAVAILABLE"
    DENSE_FAILED = "DENSE_FAILED"
    TIMEOUT = "TIMEOUT"
    MEMORY_BUDGET_EXCEEDED = "MEMORY_BUDGET_EXCEEDED"
    DISK_BUDGET_EXCEEDED = "DISK_BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"
    ENGINE_CRASH = "ENGINE_CRASH"
    INTERNAL = "INTERNAL"


# Actionable capture advice per code. Written for a person holding a camera, not for a developer.
ADVICE: dict[DiagnosticCode, str] = {
    DiagnosticCode.TOO_FEW_IMAGES: "Capture at least 15 overlapping photographs of the scene.",
    DiagnosticCode.TOO_MANY_IMAGES: "Reduce the set to the configured maximum; drop near-duplicates.",
    DiagnosticCode.UNREADABLE_IMAGES: "Re-export the images as standard JPEG or PNG and upload again.",
    DiagnosticCode.INPUT_TOO_LARGE: "Reduce total bytes or megapixels per image before uploading.",
    DiagnosticCode.DUPLICATE_IMAGES: "Remove exact duplicates; they add cost and no geometry.",
    DiagnosticCode.NO_FEATURES: (
        "Images lack texture. Avoid blank walls, glass and heavy blur; add textured surroundings."
    ),
    DiagnosticCode.INSUFFICIENT_MATCHES: (
        "Consecutive views share too little content. Move in smaller steps so each photo overlaps "
        "its neighbours by 60-80%."
    ),
    DiagnosticCode.NO_INITIAL_PAIR: (
        "No image pair has enough baseline and overlap to start. Include views with a clear sideways "
        "shift (not pure rotation) and strong overlap."
    ),
    DiagnosticCode.SFM_NO_MODEL: (
        "Structure-from-motion produced no model. Check focus, lighting consistency, and overlap."
    ),
    DiagnosticCode.LOW_REGISTRATION: (
        "Only a small fraction of images could be placed. Re-shoot the weakly overlapping part, "
        "keeping lighting constant and avoiding moving objects."
    ),
    DiagnosticCode.FRAGMENTED_MODEL: (
        "Images split into disconnected groups. Add bridging views that overlap both groups."
    ),
    DiagnosticCode.DENSE_UNAVAILABLE: (
        "The sparse result is still valid. Dense reconstruction needs an NVIDIA GPU with free "
        "memory and the CUDA build of COLMAP on the worker."
    ),
    DiagnosticCode.DENSE_FAILED: "The sparse result is still valid; try a smaller dense image size.",
    DiagnosticCode.TIMEOUT: "Reduce image count or resolution, or raise the job time budget.",
    DiagnosticCode.MEMORY_BUDGET_EXCEEDED: "Reduce image resolution or feature count.",
    DiagnosticCode.DISK_BUDGET_EXCEEDED: "Reduce image count or resolution.",
    DiagnosticCode.CANCELLED: "The job was cancelled before it finished.",
    DiagnosticCode.ENGINE_CRASH: "The reconstruction engine exited unexpectedly; see logs.",
    DiagnosticCode.INTERNAL: "Unexpected internal error; see logs.",
}


class Diagnostic(BaseModel):
    code: DiagnosticCode
    message: str
    advice: str
    stage: str | None = None
    details: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def of(
        cls,
        code: DiagnosticCode,
        message: str,
        *,
        stage: str | None = None,
        **details: object,
    ) -> Diagnostic:
        return cls(code=code, message=message, advice=ADVICE[code], stage=stage, details=details)


class ReconstructionError(Exception):
    """Raised anywhere in the pipeline to abort with a typed diagnostic."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


# Process exit codes for the CLI. 0 = success, 1 = unexpected, 2 = usage; the rest are typed.
EXIT_CODES: dict[DiagnosticCode, int] = {
    code: 10 + i for i, code in enumerate(DiagnosticCode) if code is not DiagnosticCode.INTERNAL
}
EXIT_CODES[DiagnosticCode.INTERNAL] = 1
