"""ParallaxOps vision worker: QC, COLMAP-based SfM, geometric QA and exports."""

__version__ = "0.1.0"

# Bumped when manifest.json / report.json / cameras.json change shape. The API and the worker
# roll out independently, so consumers must check this and refuse unknown major versions.
CONTRACT_VERSION = "1.0"
