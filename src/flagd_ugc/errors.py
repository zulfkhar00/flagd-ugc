class PipelineError(Exception):
    """Base class for expected pipeline failures."""


class ConfigurationError(PipelineError):
    pass


class AssetError(PipelineError):
    pass


class QualityError(PipelineError):
    pass


class HookReviewError(QualityError):
    """A reviewed draft whose rejected items can be repaired independently."""

    def __init__(
        self,
        reason: str,
        *,
        proposal: list[dict[str, str]],
        rejected_demo_files: list[str],
    ):
        super().__init__(reason)
        self.proposal = proposal
        self.rejected_demo_files = rejected_demo_files


class ProviderValidationError(QualityError):
    """A provider rejected deterministic request structure before generation."""

    pass


class TransientError(PipelineError):
    pass


class ProxyBlockError(TransientError):
    """A managed HTTPS proxy temporarily blocked an otherwise valid request."""


class ProviderError(PipelineError):
    pass


class DeliveryUncertain(PipelineError):
    """A Telegram request may have succeeded and must not be repeated."""
