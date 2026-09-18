class PipelineError(Exception):
    """Base class for expected pipeline failures."""


class ConfigurationError(PipelineError):
    pass


class AssetError(PipelineError):
    pass


class QualityError(PipelineError):
    pass


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
