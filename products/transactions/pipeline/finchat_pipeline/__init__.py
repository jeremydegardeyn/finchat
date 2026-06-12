"""FinChat transactions streaming pipeline — modular Beam components.

Split so each concern is maintained and unit-tested on its own, and so a custom
worker container can ship them as an installed package (no save_main_session):

  schema      shared Silver schema + pipeline version   (no Beam dependency)
  validation  parse / schema enforcement / DLQ envelope  (no Beam dependency)
  enrich      lineage/provenance enrichment              (no Beam dependency)
  transforms  Beam DoFn wrappers (ParseAndValidate, tagged outputs)
  dlp         sampled DLP de-identification DoFn
  pipeline    Beam graph wiring + Flex Template entrypoint (composition root)
"""
